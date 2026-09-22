"""
장중 내내 관심종목(기본값: 지금까지 수집된 전 종목 = store.symbols())을 반복
확인하면서, 전략 신호가 나오면 자동으로 주문을 넣는 라이브 매매 루프입니다.

main.py가 "하루 한 번 신호 확인하고 끝"이라면, 이건 "장중 내내 interval초마다
반복 확인"하는 버전입니다. 브로커/전략/리스크 코드는 main.py와 완전히 동일하게
재사용합니다 — 백테스트 때와 같은 이유로, 판단 로직이 실행 경로마다 갈라지면
나중에 반드시 어긋납니다.

전략(MACrossStrategy 등)은 원래 일봉 기준으로 설계됐지만, 여기서는 오늘자 마지막
행을 "지금 이 순간까지의 미완성 봉"으로 매번 갱신해서 넘깁니다 — 그래야 장중
가격이 바뀔 때마다 새로 판단할 수 있습니다. 이 실시간가는 realtime/stream_collector.py가
채워둔 realtime_ticks에서 가져오고, 그게 아직 없으면(수집기를 안 켰으면) 마지막
저장된 종가로 대체합니다 (그러면 사실상 main.py와 같은 판단이 매 반복마다 나옵니다
— 진짜 "실시간" 반응을 보려면 stream_collector.py를 먼저 켜두세요).

알려진 한계: 신호 판단에는 실시간가를 쓰지만, 실제 주문 가격(broker.place_order
내부의 ord_uv)은 KiwoomRestBroker.get_price()가 반환하는 "마지막 저장된 일봉 종가"
기준입니다 (main.py 때부터 있던 broker의 기존 동작이라 이번에 건드리지 않았습니다).
그래서 장중 변동폭이 큰 날은 실제 체결가가 신호 판단 시점의 가격과 다를 수 있습니다.

주문은 신호가 "바뀐" 시점에만 시도합니다(last_signal 추적). 처음엔 "포지션이 없을
때만 BUY"라는 규칙만으로 중복 주문을 막으려 했는데, 실측해보니 주문이 거부되면
포지션이 그대로라 신호가 유지되는 한 매 interval마다 같은(그리고 계속 거부될)
주문을 끝없이 재시도했습니다(실제로 증거금 부족 거부를 1분마다 30번 넘게 반복).
지금은 신호가 바뀔 때 딱 한 번만 시도하고, 성공(SUBMITTED/FILLED)했는지 여부를
액션에 그대로 반영합니다(성공 BUY/SELL, 실패 BUY_FAILED/SELL_FAILED). 가격이
이동평균 교차선 근처에서 오르내리면 신호 자체가 하루에도 여러 번 바뀔 수 있고,
그때마다는 여전히 매매가 시도됩니다 — 이건 버그가 아니라 이 방식으로 자주
확인할 때 생기는 정상적인 특성입니다.

손절(risk.stop_loss_price)은 신호와 무관하게 매 interval마다 우선 체크합니다.
원래는 backtest_engine.py에만 있고 여기엔 빠져 있었습니다 — 전략이 하향 교차로
SELL을 내기 전까지는 손실이 risk_limit_pct를 넘어도 자동으로 빠져나가는 장치가
없었다는 뜻입니다(실측 중 발견: 라이브로 실제 매수가 체결된 상태에서 손절 없이
장중 내내 노출되고 있었습니다). entry_prices에 진입가를 기억해뒀다가 실시간가가
그 아래로 떨어지면 전략 신호를 기다리지 않고 즉시 매도합니다. 프로세스를 재시작하면
이 기억은 초기화되므로, 시작 시점에 이미 보유 중인 종목은 원래 매수가를 모른 채
"지금 이 순간부터"를 기준으로 손절선을 잡습니다(콘솔에 경고로 남깁니다) — 정확한
원가 기준 손절은 아니지만, 손절이 아예 없는 것보다는 안전합니다.

장 시간(평일 09:00~15:30, KST)이 아니면 자동으로 대기합니다. 공휴일 캘린더는
확인하지 않습니다 (알려진 한계).

실행:
    python src/live_trade.py                      # store.symbols() 전체를 관심종목으로
    python src/live_trade.py 005930 000660         # 특정 종목만
    python src/live_trade.py --interval 30         # 확인 주기(초), 기본 60

Ctrl+C로 종료합니다.
"""

import sys
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_broker, build_strategy, build_llm_advisor
from risk.risk_manager import RiskManager

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_MARKET_OPEN_MIN = 9 * 60         # 09:00
_MARKET_CLOSE_MIN = 15 * 60 + 30  # 15:30


def _is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(_KST)
    if now.weekday() >= 5:  # 5=토, 6=일
        return False
    minutes = now.hour * 60 + now.minute
    return _MARKET_OPEN_MIN <= minutes <= _MARKET_CLOSE_MIN


def _latest_price(store: MarketDataStore, symbol: str) -> float | None:
    """realtime_ticks에 오늘 체결이 있으면 그 값, 없으면 마지막 저장된 일봉 종가."""
    ticks = store.recent_realtime_ticks(symbol, limit=1)
    if not ticks.empty:
        return float(ticks.iloc[-1]["price"])
    df = store.load(symbol)
    if df.empty:
        return None
    return float(df.iloc[-1]["close"])


def _with_live_bar(df: pd.DataFrame, today: str, live_price: float) -> pd.DataFrame:
    """전략이 보는 마지막 행을 '지금까지의 오늘자 미완성 봉'으로 갱신/추가합니다."""
    df = df.copy()
    if not df.empty and df.iloc[-1]["date"] == today:
        last = df.index[-1]
        df.loc[last, "high"] = max(float(df.loc[last, "high"]), live_price)
        df.loc[last, "low"] = min(float(df.loc[last, "low"]), live_price)
        df.loc[last, "close"] = live_price
    else:
        new_row = {"date": today, "open": live_price, "high": live_price,
                   "low": live_price, "close": live_price, "volume": 0}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    return df


def _check_symbol(symbol: str, store, broker, risk: RiskManager, llm_advisor, config: dict,
                   last_signal: dict[str, str], entry_prices: dict[str, float], max_concurrent_positions: int):
    df = store.load(symbol)
    if df.empty:
        return

    live_price = _latest_price(store, symbol)
    if live_price is None:
        return

    position = broker.get_position(symbol)

    # 손절은 전략 신호를 기다리지 않고 매 interval마다 최우선으로 체크합니다
    # (전략이 하향 교차로 SELL을 낼 때까지 기다리면 그 사이 손실이 risk_limit_pct를
    # 얼마든지 넘어설 수 있습니다).
    if position > 0 and symbol in entry_prices:
        stop_price = risk.stop_loss_price(entry_prices[symbol])
        if live_price <= stop_price:
            result = broker.place_order(symbol, "SELL", position)
            success = result.get("status") in ("SUBMITTED", "FILLED")
            action = "SELL" if success else "SELL_FAILED"
            detail = (f"손절 발동 (진입가 {entry_prices[symbol]:,.0f}원 -> 현재가 {live_price:,.0f}원, "
                       f"손절선 {stop_price:,.0f}원): {result}")
            if success:
                entry_prices.pop(symbol, None)
                last_signal[symbol] = "SELL"  # 이번 턴에 이미 팔았으니 전략 SELL 신호로 또 팔지 않도록
            opinion = llm_advisor.get_opinion(symbol, context=f"{symbol} 손절 발동 (현재가 {live_price:,.0f}원)")
            store.log_decision(symbol, "STOP_LOSS", opinion["stance"], action, detail)
            log.warning(f"[{symbol}] 손절 발동! 액션={action} {detail}")
            return  # 이번 턴은 손절 처리로 끝 — 전략 신호는 다음 턴부터 다시 봄

    live_df = _with_live_bar(df, datetime.now(_KST).strftime("%Y-%m-%d"), live_price)
    strategy = build_strategy(config, symbol)
    signal = strategy.generate_signal(symbol, live_df)

    changed = last_signal.get(symbol) != signal
    last_signal[symbol] = signal

    # 신호가 바뀐 시점에만 실제로 주문을 시도합니다. position==0/>0만으로 막으면
    # 주문이 거부됐을 때도 포지션이 그대로라 신호가 유지되는 한 매 interval마다
    # 계속 같은 주문을 다시 넣게 됩니다 (실측 확인됨: 증거금 부족으로 거부된
    # 주문을 1분마다 계속 재시도). changed 조건으로 신호 전환당 한 번만 시도합니다.
    action, detail = "NONE", ""
    if changed:
        if signal == "BUY" and position == 0:
            # entry_prices에 있는 종목 수 = 지금 보유 중이라고 이 프로세스가 알고 있는 종목 수
            # (시작할 때 실제 보유분도 백필해뒀으므로 신뢰할 수 있음). 동시 보유 종목 수를
            # 제한해서 여러 종목이 한꺼번에 신호를 낼 때 자금이 소수 종목에 몰리지 않게 합니다.
            if len(entry_prices) >= max_concurrent_positions:
                detail = f"동시 보유 한도 도달 ({len(entry_prices)}/{max_concurrent_positions}) — 매수 건너뜀"
            else:
                qty = risk.calc_buy_quantity(broker.get_cash(), live_price)
                if qty > 0:
                    result = broker.place_order(symbol, "BUY", qty)
                    success = result.get("status") in ("SUBMITTED", "FILLED")
                    action = "BUY" if success else "BUY_FAILED"
                    detail = str(result)
                    if success:
                        entry_prices[symbol] = live_price
                else:
                    detail = "매수 수량 0 (현금 부족 또는 비중 한도)"
        elif signal == "SELL" and position > 0:
            result = broker.place_order(symbol, "SELL", position)
            success = result.get("status") in ("SUBMITTED", "FILLED")
            action = "SELL" if success else "SELL_FAILED"
            detail = str(result)
            if success:
                entry_prices.pop(symbol, None)

    # 매 interval마다 전 종목을 다 로그로 남기면 decisions 테이블이 금방 커지므로,
    # 실제 주문을 시도했거나 신호가 바뀐 경우에만 기록합니다.
    if action != "NONE" or changed:
        opinion = llm_advisor.get_opinion(
            symbol, context=f"{symbol} 실시간 신호: {signal} (현재가 {live_price:,.0f}원)"
        )
        store.log_decision(symbol, signal, opinion["stance"], action, detail)
        log.info(f"[{symbol}] 신호={signal} 현재가={live_price:,.0f}원 액션={action} {detail}")


def run(symbols: list[str] | None, interval: int = 60):
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    broker = build_broker(config, store)
    llm_advisor = build_llm_advisor(config)
    # 매수 비중은 그때그때 남은 현금이 아니라, 이 세션 시작 시점에 한 번 읽은 고정
    # 총자본(get_total_deposit()) 기준으로 계산합니다. (이건 세션 내내 고정 — 대시보드로도
    # 못 바꿉니다. 바꿀 수 있는 건 그 자본의 몇 %를 쓸지/몇 종목까지 들지입니다.)
    starting_cash = broker.get_total_deposit()
    risk_defaults = {
        "max_position_pct": config["risk"]["max_position_pct"],
        "risk_limit_pct": config["risk"]["risk_limit_pct"],
        "max_concurrent_positions": config["risk"].get("max_concurrent_positions", 5),
    }
    # 대시보드(pages/3_리스크_설정.py)가 이 값을 바꿀 수 있도록 DB에 최초 기본값을 심어둡니다.
    # 이미 값이 있으면(이전에 대시보드에서 바꿨으면) 손대지 않습니다.
    store.seed_risk_settings_if_missing(**risk_defaults)

    # 관심종목(watchlist)은 "수집해둔 전체 종목"(store.symbols())과 다릅니다 — 전체
    # 종목을 아무리 많이 받아도 실시간 매매 대상은 watchlist에 있는 것만입니다.
    # 최초 실행이라 watchlist가 비어있으면, 지금까지 수집된 종목으로 시드합니다
    # (이후로는 대시보드에서 관리 — collect_all.py로 전체 종목을 더 받아도 안 늘어남).
    store.seed_watchlist_if_empty(store.symbols())
    symbols = symbols or store.get_watchlist()
    if not symbols:
        log.error("관심종목이 비어 있습니다. collect_data.py로 종목을 먼저 수집하거나 "
                   "대시보드에서 관심종목을 추가하세요.")
        return

    log.info(f"실행 환경: {config['_meta']['detected_env']} / 매매모드: {config['broker']['provider']} "
             f"(is_mock={config.get('kiwoom', {}).get('is_mock', True)})")

    # 이미 보유 중인 종목은 진짜 매수가를 모르므로(프로세스 재시작 시 메모리가 비워짐),
    # 지금 이 순간의 가격을 잠정 진입가로 써서 최소한 "지금부터"는 손절이 걸리게 합니다.
    last_signal: dict[str, str] = {}
    entry_prices: dict[str, float] = {}
    for symbol in symbols:
        try:
            if broker.get_position(symbol) > 0:
                price = _latest_price(store, symbol)
                if price is not None:
                    entry_prices[symbol] = price
                    log.warning(f"[{symbol}] 이미 보유 중인 포지션을 발견했습니다. 원래 매수가를 몰라 "
                                f"현재가({price:,.0f}원)를 잠정 진입가로 잡고 손절을 겁니다.")
        except Exception as exc:
            log.warning(f"[{symbol}] 보유 여부 확인 중 오류: {exc}")

    log.info(f"라이브 매매 감시 시작: {len(symbols)}종목, {interval}초 간격 (Ctrl+C로 종료)")

    prev_risk_settings = None
    try:
        while True:
            if not _is_market_open():
                log.info("장 시간(평일 09:00~15:30)이 아닙니다. 대기합니다...")
                time.sleep(interval)
                continue

            # 매 확인 주기마다 다시 읽습니다 — 대시보드에서 값을 바꾸면 프로세스를
            # 재시작하지 않아도 다음 주기부터 바로 반영됩니다.
            risk_settings = store.get_risk_settings(risk_defaults)
            if risk_settings != prev_risk_settings:
                log.info(f"리스크 설정 적용: 종목당 비중 {risk_settings['max_position_pct']}%, "
                         f"손절선 {risk_settings['risk_limit_pct']}%, "
                         f"동시보유한도 {risk_settings['max_concurrent_positions']}종목"
                         + ("" if prev_risk_settings is None else " (대시보드에서 변경됨)"))
                prev_risk_settings = risk_settings
            risk = RiskManager(risk_settings["max_position_pct"], risk_settings["risk_limit_pct"],
                                starting_cash=starting_cash)
            max_concurrent_positions = risk_settings["max_concurrent_positions"]

            for symbol in symbols:
                try:
                    _check_symbol(symbol, store, broker, risk, llm_advisor, config, last_signal,
                                   entry_prices, max_concurrent_positions)
                except Exception as exc:  # 종목 하나 실패해도 나머지는 계속 진행
                    log.warning(f"[{symbol}] 확인 중 오류: {exc}")

            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("종료합니다.")


if __name__ == "__main__":
    args = sys.argv[1:]
    interval_arg = 60
    if "--interval" in args:
        idx = args.index("--interval")
        interval_arg = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]
    run(args or None, interval=interval_arg)

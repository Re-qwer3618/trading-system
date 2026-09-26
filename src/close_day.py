"""
실시간 수집기(realtime/stream_collector.py)가 쌓아둔 체결 틱(realtime_ticks)을 종목별
1분봉으로 묶어, 기존 종목별 분봉 데이터(intraday_ohlcv, interval="1m")에 "이어붙입니다".

이어쓰기 규칙:
- 공식 분봉(collect_all.py --minute, ka10080)이 이미 있는 분(minute)은 절대 덮어쓰지 않습니다.
  실시간 틱에서 만든 분봉은 공식 데이터가 아직 없는 "빈 구간"만 메웁니다 (INSERT ... DO NOTHING).
  나중에 공식 분봉을 받으면 같은 분은 공식 값으로 덮어써져서(upsert) 자연스럽게 교체됩니다.
- 장중(또는 20시 이전)에 돌리면 아직 진행 중인 마지막 1분은 제외합니다 (미완성 봉 방지).
- 병합 결과는 realtime_merge_log에 남습니다. 인자 없이 실행하면 "아직 안 합친 모든 날짜"를
  한 번에 처리하고(수집기를 켜둔 날마다 빠짐없이 이어붙이기), 이미 합친 (날짜, 종목)은 틱 수가
  그대로면 건너뜁니다 — 몇 번을 실행해도 안전합니다(멱등).
- 그날 공식 일봉(ohlcv)이 없는 종목은 정규장(09:00~15:30) 틱으로 일봉도 근사해 채웁니다
  (장 마감 후에만). 공식 일봉을 받으면(collect_all.py) 당일 봉이 공식 값으로 교체됩니다.

호가(realtime_orderbook)는 캔들이 아니라 그대로 두고 씁니다 — 분/일봉으로 뭉개면
의미가 없는 스냅샷 데이터라서요. 종목별로 시간순으로 계속 쌓이는 이어쓰기 방식이고,
데이터 카탈로그(data_catalog)에서 종목별 건수/기간을 볼 수 있습니다.

실행:
    python src/close_day.py                        # 아직 안 합친 모든 날짜, 실시간 틱이 있던 전 종목
    python src/close_day.py 005930 000660           # 특정 종목만
    python src/close_day.py --date 2026-09-22        # 특정 날짜만 (그날 놓쳤을 때)
    python src/close_day.py --force                  # 이미 합친 것도 다시 계산
"""

import sys
import logging
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

from config_loader import load_config
from data_layer.storage import MarketDataStore
from data_layer.catalog import refresh_catalog

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# 공식 분봉(collect_all.py --minute)과 같은 interval 이름을 써서 한 시리즈로 이어붙입니다.
# (예전에는 "1m_rt"로 따로 저장했지만, 차트/전략이 두 시리즈를 따로 읽어야 해서 합쳤습니다)
MINUTE_INTERVAL = "1m"

_KST = ZoneInfo("Asia/Seoul")


def _ticks_to_minute_bars(ticks: pd.DataFrame, date: str) -> pd.DataFrame:
    """realtime_ticks(ts=HHMMSS 또는 그 이상 자리수 문자열, 날짜 없음)를 그날 날짜와
    합쳐 1분봉으로 묶습니다."""
    ts_str = ticks["ts"].astype(str).str.zfill(6).str.slice(-6)
    timestamps = pd.to_datetime(date + " " + ts_str, format="%Y-%m-%d %H%M%S", errors="coerce")

    df = pd.DataFrame({
        "timestamp": timestamps,
        "price": ticks["price"].astype(float),
        "volume": ticks["volume"].astype(int),
    }).dropna(subset=["timestamp"]).sort_values("timestamp")

    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    priced = df.set_index("timestamp")["price"]
    bars = priced.resample("1min").ohlc()
    bars["volume"] = df.set_index("timestamp")["volume"].resample("1min").sum()
    bars = bars.dropna(subset=["open"]).reset_index()  # 틱이 없던 분은 버림 (가짜 평봉 방지)
    bars["timestamp"] = bars["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return bars[["timestamp", "open", "high", "low", "close", "volume"]]


def _ticks_to_daily_bar(ticks: pd.DataFrame, date: str) -> pd.DataFrame | None:
    """정규장(09:00~15:30) 체결만으로 일봉을 근사합니다 (시간외/프리마켓 체결이 고가·저가·
    종가를 오염시키지 않게). 정규장 틱이 없거나, 수집기가 장 끝(15:20) 전에 꺼져서 하루를
    다 담지 못한 경우엔 None — 반쪽짜리 일봉이 진짜 일봉처럼 남는 것보다 비어있는 편이 낫습니다."""
    hhmmss = ticks["ts"].astype(str).str.zfill(6).str.slice(-6)
    regular = ticks[(hhmmss >= "090000") & (hhmmss <= "153000")]
    if regular.empty or hhmmss[regular.index].max() < "152000":
        return None
    prices = regular["price"].astype(float)
    return pd.DataFrame([{
        "date": date,
        "open": float(prices.iloc[0]),
        "high": float(prices.max()),
        "low": float(prices.min()),
        "close": float(prices.iloc[-1]),
        "volume": int(regular["volume"].astype(int).sum()),
    }])


def merge_realtime(store: MarketDataStore, date: str | None = None, symbols: list[str] | None = None,
                   force: bool = False) -> dict:
    """실시간 틱 -> 종목별 1분봉 병합 (모듈 docstring의 이어쓰기 규칙 참고).

    date=None이면 실시간 틱이 있는 모든 날짜 중 아직 안 합친 것을 처리합니다.
    반환: {"symbols": 처리한 (날짜,종목) 수, "bars_added": 새로 채운 분봉 수, "daily_filled": 근사 일봉 수,
           "touched": 카탈로그를 갱신해야 할 종목들}
    """
    now = datetime.now(_KST)
    today = now.strftime("%Y-%m-%d")
    dates = [date] if date else store.realtime_tick_dates()

    result = {"symbols": 0, "bars_added": 0, "daily_filled": 0, "touched": set()}
    for d in dates:
        counts = store.realtime_tick_counts(d)
        merged = {} if force else store.merged_realtime_symbols(d)
        targets = [s for s in (symbols or counts) if counts.get(s) and merged.get(s) != counts[s]]
        if not targets:
            continue

        # 20시(야간 세션 종료) 전의 오늘은 마지막 분이 아직 진행 중일 수 있음.
        minute_day_open = d == today and now.hour < 20
        # 정규장 근사 일봉은 오늘 15:40 이후 또는 과거 날짜만 (장중에 반쪽짜리 일봉을 만들지 않음).
        daily_ok = d < today or (d == today and now.hour * 60 + now.minute >= 15 * 60 + 40)

        for symbol in targets:
            ticks = store.ticks_for_date(symbol, d)
            if ticks.empty:
                continue
            bars = _ticks_to_minute_bars(ticks, d)
            if minute_day_open and len(bars):
                bars = bars.iloc[:-1]
            added = store.insert_intraday_if_absent(symbol, MINUTE_INTERVAL, bars)
            store.log_realtime_merge(d, symbol, len(ticks), len(bars), added)
            result["symbols"] += 1
            result["bars_added"] += added
            result["touched"].add(symbol)
            log.info(f"[{symbol}] {d} 실시간 틱 {len(ticks)}건 -> 1분봉 {len(bars)}개 중 {added}개를 "
                     f"공식 분봉의 빈 구간에 이어붙임 (나머지는 이미 공식 분봉이 있어 유지)")

            if daily_ok and not store.has_daily(symbol, d):
                daily_bar = _ticks_to_daily_bar(ticks, d)
                if daily_bar is not None:
                    store.upsert(symbol, daily_bar)
                    result["daily_filled"] += 1
                    log.info(f"[{symbol}] {d} 공식 일봉이 아직 없어 실시간 틱으로 근사 채움 "
                             f"(collect_all.py로 공식 일봉을 받으면 교체됩니다).")

    result["touched"] = sorted(result["touched"])
    if result["touched"]:
        refresh_catalog(store, result["touched"])
    return result


def close_day(symbols: list[str] | None = None, date: str | None = None, force: bool = False):
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    result = merge_realtime(store, date=date, symbols=symbols, force=force)
    if not result["symbols"]:
        log.info("새로 합칠 실시간 체결 데이터가 없습니다 (이미 모두 병합됐거나, 수집기를 켜둔 날이 없음).")
        return
    log.info(f"병합 완료: {result['symbols']}건, 분봉 {result['bars_added']}개 추가, "
             f"근사 일봉 {result['daily_filled']}개")


if __name__ == "__main__":
    args = sys.argv[1:]
    date_arg = None
    if "--date" in args:
        idx = args.index("--date")
        date_arg = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    force_arg = "--force" in args
    args = [a for a in args if a != "--force"]
    close_day(args or None, date=date_arg, force=force_arg)

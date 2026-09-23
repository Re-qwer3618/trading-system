"""
매일 실행해서 '오늘 신호가 있으면 주문을 넣는' 방식의 진입점입니다.
매번 판단 결과를 저장소의 decisions 로그에 남겨서, 대시보드(dashboard.py)가
과거 이력을 볼 수 있게 합니다.

실행:
    python src/main.py 005930
"""

import sys
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_broker, build_strategy, build_llm_advisor
from risk.risk_manager import RiskManager
from agents.decision_maker import DecisionMaker

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    config = load_config()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"

    log.info(f"실행 환경: {config['_meta']['detected_env']} / 매매모드: {config['broker']['provider']}")

    store = MarketDataStore(config["data"]["db_path"])
    broker = build_broker(config, store)
    strategy = build_strategy(config, symbol)  # symbol별로 다른 전략을 배정했다면 그걸 사용

    # 매수 비중은 그때그때 남은 현금이 아니라 고정된 총자본(get_total_deposit()) 기준으로
    # 계산합니다. 비중/손절선은 대시보드(pages/3_리스크_설정.py)에서 바꾼 값이 있으면
    # 그걸 우선 씁니다 — live_trade.py와 같은 저장소(settings 테이블)를 공유합니다.
    risk_defaults = {
        "max_position_pct": config["risk"]["max_position_pct"],
        "risk_limit_pct": config["risk"]["risk_limit_pct"],
        "max_concurrent_positions": config["risk"].get("max_concurrent_positions", 5),
    }
    store.seed_risk_settings_if_missing(**risk_defaults)
    risk_settings = store.get_risk_settings(risk_defaults)
    risk = RiskManager(risk_settings["max_position_pct"], risk_settings["risk_limit_pct"],
                        starting_cash=broker.get_total_deposit())
    llm_advisor = build_llm_advisor(config)  # 3단계 전까지는 항상 NEUTRAL

    df = store.load(symbol)
    if df.empty:
        log.error(f"{symbol}의 저장된 데이터가 없습니다. collect_data.py를 먼저 실행하세요.")
        return

    signal = strategy.generate_signal(symbol, df)
    # 튜닝 성공/실패 이력을 컨텍스트에 같이 넘겨둡니다. 지금은 NoOpAdvisor라 안 쓰이지만,
    # 나중에 llm/advisor.py에 실제 LLM을 붙이면 "이 종목/이 설정은 과거에 이랬다"는 걸
    # 그대로 참고할 수 있습니다 (tuning/analyst_tuner.py, storage.tuning_llm_context 참고).
    tuning_context = store.tuning_llm_context(symbol, "history")
    opinion = llm_advisor.get_opinion(
        symbol, context=f"{symbol} 규칙기반 신호: {signal}\n{tuning_context}"
    )
    log.info(f"[{symbol}] 규칙기반 신호: {signal} | LLM 참고의견: {opinion['stance']} ({opinion['reasoning']})")

    # 4-역할 위원회(과거/현재/비교 분석가 + 최종 결정권자). config["decision"]["enabled"]가
    # false(기본값)면 완전히 건너뛰어 기존 동작과 100% 동일합니다.
    if config.get("decision", {}).get("enabled", False):
        committee = DecisionMaker(store, config).decide(symbol)
        log.info(
            f"[{symbol}] 위원회 최종판단: {committee['action']} (확신도 {committee['confidence']}%) "
            f"| 위원별 의견: {committee['votes']}"
        )
        store.log_decision_committee(symbol, committee["action"], committee["confidence"],
                                      committee["votes"], committee["reasoning"])
        if signal == "BUY" and committee["action"] == "SELL":
            log.info("위원회가 매도 우세로 판단해 매수 신호를 보류(HOLD)합니다.")
            signal = "HOLD"
        elif signal == "SELL" and committee["action"] == "BUY":
            log.info("위원회가 매수 우세로 판단해 매도 신호를 보류(HOLD)합니다.")
            signal = "HOLD"

    action = "NONE"
    detail = ""

    if signal == "BUY":
        price = broker.get_price(symbol)
        qty = risk.calc_buy_quantity(broker.get_cash(), price)
        if qty > 0:
            result = broker.place_order(symbol, "BUY", qty)
            log.info(f"주문 결과: {result}")
            action = "BUY"
            detail = str(result)
        else:
            log.info("매수 수량이 0이라 주문하지 않습니다 (현금 부족 또는 비중 한도).")
            detail = "매수 수량 0"

    elif signal == "SELL":
        held = broker.get_position(symbol)
        if held > 0:
            result = broker.place_order(symbol, "SELL", held)
            log.info(f"주문 결과: {result}")
            action = "SELL"
            detail = str(result)
        else:
            log.info("보유 수량이 없어 매도하지 않습니다.")
            detail = "보유 수량 없음"

    else:
        log.info("HOLD — 오늘은 매매하지 않습니다.")
        detail = "HOLD"

    store.log_decision(symbol, signal, opinion["stance"], action, detail)


if __name__ == "__main__":
    main()

"""
실행:
    python src/run_backtest.py 005930
"""

import sys
import json
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_strategy
from risk.risk_manager import RiskManager
from backtest.backtest_engine import BacktestEngine

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    config = load_config()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"

    store = MarketDataStore(config["data"]["db_path"])
    strategy = build_strategy(config)
    # 매수 비중은 백테스트 내내 최초 자본(starting_cash) 기준으로 고정합니다 — live_trade.py와
    # 동일한 계산 방식이어야 백테스트 결과가 실전 사이징을 대표합니다.
    risk = RiskManager(config["risk"]["max_position_pct"], config["risk"]["risk_limit_pct"],
                        starting_cash=config["broker"]["starting_cash"])

    bt_cfg = config.get("backtest", {})
    engine = BacktestEngine(
        store, strategy, risk, config["broker"]["starting_cash"],
        fee_rate=bt_cfg.get("fee_rate", 0.00015),
        tax_rate=bt_cfg.get("tax_rate", 0.0018),
        slippage_rate=bt_cfg.get("slippage_rate", 0.001),
    )

    result = engine.run(symbol, benchmark_code=bt_cfg.get("benchmark_code"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

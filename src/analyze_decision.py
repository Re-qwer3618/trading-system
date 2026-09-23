"""
4개 역할(과거/현재/비교 분석가 + 최종 결정권자)을 한 번에 실행해서
위원회 전체 리포트를 보여주는 CLI입니다.

실행:
    python src/analyze_decision.py 005930
"""

import sys
import json
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from agents.decision_maker import DecisionMaker

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    config = load_config()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"

    store = MarketDataStore(config["data"]["db_path"])
    decision_maker = DecisionMaker(store, config)
    result = decision_maker.decide(symbol)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print()
    print(f"[최종 결정] {result['symbol']}: {result['action']} (확신도 {result['confidence']}%)")
    print(f"위원별 의견: {result['votes']}")
    print(result["reasoning"])

    store.log_decision_committee(symbol, result["action"], result["confidence"], result["votes"], result["reasoning"])


if __name__ == "__main__":
    main()

"""
저장된 일봉 데이터를 규칙기반으로 분석해서 사람이 읽을 수 있는 요약을 냅니다.
나중에 LLM(gemma-2-9b)을 붙일 자리는 agents/history_analyst.py의 analyze()
반환값(dict)을 그대로 프롬프트에 넣는 식으로 확장하면 됩니다.

실행:
    python src/analyze_history.py 005930
"""

import sys
import json
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from agents.history_analyst import HistoryAnalyst

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    config = load_config()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"

    store = MarketDataStore(config["data"]["db_path"])
    analyst = HistoryAnalyst(store)
    result = analyst.analyze(symbol)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    print(result["summary"])


if __name__ == "__main__":
    main()

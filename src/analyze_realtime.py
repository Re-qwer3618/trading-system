"""
realtime/stream_collector.py가 쌓아둔 실시간 체결 데이터를 주기적으로
분석해서 콘솔에 찍어주는 감시 스크립트입니다. Ctrl+C로 종료합니다.

실행:
    python src/analyze_realtime.py 005930           # 5초 간격 (기본)
    python src/analyze_realtime.py 005930 10        # 10초 간격
"""

import sys
import time
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from agents.realtime_analyst import RealtimeAnalyst

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    config = load_config()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"
    interval_sec = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

    store = MarketDataStore(config["data"]["db_path"])
    analyst = RealtimeAnalyst(store)

    log.info(f"[{symbol}] 실시간 분석 감시 시작 ({interval_sec}초 간격, Ctrl+C로 종료)")
    try:
        while True:
            result = analyst.analyze(symbol)
            log.info(result["summary"])
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        log.info("종료합니다.")


if __name__ == "__main__":
    main()

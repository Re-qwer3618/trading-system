"""
종목 리스트를 받아서, 저장소에 없는 부분만 채워넣습니다.
(전체 재다운로드 아님 — 증분 수집)

실행:
    python src/collect_data.py 005930 000660
    python src/collect_data.py --full 005930   # 전체 재수집 (아래 설명 참고)

--full이 필요한 경우: 연속조회(페이지네이션)와 수정주가 적용을 나중에 추가했습니다
(kiwoom_rest_provider.py 참고). 그 전에 이미 모아둔 데이터는 비수정주가·한 페이지
분량뿐이라, 최소 한 번은 --full로 다시 받아서 기존 행을 덮어써야 합니다.
"""

import sys
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_data_provider

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def collect(symbols: list[str], full: bool = False):
    config = load_config()
    provider = build_data_provider(config)
    store = MarketDataStore(config["data"]["db_path"])

    for symbol in symbols:
        last_date = None if full else store.last_saved_date(symbol)
        if full:
            log.info(f"[{symbol}] --full: 전체 기간을 다시 받아 기존 데이터를 덮어씁니다.")
        elif last_date:
            log.info(f"[{symbol}] 마지막 저장일 {last_date} 이후 데이터만 수집합니다.")
        else:
            log.info(f"[{symbol}] 저장된 데이터가 없어 전체 수집합니다.")

        df = provider.fetch_ohlcv(symbol, start_date=last_date)
        store.upsert(symbol, df)
        log.info(f"[{symbol}] {len(df)}건 저장 완료.")


if __name__ == "__main__":
    args = sys.argv[1:]
    full = "--full" in args
    symbols = [a for a in args if a != "--full"] or ["005930"]  # 인자 없으면 삼성전자로 테스트
    collect(symbols, full=full)

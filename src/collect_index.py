"""
시장 벤치마크(코스피/코스닥 등) 지수 일봉을 수집합니다. 종목 백테스트 결과를
시장 대비로 비교(알파 계산)하려면 이 데이터가 있어야 합니다 — 개별 종목 OHLCV만
있으면 전략이 잘한 건지 그냥 시장이 올라서 딸려 오른 건지 구분할 수 없습니다.

collect_data.py와 같은 증분 수집 원칙(전체 재다운로드 대신 마지막 저장일 이후만)을
따릅니다.

실행:
    python src/collect_index.py            # 기본값(001=코스피, 101=코스닥) 수집
    python src/collect_index.py 001        # 코스피 종합만
"""

import sys
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_data_provider

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def collect(index_codes: list[str]):
    config = load_config()
    provider = build_data_provider(config)
    if not hasattr(provider, "fetch_index_ohlcv"):
        log.error(f"{type(provider).__name__}는 지수 데이터 수집을 지원하지 않습니다 (kiwoom_rest 전용).")
        return
    store = MarketDataStore(config["data"]["db_path"])

    for code in index_codes:
        last_date = store.last_saved_index_date(code)
        if last_date:
            log.info(f"[지수 {code}] 마지막 저장일 {last_date} 이후 데이터만 수집합니다.")
        else:
            log.info(f"[지수 {code}] 저장된 데이터가 없어 전체 수집합니다.")

        df = provider.fetch_index_ohlcv(code, start_date=last_date)
        store.upsert_index(code, df)
        log.info(f"[지수 {code}] {len(df)}건 저장 완료.")


if __name__ == "__main__":
    codes = sys.argv[1:] or ["001", "101"]  # 인자 없으면 코스피/코스닥 종합
    collect(codes)

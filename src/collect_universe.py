"""
전체 종목 리스트(코스피/코스닥 등)를 받아서 universe 테이블에 채워넣습니다.
이 리스트가 있어야 collect_all.py가 "어떤 종목들을 다 수집해야 하는지"를 압니다.

ETF/ETN/우선주/스팩은 제외합니다 (실측 확인: 코스피/코스닥 종목 리스트 API(ka10099,
mrkt_tp=0/10)에 ETF/ETN이 그대로 섞여 나옵니다 — 별도 시장구분(ETF=8, ETN=60/70/90)로
조회해보면 그 안의 종목 전부가 0/10 리스트에도 100% 겹쳐 있었습니다). ETF/ETN은 그
시장 리스트들과 대조해서 걸러내고, 우선주/스팩은 이름 패턴으로 걸러냅니다(정식
시장구분이 따로 없음).

전체 종목 리스트 자체는 자주 바뀌지 않으므로(상장/상장폐지 정도), 매일 돌릴
필요는 없고 주기적으로(예: 주 1회) 갱신하면 충분합니다.

실행:
    python src/collect_universe.py            # config에 설정된 시장 전부
    python src/collect_universe.py 0 10        # 코스피(0), 코스닥(10)만
"""

import re
import sys
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_data_provider

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_ETF_MARKET_CODE = "8"
_ETN_MARKET_CODES = ["60", "70", "90"]  # ETN, 손실제한 ETN, 변동성 ETN
_SPAC_KEYWORD = "스팩"
# 우선주 이름 패턴: "삼성전자우", "삼성전자우B", "두산2우B" 처럼 종목명 끝에
# (숫자)우(영문자) 형태로 붙습니다. 일반 종목명이 우연히 "우"로 끝나는 경우는
# 실질적으로 없어 국내 스크리닝 툴에서 널리 쓰는 방식입니다.
_PREFERRED_PATTERN = re.compile(r"\d?우[A-Z]?$")


def _is_excluded_name(name: str) -> bool:
    return _SPAC_KEYWORD in name or bool(_PREFERRED_PATTERN.search(name))


def collect_universe(market_codes: list[str] | None = None):
    config = load_config()
    provider = build_data_provider(config)
    store = MarketDataStore(config["data"]["db_path"])

    market_codes = market_codes or config.get("data", {}).get("universe", {}).get("markets", ["0", "10"])
    log.info(f"전체 종목 리스트 수집 시작 (markets={market_codes})")

    fund_codes: set[str] = set()  # ETF + ETN 코드 (추종형 상품, 일반 종목 리스트에서 제외)
    if hasattr(provider, "fetch_universe"):
        try:
            fund_df = provider.fetch_universe([_ETF_MARKET_CODE, *_ETN_MARKET_CODES])
            fund_codes = set(fund_df["code"].astype(str))
            log.info(f"ETF/ETN {len(fund_codes)}종목 확인 — 일반 종목 리스트에서 제외합니다.")
        except Exception as exc:
            log.warning(f"ETF/ETN 목록 조회 실패, 제외 없이 진행합니다: {exc}")

    df = provider.fetch_universe(market_codes)
    if df.empty:
        log.warning("받아온 종목이 없습니다. App Key/Secret, 시장구분 값을 확인하세요.")
        return

    total_saved = 0
    for market, group in df.groupby("market"):
        group = group.drop(columns=["market"])
        before = len(group)
        filtered = group[~group["code"].astype(str).isin(fund_codes)]
        filtered = filtered[~filtered["name"].astype(str).apply(_is_excluded_name)]
        excluded = before - len(filtered)

        store.sync_universe(market, filtered)
        log.info(f"[market={market}] {before}종목 중 ETF/ETN/우선주/스팩 {excluded}건 제외, {len(filtered)}종목 저장.")
        total_saved += len(filtered)

    log.info(f"전체 {total_saved}개 종목 저장 완료. (dashboard/collect_all.py에서 사용)")


if __name__ == "__main__":
    codes = sys.argv[1:] or None
    collect_universe(codes)

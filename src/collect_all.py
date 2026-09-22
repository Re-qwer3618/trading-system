"""
universe 테이블에 있는 전체 종목을 대상으로 일봉(+옵션으로 분봉/틱)을
증분 수집합니다. collect_data.py가 "종목 몇 개를 손으로 지정"하는 용도라면,
이건 "전체 종목을 장 마감 후 한 번에 업데이트"하는 용도입니다.

키움 REST API는 초당 호출 횟수 제한이 있으므로, 종목 사이에 약간의 대기 시간을
둡니다 (config: data.rate_limit_sleep_sec).

실행:
    python src/collect_all.py                 # universe 전체, 일봉만
    python src/collect_all.py --minute         # 일봉 + 분봉(config 기본 간격)
    python src/collect_all.py --tick           # 일봉 + 틱
    python src/collect_all.py --info           # 일봉 + 기본정보(PER/PBR/시가총액 등)
    python src/collect_all.py --minute --tick --info  # 전부
    python src/collect_all.py --limit 50       # 테스트용으로 앞 50종목만
    python src/collect_all.py --full           # 전체 재수집 (수정주가/연속조회 적용 전
                                                # 데이터를 덮어쓸 때 최초 한 번, collect_data.py 참고)

주의: --minute/--tick/--info를 다 켜고 전체 종목(수천 개)을 돌리면 종목당 API 호출이
여러 번씩 늘어나서 몇 시간 단위로 걸릴 수 있습니다. 장 마감 후 스케줄러로 밤새
돌리는 용도로 설계됐습니다 — 실시간 매매/구독 대상(watchlist)은 이 스크립트가 전체
종목을 받아도 따로 관리되므로 늘어나지 않습니다 (MarketDataStore.get_watchlist() 참고).

Windows 작업 스케줄러에 등록해서 장 마감 후(예: 매일 16:00) 자동 실행하면
"장이 끝나면 전체 종목 데이터를 업데이트" 요구사항을 채울 수 있습니다.
(작업 스케줄러 등록 방법은 README 참고)
"""

import sys
import time
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_data_provider

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> dict:
    opts = {"minute": False, "tick": False, "info": False, "limit": None, "full": False}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--minute":
            opts["minute"] = True
        elif arg == "--tick":
            opts["tick"] = True
        elif arg == "--info":
            opts["info"] = True
        elif arg == "--full":
            opts["full"] = True
        elif arg == "--limit" and i + 1 < len(argv):
            opts["limit"] = int(argv[i + 1])
            i += 1
        i += 1
    return opts


def collect_all(minute: bool = False, tick: bool = False, info: bool = False,
                 limit: int | None = None, full: bool = False):
    config = load_config()
    provider = build_data_provider(config)
    store = MarketDataStore(config["data"]["db_path"])
    sleep_sec = config.get("data", {}).get("rate_limit_sleep_sec", 0.3)
    minute_scope = config.get("data", {}).get("intraday", {}).get("minute_scope", "5")
    tick_scope = config.get("data", {}).get("intraday", {}).get("tick_scope", "1")

    symbols = store.universe_codes()
    if not symbols:
        log.warning("universe 테이블이 비어 있습니다. 먼저 collect_universe.py를 실행하세요.")
        return
    if limit:
        symbols = symbols[:limit]

    log.info(f"전체 종목 일봉 수집 시작: {len(symbols)}개 (분봉={minute}, 틱={tick}, 기본정보={info})")

    ok, failed = 0, 0
    for i, symbol in enumerate(symbols, start=1):
        try:
            last_date = None if full else store.last_saved_date(symbol)
            df = provider.fetch_ohlcv(symbol, start_date=last_date)
            store.upsert(symbol, df)

            if minute:
                mdf = provider.fetch_minute(symbol, minute_scope=minute_scope)
                store.upsert_intraday(symbol, f"{minute_scope}m", mdf)
            if tick:
                tdf = provider.fetch_tick(symbol, tick_scope=tick_scope)
                store.upsert_intraday(symbol, f"{tick_scope}t", tdf)
            if info and hasattr(provider, "fetch_basic_info"):
                basic = provider.fetch_basic_info(symbol)
                store.upsert_basic_info(basic)

            ok += 1
            if i % 50 == 0 or i == len(symbols):
                log.info(f"진행 {i}/{len(symbols)} (성공 {ok}, 실패 {failed})")
        except Exception as exc:  # 종목 하나 실패해도 나머지는 계속 진행
            failed += 1
            log.warning(f"[{symbol}] 수집 실패: {exc}")
        time.sleep(sleep_sec)

    log.info(f"전체 종목 수집 완료: 성공 {ok}, 실패 {failed}")


if __name__ == "__main__":
    opts = _parse_args(sys.argv[1:])
    collect_all(minute=opts["minute"], tick=opts["tick"], info=opts["info"],
                limit=opts["limit"], full=opts["full"])

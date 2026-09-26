"""
universe 테이블에 있는 전체 종목을 대상으로 일봉(+옵션으로 분봉/틱/기본정보)을
증분 수집합니다. collect_data.py가 "종목 몇 개를 손으로 지정"하는 용도라면,
이건 "전체 종목을 장 마감 후 한 번에 업데이트"하는 용도입니다. 수집 규칙 자체는
대시보드의 "데이터 수집" 화면과 같은 엔진(data_layer/collector.py)을 씁니다.

키움 REST API는 초당 호출 횟수 제한이 있으므로, 종목 사이에 약간의 대기 시간을
둡니다 (config: data.rate_limit_sleep_sec).

실행:
    python src/collect_all.py                 # universe 전체, 일봉만
    python src/collect_all.py --minute         # 일봉 + 분봉(config 기본 간격, 증분)
    python src/collect_all.py --tick           # 일봉 + 틱
    python src/collect_all.py --info           # 일봉 + 기본정보(PER/PBR/시가총액 등)
    python src/collect_all.py --minute --tick --info  # 전부
    python src/collect_all.py --limit 50       # 테스트용으로 앞 50종목만
    python src/collect_all.py --full           # 일봉 전체 재수집 (수정주가/연속조회 적용 전
                                                # 데이터를 덮어쓸 때 최초 한 번, collect_data.py 참고)

    # 분봉 과거 확장 (기본 분봉 수집은 종목당 최근 3페이지 ≈ 2~3주라 얕습니다)
    python src/collect_all.py --minute --minute-days 60     # 오늘-60일까지 (이미 그만큼 있는 종목은 증분만)
    python src/collect_all.py --minute --minute-days max    # 서버가 가진 가장 오래된 분봉(약 1년)까지

    # 대상 종목 고르기
    python src/collect_all.py --missing --minute --minute-days 60   # 카탈로그가 "받아야 함"이라고 본 종목만
    python src/collect_all.py --watchlist --minute --minute-days max # 관심종목만
    python src/collect_all.py --symbols 005930 000660 --minute --minute-days max

주의: --minute/--tick/--info를 다 켜고 전체 종목(수천 개)을 돌리면 종목당 API 호출이
여러 번씩 늘어나서 몇 시간 단위로 걸릴 수 있고, --minute-days max는 종목당 최대 약 2분
(유동성 큰 종목 기준, 페이지당 약 1초)이라 전 종목이면 하루 이상 걸립니다. 종목은
"관심종목 -> 시가총액 큰 순"으로 처리하니 중간에 Ctrl+C로 멈춰도 중요한 종목부터 끝나 있고,
다시 실행하면 이미 받은 종목은 증분만 하므로 이어서 진행됩니다.

Windows 작업 스케줄러에 등록해서 장 마감 후(예: 매일 16:00) 자동 실행하면
"장이 끝나면 전체 종목 데이터를 업데이트" 요구사항을 채울 수 있습니다.
(작업 스케줄러 등록 방법은 README 참고)
"""

import sys
import logging
from config_loader import load_config
from data_layer.storage import MarketDataStore
from data_layer.collector import (CollectOptions, MAX_TARGET_DAYS, collect_symbols, prioritize)
from data_layer.catalog import find_gaps, refresh_catalog
from core.factory import build_data_provider

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # 페이지마다 찍히는 HTTP 로그가 진행 로그를 묻어버림
log = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> dict:
    opts = {"minute": False, "tick": False, "info": False, "limit": None, "full": False,
            "minute_days": 0, "missing": False, "watchlist": False, "symbols": []}
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
        elif arg == "--missing":
            opts["missing"] = True
        elif arg == "--watchlist":
            opts["watchlist"] = True
        elif arg == "--limit" and i + 1 < len(argv):
            opts["limit"] = int(argv[i + 1])
            i += 1
        elif arg == "--minute-days" and i + 1 < len(argv):
            value = argv[i + 1]
            opts["minute_days"] = MAX_TARGET_DAYS if value == "max" else int(value)
            i += 1
        elif arg == "--symbols":
            while i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                opts["symbols"].append(argv[i + 1])
                i += 1
        i += 1
    return opts


def collect_all(minute: bool = False, tick: bool = False, info: bool = False,
                limit: int | None = None, full: bool = False, minute_days: int = 0,
                missing: bool = False, watchlist: bool = False, symbols: list[str] | None = None):
    config = load_config()
    provider = build_data_provider(config)
    store = MarketDataStore(config["data"]["db_path"])
    data_cfg = config.get("data", {})
    intraday_cfg = data_cfg.get("intraday", {})

    if symbols:
        targets = list(symbols)
    elif watchlist:
        targets = store.get_watchlist()
    elif missing:
        refresh_catalog(store)  # 카탈로그가 오래됐을 수 있으니 먼저 다시 집계
        targets = find_gaps(store, min_minute_days=minute_days or 60)["symbol"].tolist()
    else:
        targets = store.universe_codes()
        if not targets:
            log.warning("universe 테이블이 비어 있습니다. 먼저 collect_universe.py를 실행하세요.")
            return
    targets = prioritize(store, targets)
    if limit:
        targets = targets[:limit]

    datasets = ["daily"] + [name for name, on in (("minute", minute), ("tick", tick), ("info", info)) if on]
    opts = CollectOptions(
        datasets=datasets, full=full, minute_target_days=minute_days,
        minute_scope=str(intraday_cfg.get("minute_scope", "1")),
        tick_scope=str(intraday_cfg.get("tick_scope", "1")),
        sleep_sec=data_cfg.get("rate_limit_sleep_sec", 0.3),
    )
    log.info(f"수집 시작: {len(targets)}종목 (데이터: {', '.join(datasets)}"
             f"{f', 분봉 과거 확장 {minute_days}일' if minute_days else ''})")

    def on_symbol(i, symbol, status, detail):
        if status == "failed":
            return  # collect_symbols가 이미 경고를 남김
        if i % 50 == 0 or i == len(targets) or minute_days:
            log.info(f"진행 {i}/{len(targets)} [{symbol}] {detail}")

    try:
        stats = collect_symbols(provider, store, targets, opts, on_symbol=on_symbol)
        log.info(f"수집 완료: 성공 {stats['ok']}, 실패 {stats['failed']}")
        touched = stats["processed"]
    except KeyboardInterrupt:
        log.info("중단합니다. 지금까지 받은 데이터는 저장돼 있습니다 (다시 실행하면 이어서 진행).")
        touched = None  # 어디까지 했는지 모르니 전체 재집계

    if touched is None or touched:
        result = refresh_catalog(store, touched)
        log.info(f"카탈로그 갱신: {result['rows']}행 ({result['seconds']}초)")


if __name__ == "__main__":
    a = _parse_args(sys.argv[1:])
    collect_all(minute=a["minute"], tick=a["tick"], info=a["info"], limit=a["limit"], full=a["full"],
                minute_days=a["minute_days"], missing=a["missing"], watchlist=a["watchlist"],
                symbols=a["symbols"])

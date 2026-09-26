"""
종목 데이터 수집 엔진. collect_all.py(명령줄)와 collect_worker.py(대시보드에서 시작한
백그라운드 작업)가 같은 로직을 씁니다 — 수집 규칙이 두 군데로 갈라지면 화면에서 받은
데이터와 스케줄러로 받은 데이터가 서로 달라지기 때문입니다.

데이터 종류(datasets):
    daily   일봉 (ka10081)       증분: 저장된 마지막 날짜 이후만. full=True면 전체 재수집
    minute  분봉 (ka10080)       증분 + "과거 확장"(아래)
    tick    틱봉 (ka10079)       한 번에 받을 수 있는 분량이 매우 짧아(체결 900건 ≈ 유동성 큰 종목은 1분 남짓)
                                 과거 확장은 하지 않고 최근 몇 페이지만 덮어씁니다
    info    기본정보 (ka10001)   종목당 스냅샷 1줄. info_fresh_hours 이내에 받았으면 건너뜀

분봉 과거 확장 (minute_target_days):
    키움 분봉 API는 최신 -> 과거 방향으로만 이어받을 수 있어서(기준일 지정 불가), 과거를 더
    받으려면 항상 최신 페이지부터 다시 걸어 내려가야 합니다. 그래서 종목별로 규칙을 정합니다.
      1) 목표 과거일(오늘 - N일)보다 오래된 분봉이 이미 있거나, 서버가 "더 오래된 건 없다"고
         답한 적이 있으면(exhausted 표식) -> 증분만 (저장된 마지막 날짜에 닿으면 멈춤, 보통 1~2페이지)
      2) 아니면 -> 목표 과거일이 든 페이지까지(또는 서버 데이터의 끝/페이지 상한까지) 내려감.
         서버가 끝났다고 답하면 exhausted 표식을 남겨서 다음부터 다시 시도하지 않습니다.
    실측(모의서버, 005930): 분봉은 약 1년 전(2025-09-01)까지 113페이지(약 2분)로 끝납니다.
"""

import time
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from data_layer.storage import MarketDataStore

log = logging.getLogger(__name__)

DATASETS = ("daily", "minute", "tick", "info")
DATASET_LABELS = {"daily": "일봉", "minute": "분봉(1분)", "tick": "틱", "info": "기본정보"}
MAX_TARGET_DAYS = 3650  # "서버 끝까지"를 뜻하는 큰 값 (실제로는 서버가 먼저 끝났다고 답함)


@dataclass
class CollectOptions:
    datasets: list[str] = field(default_factory=lambda: ["daily"])
    full: bool = False                 # 일봉 전체 재수집 (수정주가 반영 전 데이터를 덮어쓸 때)
    minute_target_days: int = 0        # 0=분봉 증분만, N>0=오늘-N일까지 과거 확장 (MAX_TARGET_DAYS=서버 끝까지)
    minute_scope: str = "1"
    tick_scope: str = "1"
    max_pages_minute: int = 150        # 종목 하나당 분봉 페이지 상한 (서버 최대 ≈ 113페이지)
    incremental_pages_minute: int = 60 # 증분 수집 페이지 상한 (저장된 구간에 닿으면 훨씬 먼저 멈춤)
    info_fresh_hours: int = 12         # 이 시간 안에 받은 기본정보는 다시 받지 않음
    sleep_sec: float = 0.3             # 종목 사이 대기 (키움 호출 제한 보호)

    @classmethod
    def from_dict(cls, d: dict) -> "CollectOptions":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


def _retry(fn, tries: int = 2, wait: float = 3.0):
    """일시적 오류(타임아웃 등)는 한 번 더 시도합니다 (실측: 100종목 중 1건이 타임아웃)."""
    for attempt in range(tries):
        try:
            return fn()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(wait)


def _collect_daily(provider, store: MarketDataStore, symbol: str, opts: CollectOptions) -> str:
    last_date = None if opts.full else store.last_saved_date(symbol)
    df = _retry(lambda: provider.fetch_ohlcv(symbol, start_date=last_date))
    store.upsert(symbol, df)
    if df.empty and last_date is None:
        store.set_exhausted(symbol, "daily")  # 서버에 일봉 자체가 없는 종목 (거래정지 등) — 다음부터 재시도 안 함
        return "일봉 없음(서버 데이터 없음)"
    return f"일봉 {len(df)}건"


def _collect_minute(provider, store: MarketDataStore, symbol: str, opts: CollectOptions) -> str:
    interval = f"{opts.minute_scope}m"
    span = store.intraday_span(symbol, interval)
    exhausted = store.is_exhausted(symbol, interval)
    first_date = span["first_ts"][:10] if span["first_ts"] else None
    last_date = span["last_ts"][:10] if span["last_ts"] else None

    target_start = None
    if opts.minute_target_days > 0:
        target_start = (datetime.now() - timedelta(days=opts.minute_target_days)).strftime("%Y-%m-%d")

    deepen = bool(target_start) and not exhausted and (first_date is None or first_date > target_start)
    if deepen:
        mode, kwargs = "확장", {"max_pages": opts.max_pages_minute, "stop_before": target_start}
    elif last_date:
        mode, kwargs = "증분", {"max_pages": opts.incremental_pages_minute, "stop_before": last_date}
    else:
        mode, kwargs = "최초", {}  # 기존 동작 그대로: provider 기본 페이지 수(max_pages_intraday)

    df = _retry(lambda: provider.fetch_minute(symbol, minute_scope=opts.minute_scope, **kwargs))
    store.upsert_intraday(symbol, interval, df)

    reason = df.attrs.get("end_reason")
    if reason == "end":
        # 서버가 "더 이어받을 게 없다"고 답함 = 서버가 가진 가장 오래된 분봉까지 받은 것.
        store.set_exhausted(symbol, interval)
    tail = {"end": "서버 끝까지", "stop": "목표 구간 도달", "max_pages": "페이지 상한",
            "cancelled": "중단됨"}.get(reason, "")
    return f"분봉 {mode} {len(df)}건({tail})"


def _collect_tick(provider, store: MarketDataStore, symbol: str, opts: CollectOptions) -> str:
    df = _retry(lambda: provider.fetch_tick(symbol, tick_scope=opts.tick_scope))
    store.upsert_intraday(symbol, f"{opts.tick_scope}t", df)
    return f"틱 {len(df)}건"


def _collect_info(provider, store: MarketDataStore, symbol: str, opts: CollectOptions) -> str:
    if not hasattr(provider, "fetch_basic_info"):
        return "기본정보 미지원 provider"
    existing = store.get_basic_info(symbol)
    if existing and existing.get("updated_at"):
        age = datetime.now() - datetime.fromisoformat(existing["updated_at"])
        if age < timedelta(hours=opts.info_fresh_hours):
            return "기본정보 최신(건너뜀)"
    store.upsert_basic_info(_retry(lambda: provider.fetch_basic_info(symbol)))
    return "기본정보 갱신"


_COLLECTORS = {"daily": _collect_daily, "minute": _collect_minute, "tick": _collect_tick, "info": _collect_info}


def collect_symbol(provider, store: MarketDataStore, symbol: str, opts: CollectOptions) -> str:
    """한 종목의 선택된 데이터종류를 모두 수집하고, 사람이 읽는 요약 한 줄을 돌려줍니다.
    하나라도 예외가 나면 그대로 던집니다 (호출한 쪽이 종목 단위로 실패 처리)."""
    parts = []
    for dataset in DATASETS:  # 고정 순서: 일봉 -> 분봉 -> 틱 -> 기본정보
        if dataset in opts.datasets:
            parts.append(_COLLECTORS[dataset](provider, store, symbol, opts))
    return " / ".join(parts)


def prioritize(store: MarketDataStore, symbols: list[str]) -> list[str]:
    """오래 걸리는 수집을 중간에 멈춰도 중요한 종목부터 끝나 있도록 순서를 정합니다:
    관심종목 -> 시가총액 큰 순 -> 나머지 (종목코드순). 입력에 없는 종목은 추가하지 않습니다."""
    watch = set(store.get_watchlist())
    with store._connect() as conn:
        caps = dict(conn.execute("SELECT symbol, market_cap FROM stock_basic_info").fetchall())
    return sorted(symbols, key=lambda s: (s not in watch, -(caps.get(s) or 0), s))


def collect_symbols(provider, store: MarketDataStore, symbols: list[str], opts: CollectOptions,
                    on_symbol=None, should_stop=None) -> dict:
    """symbols를 순서대로 수집합니다.

    on_symbol(index, symbol, status, detail): 종목 하나가 끝날 때마다 호출 (status: ok|failed|cancelled)
    should_stop(): True를 돌려주면 다음 종목으로 넘어가지 않고 멈춤 (provider.should_stop과 같은 함수를
                   넣어두면 종목 하나를 받는 도중에도 멈춥니다)
    """
    stats = {"ok": 0, "failed": 0, "cancelled": False, "processed": []}
    for i, symbol in enumerate(symbols, start=1):
        if should_stop is not None and should_stop():
            stats["cancelled"] = True
            break
        try:
            detail = collect_symbol(provider, store, symbol, opts)
            status = "cancelled" if (should_stop is not None and should_stop()) else "ok"
        except Exception as exc:  # 종목 하나 실패해도 나머지는 계속 진행
            detail, status = f"{type(exc).__name__}: {exc}", "failed"
            log.warning(f"[{symbol}] 수집 실패: {detail}")

        if status == "ok":
            stats["ok"] += 1
        elif status == "failed":
            stats["failed"] += 1
        else:
            stats["cancelled"] = True
        stats["processed"].append(symbol)
        if on_symbol is not None:
            on_symbol(i, symbol, status, detail)
        if stats["cancelled"]:
            break
        time.sleep(opts.sleep_sec)
    return stats

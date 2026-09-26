"""
과거 시세 데이터를 저장하는 곳.
핵심 원칙: "전체를 다시 받지 않고, 마지막으로 저장된 날짜 이후 데이터만 추가"

백테스팅과 실전 매매가 이 저장소 하나를 공유합니다.
그래야 백테스트할 때 본 데이터와 실전에서 판단하는 데이터가 어긋나지 않습니다.

3단계(전체 종목/분봉·틱/실시간) 확장:
- ohlcv        : 기존 일봉 테이블. 스키마를 건드리지 않아 기존 코드는 그대로 동작합니다.
- intraday_ohlcv: 분봉/틱 등 일봉이 아닌 시간단위 캔들 (symbol, interval)로 구분해 한 테이블에 보관.
- universe     : 전체 종목 리스트 (ka10099). 매일 장 마감 후 갱신 대상 종목을 정하는 데 씀.
- realtime_ticks: 웹소켓 실시간 체결가(0B) 스트림 적재소. realtime 수집기가 채우고,
                   realtime 분석 에이전트/close_day.py가 읽습니다.
- realtime_orderbook: 웹소켓 실시간 호가잔량(0D) 스냅샷 적재소. 매도/매수 각 10호가를
                   JSON으로 저장합니다 (가격/수량 20쌍씩 40컬럼을 늘어놓는 대신).
- settings      : 대시보드에서 조절하는 값(매수 비중, 손절선, 동시 보유 종목 수 등)을
                   담는 key-value 저장소. config/base.yaml은 "최초 기본값"이고, 여기
                   값이 있으면 이게 우선입니다. live_trade.py가 매 확인 주기마다 다시
                   읽으므로, 대시보드에서 바꾸면 프로세스 재시작 없이 다음 주기부터 반영됩니다.
- stock_basic_info: 종목 기본정보(PER/PBR/EPS/BPS/ROE, 시가총액 등, ka10001) 스냅샷.
                   차트가 아니라 종목당 최신 값 한 줄만 유지합니다(universe 테이블과 같은 설계).
- data_catalog  : (종목, 데이터종류)별 시작/끝/건수/거래일 수 요약 (catalog.py가 채움). 원본을 매번
                   집계하지 않고 누락/얕은 종목을 찾는 용도. exhausted=서버가 더 과거 데이터가 없다고 답함.
- collection_jobs / collection_job_items: 대시보드에서 시작한 수집 작업과 종목별 결과 (collect_worker.py가 갱신).
- realtime_merge_log: 실시간 틱 -> 1분봉 병합을 (날짜, 종목)별로 기록 (close_day.py, 멱등 실행용).
- watchlist     : 실시간 매매/실시간 수집이 실제로 지켜보는 종목 목록 (출처/전략그룹/메모 태그 포함). "수집해둔 전체
                   종목"(ohlcv에 있는 것 전부, store.symbols())과는 다른 개념입니다 —
                   전체 종목을 다 받아도 실시간 감시/매매 대상은 이 테이블에 있는
                   종목만입니다 (안 그러면 실시간 구독 한도를 넘고, 감시 루프 한 바퀴가
                   너무 오래 걸립니다). live_trade.py/stream_collector.py --watchlist가
                   store.symbols() 대신 여기를 봅니다.
"""

import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd


def _clean_bar_rows(df: pd.DataFrame, key_col: str) -> pd.DataFrame:
    """API가 빈 레코드(날짜/시각이 없는 행)를 돌려주는 종목이 있어서, 그대로 저장하면
    key가 문자열 'nan'인 쓰레기 행이 쌓입니다(실측: ohlcv 3건, 분봉/틱 116건).
    key가 비었거나 가격이 전부 NaN인 행은 저장 전에 걸러냅니다."""
    if df.empty:
        return df
    keys = df[key_col].astype(str)
    valid = df[key_col].notna() & ~keys.isin(["nan", "NaT", "None", ""])
    valid &= df[["open", "high", "low", "close"]].notna().any(axis=1)
    return df[valid]


# ---------------------------------------------------------------------------
# DB 파일 분리 (도메인별)
#
# 데이터 종류마다 쓰는 속도/크기/보관 정책이 완전히 달라서 파일을 나눕니다:
#   daily.db      일봉, 지수 일봉, 종목 리스트(universe), 기본정보        (작고 안정적)
#   minute.db     분봉 (종목당 약 10만 행 x 수천 종목 = 수억 행, 수십 GB)
#   tick.db       틱봉 + 실시간 체결 스트림(realtime_ticks)               (장중 계속 쓰기)
#   orderbook.db  실시간 10호가 스냅샷                                    (하루 수백 MB, 정리 대상 1순위)
#   trading.db    매매 판단/체결 기록, 대시보드 설정, 관심종목, 위원회/튜닝 기록
#   collection.db 데이터 카탈로그, 수집 작업, 실시간 병합 기록
# SQLite는 파일 하나에 동시에 쓰는 쪽이 하나뿐이라, 실시간 수집기(tick/orderbook)와 수집 워커(daily/
# minute)와 라이브 매매(trading)가 서로 안 기다리게 하는 게 가장 큰 이득입니다. 그 밖에 호가만
# 따로 정리/백업/삭제하거나, 기록이 가벼운 파일만 컴퓨터 사이에 동기화하기도 쉬워집니다.
#
# 코드는 그대로 `store.load(...)` 등을 쓰면 됩니다 — 연결마다 필요한 파일을 ATTACH해서 기존 SQL의
# 테이블 이름(ohlcv, realtime_ticks ...)이 그대로 통합니다. 예외는 intraday_ohlcv: minute.db와
# tick.db에 같은 이름의 테이블이 있어서 항상 스키마를 붙여(_intraday_ref) 씁니다.
# `<data_dir>/db/daily.db`가 있으면 분리 모드, 없으면 기존 단일 파일(db_path)로 동작하는 호환
# 모드입니다 (마이그레이션은 src/migrate_split_db.py, 다른 컴퓨터는 git pull 후에도 그대로 동작).
# ---------------------------------------------------------------------------
DOMAINS = ("daily", "minute", "tick", "orderbook", "trading", "collection")

_TABLE_DOMAIN = {
    "ohlcv": "daily", "index_ohlcv": "daily", "universe": "daily", "stock_basic_info": "daily",
    "intraday_ohlcv": "minute",  # 틱봉(interval이 't'로 끝남)은 _intraday_ref가 tick으로 보냄
    "realtime_ticks": "tick", "realtime_orderbook": "orderbook",
    "decisions": "trading", "decision_committee": "trading", "tuning_runs": "trading",
    "tuning_transitions": "trading", "settings": "trading", "watchlist": "trading",
    "data_catalog": "collection", "collection_jobs": "collection", "collection_job_items": "collection",
    "realtime_merge_log": "collection",
}


class _ClosingConnection(sqlite3.Connection):
    """`with self._connect() as conn:` 블록이 끝나면 커밋(또는 롤백) 뒤에 연결을 닫습니다.
    기본 sqlite3의 with는 커밋만 하고 닫지 않아서, 호출마다 새 연결을 여는 이 저장소 패턴에선
    가비지 수집이 돌 때까지 파일 핸들이 쌓입니다 (실시간 수집기처럼 틱마다 쓰는 프로세스에서
    누수가 되고, 파일 이동/삭제도 막습니다)."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


class MarketDataStore:
    def __init__(self, db_path: str, readonly: bool = False):
        """readonly=True: 모든 파일을 읽기 전용으로 엽니다 (전략 검증/분석처럼 시장 데이터를
        절대 바꾸면 안 되는 코드용). 스키마 생성/이관도 하지 않습니다."""
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.readonly = readonly
        self.db_dir = Path(db_path).parent / "db"
        self.split = (self.db_dir / "daily.db").exists()
        if self.split:
            self.files = {d: str(self.db_dir / f"{d}.db") for d in DOMAINS}
            self._schema = {d: d for d in DOMAINS}
        else:
            self.files = {d: db_path for d in DOMAINS}
            self._schema = {d: "main" for d in DOMAINS}
        if not readonly:
            self._init_schema()

    def _connect(self, *domains: str) -> sqlite3.Connection:
        """domains를 생략하면 모든 파일을 붙입니다. 실시간 틱처럼 자주 쓰는 경로는 필요한 도메인만
        넘겨서 ATTACH 비용을 줄이세요.

        대시보드/수집 워커/실시간 수집기/라이브 매매가 같은 DB를 동시에 쓰므로 기본 대기(5초)는
        긴 조회(카탈로그 집계 등)와 겹치면 "database is locked"가 나기 쉬워서 넉넉히(60초) 잡습니다."""
        if not self.split:
            if self.readonly:
                return sqlite3.connect(f"file:{Path(self.db_path).as_posix()}?mode=ro", uri=True, timeout=60,
                                       factory=_ClosingConnection)
            return sqlite3.connect(self.db_path, timeout=60, factory=_ClosingConnection)
        # 분리 모드: 메모리 DB를 뼈대로 두고 필요한 파일을 도메인 이름으로 ATTACH합니다.
        conn = sqlite3.connect("file::memory:", uri=True, timeout=60, factory=_ClosingConnection)
        for d in (domains or DOMAINS):
            path = Path(self.files[d]).as_posix()
            target = f"file:{path}?mode=ro" if self.readonly else f"file:{path}"
            conn.execute(f"ATTACH DATABASE '{target}' AS {d}")
        return conn

    def _intraday_ref(self, interval: str) -> str:
        """분봉은 minute.db, 틱봉(interval이 't'로 끝남: 1t, 3t ...)은 tick.db의 intraday_ohlcv."""
        if not self.split:
            return "main.intraday_ohlcv"
        return f"{'tick' if interval.endswith('t') else 'minute'}.intraday_ohlcv"

    def intraday_refs(self) -> list[str]:
        """모든 분봉/틱봉 테이블 (분리 모드면 2개, 호환 모드면 1개) — 전체 집계용."""
        return ["minute.intraday_ohlcv", "tick.intraday_ohlcv"] if self.split else ["main.intraday_ohlcv"]

    def _ddl(self, conn: sqlite3.Connection, sql: str) -> None:
        """CREATE TABLE/INDEX 문을 그 테이블이 속한 도메인의 파일에 만들도록 스키마를 붙여 실행합니다."""
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        if m:
            schema = self._schema[_TABLE_DOMAIN[m.group(1)]]
            base_sql = sql
            sql = sql.replace(m.group(0), f"CREATE TABLE IF NOT EXISTS {schema}.{m.group(1)}", 1)
            if m.group(1) == "intraday_ohlcv" and self.split:
                # 분봉(minute.db)과 틱봉(tick.db)이 같은 모양의 테이블을 각각 가집니다.
                conn.execute(base_sql.replace(m.group(0), "CREATE TABLE IF NOT EXISTS tick.intraday_ohlcv", 1))
        else:
            m = re.search(r"CREATE INDEX IF NOT EXISTS (\w+)\s+ON (\w+)", sql)
            if not m:
                raise ValueError(f"_ddl이 처리하지 못하는 문장: {sql[:60]}")
            schema = self._schema[_TABLE_DOMAIN[m.group(2)]]
            sql = sql.replace(m.group(1), f"{schema}.{m.group(1)}", 1)
        conn.execute(sql)

    def _init_schema(self):
        # WAL 모드: 읽는 쪽(대시보드/카탈로그 집계)이 쓰는 쪽(실시간 틱 적재)을 막지 않게 합니다.
        # 파일에 영구 적용되는 설정이라 이미 켜져 있으면 no-op이고, 다른 프로세스가 DB를
        # 잡고 있어 지금 못 바꾸더라도 동작에는 지장이 없으므로 실패는 무시합니다.
        try:
            conn = self._connect()
            for schema in dict.fromkeys(self._schema.values()):
                conn.execute(f"PRAGMA {schema}.journal_mode=WAL")
            conn.close()
        except sqlite3.OperationalError:
            pass
        with self._connect() as conn:
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (symbol, date)
                )
            """)
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal TEXT,
                    llm_stance TEXT,
                    action TEXT,
                    detail TEXT
                )
            """)
            # 분봉/틱 등 일봉이 아닌 캔들. interval 예: "1m","5m","1t" 등 자유 문자열.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS intraday_ohlcv (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (symbol, interval, timestamp)
                )
            """)
            # 전체 종목 리스트 (ka10099). market: "0"=코스피, "10"=코스닥 등.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS universe (
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    name TEXT,
                    market_name TEXT,
                    list_count TEXT,
                    last_price REAL,
                    listed_date TEXT,
                    state TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (code, market)
                )
            """)
            # 실시간 웹소켓 체결가 스트림. 같은 (symbol, ts)가 여러 번 와도 새 행으로 쌓습니다
            # (틱 단위로 동일 시각에 여러 체결이 있을 수 있어 upsert가 아닌 append).
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS realtime_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    price REAL,
                    volume INTEGER,
                    tick_type TEXT,
                    received_at TEXT NOT NULL
                )
            """)
            self._ddl(conn, 
                "CREATE INDEX IF NOT EXISTS idx_realtime_ticks_symbol "
                "ON realtime_ticks(symbol, id DESC)"
            )
            # 실시간 호가잔량(0D) 스냅샷. asks/bids는 [[가격, 수량], ...] 형태의 JSON 문자열
            # (매도는 1호가=최저가부터, 매수는 1호가=최고가부터 최대 10단).
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS realtime_orderbook (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    asks TEXT NOT NULL,
                    bids TEXT NOT NULL,
                    total_ask_qty INTEGER,
                    total_bid_qty INTEGER,
                    received_at TEXT NOT NULL
                )
            """)
            self._ddl(conn, 
                "CREATE INDEX IF NOT EXISTS idx_realtime_orderbook_symbol "
                "ON realtime_orderbook(symbol, id DESC)"
            )
            # 시장 벤치마크(코스피/코스닥 등) 지수 일봉. 종목 백테스트 결과를
            # 시장 대비로 비교(알파 계산)하는 데 씁니다. collect_index.py가 채웁니다.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS index_ohlcv (
                    index_code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (index_code, date)
                )
            """)
            # 대시보드에서 조절하는 런타임 설정값 (매수 비중, 손절선, 동시 보유 종목 수 등).
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # 종목 기본정보(PER/PBR/EPS/BPS/ROE, 시가총액 등, ka10001). 종목당 최신 스냅샷 1줄.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS stock_basic_info (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    market_cap REAL,
                    per REAL,
                    pbr REAL,
                    eps REAL,
                    bps REAL,
                    roe REAL,
                    sales REAL,
                    operating_profit REAL,
                    net_income REAL,
                    listed_shares REAL,
                    high_250 REAL,
                    low_250 REAL,
                    updated_at TEXT NOT NULL
                )
            """)
            # 실시간 매매/수집이 실제로 지켜보는 종목. store.symbols()(전체 수집 종목)과 분리.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL
                )
            """)
            # 관심종목 태그(뒤늦게 추가된 컬럼이라 기존 DB는 ALTER로 이관):
            #  source  : 왜 들어왔나 - manual(대시보드에서 직접) | position(보유 종목) | strategy(전략 후보) | seed(최초 자동)
            #  strategy: 이 종목을 어느 전략 그룹으로 관리하나 (대시보드 사이드바 그룹 기준)
            #  note    : 메모
            wl_schema = self._schema["trading"]
            existing_cols = {r[1] for r in conn.execute(f"PRAGMA {wl_schema}.table_info(watchlist)").fetchall()}
            for col, ddl in (("source", "TEXT DEFAULT 'seed'"), ("strategy", "TEXT DEFAULT ''"),
                             ("note", "TEXT DEFAULT ''")):
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE {wl_schema}.watchlist ADD COLUMN {col} {ddl}")

            # 데이터 카탈로그: (종목, 데이터종류)별로 "무엇이 어디부터 어디까지 몇 건 들어있나"를
            # 한 줄로 요약한 목록. 수천만 행짜리 원본 테이블을 매번 집계하지 않고도 대시보드가
            # 누락/얕은 종목을 바로 찾을 수 있게 합니다 (data_layer/catalog.py가 채웁니다).
            #  dataset : daily | 1m | 1t | (그 외 intraday interval) | realtime_tick | realtime_orderbook | basic_info
            #  exhausted: 1이면 "서버가 더 오래된 데이터는 없다고 답한 것을 확인함" — 이미 끝까지 받았으니
            #             더 과거를 받으려고 API를 반복 호출하지 않게 하는 표식 (재빌드해도 유지됨).
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS data_catalog (
                    symbol TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    first_ts TEXT,
                    last_ts TEXT,
                    row_count INTEGER,
                    day_count INTEGER,
                    exhausted INTEGER NOT NULL DEFAULT 0,
                    refreshed_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, dataset)
                )
            """)
            # 대시보드에서 시작하는 수집 작업. 실제 수집은 별도 프로세스(collect_worker.py)가 하고,
            # 진행 상황을 여기에 적어서 화면이 새로고침돼도/브라우저를 닫아도 이어서 볼 수 있습니다.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS collection_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    heartbeat_at TEXT,
                    status TEXT NOT NULL,
                    title TEXT,
                    spec TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    ok INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    current_symbol TEXT,
                    message TEXT,
                    pid INTEGER
                )
            """)
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS collection_job_items (
                    job_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    finished_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, symbol)
                )
            """)
            # 실시간 틱 -> 종목별 1분봉 병합 기록 (같은 날짜를 두 번 병합하지 않고, 아직 안 합친
            # 날짜를 찾는 용도).
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS realtime_merge_log (
                    date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ticks INTEGER,
                    bars INTEGER,
                    bars_added INTEGER,
                    merged_at TEXT NOT NULL,
                    PRIMARY KEY (date, symbol)
                )
            """)
            # 4-역할 위원회(agents/decision_maker.py)의 최종 판단 로그.
            # 기존 decisions 테이블(전략 신호+주문 실행 기록)과는 별개로,
            # "위원회가 무슨 근거로 어떤 결론을 냈는지"만 따로 남깁니다.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS decision_committee (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT,
                    confidence REAL,
                    votes TEXT,
                    reasoning TEXT
                )
            """)
            # 백테스트 기반 파라미터 튜닝(src/tuning/analyst_tuner.py) 기록.
            # "위원 교육"의 성공/실패 이력 — 나중에 LLM에게 그대로 컨텍스트로 넘길 수 있도록
            # param_set은 JSON, summary는 사람이 읽는 한 줄 요약으로 같이 저장합니다.
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS tuning_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    analyst TEXT NOT NULL,
                    param_set TEXT NOT NULL,
                    start_date TEXT,
                    end_date TEXT,
                    total_return_pct REAL,
                    benchmark_return_pct REAL,
                    num_trades INTEGER,
                    win_rate_pct REAL,
                    max_drawdown_pct REAL,
                    outcome TEXT,
                    summary TEXT
                )
            """)
            self._ddl(conn, 
                "CREATE INDEX IF NOT EXISTS idx_tuning_runs_lookup "
                "ON tuning_runs(symbol, analyst, id DESC)"
            )
            # 같은 파라미터 조합이 나중에 다시 튜닝됐을 때 성공<->실패가 뒤집힌 경우만 기록.
            # (시장 국면이 바뀌어서 예전에 잘 되던 설정이 더는 안 통한다는 신호)
            self._ddl(conn, """
                CREATE TABLE IF NOT EXISTS tuning_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    analyst TEXT NOT NULL,
                    param_set TEXT NOT NULL,
                    previous_run_id INTEGER,
                    previous_outcome TEXT,
                    previous_period TEXT,
                    new_run_id INTEGER,
                    new_outcome TEXT,
                    new_period TEXT,
                    transition_type TEXT,
                    summary TEXT
                )
            """)

    # ------------------------------------------------------------------
    # 기존 일봉 (변경 없음)
    # ------------------------------------------------------------------

    def log_decision(self, symbol: str, signal: str, llm_stance: str, action: str, detail: str = ""):
        """main.py가 매일 판단/실행 결과를 남기는 곳. 대시보드가 이 기록을 읽어서 보여줍니다."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO decisions (timestamp, symbol, signal, llm_stance, action, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), symbol, signal, llm_stance, action, detail),
            )

    def recent_decisions(self, limit: int = 50) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT timestamp, symbol, signal, llm_stance, action, detail FROM decisions ORDER BY id DESC LIMIT ?",
                conn, params=(limit,),
            )

    def symbols(self) -> list[str]:
        """지금까지 일봉 데이터를 수집해둔 종목코드 목록 (대시보드 드롭다운용)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol").fetchall()
            return [r[0] for r in rows]

    def last_saved_date(self, symbol: str) -> str | None:
        """이 종목의 마지막 저장 날짜. 증분 수집 시 '이후 날짜만' 요청하는 데 씀."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM ohlcv WHERE symbol = ?", (symbol,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def upsert(self, symbol: str, df: pd.DataFrame):
        """
        df 컬럼: date, open, high, low, close, volume
        같은 (symbol, date)가 이미 있으면 덮어쓰고, 없으면 새로 추가합니다.
        (증분 수집 도중 겹치는 날짜가 와도 안전합니다)
        """
        df = _clean_bar_rows(df, "date")
        if df.empty:
            return
        with self._connect() as conn:
            rows = [
                (symbol, str(r.date), r.open, r.high, r.low, r.close, int(r.volume))
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                """INSERT INTO ohlcv (symbol, date, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, date) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume""",
                rows,
            )

    def load(self, symbol: str, start_date: str | None = None) -> pd.DataFrame:
        """백테스팅/전략이 실제로 읽어가는 조회 함수."""
        query = "SELECT date, open, high, low, close, volume FROM ohlcv WHERE symbol = ?"
        params = [symbol]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        query += " ORDER BY date ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ------------------------------------------------------------------
    # 시장 벤치마크 지수 (신규)
    # ------------------------------------------------------------------

    def last_saved_index_date(self, index_code: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM index_ohlcv WHERE index_code = ?", (index_code,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def upsert_index(self, index_code: str, df: pd.DataFrame):
        """df 컬럼: date, open, high, low, close, volume (upsert 동작은 upsert()와 동일)."""
        df = _clean_bar_rows(df, "date")
        if df.empty:
            return
        with self._connect() as conn:
            rows = [
                (index_code, str(r.date), r.open, r.high, r.low, r.close, int(r.volume))
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                """INSERT INTO index_ohlcv (index_code, date, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(index_code, date) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume""",
                rows,
            )

    def load_index(self, index_code: str, start_date: str | None = None) -> pd.DataFrame:
        query = "SELECT date, open, high, low, close, volume FROM index_ohlcv WHERE index_code = ?"
        params = [index_code]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        query += " ORDER BY date ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ------------------------------------------------------------------
    # 분봉/틱 (신규)
    # ------------------------------------------------------------------

    def last_saved_intraday_ts(self, symbol: str, interval: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT MAX(timestamp) FROM {self._intraday_ref(interval)} WHERE symbol = ? AND interval = ?",
                (symbol, interval),
            ).fetchone()
            return row[0] if row and row[0] else None

    def upsert_intraday(self, symbol: str, interval: str, df: pd.DataFrame):
        """df 컬럼: timestamp, open, high, low, close, volume"""
        df = _clean_bar_rows(df, "timestamp")
        if df.empty:
            return
        with self._connect() as conn:
            rows = [
                (symbol, interval, str(r.timestamp), r.open, r.high, r.low, r.close, int(r.volume))
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                f"""INSERT INTO {self._intraday_ref(interval)} (symbol, interval, timestamp, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, interval, timestamp) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume""",
                rows,
            )

    def insert_intraday_if_absent(self, symbol: str, interval: str, df: pd.DataFrame) -> int:
        """이미 있는 (symbol, interval, timestamp)는 건드리지 않고 없는 것만 추가합니다.
        실시간 틱에서 만든 1분봉을 공식 분봉(ka10080)에 "빈 구간만" 메우는 용도 — 공식
        값이 항상 우선이라 덮어쓰지 않습니다. 실제로 추가된 행 수를 반환합니다."""
        df = _clean_bar_rows(df, "timestamp")
        if df.empty:
            return 0
        with self._connect() as conn:
            before = conn.total_changes
            conn.executemany(
                f"""INSERT INTO {self._intraday_ref(interval)} (symbol, interval, timestamp, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, interval, timestamp) DO NOTHING""",
                [(symbol, interval, str(r.timestamp), r.open, r.high, r.low, r.close, int(r.volume))
                 for r in df.itertuples(index=False)],
            )
            return conn.total_changes - before

    def purge_invalid_rows(self) -> dict:
        """예전 버전이 저장한 key가 'nan' 등인 쓰레기 행을 삭제합니다 (재발은 _clean_bar_rows가 막음)."""
        with self._connect() as conn:
            r1 = conn.execute(
                "DELETE FROM ohlcv WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").rowcount
            r2 = sum(conn.execute(
                f"DELETE FROM {ref} WHERE timestamp NOT GLOB "
                "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]'").rowcount
                for ref in self.intraday_refs())
            r3 = conn.execute(
                "DELETE FROM index_ohlcv WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").rowcount
        return {"ohlcv": r1, "intraday_ohlcv": r2, "index_ohlcv": r3}

    def intraday_span(self, symbol: str, interval: str) -> dict:
        """이 종목/간격에 저장된 분봉의 범위 (PK 인덱스만 타서 빠름)."""
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM {self._intraday_ref(interval)}
                   WHERE symbol = ? AND interval = ?""", (symbol, interval)).fetchone()
        return {"first_ts": row[0], "last_ts": row[1], "rows": row[2] or 0}

    def load_intraday(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                f"""SELECT timestamp, open, high, low, close, volume FROM {self._intraday_ref(interval)}
                   WHERE symbol = ? AND interval = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                conn, params=(symbol, interval, limit),
            ).iloc[::-1].reset_index(drop=True)

    # ------------------------------------------------------------------
    # 전체 종목 리스트 (신규)
    # ------------------------------------------------------------------

    def upsert_universe(self, market: str, df: pd.DataFrame):
        """df 컬럼: code, name, market_name, list_count, last_price, listed_date, state"""
        if df.empty:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = [
                (
                    str(r.code), market, r.name, r.market_name, str(r.list_count),
                    float(r.last_price) if r.last_price not in (None, "") else None,
                    r.listed_date, r.state, now,
                )
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                """INSERT INTO universe
                     (code, market, name, market_name, list_count, last_price, listed_date, state, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(code, market) DO UPDATE SET
                     name=excluded.name, market_name=excluded.market_name,
                     list_count=excluded.list_count, last_price=excluded.last_price,
                     listed_date=excluded.listed_date, state=excluded.state,
                     updated_at=excluded.updated_at""",
                rows,
            )

    def load_universe(self, market: str | None = None) -> pd.DataFrame:
        query = "SELECT code, market, name, market_name, last_price, state, updated_at FROM universe"
        params: list = []
        if market:
            query += " WHERE market = ?"
            params.append(market)
        query += " ORDER BY code ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def universe_codes(self, market: str | None = None) -> list[str]:
        df = self.load_universe(market)
        return df["code"].tolist() if not df.empty else []

    def sync_universe(self, market: str, df: pd.DataFrame):
        """upsert_universe와 달리, 이 market에서 df에 없는 기존 코드는 삭제합니다 —
        ETF/우선주/스팩처럼 "더 이상 이 시장의 수집 대상이 아닌" 코드를 다음 실행 때
        걸러내려면 upsert만으로는 안 되고(예전 행이 그대로 남음) 동기화가 필요합니다."""
        keep_codes = set(df["code"].astype(str)) if not df.empty else set()
        with self._connect() as conn:
            existing = {r[0] for r in conn.execute(
                "SELECT code FROM universe WHERE market = ?", (market,)
            ).fetchall()}
            stale = existing - keep_codes
            if stale:
                conn.executemany(
                    "DELETE FROM universe WHERE market = ? AND code = ?",
                    [(market, code) for code in stale],
                )
        self.upsert_universe(market, df)

    # ------------------------------------------------------------------
    # 실시간 체결 스트림 (신규)
    # ------------------------------------------------------------------

    def append_realtime_tick(self, symbol: str, ts: str, price: float, volume: int, tick_type: str = "0B"):
        with self._connect("tick") as conn:
            conn.execute(
                """INSERT INTO realtime_ticks (symbol, ts, price, volume, tick_type, received_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (symbol, ts, price, volume, tick_type, datetime.now().isoformat(timespec="seconds")),
            )

    def recent_realtime_ticks(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        with self._connect("tick") as conn:
            return pd.read_sql_query(
                """SELECT ts, price, volume, tick_type, received_at FROM realtime_ticks
                   WHERE symbol = ? ORDER BY id DESC LIMIT ?""",
                conn, params=(symbol, limit),
            ).iloc[::-1].reset_index(drop=True)

    def ticks_for_date(self, symbol: str, date: str) -> pd.DataFrame:
        """date(YYYY-MM-DD)에 수집된(received_at 기준) 체결 틱 전체를 시간순으로.
        close_day.py가 그날의 1분봉/일봉을 만드는 재료로 씁니다."""
        with self._connect("tick") as conn:
            return pd.read_sql_query(
                """SELECT ts, price, volume, tick_type, received_at FROM realtime_ticks
                   WHERE symbol = ? AND received_at LIKE ? ORDER BY id ASC""",
                conn, params=(symbol, f"{date}%"),
            )

    def realtime_tick_symbols(self, date: str) -> list[str]:
        """date(YYYY-MM-DD)에 실시간 체결이 하나라도 쌓인 종목 코드 목록.
        close_day.py가 인자 없이 실행됐을 때 대상 종목을 자동으로 찾는 데 씁니다."""
        with self._connect("tick") as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM realtime_ticks WHERE received_at LIKE ?",
                (f"{date}%",),
            ).fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # 실시간 호가잔량 스트림 (신규)
    # ------------------------------------------------------------------

    def append_realtime_orderbook(
        self, symbol: str, ts: str, asks: list, bids: list,
        total_ask_qty: int | None = None, total_bid_qty: int | None = None,
    ):
        """asks/bids: [[가격, 수량], ...] 리스트 (매도는 1호가부터, 매수는 1호가부터)."""
        with self._connect("orderbook") as conn:
            conn.execute(
                """INSERT INTO realtime_orderbook
                     (symbol, ts, asks, bids, total_ask_qty, total_bid_qty, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol, ts, json.dumps(asks), json.dumps(bids),
                    total_ask_qty, total_bid_qty, datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def recent_realtime_orderbook(self, symbol: str, limit: int = 50) -> pd.DataFrame:
        with self._connect("orderbook") as conn:
            df = pd.read_sql_query(
                """SELECT ts, asks, bids, total_ask_qty, total_bid_qty, received_at
                   FROM realtime_orderbook WHERE symbol = ? ORDER BY id DESC LIMIT ?""",
                conn, params=(symbol, limit),
            ).iloc[::-1].reset_index(drop=True)
        if not df.empty:
            df["asks"] = df["asks"].apply(json.loads)
            df["bids"] = df["bids"].apply(json.loads)
        return df

    def latest_orderbook(self, symbol: str) -> dict | None:
        """'지금 호가창' 한 장만 필요할 때 쓰는 편의 함수 (대시보드 등)."""
        df = self.recent_realtime_orderbook(symbol, limit=1)
        return df.iloc[-1].to_dict() if not df.empty else None

    # ------------------------------------------------------------------
    # 런타임 설정 (신규) — 대시보드 ↔ live_trade.py 공유
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, str(value), datetime.now().isoformat(timespec="seconds")),
            )

    def seed_setting_if_missing(self, key: str, value) -> None:
        """이미 값이 있으면 손대지 않고, 없을 때만 기본값(config)으로 채웁니다 —
        대시보드에서 바꾼 값을 프로세스 재시작 때 config 기본값으로 덮어쓰지 않기 위함."""
        if self.get_setting(key) is None:
            self.set_setting(key, value)

    def seed_risk_settings_if_missing(self, max_position_pct: float, risk_limit_pct: float,
                                        max_concurrent_positions: int) -> None:
        self.seed_setting_if_missing("risk.max_position_pct", max_position_pct)
        self.seed_setting_if_missing("risk.risk_limit_pct", risk_limit_pct)
        self.seed_setting_if_missing("risk.max_concurrent_positions", max_concurrent_positions)

    def get_risk_settings(self, defaults: dict) -> dict:
        """settings 테이블에 값이 있으면 그걸, 없으면 defaults(보통 config)를 씁니다."""
        return {
            "max_position_pct": float(self.get_setting("risk.max_position_pct", defaults["max_position_pct"])),
            "risk_limit_pct": float(self.get_setting("risk.risk_limit_pct", defaults["risk_limit_pct"])),
            "max_concurrent_positions": int(float(
                self.get_setting("risk.max_concurrent_positions", defaults["max_concurrent_positions"])
            )),
        }

    def set_risk_settings(self, max_position_pct: float, risk_limit_pct: float,
                           max_concurrent_positions: int) -> None:
        self.set_setting("risk.max_position_pct", max_position_pct)
        self.set_setting("risk.risk_limit_pct", risk_limit_pct)
        self.set_setting("risk.max_concurrent_positions", max_concurrent_positions)

    # ------------------------------------------------------------------
    # 종목 기본정보 (신규)
    # ------------------------------------------------------------------

    _BASIC_INFO_COLUMNS = [
        "symbol", "name", "market_cap", "per", "pbr", "eps", "bps", "roe", "sales",
        "operating_profit", "net_income", "listed_shares", "high_250", "low_250",
    ]

    def upsert_basic_info(self, info: dict) -> None:
        """info: fetch_basic_info()가 반환하는 형태의 dict (symbol 필수)."""
        with self._connect() as conn:
            values = [info.get(c) for c in self._BASIC_INFO_COLUMNS]
            placeholders = ", ".join(["?"] * (len(self._BASIC_INFO_COLUMNS) + 1))
            update_clause = ", ".join(f"{c}=excluded.{c}" for c in self._BASIC_INFO_COLUMNS if c != "symbol")
            conn.execute(
                f"""INSERT INTO stock_basic_info ({", ".join(self._BASIC_INFO_COLUMNS)}, updated_at)
                    VALUES ({placeholders})
                    ON CONFLICT(symbol) DO UPDATE SET {update_clause}, updated_at=excluded.updated_at""",
                (*values, datetime.now().isoformat(timespec="seconds")),
            )

    def get_basic_info(self, symbol: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT {", ".join(self._BASIC_INFO_COLUMNS)}, updated_at
                    FROM stock_basic_info WHERE symbol = ?""",
                (symbol,),
            ).fetchone()
            if not row:
                return None
            return dict(zip(self._BASIC_INFO_COLUMNS + ["updated_at"], row))

    # ------------------------------------------------------------------
    # 관심종목 (신규) — 실시간 매매/수집이 실제로 지켜보는 종목 목록
    # ------------------------------------------------------------------

    def get_watchlist(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
            return [r[0] for r in rows]

    def add_to_watchlist(self, symbol: str, source: str = "manual", strategy: str = "", note: str = "") -> None:
        """이미 있는 종목이면 아무것도 바꾸지 않습니다 (태그를 덮어쓰려면 set_watch_tags)."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO watchlist (symbol, added_at, source, strategy, note) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO NOTHING""",
                (symbol, datetime.now().isoformat(timespec="seconds"), source, strategy, note),
            )

    def get_watchlist_detail(self) -> pd.DataFrame:
        """관심종목 + 태그(source/strategy/note). 대시보드의 전략별 그룹/관리 화면용."""
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT symbol, added_at, source, strategy, note FROM watchlist ORDER BY symbol", conn)

    def set_watch_tags(self, symbol: str, strategy: str | None = None, note: str | None = None,
                        source: str | None = None) -> None:
        """None으로 넘긴 항목은 그대로 두고, 값을 준 항목만 바꿉니다."""
        sets, params = [], []
        for col, val in (("strategy", strategy), ("note", note), ("source", source)):
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if not sets:
            return
        with self._connect() as conn:
            conn.execute(f"UPDATE watchlist SET {', '.join(sets)} WHERE symbol = ?", (*params, symbol))

    def remove_from_watchlist(self, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))

    def seed_watchlist_if_empty(self, symbols: list[str]) -> None:
        """watchlist가 완전히 비어있을 때만(최초 1회) 채웁니다 — 이미 관리 중인 목록이
        있으면 손대지 않습니다. 전체 종목을 새로 수집해도 이 시드 타이밍 이전에
        이미 채워져 있었다면 관심종목 범위가 멋대로 커지지 않습니다."""
        if not self.get_watchlist():
            for s in symbols:
                self.add_to_watchlist(s, source="seed")

    # ------------------------------------------------------------------
    # 데이터 카탈로그 (신규) — data_layer/catalog.py가 채우고 대시보드가 읽음
    # ------------------------------------------------------------------

    def upsert_catalog(self, rows: list[tuple]) -> None:
        """rows: (symbol, dataset, first_ts, last_ts, row_count, day_count).
        exhausted 표식은 건드리지 않습니다 (재집계해도 '끝까지 받음' 정보가 유지되도록)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO data_catalog (symbol, dataset, first_ts, last_ts, row_count, day_count, refreshed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, dataset) DO UPDATE SET
                     first_ts=excluded.first_ts, last_ts=excluded.last_ts,
                     row_count=excluded.row_count, day_count=excluded.day_count,
                     refreshed_at=excluded.refreshed_at""",
                [(*r, now) for r in rows],
            )

    def load_catalog(self, dataset: str | None = None) -> pd.DataFrame:
        query = ("SELECT symbol, dataset, first_ts, last_ts, row_count, day_count, exhausted, refreshed_at "
                 "FROM data_catalog")
        params: list = []
        if dataset:
            query += " WHERE dataset = ?"
            params.append(dataset)
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def set_exhausted(self, symbol: str, dataset: str, exhausted: bool = True) -> None:
        """서버가 '더 오래된 데이터 없음'이라고 답한 종목/데이터종류를 표시합니다."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO data_catalog (symbol, dataset, exhausted, refreshed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(symbol, dataset) DO UPDATE SET exhausted=excluded.exhausted""",
                (symbol, dataset, 1 if exhausted else 0, datetime.now().isoformat(timespec="seconds")),
            )

    def is_exhausted(self, symbol: str, dataset: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT exhausted FROM data_catalog WHERE symbol = ? AND dataset = ?",
                (symbol, dataset)).fetchone()
        return bool(row and row[0])

    # ------------------------------------------------------------------
    # 수집 작업 (신규) — 대시보드가 만들고 collect_worker.py가 진행 상황을 갱신
    # ------------------------------------------------------------------

    def create_job(self, title: str, spec: dict, total: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO collection_jobs (created_at, status, title, spec, total)
                   VALUES (?, 'pending', ?, ?, ?)""",
                (datetime.now().isoformat(timespec="seconds"), title, json.dumps(spec, ensure_ascii=False), total),
            )
            return cur.lastrowid

    _JOB_FIELDS = {"started_at", "finished_at", "heartbeat_at", "status", "done", "ok", "failed",
                    "current_symbol", "message", "pid"}

    def update_job(self, job_id: int, **fields) -> None:
        bad = set(fields) - self._JOB_FIELDS
        if bad:
            raise ValueError(f"update_job: 알 수 없는 필드 {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(f"UPDATE collection_jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))

    def touch_job(self, job_id: int) -> None:
        self.update_job(job_id, heartbeat_at=datetime.now().isoformat(timespec="seconds"))

    def get_job(self, job_id: int) -> dict | None:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM collection_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            job = dict(zip([d[0] for d in cur.description], row))
        job["spec"] = json.loads(job["spec"])
        return job

    def recent_jobs(self, limit: int = 20) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                """SELECT id, created_at, started_at, finished_at, heartbeat_at, status, title,
                          total, done, ok, failed, current_symbol, message, pid
                   FROM collection_jobs ORDER BY id DESC LIMIT ?""", conn, params=(limit,))

    def active_job(self, stale_after_sec: int = 300) -> dict | None:
        """지금 살아있는 작업(pending/running/cancelling) 하나. 워커가 죽어서 heartbeat가
        stale_after_sec 넘게 끊긴 작업은 'aborted'로 정리하고 무시합니다."""
        now = datetime.now()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, status, created_at, heartbeat_at FROM collection_jobs
                   WHERE status IN ('pending','running','cancelling') ORDER BY id DESC""").fetchall()
        alive = None
        for job_id, status, created_at, heartbeat_at in rows:
            last = datetime.fromisoformat(heartbeat_at or created_at)
            if (now - last).total_seconds() > stale_after_sec:
                self.update_job(job_id, status="aborted",
                                finished_at=now.isoformat(timespec="seconds"),
                                message="워커 응답 없음(프로세스가 종료된 것으로 보여 중단 처리)")
            elif alive is None:
                alive = job_id
        return self.get_job(alive) if alive else None

    def log_job_item(self, job_id: int, symbol: str, status: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO collection_job_items (job_id, symbol, status, detail, finished_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, symbol) DO UPDATE SET
                     status=excluded.status, detail=excluded.detail, finished_at=excluded.finished_at""",
                (job_id, symbol, status, detail[:500], datetime.now().isoformat(timespec="seconds")),
            )

    def job_items(self, job_id: int, status: str | None = None) -> pd.DataFrame:
        query = "SELECT symbol, status, detail, finished_at FROM collection_job_items WHERE job_id = ?"
        params: list = [job_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY finished_at"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ------------------------------------------------------------------
    # 실시간 틱 -> 1분봉 병합 기록 (신규)
    # ------------------------------------------------------------------

    def realtime_tick_dates(self) -> list[str]:
        """실시간 체결이 하나라도 쌓인 날짜(received_at 기준) 목록."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT substr(received_at, 1, 10) FROM realtime_ticks ORDER BY 1").fetchall()
        return [r[0] for r in rows]

    def realtime_tick_counts(self, date: str) -> dict[str, int]:
        """date(YYYY-MM-DD)에 종목별로 쌓인 실시간 체결 틱 수."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT symbol, COUNT(*) FROM realtime_ticks WHERE received_at LIKE ?
                   GROUP BY symbol""", (f"{date}%",)).fetchall()
        return {r[0]: r[1] for r in rows}

    def has_daily(self, symbol: str, date: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM ohlcv WHERE symbol = ? AND date = ?",
                                (symbol, date)).fetchone() is not None

    def log_realtime_merge(self, date: str, symbol: str, ticks: int, bars: int, bars_added: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO realtime_merge_log (date, symbol, ticks, bars, bars_added, merged_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date, symbol) DO UPDATE SET
                     ticks=excluded.ticks, bars=excluded.bars, bars_added=excluded.bars_added,
                     merged_at=excluded.merged_at""",
                (date, symbol, ticks, bars, bars_added, datetime.now().isoformat(timespec="seconds")),
            )

    def merged_realtime_symbols(self, date: str) -> dict[str, int]:
        """이 날짜에 이미 병합한 종목 -> 그때 병합한 틱 수."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, ticks FROM realtime_merge_log WHERE date = ?", (date,)).fetchall()
        return {r[0]: r[1] or 0 for r in rows}

    # ------------------------------------------------------------------
    # 4-역할 위원회 판단 로그 (신규)
    # ------------------------------------------------------------------

    def log_decision_committee(self, symbol: str, action: str, confidence: float, votes: dict, reasoning: str):
        """analyze_decision.py / main.py가 위원회 최종 결론을 남기는 곳. 대시보드가 이력을 보여줍니다."""
        import json
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decision_committee (timestamp, symbol, action, confidence, votes, reasoning)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    symbol, action, confidence,
                    json.dumps(votes, ensure_ascii=False), reasoning,
                ),
            )

    def recent_decision_committee(self, symbol: str | None = None, limit: int = 50) -> pd.DataFrame:
        query = "SELECT timestamp, symbol, action, confidence, votes, reasoning FROM decision_committee"
        params: list = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ------------------------------------------------------------------
    # 백테스트 기반 위원 파라미터 튜닝 이력 (신규)
    # ------------------------------------------------------------------

    @staticmethod
    def _param_set_key(param_set: dict) -> str:
        """같은 파라미터 조합인지 비교하기 위한 정규화된 JSON 문자열 (키 정렬)."""
        import json
        return json.dumps(param_set, ensure_ascii=False, sort_keys=True)

    def find_previous_tuning_run(self, symbol: str, analyst: str, param_set: dict) -> dict | None:
        """같은 종목·같은 위원·완전히 같은 파라미터 조합으로 가장 최근에 튜닝했던 기록.
        (지금 막 넣으려는 새 기록보다 먼저 있어야 하므로, 이 함수는 log_tuning_run 호출 *전에* 씁니다.)
        """
        import json
        key = self._param_set_key(param_set)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, timestamp, start_date, end_date, outcome, total_return_pct, benchmark_return_pct, param_set
                   FROM tuning_runs WHERE symbol = ? AND analyst = ? ORDER BY id DESC""",
                (symbol, analyst),
            ).fetchall()
        for row in rows:
            if row[7] == key:
                return {
                    "id": row[0], "timestamp": row[1], "start_date": row[2], "end_date": row[3],
                    "outcome": row[4], "total_return_pct": row[5], "benchmark_return_pct": row[6],
                }
        return None

    def log_tuning_run(self, symbol: str, analyst: str, param_set: dict, start_date: str, end_date: str,
                        metrics: dict, outcome: str, summary: str) -> int:
        """튜닝(그리드서치) 한 번의 파라미터 조합 결과를 기록하고, 새로 생긴 행의 id를 반환합니다."""
        import json
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO tuning_runs
                     (timestamp, symbol, analyst, param_set, start_date, end_date,
                      total_return_pct, benchmark_return_pct, num_trades, win_rate_pct,
                      max_drawdown_pct, outcome, summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(timespec="seconds"), symbol, analyst,
                    self._param_set_key(param_set), start_date, end_date,
                    metrics.get("total_return_pct"), metrics.get("benchmark_return_pct"),
                    metrics.get("num_trades"), metrics.get("win_rate_pct"),
                    metrics.get("max_drawdown_pct"), outcome, summary,
                ),
            )
            return cur.lastrowid

    def log_tuning_transition(self, symbol: str, analyst: str, param_set: dict, previous: dict,
                               new_run_id: int, new_outcome: str, new_period: str, summary: str) -> int:
        """같은 파라미터 조합의 성공/실패가 이전 기록과 달라졌을 때만 호출합니다 (뒤집힌 경우)."""
        transition_type = f"{previous['outcome']}_TO_{new_outcome}"
        previous_period = f"{previous['start_date']}~{previous['end_date']}"
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO tuning_transitions
                     (timestamp, symbol, analyst, param_set, previous_run_id, previous_outcome,
                      previous_period, new_run_id, new_outcome, new_period, transition_type, summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(timespec="seconds"), symbol, analyst,
                    self._param_set_key(param_set), previous["id"], previous["outcome"],
                    previous_period, new_run_id, new_outcome, new_period, transition_type, summary,
                ),
            )
            return cur.lastrowid

    def param_set_flip_count(self, symbol: str, analyst: str, param_set: dict) -> int:
        """이 파라미터 조합이 지금까지 성공<->실패로 뒤집힌 횟수 (많을수록 불안정한 설정)."""
        key = self._param_set_key(param_set)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tuning_transitions WHERE symbol = ? AND analyst = ? AND param_set = ?",
                (symbol, analyst, key),
            ).fetchone()
            return row[0] if row else 0

    def recent_tuning_runs(self, symbol: str | None = None, analyst: str | None = None, limit: int = 50) -> pd.DataFrame:
        query = ("SELECT timestamp, symbol, analyst, param_set, start_date, end_date, total_return_pct, "
                  "benchmark_return_pct, num_trades, win_rate_pct, max_drawdown_pct, outcome, summary "
                  "FROM tuning_runs")
        conditions, params = [], []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if analyst:
            conditions.append("analyst = ?")
            params.append(analyst)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def recent_tuning_transitions(self, symbol: str | None = None, analyst: str | None = None, limit: int = 50) -> pd.DataFrame:
        query = ("SELECT timestamp, symbol, analyst, param_set, previous_outcome, previous_period, "
                  "new_outcome, new_period, transition_type, summary FROM tuning_transitions")
        conditions, params = [], []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if analyst:
            conditions.append("analyst = ?")
            params.append(analyst)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def tuning_llm_context(self, symbol: str, analyst: str, limit: int = 10) -> str:
        """
        지금까지의 튜닝 성공/실패 + 성공<->실패 전환 이력을 사람이 읽는 텍스트로 묶어서 반환합니다.
        나중에 llm/advisor.py가 실제 LLM을 호출할 때 이 문자열을 context에 그대로 붙여넣으면,
        "이 종목/이 위원은 과거에 이런 설정이 통했고, 이런 설정은 나중에 안 통하게 됐다"는
        내용을 LLM이 참고할 수 있습니다.
        """
        runs = self.recent_tuning_runs(symbol, analyst, limit)
        transitions = self.recent_tuning_transitions(symbol, analyst, limit)
        lines = [f"[{symbol} / {analyst} 위원 튜닝 이력]"]
        if runs.empty:
            lines.append("- 튜닝 기록 없음")
        else:
            for _, r in runs.iterrows():
                lines.append(f"- {r['summary']}")
        if not transitions.empty:
            lines.append("[성공/실패가 뒤집힌 이력 — 시장 국면 변화 가능성]")
            for _, t in transitions.iterrows():
                lines.append(f"- {t['summary']}")
        return "\n".join(lines)

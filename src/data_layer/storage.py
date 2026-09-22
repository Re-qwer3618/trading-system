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
- watchlist     : 실시간 매매/실시간 수집이 실제로 지켜보는 종목 목록. "수집해둔 전체
                   종목"(ohlcv에 있는 것 전부, store.symbols())과는 다른 개념입니다 —
                   전체 종목을 다 받아도 실시간 감시/매매 대상은 이 테이블에 있는
                   종목만입니다 (안 그러면 실시간 구독 한도를 넘고, 감시 루프 한 바퀴가
                   너무 오래 걸립니다). live_trade.py/stream_collector.py --watchlist가
                   store.symbols() 대신 여기를 봅니다.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd


class MarketDataStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intraday_ohlcv (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (symbol, interval, timestamp)
                )
            """)
            # 전체 종목 리스트 (ka10099). market: "0"=코스피, "10"=코스닥 등.
            conn.execute("""
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
            conn.execute("""
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_realtime_ticks_symbol "
                "ON realtime_ticks(symbol, id DESC)"
            )
            # 실시간 호가잔량(0D) 스냅샷. asks/bids는 [[가격, 수량], ...] 형태의 JSON 문자열
            # (매도는 1호가=최저가부터, 매수는 1호가=최고가부터 최대 10단).
            conn.execute("""
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_realtime_orderbook_symbol "
                "ON realtime_orderbook(symbol, id DESC)"
            )
            # 시장 벤치마크(코스피/코스닥 등) 지수 일봉. 종목 백테스트 결과를
            # 시장 대비로 비교(알파 계산)하는 데 씁니다. collect_index.py가 채웁니다.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_ohlcv (
                    index_code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                    PRIMARY KEY (index_code, date)
                )
            """)
            # 대시보드에서 조절하는 런타임 설정값 (매수 비중, 손절선, 동시 보유 종목 수 등).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # 종목 기본정보(PER/PBR/EPS/BPS/ROE, 시가총액 등, ka10001). 종목당 최신 스냅샷 1줄.
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL
                )
            """)

    # ------------------------------------------------------------------
    # 기존 일봉 (변경 없음)
    # ------------------------------------------------------------------

    def log_decision(self, symbol: str, signal: str, llm_stance: str, action: str, detail: str = ""):
        """main.py가 매일 판단/실행 결과를 남기는 곳. 대시보드가 이 기록을 읽어서 보여줍니다."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO decisions (timestamp, symbol, signal, llm_stance, action, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), symbol, signal, llm_stance, action, detail),
            )

    def recent_decisions(self, limit: int = 50) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT timestamp, symbol, signal, llm_stance, action, detail FROM decisions ORDER BY id DESC LIMIT ?",
                conn, params=(limit,),
            )

    def symbols(self) -> list[str]:
        """지금까지 일봉 데이터를 수집해둔 종목코드 목록 (대시보드 드롭다운용)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol").fetchall()
            return [r[0] for r in rows]

    def last_saved_date(self, symbol: str) -> str | None:
        """이 종목의 마지막 저장 날짜. 증분 수집 시 '이후 날짜만' 요청하는 데 씀."""
        with sqlite3.connect(self.db_path) as conn:
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
        if df.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ------------------------------------------------------------------
    # 시장 벤치마크 지수 (신규)
    # ------------------------------------------------------------------

    def last_saved_index_date(self, index_code: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM index_ohlcv WHERE index_code = ?", (index_code,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def upsert_index(self, index_code: str, df: pd.DataFrame):
        """df 컬럼: date, open, high, low, close, volume (upsert 동작은 upsert()와 동일)."""
        if df.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ------------------------------------------------------------------
    # 분봉/틱 (신규)
    # ------------------------------------------------------------------

    def last_saved_intraday_ts(self, symbol: str, interval: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM intraday_ohlcv WHERE symbol = ? AND interval = ?",
                (symbol, interval),
            ).fetchone()
            return row[0] if row and row[0] else None

    def upsert_intraday(self, symbol: str, interval: str, df: pd.DataFrame):
        """df 컬럼: timestamp, open, high, low, close, volume"""
        if df.empty:
            return
        with sqlite3.connect(self.db_path) as conn:
            rows = [
                (symbol, interval, str(r.timestamp), r.open, r.high, r.low, r.close, int(r.volume))
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                """INSERT INTO intraday_ohlcv (symbol, interval, timestamp, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, interval, timestamp) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume""",
                rows,
            )

    def load_intraday(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                """SELECT timestamp, open, high, low, close, volume FROM intraday_ohlcv
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)

    def universe_codes(self, market: str | None = None) -> list[str]:
        df = self.load_universe(market)
        return df["code"].tolist() if not df.empty else []

    def sync_universe(self, market: str, df: pd.DataFrame):
        """upsert_universe와 달리, 이 market에서 df에 없는 기존 코드는 삭제합니다 —
        ETF/우선주/스팩처럼 "더 이상 이 시장의 수집 대상이 아닌" 코드를 다음 실행 때
        걸러내려면 upsert만으로는 안 되고(예전 행이 그대로 남음) 동기화가 필요합니다."""
        keep_codes = set(df["code"].astype(str)) if not df.empty else set()
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO realtime_ticks (symbol, ts, price, volume, tick_type, received_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (symbol, ts, price, volume, tick_type, datetime.now().isoformat(timespec="seconds")),
            )

    def recent_realtime_ticks(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                """SELECT ts, price, volume, tick_type, received_at FROM realtime_ticks
                   WHERE symbol = ? ORDER BY id DESC LIMIT ?""",
                conn, params=(symbol, limit),
            ).iloc[::-1].reset_index(drop=True)

    def ticks_for_date(self, symbol: str, date: str) -> pd.DataFrame:
        """date(YYYY-MM-DD)에 수집된(received_at 기준) 체결 틱 전체를 시간순으로.
        close_day.py가 그날의 1분봉/일봉을 만드는 재료로 씁니다."""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                """SELECT ts, price, volume, tick_type, received_at FROM realtime_ticks
                   WHERE symbol = ? AND received_at LIKE ? ORDER BY id ASC""",
                conn, params=(symbol, f"{date}%"),
            )

    def realtime_tick_symbols(self, date: str) -> list[str]:
        """date(YYYY-MM-DD)에 실시간 체결이 하나라도 쌓인 종목 코드 목록.
        close_day.py가 인자 없이 실행됐을 때 대상 종목을 자동으로 찾는 데 씁니다."""
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value) -> None:
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
            return [r[0] for r in rows]

    def add_to_watchlist(self, symbol: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO watchlist (symbol, added_at) VALUES (?, ?) ON CONFLICT(symbol) DO NOTHING",
                (symbol, datetime.now().isoformat(timespec="seconds")),
            )

    def remove_from_watchlist(self, symbol: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))

    def seed_watchlist_if_empty(self, symbols: list[str]) -> None:
        """watchlist가 완전히 비어있을 때만(최초 1회) 채웁니다 — 이미 관리 중인 목록이
        있으면 손대지 않습니다. 전체 종목을 새로 수집해도 이 시드 타이밍 이전에
        이미 채워져 있었다면 관심종목 범위가 멋대로 커지지 않습니다."""
        if not self.get_watchlist():
            for s in symbols:
                self.add_to_watchlist(s)

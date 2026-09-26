"""
데이터 카탈로그: 시장 데이터 DB들(daily/minute/tick/orderbook)에 "무슨 데이터가 종목별로 어디부터 어디까지 있는지"를 정리합니다.

원본 테이블(ohlcv 1천만+ 행, 분봉 수억 행, 실시간 틱/호가 수십만+ 행)을
대시보드가 열릴 때마다 집계하면 수십 초가 걸리므로, 종목 x 데이터종류별 요약 한 줄을
data_catalog 테이블에 미리 계산해 둡니다. 수집이 끝나면 refresh_catalog(symbols)로
해당 종목만 다시 계산하고, 전체 재집계(refresh_catalog())는 몇십 초 걸립니다.

데이터종류(dataset) 이름:
    daily               일봉 (ohlcv)
    1m / 1t / ...       분봉/틱봉 (intraday_ohlcv.interval 그대로)
    realtime_tick       실시간 체결 스트림 (realtime_ticks)
    realtime_orderbook  실시간 10호가 스냅샷 (realtime_orderbook)
    basic_info          종목 기본정보 스냅샷 (stock_basic_info)

find_gaps()는 카탈로그를 보고 "받아야 할 종목"(누락/오래됨/분봉이 얕음)을 골라줍니다 —
대시보드의 수집 시작 화면과 collect_all.py --missing이 같은 함수를 씁니다.

실행:
    python src/data_layer/catalog.py            # 전체 재집계 + 요약 출력
"""

import sys
from pathlib import Path

if __name__ == "__main__":  # 스크립트로 직접 실행할 때도 src/를 import 경로에 올림
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time
import pandas as pd

from data_layer.storage import MarketDataStore

_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
_CHUNK = 500  # SQLite 변수 개수 한도(기본 999)를 넘지 않도록 IN 절 분할


def _chunks(items: list[str], n: int = _CHUNK):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _in_clause(column: str, symbols: list[str] | None) -> tuple[str, list]:
    if symbols is None:
        return "", []
    return f" AND {column} IN ({','.join('?' * len(symbols))})", list(symbols)


def _aggregate(store: MarketDataStore, symbols: list[str] | None) -> list[tuple]:
    """symbols가 None이면 전 종목, 아니면 그 종목들만 집계해서
    (symbol, dataset, first_ts, last_ts, row_count, day_count) 목록을 돌려줍니다."""
    rows: list[tuple] = []
    with store._connect() as conn:
        # 일봉
        where, params = _in_clause("symbol", symbols)
        rows += conn.execute(
            f"""SELECT symbol, 'daily', MIN(date), MAX(date), COUNT(*), COUNT(*)
                FROM ohlcv WHERE date GLOB '{_DATE_GLOB}'{where} GROUP BY symbol""", params).fetchall()

        # 분봉/틱봉 (interval별). day_count = 거래가 있었던 날짜 수
        for ref in store.intraday_refs():  # 분리 모드: minute.db + tick.db, 호환 모드: 한 테이블
            rows += conn.execute(
                f"""SELECT symbol, interval, MIN(timestamp), MAX(timestamp), COUNT(*),
                           COUNT(DISTINCT substr(timestamp, 1, 10))
                    FROM {ref} WHERE timestamp GLOB '{_DATE_GLOB} *'{where}
                    GROUP BY symbol, interval""", params).fetchall()

        # 실시간 체결/호가 스트림 (received_at 기준)
        for table, dataset in (("realtime_ticks", "realtime_tick"), ("realtime_orderbook", "realtime_orderbook")):
            rows += conn.execute(
                f"""SELECT symbol, '{dataset}', MIN(received_at), MAX(received_at), COUNT(*),
                           COUNT(DISTINCT substr(received_at, 1, 10))
                    FROM {table} WHERE 1=1{where} GROUP BY symbol""", params).fetchall()

        # 기본정보 (종목당 1행 스냅샷)
        rows += conn.execute(
            f"""SELECT symbol, 'basic_info', updated_at, updated_at, 1, 1
                FROM stock_basic_info WHERE 1=1{where}""", params).fetchall()
    return rows


def refresh_catalog(store: MarketDataStore, symbols: list[str] | None = None) -> dict:
    """카탈로그를 다시 계산해서 저장합니다.

    symbols=None : 전체 재집계 (수십 초). 원본이 사라진 카탈로그 행도 정리합니다.
    symbols=[..] : 그 종목들만 (수집 직후 빠른 갱신용).
    """
    started = time.time()
    purged = store.purge_invalid_rows() if symbols is None else {}

    if symbols is None:
        rows = _aggregate(store, None)
    else:
        rows = []
        for chunk in _chunks(list(symbols)):
            rows += _aggregate(store, chunk)

    store.upsert_catalog(rows)
    keep = {(r[0], r[1]) for r in rows}
    # 원본이 사라진 카탈로그 행은 정리하되, exhausted=1 표식은 "서버가 더 과거를 안 준다"는
    # 서버 쪽 사실이라 원본 유무와 무관하게 보존합니다 (_stale_catalog_rows가 제외).
    stale = _stale_catalog_rows(store, keep, symbols)
    removed = 0
    if stale:
        with store._connect() as conn:
            conn.executemany("DELETE FROM data_catalog WHERE symbol = ? AND dataset = ?", stale)
        removed = len(stale)
    return {"rows": len(rows), "removed": removed, "purged": purged,
            "seconds": round(time.time() - started, 1)}


def _stale_catalog_rows(store: MarketDataStore, keep: set, symbols: list[str] | None) -> list[tuple]:
    with store._connect() as conn:
        if symbols is None:
            existing = conn.execute(
                "SELECT symbol, dataset, exhausted FROM data_catalog").fetchall()
        else:
            existing = []
            for chunk in _chunks(list(symbols)):
                existing += conn.execute(
                    f"SELECT symbol, dataset, exhausted FROM data_catalog WHERE symbol IN ({','.join('?' * len(chunk))})",
                    chunk).fetchall()
    return [(s, d) for s, d, ex in existing if (s, d) not in keep and not ex]


# ----------------------------------------------------------------------
# 대시보드/수집 대상 선정용 조회
# ----------------------------------------------------------------------

def catalog_matrix(store: MarketDataStore) -> pd.DataFrame:
    """종목 1행 x 데이터종류별 열의 넓은 표. 대시보드의 종목별 현황 표와 find_gaps가 씁니다.

    열: symbol, name, market, watch(관심종목 여부), daily_first/last/days,
        m1_first/last/days/exhausted (1분봉), t1_days (틱봉), rt_tick_rows, rt_ob_rows, info_at
    universe에 있는데 아직 하나도 못 받은 종목도 (열이 비어있는 채로) 포함합니다.
    """
    cat = store.load_catalog()
    uni = store.load_universe()
    if uni.empty:
        uni = pd.DataFrame(columns=["symbol", "name", "market"])
    else:
        uni = uni[["code", "name", "market"]].rename(columns={"code": "symbol"}).drop_duplicates("symbol")

    symbols = sorted(set(cat["symbol"]).union(uni["symbol"]))
    out = pd.DataFrame({"symbol": symbols}).merge(uni, on="symbol", how="left")

    def pick(dataset: str, cols: dict) -> pd.DataFrame:
        sub = cat[cat["dataset"] == dataset]
        return sub[["symbol", *cols]].rename(columns=cols)

    out = out.merge(pick("daily", {"first_ts": "daily_first", "last_ts": "daily_last", "day_count": "daily_days",
                                    "exhausted": "d_exhausted"}), on="symbol", how="left")
    out = out.merge(pick("1m", {"first_ts": "m1_first", "last_ts": "m1_last", "day_count": "m1_days",
                                 "exhausted": "m1_exhausted"}), on="symbol", how="left")
    out = out.merge(pick("1t", {"day_count": "t1_days"}), on="symbol", how="left")
    out = out.merge(pick("realtime_tick", {"row_count": "rt_tick_rows"}), on="symbol", how="left")
    out = out.merge(pick("realtime_orderbook", {"row_count": "rt_ob_rows"}), on="symbol", how="left")
    out = out.merge(pick("basic_info", {"last_ts": "info_at"}), on="symbol", how="left")

    watch = set(store.get_watchlist())
    out["watch"] = out["symbol"].isin(watch)
    for col in ("m1_exhausted", "d_exhausted"):
        out[col] = out[col].fillna(0).astype(int).astype(bool)
    return out


def latest_market_date(store: MarketDataStore) -> str | None:
    """시장 전체에서 가장 최근 일봉 날짜. 종목별 '일봉이 오래됐는지' 판단 기준으로 씁니다
    (거래일 캘린더 없이도, 대부분의 종목이 가진 가장 최근 날짜가 곧 마지막 거래일입니다)."""
    daily = store.load_catalog("daily")
    if daily.empty:
        return None
    # 혼자 튀는 미래/이상 날짜 하나에 끌려가지 않도록, 최소 3종목(또는 전체의 1%)이 가진
    # 날짜 중 가장 최신 것을 씁니다.
    counts = daily["last_ts"].dropna().value_counts()
    common = counts[counts >= max(3, int(len(daily) * 0.01))]
    return str((common if not common.empty else counts).index.max())


def find_gaps(store: MarketDataStore, min_minute_days: int = 60,
              matrix: pd.DataFrame | None = None) -> pd.DataFrame:
    """수집이 필요한 종목과 이유를 돌려줍니다. 열: symbol, name, market, watch, reasons(list[str]).

    이유(reasons):
      no_daily      일봉이 하나도 없음 (universe에는 있는데 아직 안 받음). 서버가 "데이터 없음"이라고
                    답한 종목(거래정지 등)은 제외
      daily_stale   일봉의 마지막 날짜가 시장 최신 날짜보다 오래됨
      no_minute     1분봉 없음 (서버 데이터 없음으로 확인된 종목은 제외)
      minute_short  1분봉 커버 거래일 수가 min_minute_days 미만이고, 서버에 더 과거가 남아있을 수 있음
                    (서버 끝까지 받은 종목(exhausted)은 제외)
      no_info       기본정보 없음
    """
    m = matrix if matrix is not None else catalog_matrix(store)
    # 수집 대상 = universe(ETF/ETN/우선주/스팩 제외된 전체 종목) + 관심종목. 그 밖에 예전에 받아둔
    # 잔여 종목(universe에서 빠진 ETF 등)은 새로 받을 대상이 아닙니다.
    m = m[m["market"].notna() | m["watch"]].reset_index(drop=True)
    ref = latest_market_date(store)

    reasons: list[list[str]] = []
    for r in m.itertuples(index=False):
        why = []
        if pd.isna(r.daily_last):
            if not r.d_exhausted:
                why.append("no_daily")
        elif ref and r.daily_last < ref:
            why.append("daily_stale")
        if pd.isna(r.m1_days):
            if not r.m1_exhausted:
                why.append("no_minute")
        elif r.m1_days < min_minute_days and not r.m1_exhausted:
            why.append("minute_short")
        if pd.isna(r.info_at):
            why.append("no_info")
        reasons.append(why)

    out = m[["symbol", "name", "market", "watch"]].copy()
    out["reasons"] = reasons
    return out[out["reasons"].map(len) > 0].reset_index(drop=True)


REASON_LABELS = {
    "no_daily": "일봉 없음",
    "daily_stale": "일봉 오래됨",
    "no_minute": "분봉 없음",
    "minute_short": "분봉 얕음",
    "no_info": "기본정보 없음",
}


def summarize(store: MarketDataStore) -> dict:
    """대시보드 상단 요약 카드용 숫자들."""
    m = catalog_matrix(store)
    return {
        "symbols": len(m),
        "universe": int(m["market"].notna().sum()),
        "daily": int(m["daily_days"].notna().sum()),
        "minute": int(m["m1_days"].notna().sum()),
        "rt_tick": int(m["rt_tick_rows"].notna().sum()),
        "rt_orderbook": int(m["rt_ob_rows"].notna().sum()),
        "info": int(m["info_at"].notna().sum()),
        "watch": int(m["watch"].sum()),
    }


if __name__ == "__main__":
    from config_loader import load_config

    _store = MarketDataStore(load_config()["data"]["db_path"])
    print("카탈로그 재집계 중... (수십 초 걸립니다)")
    print(refresh_catalog(_store))
    print(summarize(_store))
    _gaps = find_gaps(_store)
    print(f"수집 필요 종목: {len(_gaps)}개")
    for _reason, _label in REASON_LABELS.items():
        print(f"  {_label}: {int(_gaps['reasons'].map(lambda x: _reason in x).sum())}개")

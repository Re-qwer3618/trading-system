"""
"오늘 상승 가능성이 높은 종목"을 찾아 관심종목에 추가합니다.

여기 쓰는 규칙은 감이 아니라 `research_strategy.py`(전략 검증 도구)로 실제 검증한 것입니다
(시가총액 상위 300종목, 2021-01~2026-09, 5거래일 보유, 손절 2%, 왕복비용 0.41% 기준. 기준: 같은 날
표본 평균 대비 초과수익, 월 단위 t값, 앞 70% 학습/뒤 30% 검증 모두 통과). 검증 결과는 모두 "단기
낙폭과대 반등" 계열이었고, 거래량 축소/증가 계열은 유의한 초과수익이 없었습니다(그래서 여기 없음).
RULES 딕셔너리에 검증 날짜를 적어 두었으니, 몇 달 지나면 `run.bat research`로 다시 검증하고
이 규칙을 갱신하세요 — 시장 국면이 바뀌면 안 통할 수 있습니다.

**주의**: 통계적으로 약한 우위(평균 초과수익 +0.1~0.3%/5일 수준)이지 확실한 상승 보장이 아닙니다.
여기서 하는 일은 "관심종목에 후보로 추가"까지입니다 — 실제 매수는 하지 않고, 매수 판단은
live_trade.py(전략+리스크 관리)가 별도로 합니다.

실행:
    python src/screen_candidates.py                 # 시가총액 상위 300종목 스캔, 상위 10개 미리보기
    python src/screen_candidates.py --add            # 위와 같되 실제로 관심종목에 추가
    python src/screen_candidates.py --scope watchlist --add   # 관심종목만 다시 스캔해 태그 갱신
    python src/screen_candidates.py --n 500 --top 15 --add
"""

import sys
import argparse
import pandas as pd

from config_loader import load_config
from data_layer.storage import MarketDataStore
from data_layer.catalog import latest_market_date
from research.features import compute_features
from research import study

# 검증 날짜: 2026-09-26 (표본 top_cap 300, 2021-01-01~2026-09-23, horizon=5). 재검증 주기: 분기 1회 권장.
RULES = [
    {
        "name": "과매도 반등",
        "desc": "RSI(14) ≤ 30 (낙폭과대)",
        "check": lambda r: pd.notna(r.get("rsi14")) and r["rsi14"] <= 30,
    },
    {
        "name": "급락 후 박스하단",
        "desc": "5일 수익률 ≤ -4.1% 그리고 60일 박스 내 위치 ≤ 0.08 (검증: 학습 t=5.05, 검증 t=1.52, 둘 다 양(+))",
        "check": lambda r: (pd.notna(r.get("ret_5d")) and r["ret_5d"] <= -0.041
                            and pd.notna(r.get("range_pos_60")) and r["range_pos_60"] <= 0.08),
    },
    {
        "name": "약세과열 반전",
        "desc": "RSI(14) ≤ 39.63 그리고 캔들 몸통 ≤ -1.7% (검증: 검증구간 t=2.51)",
        "check": lambda r: (pd.notna(r.get("rsi14")) and r["rsi14"] <= 39.63
                            and pd.notna(r.get("body_pct")) and r["body_pct"] <= -0.017),
    },
]

_WARMUP_DAYS = 450  # 250일 최고가 등 특징 계산에 필요한 최소 과거 구간


def scan(store: MarketDataStore, symbols: list[str], as_of: str | None = None) -> pd.DataFrame:
    """symbols 각각의 '기준일' 시점 특징을 계산해 RULES에 걸리는 종목만 돌려줍니다.

    as_of=None이면 종목마다 저장된 마지막 일봉(보통 같은 날 — latest_market_date)을 씁니다.
    반환 열: symbol, name, market, last_date, last_close, hits(걸린 규칙 이름 목록), rsi14, ret_5d, range_pos_60, body_pct
    """
    cutoff = None
    rows = []
    for symbol in symbols:
        df = store.load(symbol)
        if len(df) < 61:
            continue
        if as_of:
            df = df[df["date"] <= as_of]
            if df.empty:
                continue
        if cutoff is None:  # 워밍업 구간은 종목마다 같은 날짜 수만 있으면 되므로 tail로 충분
            pass
        df = df.tail(_WARMUP_DAYS)
        feats = compute_features(df)
        row = feats.iloc[-1].to_dict()
        hits = [rule["name"] for rule in RULES if rule["check"](row)]
        if not hits:
            continue
        rows.append({
            "symbol": symbol, "last_date": df.iloc[-1]["date"], "last_close": float(df.iloc[-1]["close"]),
            "hits": hits, "n_hits": len(hits),
            "rsi14": row.get("rsi14"), "ret_5d": row.get("ret_5d"), "range_pos_60": row.get("range_pos_60"),
            "body_pct": row.get("body_pct"),
        })
    if not rows:
        return pd.DataFrame(columns=["symbol", "last_date", "last_close", "hits", "n_hits",
                                     "rsi14", "ret_5d", "range_pos_60", "body_pct"])
    out = pd.DataFrame(rows)
    # 정렬: 여러 규칙에 동시에 걸릴수록 위로, 같으면 RSI가 더 낮을수록(더 과매도) 위로
    return out.sort_values(["n_hits", "rsi14"], ascending=[False, True]).reset_index(drop=True)


def screen_and_report(store: MarketDataStore, scope: str = "top_cap", n: int = 300, top: int = 10) -> pd.DataFrame:
    symbols = study.select_symbols(store, scope, n)
    as_of = latest_market_date(store)  # 표본 전체가 같은 기준일을 보도록 고정 (종목마다 최신일이 다를 수 있음)
    result = scan(store, symbols, as_of=as_of)

    names = store.basic_info_names()
    uni = store.load_universe()
    market_of = dict(zip(uni["code"], uni["market"])) if not uni.empty else {}
    watch = set(store.get_watchlist())

    result["name"] = result["symbol"].map(names)
    result["market"] = result["symbol"].map(market_of).map({"0": "코스피", "10": "코스닥"}).fillna("-")
    result["already_watched"] = result["symbol"].isin(watch)
    result.attrs["as_of"] = as_of
    result.attrs["universe_size"] = len(symbols)
    return result.head(top)


def add_candidates(store: MarketDataStore, result: pd.DataFrame, as_of: str) -> list[str]:
    added = []
    for row in result.itertuples(index=False):
        if row.already_watched:
            continue
        note = f"{as_of} 기준 스캔: " + ", ".join(row.hits)
        store.add_to_watchlist(row.symbol, source="strategy", strategy="검증규칙", note=note)
        added.append(row.symbol)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="top_cap", choices=["top_cap", "watchlist", "random"])
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--add", action="store_true", help="찾은 후보를 실제로 관심종목에 추가 (기본은 미리보기만)")
    args = ap.parse_args()

    config = load_config()
    store = MarketDataStore(config["data"]["db_path"], readonly=not args.add)

    result = screen_and_report(store, args.scope, args.n, args.top)
    as_of = result.attrs.get("as_of")
    print(f"기준일: {as_of} (표본 {result.attrs.get('universe_size')}종목 중 규칙에 걸린 상위 {len(result)}개)")
    if result.empty:
        print("오늘은 규칙에 걸리는 종목이 없습니다.")
        return
    show = result[["symbol", "name", "market", "last_close", "n_hits", "hits", "already_watched"]].copy()
    show["hits"] = show["hits"].map(lambda h: " / ".join(h))
    print(show.to_string(index=False))

    if args.add:
        added = add_candidates(store, result, as_of)
        print(f"\n관심종목에 {len(added)}종목 추가함: {added}")
        already = [s for s in result["symbol"] if s not in added]
        if already:
            print(f"이미 관심종목이라 건너뜀: {already}")
    else:
        print("\n미리보기입니다. 실제로 관심종목에 추가하려면 --add 를 붙이세요.")


if __name__ == "__main__":
    main()

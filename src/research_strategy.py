"""
매수 타이밍 전략 검증 (명령줄). 대시보드의 "🔬 전략 검증" 페이지와 같은 분석을 콘솔로 봅니다.
시장 데이터는 읽기 전용으로만 엽니다 (분석이 수집 데이터를 바꿀 수 없게).

실행:
    python src/research_strategy.py                          # 시가총액 상위 300종목, 2021~, 5일 보유
    python src/research_strategy.py --scope watchlist        # 관심종목만
    python src/research_strategy.py --n 500 --start 2019-01-01 --horizon 10
    python src/research_strategy.py --stop 3 --minute         # 손절 3%, 분봉 특징 포함(분봉 1년치가 있는 기간만)
    python src/research_strategy.py --no-discover            # 규칙 탐색 생략(더 빠름)

읽는 법과 한계는 research/study.py 상단 설명 참고 (초과수익, 월 단위 t값, 학습/검증 분리, 생존편향).
"""

import sys
import time
import argparse
import pandas as pd

from config_loader import load_config
from data_layer.storage import MarketDataStore
from research.features import FEATURES
from research import study, events

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 70)


def _pct(x):
    return "-" if x != x else f"{x:+.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="top_cap", choices=["top_cap", "watchlist", "random"])
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--horizon", type=int, default=5, choices=[1, 3, 5, 10, 20])
    ap.add_argument("--stop", type=float, default=2.0, help="손절선(%%) — 진입가 대비 이만큼 아래 저가가 나오면 손절로 계산")
    ap.add_argument("--cost", type=float, default=0.41, help="왕복 거래비용(%%)")
    ap.add_argument("--minute", action="store_true", help="분봉 기반 특징 포함 (분봉이 있는 기간만 분석)")
    ap.add_argument("--no-discover", action="store_true")
    args = ap.parse_args()

    config = load_config()
    store = MarketDataStore(config["data"]["db_path"], readonly=True)

    symbols = study.select_symbols(store, args.scope, args.n)
    print(f"표본: {len(symbols)}종목 ({args.scope}), 기간 {args.start}~, 보유 {args.horizon}일, 손절 {args.stop}%, 비용 {args.cost}%")
    t0 = time.time()
    panel = study.build_panel(store, symbols, args.start, stop_pct=args.stop, cost_pct=args.cost,
                              use_minute=args.minute)
    if panel.empty:
        print("분석할 데이터가 없습니다.")
        return 1
    if args.minute:
        panel = panel[panel["open30_vol_share"].notna()].copy()
        panel["pos"] = panel.groupby("symbol").cumcount()
    print(f"패널: {len(panel):,}행 ({panel['symbol'].nunique()}종목, {panel['date'].min()}~{panel['date'].max()}), "
          f"준비 {time.time() - t0:.0f}초\n")
    hz = args.horizon
    feats = FEATURES + (list(study.MINUTE_FEATURE_INFO) if args.minute else [])

    print("=" * 30, "1. 이름 붙은 매수 타이밍 비교", "=" * 30)
    cmp = events.compare_candidates(panel, hz, study.evaluate_event)
    if not cmp.empty:
        show = cmp[["전략", "n", "n_symbols", "mean_ret_pct", "excess_pct", "win_pct", "base_win_pct", "month_t",
                    "pos_months_pct", "symbol_pos_pct", "train_excess_pct", "test_excess_pct"]].copy()
        show.columns = ["전략", "신호수", "종목수", "평균수익%", "초과수익%", "승률%", "전체승률%", "월t", "+월%",
                        "+종목%", "학습초과%", "검증초과%"]
        print(show.round(2).to_string(index=False))
    print("* 초과수익 = 같은 날 표본 평균 대비. 월t가 2 이상이고 학습/검증 초과가 같은 부호일 때만 의미를 둘 만합니다.\n")

    print("=" * 30, "2. 특징별 유효성 (5분위, Q5-Q1 스프레드)", "=" * 30)
    scan = study.feature_scan(panel, hz, feats)
    if not scan.empty:
        show = scan[["name", "ic", "spread_pct", "spread_t", "same_sign_months", "monotonic",
                     "q1_ex_pct", "q3_ex_pct", "q5_ex_pct"]].copy()
        show.columns = ["특징", "IC", "스프레드%", "월t", "같은부호월", "단조성", "Q1초과%", "Q3초과%", "Q5초과%"]
        print(show.round(3).to_string(index=False))
    print("* 스프레드>0: 값이 클수록 좋음, <0: 값이 작을수록 좋음. 월t는 월별 스프레드 기준.\n")

    print("=" * 30, "3. 좋은 매수 시점의 공통 특징 (초과수익 상위 10% vs 하위 10%)", "=" * 30)
    prof = study.entry_profile(panel, hz, features=feats)
    if not prof.empty:
        show = prof[["name", "good_pos", "bad_pos", "gap", "symbol_consistency_pct", "symbols"]].copy()
        show.columns = ["특징", "좋은시점 위치", "나쁜시점 위치", "차이", "종목일관성%", "종목수"]
        print(show.head(12).round(3).to_string(index=False))
    print("* 위치: 같은 날 종목들 중 백분위(0=최저, 1=최고, 0.5=차이없음).\n")

    if not args.no_discover:
        print("=" * 30, "4. 규칙 탐색 (앞 70% 학습 -> 뒤 30% 검증)", "=" * 30)
        rules, tried = study.discover_rules(panel, hz, features=feats)
        print(f"시도한 조건 {tried}개 중 학습 구간 상위 {len(rules)}개:")
        if not rules.empty:
            show = rules[["rule", "train_n", "train_excess_pct", "train_t", "test_n", "test_excess_pct", "test_t", "holds"]].copy()
            show.columns = ["규칙", "학습n", "학습초과%", "학습t", "검증n", "검증초과%", "검증t", "검증통과"]
            print(show.round(2).to_string(index=False))
        print("* 검증통과 = 검증 구간에서도 초과수익 > 0 이고 t ≥ 1. 수백 개를 시도했으니 통과해도 종목별/기간별 일관성을 "
              "추가로 확인하세요.\n")

    print("=" * 30, "5. 펀더멘털과의 연관성 (종목 단위, 참고용)", "=" * 30)
    with store._connect() as conn:
        basic = pd.read_sql_query("SELECT symbol, per, pbr, roe, market_cap FROM stock_basic_info", conn)
    fl = study.fundamental_link(panel, hz, basic)
    print(fl.round(3).to_string(index=False) if not fl.empty else "기본정보가 부족합니다.")
    print("* 기본정보는 현재 시점 스냅샷이라 과거 시점 값이 아닙니다 (미래 정보 혼입).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
매수 타이밍 전략 검증 페이지.

"이런 조건에서 샀더니 정말 더 올랐나?"를 여러 종목 x 수년치 데이터에서 통계적으로 확인하고,
검증된 조건을 실제 전략(feature_rule)으로 옮길 수 있게 합니다. 분석 로직은 research/ 패키지에 있고
이 페이지는 화면만 담당합니다 (명령줄: run.bat research). 시장 데이터는 읽기 전용으로만 엽니다.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import streamlit as st

from ui_common import require_login, render_header
from config_loader import load_config
from data_layer.storage import MarketDataStore
from research import study, events
from research.features import FEATURES, FEATURE_INFO, MINUTE_FEATURE_INFO
from strategy.rule_strategy import FeatureRuleStrategy
from strategy.ma_cross_strategy import MACrossStrategy
from backtest.backtest_engine import BacktestEngine
from risk.risk_manager import RiskManager

st.set_page_config(page_title="전략 검증", page_icon="🔬", layout="wide")
require_login()
render_header("🔬 매수 타이밍 전략 검증")

config = load_config()
store = MarketDataStore(config["data"]["db_path"], readonly=True)  # 분석은 시장 데이터를 바꾸지 못하게 읽기 전용
bt_cfg = config.get("backtest", {})
default_stop = float(config["risk"].get("risk_limit_pct", 2))
default_cost = round((bt_cfg.get("fee_rate", 0.00015) * 2 + bt_cfg.get("tax_rate", 0.0018)
                      + bt_cfg.get("slippage_rate", 0.001) * 2) * 100, 2)

with st.expander("📖 이 화면을 읽는 법과 한계 (꼭 한 번 읽어주세요)"):
    st.markdown("""
- **하는 일**: 매일·종목마다 거래량/이동평균/RSI 등 특징을 계산하고, "그날 종가를 보고 → 다음날 시가에 사서 N일 뒤 종가에 팔았다면"의
  성과를 봅니다. 손절선에 닿으면 손절한 것으로 계산하고, 왕복 거래비용을 뺍니다.
- **초과수익**: 같은 날 표본 전체 평균을 뺀 값입니다. 시장이 오른 날 산 것이 전략의 공이 되지 않게 합니다.
- **월t / +월%**: 월 단위로 묶어 계산합니다. 같은 날 여러 종목은 시장 요인으로 함께 움직여서, 종목-일 단위로 계산하면
  유의성이 크게 부풀려집니다. **월t 2 이상 + 양(+)의 달 60% 이상 + 종목별 일관성**이 함께 보여야 의미를 둘 만합니다.
- **규칙 탐색**은 앞 70% 기간에서 찾고 뒤 30%에서 다시 확인합니다. 수백 개 조건을 시도하므로 우연히 좋아 보이는 규칙이
  반드시 섞입니다 — 검증 구간에서도 유지되는지가 핵심이고, 그래도 **실전 투입 전에 모의로 충분히 지켜보세요**.
- **한계**: 지금 상장된 종목만 있어 상장폐지 종목이 빠진 *생존편향*(수익률이 낙관적)이 있고, 기본정보(PER 등)는 과거 시점 값이
  아니라 현재 스냅샷입니다. 일봉은 같은 날 고가/저가 순서를 몰라 저가가 손절선 아래면 손절로 봅니다(보수적).
""")


# ---------------------------------------------------------------------------
# 분석 조건
# ---------------------------------------------------------------------------
with st.form("research_form"):
    c1, c2, c3, c4 = st.columns(4)
    scope_label = c1.selectbox("표본 종목", ["시가총액 상위", "관심종목", "무작위"], help="상장 이래 일봉이 400일 이상 있는 종목만")
    n_symbols = c2.slider("종목 수", 30, 800, 300, step=10, disabled=scope_label == "관심종목")
    start = c3.text_input("시작일", value="2021-01-01", help="이 날짜 이후의 신호만 평가합니다 (지표 계산용 과거 데이터는 자동으로 더 읽음)")
    horizon = c4.selectbox("보유기간(거래일)", [1, 3, 5, 10, 20], index=2)
    c5, c6, c7 = st.columns(3)
    stop_pct = c5.number_input("손절선 (%)", 0.5, 20.0, default_stop, step=0.5)
    cost_pct = c6.number_input("왕복 거래비용 (%)", 0.0, 2.0, default_cost, step=0.01,
                               help="수수료 양쪽 + 매도세 + 슬리피지 양쪽 (config backtest 값 합산)")
    use_minute = c7.toggle("분봉 특징 포함 (장초반 거래량 등)", value=False,
                           help="분봉이 있는 기간(약 1년)만 분석합니다. 분봉 수집이 끝난 종목이 많을수록 정확합니다.")
    submitted = st.form_submit_button("분석 실행", type="primary")


@st.cache_resource(show_spinner=False, max_entries=2)
def _build(scope, n, start_date, stop, cost, minute):
    symbols = study.select_symbols(store, scope, n)
    bar = st.progress(0.0, text="데이터 준비 중...")
    panel = study.build_panel(store, symbols, start_date, stop_pct=stop, cost_pct=cost, use_minute=minute,
                              progress=lambda i, total: bar.progress(i / total, text=f"특징 계산 {i}/{total}"))
    bar.empty()
    if minute and not panel.empty:
        panel = panel[panel["open30_vol_share"].notna()].copy()
        panel["pos"] = panel.groupby("symbol").cumcount()
    return symbols, panel


if submitted:
    scope_key = {"시가총액 상위": "top_cap", "관심종목": "watchlist", "무작위": "random"}[scope_label]
    st.session_state["research_params"] = (scope_key, int(n_symbols), start, float(stop_pct), float(cost_pct), bool(use_minute))

params = st.session_state.get("research_params")
if not params:
    st.info("조건을 정하고 [분석 실행]을 누르세요. 종목 300개 x 5년 기준 준비에 약 10~30초 걸립니다.")
    st.stop()

symbols, panel = _build(*params)
if panel.empty:
    st.error("분석할 데이터가 없습니다. 표본/시작일을 바꿔보세요.")
    st.stop()

feats = FEATURES + (list(MINUTE_FEATURE_INFO) if params[5] else [])
ex_col = f"ex_{horizon}"
st.caption(f"표본 {panel['symbol'].nunique():,}종목 · {len(panel):,}개 종목-일 · {panel['date'].min()} ~ {panel['date'].max()} · "
           f"보유 {horizon}일 · 손절 {params[3]}% · 비용 {params[4]}% · 생존편향 있음(상장폐지 종목 제외)")

tab_cmp, tab_feat, tab_prof, tab_rule, tab_fund = st.tabs(
    ["📋 전략 후보 비교", "📈 특징별 유효성", "🧬 좋은 매수 시점의 공통 특징", "🔎 규칙 탐색 · 전략 반영", "🏦 기본정보 연관성"])


def _rule_backtest(conds: list[dict], hold: int, n_sym: int = 20) -> tuple[pd.DataFrame, dict]:
    """규칙 전략을 표본 종목 일부에 실제 백테스트 엔진(비용/손절/다음날 시가 체결)으로 돌려 봅니다."""
    picks = symbols[:n_sym]
    risk = RiskManager(config["risk"]["max_position_pct"], params[3], starting_cash=config["broker"]["starting_cash"])
    rows = []
    bar = st.progress(0.0, text="백테스트 중...")
    for i, sym in enumerate(picks, start=1):
        common = dict(risk_manager=risk, starting_cash=config["broker"]["starting_cash"],
                      fee_rate=bt_cfg.get("fee_rate", 0.00015), tax_rate=bt_cfg.get("tax_rate", 0.0018),
                      slippage_rate=bt_cfg.get("slippage_rate", 0.001), execution="next_open", start_date=params[2])
        rule_res = BacktestEngine(store, FeatureRuleStrategy(conds, max_hold_days=hold), **common).run(sym)
        base_res = BacktestEngine(store, MACrossStrategy(), **common).run(sym)
        if "error" in rule_res:
            continue
        rows.append({"종목": sym, "규칙 수익%": rule_res["return_pct"], "규칙 거래수": rule_res["num_trades"],
                     "기존전략(MA교차) 수익%": base_res.get("return_pct"), "기존전략 거래수": base_res.get("num_trades")})
        bar.progress(i / len(picks), text=f"백테스트 {i}/{len(picks)}")
    bar.empty()
    df = pd.DataFrame(rows)
    summary = {}
    if not df.empty:
        summary = {"종목수": len(df), "규칙 평균수익%": round(df["규칙 수익%"].mean(), 2),
                   "규칙 수익 종목%": round((df["규칙 수익%"] > 0).mean() * 100, 1),
                   "기존 평균수익%": round(df["기존전략(MA교차) 수익%"].mean(), 2),
                   "기존 수익 종목%": round((df["기존전략(MA교차) 수익%"] > 0).mean() * 100, 1)}
    return df, summary


def _yaml_snippet(conds: list[dict], hold: int) -> str:
    lines = ['strategy:', '  name: "feature_rule"', '  params:', '    entry:']
    for c in conds:
        lines.append(f'      - {{feature: {c["feature"]}, op: "{c["op"]}", value: {c["value"]:.6g}}}')
    lines.append(f'    max_hold_days: {hold}')
    return "\n".join(lines)


def _show_event(result: dict):
    if result.get("n", 0) == 0:
        st.warning("이 조건에 해당하는 신호가 없습니다.")
        return
    m = st.columns(6)
    m[0].metric("신호 수", f"{result['n']:,}", help="같은 종목에서 보유기간 안에 겹친 신호는 하나로 셈")
    m[1].metric("평균 수익", f"{result['mean_ret_pct']:+.2f}%", f"전체 {result['base_mean_ret_pct']:+.2f}%")
    m[2].metric("초과수익", f"{result['excess_pct']:+.2f}%")
    m[3].metric("승률", f"{result['win_pct']:.1f}%", f"전체 {result['base_win_pct']:.1f}%")
    m[4].metric("월 t값", f"{result['month_t']:.2f}" if result['month_t'] == result['month_t'] else "-",
                help="2 이상이면 통계적으로 유의한 편 (월 단위 군집)")
    m[5].metric("수익 종목 비율", f"{result['symbol_pos_pct']:.0f}%" if result['symbol_pos_pct'] == result['symbol_pos_pct'] else "-",
                help="신호가 5번 이상 있던 종목 중 초과수익이 +인 종목 비율 (공통성)")
    st.caption(f"학습(앞 70%) 초과수익 {result['train_excess_pct']:+.2f}% (n={result['train_n']:,}) → "
               f"검증(뒤 30%) {result['test_excess_pct']:+.2f}% (n={result['test_n']:,}) · "
               f"양(+)의 달 {result['pos_months_pct']:.0f}% ({result['months']}개월) · 평균 최대손실폭 {result['avg_mae_pct']:.2f}% / 최대이익폭 {result['avg_mfe_pct']:+.2f}%")
    st.dataframe(result["by_year"].rename(columns={"n": "신호수", "초과수익": "초과수익%", "승률": "승률%"}), use_container_width=True)


# ===========================================================================
# 탭 1: 이름 붙은 전략 후보 + 직접 만든 조건
# ===========================================================================
with tab_cmp:
    st.caption("거래량 축소/증가 같은 아이디어를 같은 표본·같은 보유기간으로 나란히 비교합니다. 임계값은 출발점이며 아래에서 직접 바꿔볼 수 있습니다.")
    cmp = events.compare_candidates(panel, horizon, study.evaluate_event)
    if cmp.empty:
        st.info("신호가 나온 후보가 없습니다.")
    else:
        show = cmp[["전략", "설명", "n", "n_symbols", "mean_ret_pct", "excess_pct", "win_pct", "base_win_pct", "month_t",
                    "pos_months_pct", "symbol_pos_pct", "train_excess_pct", "test_excess_pct"]].rename(columns={
            "n": "신호수", "n_symbols": "종목수", "mean_ret_pct": "평균수익%", "excess_pct": "초과수익%", "win_pct": "승률%",
            "base_win_pct": "전체승률%", "month_t": "월t", "pos_months_pct": "+월%", "symbol_pos_pct": "+종목%",
            "train_excess_pct": "학습초과%", "test_excess_pct": "검증초과%"})
        st.dataframe(show.round(2), hide_index=True, use_container_width=True)
        st.caption("초과수익이 + 이고 월t ≥ 2, 학습/검증 초과가 같은 부호, +종목이 60% 이상이면 후보로 볼 만합니다. 대부분은 여기서 탈락하는 게 정상입니다.")

    st.markdown("##### 직접 조건 만들기")
    st.caption("최대 3개 조건을 AND로 묶어 평가합니다. 값의 범위는 표본에서 10/50/90% 분위를 참고하세요.")
    custom: list[dict] = []
    for i in range(3):
        a, b, c, d = st.columns([3, 1, 2, 3])
        f = a.selectbox(f"조건 {i + 1} 특징", ["(사용 안 함)"] + feats, format_func=lambda x: "(사용 안 함)" if x == "(사용 안 함)" else study.feature_name(x),
                        key=f"cf_{i}")
        if f == "(사용 안 함)":
            continue
        q = panel[f].quantile([.1, .5, .9])
        op = b.selectbox("비교", ["<=", ">="], key=f"co_{i}", label_visibility="collapsed")
        val = c.number_input("값", value=float(q[.5]), format="%.4f", key=f"cv_{i}_{f}", label_visibility="collapsed")
        d.caption(f"{FEATURE_INFO.get(f, MINUTE_FEATURE_INFO.get(f, ('', '')))[1]}  \n표본 10/50/90%: {q[.1]:.3g} / {q[.5]:.3g} / {q[.9]:.3g}")
        custom.append({"feature": f, "op": op, "value": float(val)})
    if custom:
        mask = study.mask_from_conditions(panel, custom).fillna(False)
        st.markdown(f"**{study.describe_conditions(custom)}**")
        _show_event(study.evaluate_event(panel, mask, horizon))
        with st.expander("이 조건을 전략으로 옮기기 / 백테스트"):
            st.code(_yaml_snippet(custom, horizon), language="yaml")
            st.caption("config/base.yaml의 strategy 항목에 붙여넣으면 main.py/live_trade.py가 이 규칙으로 신호를 냅니다 "
                       "(종목별로 쓰려면 strategy.assignments.<종목코드> 아래에 name/params로).")
            if st.button("표본 상위 20종목에 백테스트", key="bt_custom"):
                bt_df, bt_sum = _rule_backtest(custom, horizon)
                st.session_state["res_bt_custom"] = (bt_df, bt_sum)
            if "res_bt_custom" in st.session_state:
                bt_df, bt_sum = st.session_state["res_bt_custom"]
                if bt_sum:
                    st.write(bt_sum)
                    st.dataframe(bt_df.round(2), hide_index=True, use_container_width=True)

# ===========================================================================
# 탭 2: 특징별 유효성
# ===========================================================================
with tab_feat:
    st.caption("같은 날 종목들을 특징 값 기준 5분위로 나눠(Q1=가장 낮음 … Q5=가장 높음) 이후 초과수익을 비교합니다. "
               "스프레드(Q5−Q1)가 양수면 값이 클수록, 음수면 값이 작을수록 좋다는 뜻입니다.")
    scan = study.feature_scan(panel, horizon, feats)
    if scan.empty:
        st.info("계산할 수 있는 특징이 없습니다.")
    else:
        show = scan[["name", "ic", "spread_pct", "spread_t", "same_sign_months", "monotonic",
                     "q1_ex_pct", "q2_ex_pct", "q3_ex_pct", "q4_ex_pct", "q5_ex_pct"]].rename(columns={
            "name": "특징", "ic": "IC", "spread_pct": "스프레드%", "spread_t": "월t", "same_sign_months": "같은부호 월비율",
            "monotonic": "단조성", "q1_ex_pct": "Q1%", "q2_ex_pct": "Q2%", "q3_ex_pct": "Q3%", "q4_ex_pct": "Q4%", "q5_ex_pct": "Q5%"})
        st.dataframe(show.round(3), hide_index=True, use_container_width=True)
        pick = st.selectbox("분위별 성과를 볼 특징", scan["feature"].tolist(), format_func=study.feature_name)
        row = scan[scan["feature"] == pick].iloc[0]
        chart = pd.DataFrame({"초과수익%": [row[f"q{i}_ex_pct"] for i in range(1, 6)],
                              "승률%": [row[f"q{i}_win_pct"] for i in range(1, 6)]}, index=[f"Q{i}" for i in range(1, 6)])
        c1, c2 = st.columns(2)
        c1.bar_chart(chart[["초과수익%"]])
        c2.bar_chart(chart[["승률%"]])
        st.caption(FEATURE_INFO.get(pick, MINUTE_FEATURE_INFO.get(pick, ("", "")))[1])

# ===========================================================================
# 탭 3: 좋은 매수 시점의 공통 특징
# ===========================================================================
with tab_prof:
    top_frac = st.slider("좋은/나쁜 시점의 기준 (상위·하위 %)", 5, 25, 10, step=5) / 100
    prof = study.entry_profile(panel, horizon, top_frac=top_frac, features=feats)
    st.caption("초과수익이 상위 구간(좋은 시점)/하위 구간(나쁜 시점)이었던 날, 각 특징이 같은 날 종목들 사이에서 평균 몇 %위치였나 "
               "(0.5=차이 없음). 두 값이 0.5 양쪽으로 갈릴수록 구분력이 있고, **종목일관성**이 높을수록 특정 종목만의 현상이 아니라 공통 특징입니다.")
    if not prof.empty:
        show = prof[["name", "good_pos", "bad_pos", "gap", "symbol_consistency_pct", "symbols"]].rename(columns={
            "name": "특징", "good_pos": "좋은 시점 위치", "bad_pos": "나쁜 시점 위치", "gap": "차이",
            "symbol_consistency_pct": "종목 일관성%", "symbols": "비교 종목수"})
        st.dataframe(show.round(3), hide_index=True, use_container_width=True)
        st.caption("주의: 이 표는 '좋았던 날의 사후 특징'이라 그대로 매수 규칙이 되진 않습니다. 구분력이 큰 특징을 골라 "
                   "[규칙 탐색] 탭에서 임계값을 찾고 검증 구간에서 다시 확인하세요.")

# ===========================================================================
# 탭 4: 규칙 탐색 + 전략 반영
# ===========================================================================
with tab_rule:
    st.caption("특징 조건(단일 또는 2개 조합)을 앞 70% 기간에서 찾아 뒤 30% 기간에서 다시 확인합니다. 수백 개를 시도하므로 "
               "'검증통과'만 후보로 보고, 종목별 일관성과 백테스트까지 확인하세요.")
    min_events = st.slider("학습 구간 최소 신호 수", 100, 2000, 300, step=50)
    if st.button("🔎 규칙 탐색 실행", key="discover"):
        with st.spinner("조건을 탐색하는 중..."):
            rules, tried = study.discover_rules(panel, horizon, features=feats, min_events=min_events)
        st.session_state["rules"] = (rules, tried, horizon)
    found = st.session_state.get("rules")
    if found and found[2] == horizon:
        rules, tried, _ = found
        st.caption(f"시도한 조건 {tried}개 중 학습 구간 t값 상위 {len(rules)}개. 시도가 많을수록 우연히 좋아 보이는 규칙이 섞일 확률이 큽니다.")
        if rules.empty:
            st.info("조건을 만족하는 규칙이 없습니다 (최소 신호 수를 낮춰보세요).")
        else:
            show = rules[["rule", "train_n", "train_excess_pct", "train_t", "test_n", "test_excess_pct", "test_t",
                          "test_pos_months", "holds"]].rename(columns={
                "rule": "규칙", "train_n": "학습n", "train_excess_pct": "학습초과%", "train_t": "학습t", "test_n": "검증n",
                "test_excess_pct": "검증초과%", "test_t": "검증t", "test_pos_months": "검증 +월%", "holds": "검증통과"})
            st.dataframe(show.round(2), hide_index=True, use_container_width=True)
            idx = st.selectbox("자세히 볼 규칙", range(len(rules)), format_func=lambda i: f"{i + 1}. {rules.iloc[i]['rule']}")
            conds = rules.iloc[idx]["conds"]
            _show_event(study.evaluate_event(panel, study.mask_from_conditions(panel, conds).fillna(False), horizon))
            st.code(_yaml_snippet(conds, horizon), language="yaml")
            if st.button("표본 상위 20종목에 백테스트", key="bt_rule"):
                st.session_state["res_bt_rule"] = _rule_backtest(conds, horizon)
            if "res_bt_rule" in st.session_state:
                bt_df, bt_sum = st.session_state["res_bt_rule"]
                if bt_sum:
                    st.write(bt_sum)
                    st.dataframe(bt_df.round(2), hide_index=True, use_container_width=True)
                    st.caption("백테스트는 다음날 시가 체결 + 수수료/세금/슬리피지 + 손절을 반영하고, 비교 대상은 현재 기본 전략(MA 5/20 교차)입니다. "
                               "종목 하나하나는 신호가 적어 변동이 큽니다 — 평균과 수익 종목 비율을 보세요.")

# ===========================================================================
# 탭 5: 기본정보 연관성
# ===========================================================================
with tab_fund:
    st.caption("종목별 평균 초과수익(모든 날)과 PER/PBR/ROE/시가총액의 순위상관입니다. **기본정보는 현재 시점 값**이라 과거 시점의 "
               "값이 아니고(미래 정보 혼입), 종목 수만큼의 표본이라 통계적 힘이 약합니다 — 가설을 세우는 참고용으로만 보세요.")
    with store._connect() as conn:
        basic = pd.read_sql_query("SELECT symbol, per, pbr, roe, market_cap FROM stock_basic_info", conn)
    fl = study.fundamental_link(panel, horizon, basic)
    st.dataframe(fl.round(3), hide_index=True, use_container_width=True) if not fl.empty else st.info("기본정보가 부족합니다.")

"""
차트 및 전략 분석 페이지.
좌측 사이드바 아코디언은 "관심종목"(watchlist — 실시간 매매/수집이 실제로 지켜보는
종목)만 보여줍니다. collect_all.py로 전체 종목(수천 개)을 받아도 여기 버튼이 수천
개로 늘어나지 않는 이유입니다. 관심종목이 아닌 종목의 과거 기록을 보고 싶으면
검색으로 찾아서 바로 조회하거나 관심종목에 추가할 수 있습니다.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import random
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
import pandas as pd
from ui_common import require_login, render_header
from ui_charts import kiwoom_candle_chart, kiwoom_orderbook_html
from config_loader import load_config
from data_layer.storage import MarketDataStore
from close_day import _ticks_to_minute_bars  # 실시간 틱 -> 1분봉, close_day.py와 같은 로직 재사용
from agents.decision_maker import DecisionMaker

st.set_page_config(page_title="차트 및 분석", page_icon="📈", layout="wide")
require_login()
render_header("📈 차트 및 분석")

config = load_config()
store = MarketDataStore(config["data"]["db_path"])
watchlist = store.get_watchlist() or store.symbols()  # watchlist가 아직 비어있으면(최초 상태) 수집된 것 전체로 폴백

# ---------------------------------------------------------------------------
# 사이드바: 종목 검색 (전체 수집 종목 대상 — 관심종목이 아니어도 조회/추가 가능)
# ---------------------------------------------------------------------------
search_query = st.sidebar.text_input("🔍 종목 검색 (코드 또는 이름)", placeholder="예: 005930 또는 삼성전자")
if search_query:
    q = search_query.strip()
    matches = []
    for s in store.symbols():
        info = store.get_basic_info(s)
        name = info["name"] if info else None
        if q in s or (name and q in name):
            matches.append((s, name))
    if not matches:
        st.sidebar.caption("일치하는 수집 종목이 없습니다 (아직 수집 안 됐을 수 있습니다).")
    for s, name in matches[:15]:
        label = f"{s} ({name})" if name else s
        col_a, col_b = st.sidebar.columns([2, 1])
        if col_a.button(label, key=f"search_pick_{s}", use_container_width=True):
            st.session_state.selected_symbol = s
        if s in watchlist:
            col_b.caption("관심✓")
        elif col_b.button("＋관심", key=f"search_add_{s}", use_container_width=True):
            store.add_to_watchlist(s)
            st.rerun()

st.sidebar.markdown("---")

# ---------------------------------------------------------------------------
# 사이드바: 전략별 아코디언 그룹 (관심종목만)
# ---------------------------------------------------------------------------
# 지금은 실제 전략별 자동분류 로직이 없어서, 관심종목을 예시로 두 그룹에 나눠 보여줍니다.
# TODO: 전략 엔진이 종목마다 태그를 남기면 여기서 실제 분류로 교체
strategy_groups = {
    "전략-1 (단타)": watchlist[: max(1, len(watchlist) // 2)] or ["005930"],
    "전략-2 (스윙)": watchlist[max(1, len(watchlist) // 2):] or [],
}

if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = watchlist[0] if watchlist else "005930"

for group_name, syms in strategy_groups.items():
    with st.sidebar.expander(group_name, expanded=(group_name == "전략-1 (단타)")):
        if not syms:
            st.caption("종목 없음")
        for sym in syms:
            col_a, col_b = st.columns([3, 1])
            if col_a.button(sym, key=f"sym_{sym}", use_container_width=True):
                st.session_state.selected_symbol = sym
            if col_b.button("✕", key=f"unwatch_{sym}", help="관심종목에서 제거"):
                store.remove_from_watchlist(sym)
                st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(f"관심종목 {len(watchlist)}개 · 전체 수집 종목 {len(store.symbols())}개")
st.sidebar.caption("종목 목록은 `run.bat collect 종목코드`로 수집한 종목만 실제 차트가 보입니다.")

selected = st.session_state.selected_symbol

# ---------------------------------------------------------------------------
# 종목 기본정보 (PER/PBR/시가총액 등, ka10001 — collect_all.py --info로 채워짐)
# ---------------------------------------------------------------------------
basic_info = store.get_basic_info(selected)
if basic_info:
    name = basic_info.get("name") or selected
    st.subheader(f"{selected} {name} — 기본정보")
    b1, b2, b3, b4, b5 = st.columns(5)
    b1.metric("시가총액", f"{basic_info['market_cap']:,.0f}억" if basic_info.get("market_cap") else "-")
    b2.metric("PER", f"{basic_info['per']:.2f}" if basic_info.get("per") is not None else "-")
    b3.metric("PBR", f"{basic_info['pbr']:.2f}" if basic_info.get("pbr") is not None else "-")
    b4.metric("ROE", f"{basic_info['roe']:.1f}%" if basic_info.get("roe") is not None else "-")
    b5.metric("250일 최고/최저", (
        f"{basic_info['high_250']:,.0f} / {basic_info['low_250']:,.0f}"
        if basic_info.get("high_250") and basic_info.get("low_250") else "-"
    ))
    st.caption(f"기준: {basic_info['updated_at']} · PER/ROE는 외부 벤더 제공 데이터라 종목에 따라 비어있을 수 있습니다.")
    st.markdown("---")

# ---------------------------------------------------------------------------
# 상단: 지수 요약 (Mock — 실시간 지수 연동 전)
# ---------------------------------------------------------------------------
idx_col1, idx_col2 = st.columns(2)
idx_col1.metric("코스피 (Mock)", "2,650.00", "+0.8%")
idx_col2.metric("코스닥 (Mock)", "850.50", "+1.2%")
st.caption("⚠️ 지수는 아직 실시간 연동 전이라 예시 값입니다.")
st.markdown("---")

main_col, side_col = st.columns([2.2, 1])

# ---------------------------------------------------------------------------
# 중앙: 선택 종목 캔들차트 (실데이터)
# ---------------------------------------------------------------------------
with main_col:
    st.subheader(f"{selected} 상세 차트")
    df = store.load(selected)

    if df.empty:
        st.info(f"{selected}의 저장된 데이터가 없습니다. `run.bat collect {selected}`를 먼저 실행해주세요.")
    else:
        df["date"] = pd.to_datetime(df["date"])
        opt_a, opt_b = st.columns([3, 1])
        visible = opt_a.select_slider("표시 봉 수", options=[60, 120, 200, 300], value=120)
        dark_chart = opt_b.toggle("어두운 배경", key="chart_dark")
        # 이동평균은 전체 기간으로 계산하고 화면에는 최근 visible개만 (120일선이 첫 봉부터 보이도록)
        fig = kiwoom_candle_chart(df, "date", visible_bars=visible, dark=dark_chart)
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True, "displaylogo": False})
        shown = df.tail(visible)
        st.caption(f"데이터 기간: {shown['date'].min().date()} ~ {shown['date'].max().date()} · 휠로 확대/축소, 드래그로 이동")

# ---------------------------------------------------------------------------
# 우측: 시장 지표 미니 차트 (Mock)
# ---------------------------------------------------------------------------
with side_col:
    st.subheader("시장 차트 및 지표")
    market = st.radio("시장 선택", ["코스피", "코스닥"], horizontal=True)
    random.seed(hash(market) % 1000)
    mock_series = pd.Series([2400 + random.uniform(-50, 80) * i * 0.1 for i in range(60)])
    st.line_chart(mock_series, height=260)
    st.caption("⚠️ Mock 데이터입니다. 실제 지수 연동은 아직 없습니다.")

st.markdown("---")

# ---------------------------------------------------------------------------
# 실시간 1분봉 + 호가창 (2초마다 자동 갱신)
# realtime/stream_collector.py가 이 종목을 구독 중이어야 값이 보입니다
# (run.bat realtime <종목코드> 또는 run.bat realtime --watchlist).
# ---------------------------------------------------------------------------
st.subheader(f"⚡ {selected} 실시간 분봉 · 호가창")


def _reference_price(symbol: str) -> float | None:
    """호가 색/등락률 기준가 = 오늘 이전 마지막 일봉 종가 (HTS의 전일종가)."""
    daily = store.load(symbol)
    if daily.empty:
        return None
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    prev = daily[pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d") < today]
    return float(prev.iloc[-1]["close"]) if not prev.empty else None


@st.fragment(run_every="2s")
def _render_realtime_section(symbol: str, ref_price: float | None):
    ticks = store.recent_realtime_ticks(symbol, limit=500)
    chart_col, book_col = st.columns([2, 1])

    with chart_col:
        if ticks.empty:
            st.info(f"{symbol}의 실시간 체결 데이터가 아직 없습니다. "
                    f"`run.bat realtime {symbol}` (또는 `run.bat realtime --watchlist`)을 먼저 켜두세요.")
        else:
            today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
            bars = _ticks_to_minute_bars(ticks, today)
            if bars.empty:
                st.info("체결은 들어오고 있는데 아직 1분봉을 채울 만큼 쌓이지 않았습니다.")
            else:
                fig = kiwoom_candle_chart(
                    bars, "timestamp", ma_periods=(5, 20), x_label_fmt="%H:%M", tick_label_fmt="%H:%M", height=380,
                    dark=st.session_state.get("chart_dark", False),
                )
                st.plotly_chart(fig, use_container_width=True, key=f"rt_chart_{symbol}",
                                config={"displaylogo": False})
            last = ticks.iloc[-1]
            st.caption(f"최근 체결: {float(last['price']):,.0f}원 · 최근 {len(ticks)}틱 · 마지막 수신 {last['received_at']}")

    with book_col:
        book = store.latest_orderbook(symbol)
        if not book:
            st.info("실시간 호가 데이터가 아직 없습니다.")
        else:
            last_price = float(ticks.iloc[-1]["price"]) if not ticks.empty else None
            st.markdown(
                kiwoom_orderbook_html(book, ref_price=ref_price, last_price=last_price,
                                      dark=st.session_state.get("chart_dark", False)),
                unsafe_allow_html=True,
            )
            ref_note = f"기준가(전일종가) {ref_price:,.0f}" if ref_price else "기준가 없음 (일봉 수집 필요)"
            st.caption(f"{ref_note} · 호가시각 {book['ts']} · 마지막 수신 {book['received_at']}")


_render_realtime_section(selected, _reference_price(selected))

st.markdown("---")

# ---------------------------------------------------------------------------
# 4-역할 위원회 (과거/현재/비교 분석가 + 매매 최종 결정권자)
# ---------------------------------------------------------------------------
st.subheader("🧭 매매 위원회 판단")
st.caption(
    "차트 분석가-1(과거) · 분석가-2(현재) · 분석가-3(과거-현재 비교)의 의견을 "
    "최종 결정권자가 취합합니다. (규칙기반, `agents/decision_maker.py`)"
)

if st.button(f"{selected} 위원회 리포트 실행", key="run_committee"):
    committee = DecisionMaker(store, config).decide(selected)
    st.session_state["committee_result"] = committee

committee = st.session_state.get("committee_result")
if committee and committee["symbol"] == selected:
    action_icon = {"BUY": "🟢 매수", "SELL": "🔴 매도", "HOLD": "🟡 관망"}.get(committee["action"], committee["action"])
    m1, m2 = st.columns(2)
    m1.metric("최종 결정", action_icon)
    m2.metric("확신도", f"{committee['confidence']}%")

    vote_cols = st.columns(3)
    vote_labels = {"history": "분석가-1 (과거)", "realtime": "분석가-2 (현재)", "comparison": "분석가-3 (비교)"}
    vote_icons = {"POSITIVE": "🟢 긍정", "NEGATIVE": "🔴 부정", "NEUTRAL": "⚪ 중립"}
    for col, (key, label) in zip(vote_cols, vote_labels.items()):
        stance = committee["votes"].get(key, "NEUTRAL")
        col.metric(label, vote_icons.get(stance, stance))

    with st.expander("판단 근거 자세히 보기"):
        st.write(committee["reasoning"])
        for key, label in vote_labels.items():
            report = committee["reports"].get(key, {})
            if report.get("summary"):
                st.markdown(f"**{label}**: {report['summary']}")
else:
    st.caption("버튼을 누르면 현재 선택된 종목에 대해 위원회를 실행합니다 (일봉/실시간 데이터가 있어야 정확합니다).")

st.markdown("---")

# ---------------------------------------------------------------------------
# 위원 파라미터 튜닝 이력 (백테스트 성공/실패 + 전환 기록)
# ---------------------------------------------------------------------------
st.subheader("🧪 위원 튜닝 이력 (분석가-1: 과거)")
st.caption(
    "`run.bat tune-history 종목코드`로 백테스트한 성공/실패 기록입니다. "
    "같은 설정이 나중에 성공↔실패로 뒤집힌 경우는 따로 표시됩니다."
)

tuning_runs = store.recent_tuning_runs(selected, "history", limit=10)
tuning_transitions = store.recent_tuning_transitions(selected, "history", limit=5)

with st.expander(f"{selected} 튜닝 기록 보기", expanded=False):
    if tuning_runs.empty:
        st.caption("아직 튜닝 기록이 없습니다. `run.bat tune-history " + selected + "` 를 실행해보세요.")
    else:
        for _, row in tuning_runs.iterrows():
            icon = "🟢" if row["outcome"] == "SUCCESS" else "🔴"
            st.markdown(f"{icon} `{row['timestamp']}` {row['summary']}")
    if not tuning_transitions.empty:
        st.markdown("**⚠️ 성공/실패가 뒤집힌 설정**")
        for _, row in tuning_transitions.iterrows():
            st.markdown(f"- {row['summary']}")

st.markdown("---")

# ---------------------------------------------------------------------------
# 하단: 실시간 시스템 로그 (실데이터 — decisions 로그)
# ---------------------------------------------------------------------------
st.subheader("🖥️ 실시간 시스템 로그")
decisions = store.recent_decisions(limit=20)

log_box = st.container(height=220, border=True)
if decisions.empty:
    log_box.caption("아직 기록이 없습니다. `run.bat main 종목코드`를 실행하면 여기 쌓입니다.")
else:
    for _, row in decisions.iterrows():
        color = {"BUY": "🟢", "SELL": "🔴", "NONE": "⚪"}.get(row["action"], "⚪")
        log_box.markdown(
            f"`{row['timestamp']}` {color} **[{row['symbol']}]** 신호: {row['signal']} · "
            f"LLM: {row['llm_stance']} · 실행: {row['action']} · {row['detail']}"
        )

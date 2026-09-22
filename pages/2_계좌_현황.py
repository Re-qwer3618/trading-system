"""
계좌 현황 및 매매기록 페이지.
현금/보유수량은 실제 브로커 연동 데이터, 실현손익/승률은 계산 로직이 아직 없어
정직하게 '준비 중'으로 표시합니다 (가짜 수익 숫자를 보여주지 않기 위함).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui_common import require_login, render_header
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_broker

st.set_page_config(page_title="계좌 현황", page_icon="💰", layout="wide")
require_login()
render_header("💰 계좌 현황 및 매매기록")

config = load_config()
store = MarketDataStore(config["data"]["db_path"])
symbols = store.symbols()
selected = st.selectbox("종목 선택", symbols) if symbols else None

# ---------------------------------------------------------------------------
# 상단 요약 카드
# ---------------------------------------------------------------------------
try:
    broker = build_broker(config, store)
    cash = broker.get_cash()
    position_qty = broker.get_position(selected) if selected else 0
    position_value = 0.0
    if selected and position_qty > 0:
        try:
            position_value = broker.get_price(selected) * position_qty
        except Exception:
            position_value = 0.0
    total_asset = cash + position_value

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 평가 자산 (현금+선택종목)", f"{total_asset:,.0f} 원")
    col2.metric("당일 실현손익", "준비 중", help="일별 실현손익 계산 로직은 아직 구현되지 않았습니다.")
    col3.metric("누적 실현손익", "준비 중", help="매매 체결 단가 기반 손익 계산은 2단계에서 추가 예정입니다.")
    col4.metric("총 승률", "준비 중", help="승/패 집계 로직은 아직 없습니다.")
except Exception as e:
    st.error(f"계좌 정보를 불러오지 못했습니다: {e}")
    cash, position_qty = None, None

st.caption("⚠️ 실현손익/승률은 아직 계산 로직이 없어 '준비 중'으로 표시했습니다 — 실제 돈이 걸린 화면이라 숫자를 지어내지 않았습니다.")
st.markdown("---")

# ---------------------------------------------------------------------------
# 보유 종목 현황 / 최근 매매 내역
# ---------------------------------------------------------------------------
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("보유 종목 현황")
    if selected and cash is not None:
        rows = []
        if position_qty and position_qty > 0:
            try:
                price = broker.get_price(selected)
                rows.append({"종목명": selected, "보유수량": position_qty, "현재가": price,
                             "평가금액": price * position_qty})
            except Exception:
                pass
        if rows:
            df_pos = pd.DataFrame(rows)
            st.dataframe(
                df_pos, hide_index=True, use_container_width=True,
                column_config={
                    "현재가": st.column_config.NumberColumn(format="%,d 원"),
                    "평가금액": st.column_config.NumberColumn(format="%,d 원"),
                },
            )
        else:
            st.info("보유 중인 종목이 없습니다.")
    else:
        st.info("데이터를 불러올 수 없습니다.")

with col_right:
    st.subheader("최근 매매 판단/체결 내역")
    decisions = store.recent_decisions(limit=30)
    if decisions.empty:
        st.info("아직 기록이 없습니다.")
    else:
        st.dataframe(decisions, hide_index=True, use_container_width=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# 하단: 월별 손익 / 일별 승률 (실데이터가 쌓이기 전까지는 안내만)
# ---------------------------------------------------------------------------
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("월별 손익 현황")
    st.info("실제 체결 단가 기반 손익 계산 로직이 아직 없어 차트를 그릴 데이터가 없습니다. "
            "2단계(전략 고도화)에서 체결 손익 계산을 추가하면 여기 자동으로 채워지도록 만들어뒀습니다.")

with chart_col2:
    st.subheader("일별 승률 추이")
    decisions_all = store.recent_decisions(limit=1000)
    trade_decisions = decisions_all[decisions_all["action"].isin(["BUY", "SELL"])]
    if trade_decisions.empty:
        st.info("아직 실제 체결 기록이 없습니다.")
    else:
        counts = trade_decisions.groupby(trade_decisions["timestamp"].str[:10]).size()
        fig = go.Figure(go.Scatter(x=counts.index, y=counts.values, mode="lines+markers"))
        fig.update_layout(template="plotly_dark", height=280, margin=dict(l=10, r=10, t=10, b=10),
                           yaxis_title="일별 매매 건수")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("승률 계산에는 손익 로직이 필요해, 지금은 일별 매매 건수만 보여드립니다.")

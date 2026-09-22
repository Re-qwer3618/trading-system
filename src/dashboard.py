"""
계좌/매매 현황을 한눈에 보는 대시보드.
직접 실행하지 말고 아래 명령어(또는 run.bat dashboard)로 실행하세요.

    streamlit run src/dashboard.py

브라우저가 자동으로 열리며 http://localhost:8501 에서 확인할 수 있습니다.
"""

import sys
from pathlib import Path

# src/ 폴더를 import 경로에 추가 (streamlit run은 PYTHONPATH를 안 물려받음)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_broker

st.set_page_config(page_title="자동매매 대시보드", page_icon="📈", layout="wide")

config = load_config()
store = MarketDataStore(config["data"]["db_path"])

st.title("📈 자동매매 시스템 대시보드")
st.caption(
    f"실행 환경: **{config['_meta']['detected_env']}**  |  "
    f"매매 모드: **{config['broker']['provider']}**"
    + ("  |  🧪 모의투자" if config.get("kiwoom", {}).get("is_mock", True) else "  |  🔴 실전투자")
)

symbols = store.symbols()
if not symbols:
    st.warning("아직 수집된 데이터가 없습니다. 먼저 `run.bat collect 종목코드`를 실행해주세요.")
    st.stop()

selected_symbol = st.sidebar.selectbox("종목 선택", symbols)
st.sidebar.markdown("---")
st.sidebar.caption("이 목록은 데이터를 한 번이라도 수집한 종목만 보여줍니다.")

# ---------------------------------------------------------------------------
# 계좌 현황
# ---------------------------------------------------------------------------
st.subheader("💰 계좌 현황")

try:
    broker = build_broker(config, store)
    cash = broker.get_cash()
    position = broker.get_position(selected_symbol)

    col1, col2, col3 = st.columns(3)
    col1.metric("주문 가능 현금", f"{cash:,.0f} 원")
    col2.metric(f"{selected_symbol} 보유수량", f"{position:,} 주")
    try:
        price = broker.get_price(selected_symbol)
        col3.metric("현재가(마지막 저장값)", f"{price:,.0f} 원", help="실시간 시세가 아니라 마지막 수집 시점 종가입니다.")
    except Exception:
        col3.metric("현재가", "N/A")
except Exception as e:
    st.error(f"계좌 정보를 불러오지 못했습니다: {e}")
    st.info("키움 API 키가 .env에 없거나, 네트워크/서버 문제일 수 있습니다.")

st.markdown("---")

# ---------------------------------------------------------------------------
# 시세 차트
# ---------------------------------------------------------------------------
st.subheader(f"📊 {selected_symbol} 시세")

df = store.load(selected_symbol)
if not df.empty:
    df["date"] = pd.to_datetime(df["date"])
    chart_range = st.radio("기간", ["최근 3개월", "최근 1년", "전체"], horizontal=True)
    if chart_range == "최근 3개월":
        df_view = df[df["date"] >= df["date"].max() - pd.Timedelta(days=90)]
    elif chart_range == "최근 1년":
        df_view = df[df["date"] >= df["date"].max() - pd.Timedelta(days=365)]
    else:
        df_view = df

    st.line_chart(df_view.set_index("date")[["close"]])
    st.caption(f"데이터 기간: {df['date'].min().date()} ~ {df['date'].max().date()} (총 {len(df)}건)")
else:
    st.info("이 종목의 시세 데이터가 없습니다.")

st.markdown("---")

# ---------------------------------------------------------------------------
# 매매 판단/실행 이력
# ---------------------------------------------------------------------------
st.subheader("📝 최근 매매 판단 이력")

decisions = store.recent_decisions(limit=50)
if decisions.empty:
    st.info("아직 기록이 없습니다. `run.bat main 종목코드`를 실행하면 여기 쌓입니다.")
else:
    st.dataframe(decisions, use_container_width=True, hide_index=True)

st.caption("이 화면은 새로고침해야 최신 상태로 갱신됩니다 (자동 갱신 아님).")

"""
매수 비중 / 손절선 / 동시 보유 종목 수를 대시보드에서 직접 조절하는 페이지.

여기서 저장하면 DB(settings 테이블)에 들어가고, live_trade.py가 매 확인 주기(기본
60초)마다 이 값을 다시 읽습니다 — 즉 라이브 매매 프로세스를 재시작하지 않아도
다음 주기부터 바로 반영됩니다. main.py(하루 한 번 실행)도 실행될 때마다 이 값을
읽습니다. 백테스트(run_backtest.py)는 재현성을 위해 이 설정을 쓰지 않고 항상
config/base.yaml 값 그대로 씁니다 — 같은 조건으로 여러 번 돌려도 결과가 달라지면
안 되기 때문입니다.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st

from ui_common import require_login, render_header
from config_loader import load_config
from data_layer.storage import MarketDataStore
from core.factory import build_broker

st.set_page_config(page_title="리스크 설정", page_icon="🎛️", layout="wide")
require_login()
render_header("🎛️ 매매 리스크 설정")

config = load_config()
store = MarketDataStore(config["data"]["db_path"])

risk_defaults = {
    "max_position_pct": config["risk"]["max_position_pct"],
    "risk_limit_pct": config["risk"]["risk_limit_pct"],
    "max_concurrent_positions": config["risk"].get("max_concurrent_positions", 5),
}
store.seed_risk_settings_if_missing(**risk_defaults)
current = store.get_risk_settings(risk_defaults)

st.caption(
    "여기서 바꾼 값은 live_trade.py가 다음 확인 주기(기본 60초) 안에 자동으로 반영합니다 — "
    "코드를 고치거나 프로세스를 재시작할 필요가 없습니다."
)
st.markdown("---")

# ---------------------------------------------------------------------------
# 참고용 계좌 정보 (비중이 실제로 얼마인지 감을 잡는 용도)
# ---------------------------------------------------------------------------
try:
    broker = build_broker(config, store)
    total_deposit = broker.get_total_deposit()
    cash_now = broker.get_cash()
    col1, col2 = st.columns(2)
    col1.metric("고정 기준 총자본 (세션 시작 시점)", f"{total_deposit:,.0f} 원",
                help="매수 비중 %는 이 값을 기준으로 계산됩니다. 그때그때 남은 현금이 아니라 고정된 값입니다.")
    col2.metric("지금 실제 주문가능금액", f"{cash_now:,.0f} 원",
                help="미체결/미정산 주문 증거금이 반영된, 지금 당장 쓸 수 있는 현금입니다.")
except Exception as e:
    total_deposit = None
    st.warning(f"계좌 정보를 불러오지 못해 원화 환산은 생략합니다: {e}")

st.markdown("---")

# ---------------------------------------------------------------------------
# 설정 폼
# ---------------------------------------------------------------------------
with st.form("risk_settings_form"):
    st.subheader("설정값")

    max_position_pct = st.slider(
        "종목당 매수 비중 (%)", min_value=1, max_value=100,
        value=int(current["max_position_pct"]), step=1,
        help="고정 기준 총자본 대비, 한 종목을 살 때 목표로 하는 금액의 비율입니다.",
    )
    max_concurrent_positions = st.number_input(
        "동시 보유 최대 종목 수", min_value=1, max_value=100,
        value=int(current["max_concurrent_positions"]), step=1,
        help="이미 이 수만큼 종목을 보유 중이면, 새로 매수 신호가 나도 건너뜁니다.",
    )
    risk_limit_pct = st.slider(
        "손절 기준 (%)", min_value=0.5, max_value=20.0,
        value=float(current["risk_limit_pct"]), step=0.5,
        help="진입가 대비 이 비율만큼 떨어지면, 전략 신호와 무관하게 즉시 전량 매도합니다.",
    )

    max_exposure_pct = min(max_position_pct * max_concurrent_positions, 100 * max_concurrent_positions)
    if total_deposit is not None:
        st.info(f"현재 설정 기준 **최대 총 노출**: 종목당 {max_position_pct}% × 최대 {max_concurrent_positions}종목 "
                f"= 총자본의 **{max_exposure_pct}%** (약 {total_deposit * max_exposure_pct / 100:,.0f}원)")
    else:
        st.info(f"현재 설정 기준 **최대 총 노출**: 종목당 {max_position_pct}% × 최대 {max_concurrent_positions}종목 "
                f"= 총자본의 **{max_exposure_pct}%**")
    if max_exposure_pct > 100:
        st.warning("⚠️ 모든 종목이 동시에 최대 비중까지 매수되면 총자본을 초과할 수 있는 조합입니다. "
                   "실제로는 그때그때 남은 현금 한도 안에서만 사지만, 비중을 줄이거나 동시 보유 종목 수를 "
                   "줄이는 걸 권장합니다.")

    submitted = st.form_submit_button("저장", use_container_width=True, type="primary")
    if submitted:
        store.set_risk_settings(
            max_position_pct=max_position_pct,
            risk_limit_pct=risk_limit_pct,
            max_concurrent_positions=int(max_concurrent_positions),
        )
        st.success("저장했습니다. 라이브 매매가 켜져 있다면 다음 확인 주기 안에 자동으로 반영됩니다.")
        st.rerun()

st.caption(f"config/base.yaml의 최초 기본값: 종목당 비중 {risk_defaults['max_position_pct']}%, "
           f"손절선 {risk_defaults['risk_limit_pct']}%, "
           f"동시보유한도 {risk_defaults['max_concurrent_positions']}종목")

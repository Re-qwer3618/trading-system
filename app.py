"""
로그인 및 환경 선택 페이지. 이게 앱의 진입점입니다.

실행:
    streamlit run app.py

보안 참고:
- DASHBOARD_PASSWORD(.env)와 정확히 일치해야 통과합니다.
- .env에 DASHBOARD_PASSWORD가 없으면 대시보드 자체가 실행을 거부합니다
  (인증 없이 실수로 열어두는 걸 막기 위한 안전장치입니다).
- 연속 실패 시 잠시 대기시간을 둬서 무차별 대입 시도를 늦춥니다.
  완전한 방어는 아니므로, 외부(인터넷)에 노출할 경우 리버스 프록시의
  Rate limit이나 Tailscale 같은 사설망 경유를 함께 쓰는 걸 권장합니다.
"""

import sys
import os
import time
import secrets
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

st.set_page_config(page_title="자동매매 시스템 대시보드", page_icon="📈", layout="centered")

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "failed_attempts" not in st.session_state:
    st.session_state.failed_attempts = 0
if "lockout_until" not in st.session_state:
    st.session_state.lockout_until = 0.0

st.markdown("## 🔐 자동매매 시스템 대시보드 로그인")
st.caption("집/회사 어디서든 홈서버에 원격으로 접속하는 화면입니다.")

# ---------------------------------------------------------------------------
# 안전장치: 비밀번호가 설정 안 돼 있으면 아예 못 열게 막습니다.
# ---------------------------------------------------------------------------
if not DASHBOARD_PASSWORD:
    st.error(
        "대시보드를 열 수 없습니다: .env에 DASHBOARD_PASSWORD가 설정되어 있지 않습니다.\n\n"
        "포트포워딩 등으로 외부에 노출하기 전, .env 파일에 다음 줄을 추가해주세요:\n"
        "DASHBOARD_PASSWORD=원하는비밀번호"
    )
    st.stop()

# ---------------------------------------------------------------------------
# 연속 실패 시 대기 (무차별 대입 완화)
# ---------------------------------------------------------------------------
now = time.time()
if now < st.session_state.lockout_until:
    remaining = int(st.session_state.lockout_until - now)
    st.warning(f"로그인 시도가 너무 많습니다. {remaining}초 후 다시 시도해주세요.")
    st.stop()

with st.form("login_form"):
    server_addr = st.text_input("서버 주소", value="127.0.0.1", help="홈서버를 원격으로 열어둔 경우 그 IP/도메인")
    port = st.text_input("포트", value="8501")
    api_key = st.text_input("API 키", type="password", help="키움 APP KEY와는 별개로, 이 대시보드 자체 접근용 키입니다 (선택, 지금은 사용 안 함).")
    password = st.text_input("비밀번호", type="password")
    environment = st.selectbox("투자 환경 선택", ["모의투자 환경 (Simulation)", "실계좌 환경 (Live)"])

    submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")

    if submitted:
        # secrets.compare_digest: 한 글자씩 순서대로 비교하지 않고 일정 시간에 비교해서
        # "몇 글자까지 맞았는지"를 응답시간으로 추측하는 타이밍 공격을 막습니다.
        if secrets.compare_digest(password, DASHBOARD_PASSWORD):
            st.session_state.logged_in = True
            st.session_state.failed_attempts = 0
            st.session_state.environment = "live" if "Live" in environment else "mock"
            st.session_state.server_addr = server_addr
            st.success("로그인 성공. 대시보드로 이동합니다...")
            st.switch_page("pages/1_차트_및_분석.py")
        else:
            st.session_state.failed_attempts += 1
            if st.session_state.failed_attempts >= 5:
                st.session_state.lockout_until = time.time() + 60
                st.session_state.failed_attempts = 0
                st.error("5회 연속 실패하여 60초간 잠급니다.")
            else:
                st.error(f"비밀번호가 올바르지 않습니다. ({st.session_state.failed_attempts}/5회 실패)")

st.caption("⚠️ '서버 주소'/'포트'/'API 키' 입력란은 아직 화면 표시용입니다 (실제 원격 서버 전환 로직 없음). "
           "지금은 이 컴퓨터에서 직접 실행 중인 대시보드에 비밀번호로 접근을 제한하는 용도로만 쓰입니다.")

st.info("💡 이미 Tailscale로 홈서버를 연결해두셨다면, 포트포워딩 없이 Tailscale 주소로만 "
        "접속하는 게 더 안전합니다 — 포트포워딩은 8501번 포트를 인터넷 전체에 여는 것이라, "
        "이 비밀번호 하나로만 막게 됩니다.")

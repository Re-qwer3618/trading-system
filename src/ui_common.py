"""
모든 페이지가 공유하는 UI 조각들.
전략 로직이 바뀌어도 이 파일은 건드릴 필요가 없도록 화면 표시만 담당합니다.
"""

import streamlit as st


def require_login():
    """로그인 안 했으면 로그인 페이지로 돌려보냄. 모든 페이지 맨 위에서 호출."""
    if not st.session_state.get("logged_in"):
        st.switch_page("app.py")


def render_header(title: str):
    """상단 공통 헤더: 타이틀 + 연결 상태 + 긴급정지 + 현재 모드."""
    if "kill_switch" not in st.session_state:
        st.session_state.kill_switch = False

    col1, col2, col3 = st.columns([3, 1.2, 1])

    with col1:
        st.markdown(f"## {title}")
        env_label = "🔴 실계좌 환경 (Live)" if st.session_state.get("environment") == "live" else "🧪 모의투자 환경 (Simulation)"
        st.caption(f"현재 모드: **{env_label}**")

    with col2:
        if st.session_state.kill_switch:
            st.error("🛑 시스템 정지됨")
        else:
            st.success("🟢 시스템 정상 구동중")

    with col3:
        btn_label = "정지 해제" if st.session_state.kill_switch else "🛑 긴급 정지"
        if st.button(btn_label, use_container_width=True, type="primary" if not st.session_state.kill_switch else "secondary"):
            st.session_state.kill_switch = not st.session_state.kill_switch
            st.rerun()

    if st.session_state.kill_switch:
        st.warning("긴급 정지가 활성화되었습니다. 이 상태에서는 신규 주문 실행 로직(main.py)을 사람이 직접 멈춰야 합니다 — "
                    "이 버튼은 화면 표시용이며 실제 실행 중인 프로세스를 강제 종료하지는 않습니다. "
                    "작업 스케줄러/터미널에서 직접 중지해주세요.")

    st.markdown("---")


def render_page_nav():
    """페이지 이동 링크 (사이드바 상단)."""
    st.sidebar.page_link("pages/1_차트_및_분석.py", label="📈 차트 및 분석")
    st.sidebar.page_link("pages/2_계좌_현황.py", label="💰 계좌 현황 및 매매기록")
    st.sidebar.markdown("---")

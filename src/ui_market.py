"""
대시보드의 시장(코스피/코스닥) 지수 표시 — 예전 Mock 값을 실제 데이터로 바꾼 부분.

데이터 출처 (우선순위):
1. 실시간 스냅샷: 키움 업종현재가(ka20001). 장중엔 현재가, 장 마감 후엔 그날 종가.
   상승/하락 종목 수(시장 폭), 거래대금, 52주 최고/최저까지 함께 옵니다.
2. 지수 분봉: 키움 업종분봉 — 당일 흐름 차트용.
3. 지수 일봉: DB의 index_ohlcv (collect_index.py, 장마감 자동 갱신 스케줄에 포함).
   실시간 조회가 실패하면(서버 오류 등) 이 마지막 종가로 대체해서 보여주고 그렇다고 표시합니다.

API 호출은 st.cache_data로 짧게 캐시합니다(스냅샷 20초, 분봉 60초) — 화면이 2~5초마다
다시 그려져도 키움 서버에는 그보다 훨씬 적게 요청이 갑니다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from core.factory import build_data_provider
from ui_charts import kiwoom_candle_chart, relative_strength_chart

INDEXES = {"코스피": "001", "코스닥": "101"}
MARKET_TO_INDEX = {"0": "001", "10": "101"}   # universe.market -> 지수 코드
_KST = ZoneInfo("Asia/Seoul")


def is_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(_KST)
    return now.weekday() < 5 and 9 * 60 <= now.hour * 60 + now.minute <= 15 * 60 + 30


@st.cache_resource(show_spinner=False)
def _provider(_config_key: str, _config: dict):
    return build_data_provider(_config)


@st.cache_data(ttl=20, show_spinner=False)
def fetch_snapshot(_config: dict, index_code: str) -> dict | None:
    try:
        return _provider("p", _config).fetch_index_snapshot(index_code)
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_minute(_config: dict, index_code: str) -> pd.DataFrame:
    try:
        return _provider("p", _config).fetch_index_minute(index_code)
    except Exception:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def _latest_trading_date(minute: pd.DataFrame) -> str | None:
    """분봉의 마지막 날짜 = 스냅샷이 가리키는 실제 거래일. 휴장일(평일)에 스냅샷이 전 거래일 값을
    그대로 주더라도 '오늘 봉'을 가짜로 만들지 않기 위해 날짜는 분봉에서 가져옵니다."""
    return None if minute.empty else str(minute["timestamp"].iloc[-1])[:10]


def daily_with_live_bar(store, config: dict, index_code: str) -> pd.DataFrame:
    """DB 일봉 + (아직 저장 안 된) 최근 거래일 봉을 스냅샷으로 덧붙인 지수 일봉."""
    daily = store.load_index(index_code)
    snap = fetch_snapshot(config, index_code)
    trade_date = _latest_trading_date(fetch_minute(config, index_code))
    if snap and snap.get("price") and trade_date and (daily.empty or trade_date > daily["date"].iloc[-1]):
        live = {"date": trade_date, "open": snap["open"] or snap["price"], "high": snap["high"] or snap["price"],
                "low": snap["low"] or snap["price"], "close": snap["price"], "volume": 0}
        daily = pd.concat([daily, pd.DataFrame([live])], ignore_index=True)
    return daily


# ---------------------------------------------------------------------------
# 상단 지수 요약 바 (30초마다 갱신)
# ---------------------------------------------------------------------------
def render_index_bar(store, config: dict):
    @st.fragment(run_every="30s")
    def _bar():
        cols = st.columns(len(INDEXES))
        notes = []
        for col, (label, code) in zip(cols, INDEXES.items()):
            snap = fetch_snapshot(config, code)
            if snap and snap.get("price"):
                col.metric(label, f"{snap['price']:,.2f}",
                           f"{snap['change']:+,.2f} ({snap['change_pct']:+.2f}%)" if snap.get("change") is not None else None)
                rising, falling = snap.get("rising") or 0, snap.get("falling") or 0
                col.caption(f"상승 {rising:,.0f} · 보합 {snap.get('flat') or 0:,.0f} · 하락 {falling:,.0f} "
                            f"(상한 {snap.get('upper_limit') or 0:,.0f}/하한 {snap.get('lower_limit') or 0:,.0f}) · "
                            f"거래대금 {snap['trade_value_eok'] / 10000:,.1f}조")
            else:
                daily = store.load_index(code)
                if daily.empty:
                    col.metric(label, "-")
                    notes.append(f"{label}: 데이터 없음 (`run.bat collect-index`)")
                    continue
                last, prev = daily.iloc[-1], daily.iloc[-2] if len(daily) > 1 else daily.iloc[-1]
                chg = last["close"] - prev["close"]
                col.metric(f"{label} (종가)", f"{last['close']:,.2f}", f"{chg:+,.2f} ({chg / prev['close'] * 100:+.2f}%)")
                notes.append(f"{label}: 실시간 조회 실패로 {last['date']} 종가 표시")
        status = "장중 · 30초마다 갱신" if is_market_hours() else "장 마감 · 최근 거래일 종가 기준"
        st.caption(f"📡 키움 업종현재가 실시간 조회 ({status}) · {datetime.now(_KST).strftime('%H:%M:%S')}"
                   + (" · ⚠️ " + " / ".join(notes) if notes else ""))

    _bar()


# ---------------------------------------------------------------------------
# 우측 시장 차트 + 지표 + 종목 상대강도
# ---------------------------------------------------------------------------
def _pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def render_market_panel(store, config: dict, selected: str, dark: bool = False):
    uni = store.load_universe()
    sel_market = dict(zip(uni["code"], uni["market"])).get(selected) if not uni.empty else None
    default_label = "코스닥" if MARKET_TO_INDEX.get(sel_market) == "101" else "코스피"

    c1, c2 = st.columns(2)
    label = c1.radio("시장", list(INDEXES), index=list(INDEXES).index(default_label), horizontal=True,
                     key="mkt_pick", help="기본값은 선택 종목이 상장된 시장입니다.")
    view = c2.radio("차트", ["일봉", "당일 분봉"], horizontal=True, key="mkt_view")
    code = INDEXES[label]

    daily = daily_with_live_bar(store, config, code)
    if view == "일봉":
        if daily.empty:
            st.info("지수 일봉이 없습니다. `run.bat collect-index`로 수집하세요.")
        else:
            fig = kiwoom_candle_chart(daily, "date", ma_periods=(5, 20, 60), visible_bars=90, height=330, dark=dark)
            st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False}, key=f"idx_daily_{code}")
    else:
        minute = fetch_minute(config, code)
        trade_date = _latest_trading_date(minute)
        today = minute[minute["timestamp"].str.startswith(trade_date)] if trade_date else minute
        if today.empty:
            st.info("지수 분봉을 불러오지 못했습니다.")
        else:
            fig = kiwoom_candle_chart(today, "timestamp", ma_periods=(5, 20), x_label_fmt="%H:%M",
                                      tick_label_fmt="%H:%M", height=330, dark=dark)
            st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False}, key=f"idx_min_{code}")
            st.caption(f"{trade_date} 1분봉")

    # ---- 지표
    snap = fetch_snapshot(config, code)
    if not daily.empty:
        close = daily["close"]
        last = float(close.iloc[-1])
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        rows = {
            "현재(종가)": f"{last:,.2f}",
            "20일선 이격": f"{_pct(last, ma20):+.2f}%" if pd.notna(ma20) else "-",
            "60일선 이격": f"{_pct(last, ma60):+.2f}%" if pd.notna(ma60) else "-",
        }
        if snap and snap.get("high_52w"):
            rows["52주 최고 대비"] = f"{_pct(last, snap['high_52w']):+.1f}% ({snap['high_52w']:,.0f})"
            rows["52주 최저 대비"] = f"{_pct(last, snap['low_52w']):+.1f}% ({snap['low_52w']:,.0f})"
        if snap and snap.get("rising") is not None and snap.get("falling"):
            ratio = snap["rising"] / snap["falling"]
            mood = "상승 우위" if ratio > 1.2 else "하락 우위" if ratio < 0.8 else "혼조"
            rows["상승/하락 비율"] = f"{ratio:.2f} ({mood})"
        st.dataframe(pd.DataFrame({"지표": list(rows), "값": list(rows.values())}),
                     hide_index=True, use_container_width=True)

    # ---- 선택 종목 vs 시장 상대강도 (차트 심리분석의 "시장 대비 움직임")
    st.markdown(f"**{selected} vs {label} 상대강도**")
    period = st.select_slider("기간(거래일)", options=[20, 60, 120], value=60, key="rs_period")
    stock = store.load(selected)
    if stock.empty or daily.empty:
        st.caption("비교할 데이터가 없습니다.")
        return
    stock = stock.tail(period + 5)
    idx = daily[daily["date"] >= stock["date"].iloc[0]]
    merged = stock[["date", "close"]].merge(idx[["date", "close"]], on="date").tail(period)
    if len(merged) < 2:
        st.caption("겹치는 기간의 데이터가 부족합니다.")
        return
    fig = relative_strength_chart(merged.rename(columns={"close_x": "close"})[["date", "close"]],
                                  merged.rename(columns={"close_y": "close"})[["date", "close"]],
                                  selected, label, dark=dark)
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False}, key=f"rs_{selected}_{code}")
    s_ret = _pct(merged["close_x"].iloc[-1], merged["close_x"].iloc[0])
    i_ret = _pct(merged["close_y"].iloc[-1], merged["close_y"].iloc[0])
    diff = s_ret - i_ret
    verdict = "시장보다 강함" if diff > 3 else "시장보다 약함" if diff < -3 else "시장과 비슷"
    st.caption(f"최근 {len(merged)}거래일: 종목 {s_ret:+.1f}% / {label} {i_ret:+.1f}% → 상대강도 {diff:+.1f}%p ({verdict})")

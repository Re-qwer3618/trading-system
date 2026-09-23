"""
"실시간 차트 분석 에이전트" — 1단계 규칙기반 버전.

realtime/stream_collector.py가 채워둔 realtime_ticks 테이블을 읽어서
아주 짧은 구간의 모멘텀/거래량 급증을 판단합니다. 실시간 수집기가 아직
안 돌고 있어도(=데이터가 없어도) 에러 없이 "데이터 없음"을 알려줍니다.

"위원 교육"(판단 기준 조정)은 코드가 아니라 config/base.yaml의 analysts.realtime
값을 바꾸는 방식으로 합니다. 예: 더 예민하게 반응시키고 싶으면
momentum_up_pct/momentum_down_pct의 절대값을 줄이세요.
"""

from __future__ import annotations

import pandas as pd

# config/base.yaml의 analysts.realtime이 없을 때 쓰는 기본값 (기존 동작과 100% 동일).
_DEFAULT_PARAMS = {
    "lookback": 200,
    "momentum_up_pct": 0.5,
    "momentum_down_pct": -0.5,
    "volume_spike_window": 20,
    "volume_spike_alert_ratio": 2.0,
}


class RealtimeAnalyst:
    def __init__(self, store, params: dict | None = None):
        self.store = store
        self.params = {**_DEFAULT_PARAMS, **(params or {})}

    def analyze(self, symbol: str, lookback: int | None = None) -> dict:
        p = self.params
        lookback = lookback if lookback is not None else p["lookback"]
        df = self.store.recent_realtime_ticks(symbol, limit=lookback)
        if df.empty:
            return {
                "symbol": symbol,
                "ok": False,
                "summary": f"{symbol}: 실시간 체결 데이터가 아직 없습니다. "
                           f"'run.bat realtime {symbol}' 로 수집기를 먼저 켜두세요.",
            }

        prices = df["price"].astype(float)
        volumes = df["volume"].astype(float)

        first_price = prices.iloc[0]
        last_price = prices.iloc[-1]
        momentum_pct = round((last_price - first_price) / first_price * 100, 3) if first_price else 0.0

        window = min(p["volume_spike_window"], len(volumes))
        recent_vol = volumes.tail(window).mean()
        overall_vol = volumes.mean()
        volume_spike_ratio = round(recent_vol / overall_vol, 2) if overall_vol else None

        if momentum_pct > p["momentum_up_pct"]:
            momentum_flag = "단기 급등"
        elif momentum_pct < p["momentum_down_pct"]:
            momentum_flag = "단기 급락"
        else:
            momentum_flag = "보합"

        result = {
            "symbol": symbol,
            "ok": True,
            "tick_count": len(df),
            "last_price": float(last_price),
            "momentum_pct": momentum_pct,
            "momentum_flag": momentum_flag,
            "volume_spike_ratio": volume_spike_ratio,
            "last_received_at": df["received_at"].iloc[-1],
        }
        note = ""
        if volume_spike_ratio is not None and volume_spike_ratio > p["volume_spike_alert_ratio"]:
            note = " 최근 거래량이 평소보다 눈에 띄게 몰리고 있습니다."
        result["summary"] = (
            f"{symbol}: 최근 {len(df)}틱 기준 {momentum_flag} ({momentum_pct:+.2f}%), "
            f"현재가 {last_price:,.0f}원.{note}"
        )
        return result

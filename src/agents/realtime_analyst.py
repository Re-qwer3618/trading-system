"""
"실시간 차트 분석 에이전트" — 1단계 규칙기반 버전.

realtime/stream_collector.py가 채워둔 realtime_ticks 테이블을 읽어서
아주 짧은 구간의 모멘텀/거래량 급증을 판단합니다. 실시간 수집기가 아직
안 돌고 있어도(=데이터가 없어도) 에러 없이 "데이터 없음"을 알려줍니다.
"""

from __future__ import annotations

import pandas as pd


class RealtimeAnalyst:
    def __init__(self, store):
        self.store = store

    def analyze(self, symbol: str, lookback: int = 200) -> dict:
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

        window = min(20, len(volumes))
        recent_vol = volumes.tail(window).mean()
        overall_vol = volumes.mean()
        volume_spike_ratio = round(recent_vol / overall_vol, 2) if overall_vol else None

        if momentum_pct > 0.5:
            momentum_flag = "단기 급등"
        elif momentum_pct < -0.5:
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
        if volume_spike_ratio is not None and volume_spike_ratio > 2.0:
            note = " 최근 거래량이 평소보다 눈에 띄게 몰리고 있습니다."
        result["summary"] = (
            f"{symbol}: 최근 {len(df)}틱 기준 {momentum_flag} ({momentum_pct:+.2f}%), "
            f"현재가 {last_price:,.0f}원.{note}"
        )
        return result

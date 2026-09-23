"""
"과거 차트 분석 에이전트" — 1단계 규칙기반 버전.

llm/advisor.py의 NoOpAdvisor/LocalGemmaAdvisor와 같은 철학입니다: 지금은
pandas로 계산하는 결정적 규칙이고, 나중에 LLM을 붙일 때도 analyze()의
반환 형태(dict)는 그대로 두고 안에서 LLM 해설을 추가하는 식으로 확장합니다.

분석 항목 (기본값 기준, 실제 값은 config/base.yaml의 analysts.history로 조정 가능):
- 추세: 5/20/60일 이동평균의 정렬 및 기울기
- 변동성: 최근 20일 일간수익률 표준편차 (연율화)
- 박스권: 최근 60일 고가/저가 대비 현재가 위치
- 거래량: 최근 5일 평균 vs 이전 20일 평균

"위원 교육"(판단 기준 조정)은 코드가 아니라 config/base.yaml의 analysts.history
값을 바꾸는 방식으로 합니다. 예: 더 짧은 추세에 민감하게 반응시키고 싶으면
sma_short/sma_mid/sma_long을 줄이세요.
"""

from __future__ import annotations

import pandas as pd

# config/base.yaml의 analysts.history가 없을 때 쓰는 기본값 (기존 동작과 100% 동일).
_DEFAULT_PARAMS = {
    "sma_short": 5,
    "sma_mid": 20,
    "sma_long": 60,
    "volatility_window": 20,
    "box_window": 60,
    "volume_recent_days": 5,
    "volume_prior_days": 20,
    "min_bars_required": 20,
}


class HistoryAnalyst:
    def __init__(self, store, params: dict | None = None):
        self.store = store
        self.params = {**_DEFAULT_PARAMS, **(params or {})}

    def analyze(self, symbol: str) -> dict:
        p = self.params
        df = self.store.load(symbol)
        if df.empty or len(df) < p["min_bars_required"]:
            return {
                "symbol": symbol,
                "ok": False,
                "summary": f"{symbol}: 분석하기엔 데이터가 부족합니다 (최소 {p['min_bars_required']}일봉 필요, 현재 {len(df)}건).",
            }

        close = df["close"]
        volume = df["volume"]

        sma5 = close.rolling(p["sma_short"]).mean().iloc[-1]
        sma20 = close.rolling(p["sma_mid"]).mean().iloc[-1]
        sma60 = close.rolling(min(p["sma_long"], len(close))).mean().iloc[-1]
        last_close = close.iloc[-1]

        if sma5 > sma20 > sma60:
            trend = "상승 정배열"
        elif sma5 < sma20 < sma60:
            trend = "하락 역배열"
        else:
            trend = "혼조/전환 구간"

        daily_return = close.pct_change().dropna()
        volatility_window = daily_return.tail(p["volatility_window"]).std()
        annualized_vol = (volatility_window * (252 ** 0.5)) if pd.notna(volatility_window) else None

        window = min(p["box_window"], len(close))
        recent_high = close.tail(window).max()
        recent_low = close.tail(window).min()
        if recent_high != recent_low:
            box_position_pct = round((last_close - recent_low) / (recent_high - recent_low) * 100, 1)
        else:
            box_position_pct = 50.0

        recent_days = p["volume_recent_days"]
        prior_days = p["volume_prior_days"]
        vol_recent = volume.tail(recent_days).mean()
        vol_prior = (
            volume.tail(recent_days + prior_days).head(prior_days).mean()
            if len(volume) >= recent_days + prior_days
            else volume.mean()
        )
        volume_ratio = round(vol_recent / vol_prior, 2) if vol_prior else None

        result = {
            "symbol": symbol,
            "ok": True,
            "as_of": df["date"].iloc[-1],
            "last_close": float(last_close),
            "trend": trend,
            "sma5": round(float(sma5), 2),
            "sma20": round(float(sma20), 2),
            "sma60": round(float(sma60), 2) if pd.notna(sma60) else None,
            "annualized_volatility_pct": round(float(annualized_vol) * 100, 1) if annualized_vol is not None else None,
            "box_range": {"low": float(recent_low), "high": float(recent_high), "position_pct": box_position_pct},
            "volume_ratio_recent_vs_prior": volume_ratio,
        }
        result["summary"] = self._summarize(result, p)
        return result

    @staticmethod
    def _summarize(r: dict, p: dict) -> str:
        parts = [
            f"{r['symbol']} ({r['as_of']} 기준, 종가 {r['last_close']:,.0f}원): {r['trend']}.",
            f"최근 {r['box_range']['position_pct']}% 위치 (구간 {r['box_range']['low']:,.0f}~{r['box_range']['high']:,.0f}원).",
        ]
        if r["annualized_volatility_pct"] is not None:
            parts.append(f"연율화 변동성 약 {r['annualized_volatility_pct']}%.")
        if r["volume_ratio_recent_vs_prior"] is not None:
            ratio = r["volume_ratio_recent_vs_prior"]
            note = "거래량 증가" if ratio > 1.3 else ("거래량 감소" if ratio < 0.7 else "거래량 평이")
            parts.append(
                f"최근 {p['volume_recent_days']}일 평균거래량은 이전 {p['volume_prior_days']}일 평균의 {ratio}배 ({note})."
            )
        return " ".join(parts)

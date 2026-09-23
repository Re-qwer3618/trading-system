"""
"차트 분석가-3 (과거 vs 현재 비교)" — 1단계 규칙기반 버전.

HistoryAnalyst(과거 차트)와 RealtimeAnalyst(현재 체결)의 결과를 받아서
"둘이 같은 얘기를 하고 있는지, 엇갈리고 있는지"를 판단합니다.
LLM 없이도 동작하는 결정적 규칙이며, decision_maker.py의 위원회 구조에서
세 번째 위원으로 참여합니다.

단독 실행도 가능합니다 (history_result/realtime_result를 생략하면
내부적으로 HistoryAnalyst/RealtimeAnalyst를 새로 호출합니다). 다만
DecisionMaker에서 쓸 때는 이미 계산해둔 결과를 넘겨서 DB 조회를
중복하지 않는 편이 좋습니다.

"위원 교육"(판단 기준 조정)은 코드가 아니라 config/base.yaml의 analysts.comparison
값을 바꾸는 방식으로 합니다. 예: 박스권 돌파를 더 보수적으로 보고 싶으면
box_breakout_high_pct를 95로 올리세요.
"""

from __future__ import annotations

from agents.history_analyst import HistoryAnalyst
from agents.realtime_analyst import RealtimeAnalyst

# config/base.yaml의 analysts.comparison이 없을 때 쓰는 기본값 (기존 동작과 100% 동일).
_DEFAULT_PARAMS = {
    "box_breakout_high_pct": 90,
    "box_breakout_low_pct": 10,
}


class ComparisonAnalyst:
    def __init__(self, store, params: dict | None = None):
        self.store = store
        self.params = {**_DEFAULT_PARAMS, **(params or {})}

    def analyze(self, symbol: str, history_result: dict | None = None, realtime_result: dict | None = None) -> dict:
        history = history_result if history_result is not None else HistoryAnalyst(self.store).analyze(symbol)
        realtime = realtime_result if realtime_result is not None else RealtimeAnalyst(self.store).analyze(symbol)

        if not history.get("ok") or not realtime.get("ok"):
            missing = []
            if not history.get("ok"):
                missing.append("과거 차트 데이터")
            if not realtime.get("ok"):
                missing.append("실시간 체결 데이터")
            return {
                "symbol": symbol,
                "ok": False,
                "stance": "NEUTRAL",
                "summary": f"{symbol}: {', '.join(missing)}가 부족해 과거/현재 비교를 할 수 없습니다.",
            }

        trend = history["trend"]  # "상승 정배열" | "하락 역배열" | "혼조/전환 구간"
        momentum_flag = realtime["momentum_flag"]  # "단기 급등" | "단기 급락" | "보합"
        box_position_pct = history["box_range"]["position_pct"]
        momentum_pct = realtime["momentum_pct"]

        trend_dir = 1 if trend == "상승 정배열" else (-1 if trend == "하락 역배열" else 0)
        momentum_dir = 1 if momentum_flag == "단기 급등" else (-1 if momentum_flag == "단기 급락" else 0)

        if trend_dir != 0 and trend_dir == momentum_dir:
            agreement = "일치(추세 강화)"
            stance = "POSITIVE" if trend_dir > 0 else "NEGATIVE"
        elif trend_dir != 0 and momentum_dir != 0 and trend_dir != momentum_dir:
            agreement = "괴리(단기 vs 추세 반대)"
            # 추세 전환 초입일 수도, 단기 되돌림일 수도 있어 여기서는 중립으로 취급
            stance = "NEUTRAL"
        elif trend_dir != 0 and momentum_dir == 0:
            agreement = "추세 유지, 단기 모멘텀은 보합"
            stance = "POSITIVE" if trend_dir > 0 else "NEGATIVE"
        else:
            agreement = "뚜렷한 추세 없음, 단기 흐름 위주로 관망"
            stance = "NEUTRAL"

        breakout_note = None
        if box_position_pct >= self.params["box_breakout_high_pct"] and momentum_pct > 0:
            breakout_note = "박스권 상단 부근에서 추가 상승 시도 중일 수 있습니다 (돌파 여부 확인 필요)."
        elif box_position_pct <= self.params["box_breakout_low_pct"] and momentum_pct < 0:
            breakout_note = "박스권 하단 부근에서 추가 하락 중일 수 있습니다 (이탈 여부 확인 필요)."

        result = {
            "symbol": symbol,
            "ok": True,
            "stance": stance,
            "agreement": agreement,
            "history_trend": trend,
            "realtime_momentum": momentum_flag,
            "box_position_pct": box_position_pct,
            "breakout_note": breakout_note,
        }
        summary = f"{symbol}: 과거 추세({trend})와 현재 흐름({momentum_flag})은 {agreement}."
        if breakout_note:
            summary += f" {breakout_note}"
        result["summary"] = summary
        return result

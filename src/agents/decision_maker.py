"""
"매매 최종 결정권자" — 3명의 차트 분석가(위원)의 의견을 취합해서
최종 BUY/SELL/HOLD를 결정하는 오케스트레이터입니다.

위원 구성:
  - 차트 분석가-1 (과거 차트)   : agents.history_analyst.HistoryAnalyst
  - 차트 분석가-2 (현재 차트)   : agents.realtime_analyst.RealtimeAnalyst
  - 차트 분석가-3 (과거-현재 비교) : agents.comparison_analyst.ComparisonAnalyst

각 위원은 stance(POSITIVE/NEGATIVE/NEUTRAL)를 내고, DecisionMaker는
config["decision"]["weights"]에 정의된 가중치로 다수결/가중합을 내서
최종 action과 confidence(0~100)를 계산합니다.

llm/advisor.py의 NoOpAdvisor와 같은 철학입니다: 지금은 규칙기반
가중합이고, 나중에 LLM을 "네 번째 위원"으로 추가하고 싶으면
config["decision"]["weights"]["llm"]을 넣고 이 파일의
_collect_votes()에 한 줄만 추가하면 됩니다.

main.py에서는 config["decision"]["enabled"]가 true일 때만 이 결과로
매매 신호를 보강(게이팅)합니다. false(기본값)면 기존 규칙기반
strategy.generate_signal() 로직만 그대로 사용되어 하위호환이 유지됩니다.
"""

from __future__ import annotations

from agents.history_analyst import HistoryAnalyst
from agents.realtime_analyst import RealtimeAnalyst
from agents.comparison_analyst import ComparisonAnalyst

_STANCE_SCORE = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0}

_DEFAULT_WEIGHTS = {
    "history": 1.0,      # 차트 분석가-1 (과거)
    "realtime": 1.0,     # 차트 분석가-2 (현재)
    "comparison": 1.5,   # 차트 분석가-3 (비교) — 둘의 정합성을 보는 역할이라 가중치를 조금 더 줌
}

_DEFAULT_BUY_THRESHOLD = 0.4
_DEFAULT_SELL_THRESHOLD = -0.4


class DecisionMaker:
    def __init__(self, store, config: dict | None = None):
        self.store = store
        decision_cfg = (config or {}).get("decision", {})
        self.weights = {**_DEFAULT_WEIGHTS, **decision_cfg.get("weights", {})}
        self.buy_threshold = decision_cfg.get("buy_threshold", _DEFAULT_BUY_THRESHOLD)
        self.sell_threshold = decision_cfg.get("sell_threshold", _DEFAULT_SELL_THRESHOLD)

        # 각 위원의 판단 기준(임계값)은 config/base.yaml의 analysts.* 섹션에서 조정합니다.
        analysts_cfg = (config or {}).get("analysts", {})
        self.history_analyst = HistoryAnalyst(store, analysts_cfg.get("history"))
        self.realtime_analyst = RealtimeAnalyst(store, analysts_cfg.get("realtime"))
        self.comparison_analyst = ComparisonAnalyst(store, analysts_cfg.get("comparison"))

    @staticmethod
    def _history_stance(result: dict) -> str:
        if not result.get("ok"):
            return "NEUTRAL"
        trend = result["trend"]
        if trend == "상승 정배열":
            return "POSITIVE"
        if trend == "하락 역배열":
            return "NEGATIVE"
        return "NEUTRAL"

    @staticmethod
    def _realtime_stance(result: dict) -> str:
        if not result.get("ok"):
            return "NEUTRAL"
        flag = result["momentum_flag"]
        if flag == "단기 급등":
            return "POSITIVE"
        if flag == "단기 급락":
            return "NEGATIVE"
        return "NEUTRAL"

    def decide(self, symbol: str) -> dict:
        history = self.history_analyst.analyze(symbol)
        realtime = self.realtime_analyst.analyze(symbol)
        comparison = self.comparison_analyst.analyze(symbol, history, realtime)

        reports = {
            "history": {"ok": history.get("ok", False), "stance": self._history_stance(history), "report": history},
            "realtime": {"ok": realtime.get("ok", False), "stance": self._realtime_stance(realtime), "report": realtime},
            "comparison": {"ok": comparison.get("ok", False), "stance": comparison.get("stance", "NEUTRAL"), "report": comparison},
        }

        total_weight = 0.0
        weighted_score = 0.0
        votes_summary = []
        for name, vote in reports.items():
            if not vote["ok"]:
                votes_summary.append(f"{name}=데이터없음(제외)")
                continue
            w = self.weights.get(name, 0.0)
            score = _STANCE_SCORE[vote["stance"]]
            weighted_score += w * score
            total_weight += w
            votes_summary.append(f"{name}={vote['stance']}(가중치 {w})")

        if total_weight == 0:
            action = "HOLD"
            confidence = 0.0
        else:
            score = weighted_score / total_weight
            confidence = round(min(abs(score), 1.0) * 100, 1)
            if score >= self.buy_threshold:
                action = "BUY"
            elif score <= self.sell_threshold:
                action = "SELL"
            else:
                action = "HOLD"

        reasoning_parts = [f"위원 의견: {', '.join(votes_summary)}."]
        for name in ("history", "realtime", "comparison"):
            summary = reports[name]["report"].get("summary")
            if summary:
                reasoning_parts.append(summary)

        return {
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "reasoning": " ".join(reasoning_parts),
            "votes": {name: reports[name]["stance"] for name in reports},
            "reports": {name: reports[name]["report"] for name in reports},
        }

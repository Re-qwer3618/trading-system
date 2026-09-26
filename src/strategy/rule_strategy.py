"""
검증 화면(research/)에서 찾은 "특징 조건" 규칙을 그대로 매매 전략으로 쓰는 전략.

    strategy:
      name: "feature_rule"
      params:
        entry:                       # 모두 만족하면 BUY
          - {feature: vol_ratio_5_20, op: "<=", value: 0.6}
          - {feature: close_ma20_gap, op: ">=", value: 0.0}
        exit:                        # (선택) 모두 만족하면 SELL
          - {feature: close_ma20_gap, op: "<=", value: -0.03}
        max_hold_days: 5             # (선택) 진입 후 이 거래일이 지나면 청산 — 검증에서 쓴 보유기간과 맞추세요

신호는 상태 없이(stateless) "오늘 종가까지의 특징이 조건을 만족하나"로만 정합니다. 보유 여부는
엔진(백테스트/live_trade)이 이미 알고 있어서(BUY는 무포지션일 때만, SELL은 포지션이 있을 때만 실행),
전략이 따로 기억하면 손절·주문 거부 때 실제 포지션과 어긋나기 때문입니다. 보유기간 청산
(max_hold_days)은 엔진이 이 속성을 읽어 처리합니다.

특징 계산은 research/features.py와 동일한 함수를 씁니다 — 검증에서 본 숫자와 실제 신호가 어긋나지 않게
하기 위해서입니다 (검증은 "신호일 종가까지의 정보 -> 다음날 시가 진입"이 기준입니다).

백테스트 엔진은 하루씩 늘어나는 창(window)을 넘기므로 매번 특징을 처음부터 다시 계산하면 너무
느립니다. prepare()가 전체 기간의 특징을 한 번 계산해 두고(특징은 과거 값만 쓰므로 창을 잘라 계산한
값과 같음 — 테스트로 확인), generate_signal은 해당 날짜 행만 조회합니다. 실전(live_trade)에서는
prepare 없이 호출되므로 그때는 최근 구간으로 계산합니다.
"""

import pandas as pd

from .base_strategy import BaseStrategy
from research.features import compute_features, FEATURES


class FeatureRuleStrategy(BaseStrategy):
    def __init__(self, entry: list[dict], exit: list[dict] | None = None, max_hold_days: int | None = None):
        if not entry:
            raise ValueError("feature_rule 전략에는 entry 조건이 하나 이상 필요합니다.")
        for cond in list(entry) + list(exit or []):
            if cond["feature"] not in FEATURES:
                raise ValueError(f"알 수 없는 특징: {cond['feature']} (사용 가능: {FEATURES})")
            if cond["op"] not in ("<=", ">="):
                raise ValueError(f"op는 '<=' 또는 '>='만 가능합니다: {cond}")
        self.entry = list(entry)
        self.exit = list(exit or [])
        self.max_hold_days = max_hold_days
        self._cache: dict[str, pd.DataFrame] = {}  # symbol -> 날짜 인덱스의 특징 표

    def prepare(self, symbol: str, df: pd.DataFrame) -> None:
        """백테스트용: 전체 일봉의 특징을 한 번에 계산해 둡니다."""
        feats = compute_features(df)
        feats.index = pd.Index([str(d) for d in df["date"]])
        self._cache[symbol] = feats

    @staticmethod
    def _all(row: pd.Series, conds: list[dict]) -> bool:
        for c in conds:
            v = row.get(c["feature"])
            if v is None or pd.isna(v):
                return False
            if (c["op"] == "<=" and not v <= c["value"]) or (c["op"] == ">=" and not v >= c["value"]):
                return False
        return True

    def _row(self, symbol: str, df: pd.DataFrame) -> pd.Series:
        date = df.iloc[-1]["date"]
        cached = self._cache.get(symbol)
        if cached is not None and date in cached.index:
            return cached.loc[date]
        # 캐시가 없거나(실전) 날짜가 없으면: 250일 특징에 필요한 만큼의 최근 구간으로 계산
        return compute_features(df.tail(400)).iloc[-1]

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> str:
        if len(df) < 61:
            return "HOLD"  # 60일 이동평균 등 특징 계산에 데이터가 부족
        row = self._row(symbol, df)
        if self.exit and self._all(row, self.exit):
            return "SELL"
        if self._all(row, self.entry):
            return "BUY"
        return "HOLD"

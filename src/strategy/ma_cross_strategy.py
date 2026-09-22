"""
1단계 기본 전략: 단순 이동평균선 교차
- 단기 이동평균이 장기 이동평균을 위로 뚫으면 BUY
- 아래로 뚫으면 SELL
- 그 외엔 HOLD

이건 '검증용 최소 규칙'입니다. 시스템 배관(주문~체결~로깅)이
문제없이 도는지 확인하는 게 목적이고, 수익을 내는 게 목적이 아닙니다.
"""

import pandas as pd
from .base_strategy import BaseStrategy


class MACrossStrategy(BaseStrategy):
    def __init__(self, short_window: int = 5, long_window: int = 20):
        self.short_window = short_window
        self.long_window = long_window

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> str:
        if len(df) < self.long_window + 1:
            return "HOLD"  # 데이터가 아직 부족함

        close = df["close"]
        short_ma = close.rolling(self.short_window).mean()
        long_ma = close.rolling(self.long_window).mean()

        prev_diff = short_ma.iloc[-2] - long_ma.iloc[-2]
        curr_diff = short_ma.iloc[-1] - long_ma.iloc[-1]

        if prev_diff <= 0 and curr_diff > 0:
            return "BUY"
        if prev_diff >= 0 and curr_diff < 0:
            return "SELL"
        return "HOLD"

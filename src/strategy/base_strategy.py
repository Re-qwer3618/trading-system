"""
'매매 신호를 만드는 방식'의 규격을 정의합니다.
1단계는 단순 규칙(이동평균 교차)이고,
3단계에서 LLM 자문을 추가할 때도 이 규격(generate_signal)은 그대로 유지됩니다.
"""

from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    @abstractmethod
    def generate_signal(self, symbol: str, df: pd.DataFrame) -> str:
        """
        df: date, open, high, low, close, volume 컬럼을 가진 과거 데이터 (최신이 마지막 행)
        반환값: "BUY" | "SELL" | "HOLD"
        """
        raise NotImplementedError

    def prepare(self, symbol: str, df: pd.DataFrame) -> None:
        """(선택) 백테스트가 전체 기간 데이터를 넘겨주는 사전 준비 훅. 지표를 한 번에 계산해 두는 전략이
        오버라이드합니다. 실전(live_trade)에서는 호출되지 않으므로 generate_signal만으로도 동작해야 합니다."""
        return None

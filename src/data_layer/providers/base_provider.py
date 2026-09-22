"""
데이터 제공자의 '규격'을 정의합니다.
키움증권 MCP가 실제로 연결될 때는, 이 규격에 맞춰
KiwoomMCPProvider 클래스 하나만 새로 만들면 됩니다.
strategy나 backtest 코드는 전혀 손댈 필요가 없습니다.
"""

from abc import ABC, abstractmethod
import pandas as pd


class BaseDataProvider(ABC):
    @abstractmethod
    def fetch_ohlcv(self, symbol: str, start_date: str | None = None) -> pd.DataFrame:
        """
        symbol의 일봉 데이터를 가져옵니다.
        start_date를 주면 그 이후 데이터만 (증분 수집용).
        반환 컬럼: date, open, high, low, close, volume
        """
        raise NotImplementedError

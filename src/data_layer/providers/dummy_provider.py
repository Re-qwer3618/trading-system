"""
아직 키움 API/MCP가 연결되기 전, 시스템 전체가 잘 도는지 검증하기 위한 가짜 데이터 제공자.
실제 증권사 데이터로 바꿀 때는 이 파일 대신 kiwoom_mcp_provider.py를 쓰도록
config만 바꾸면 됩니다.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from .base_provider import BaseDataProvider


class DummyProvider(BaseDataProvider):
    def fetch_ohlcv(self, symbol: str, start_date: str | None = None) -> pd.DataFrame:
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else datetime.now() - timedelta(days=120)
        end = datetime.now()
        dates = pd.date_range(start=start, end=end, freq="B")  # 영업일 기준

        rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
        price = 50000 + np.cumsum(rng.normal(0, 500, size=len(dates)))
        price = np.clip(price, 1000, None)

        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": rng.integers(100_000, 1_000_000, size=len(dates)),
        })
        return df

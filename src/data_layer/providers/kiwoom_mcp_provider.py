"""
[향후 구현 예정]
키움증권 MCP가 연결되면 이 클래스 안의 fetch_ohlcv()를 실제 MCP 호출로 채워넣으면 됩니다.
지금은 자리만 잡아둔 상태입니다 (config에서 provider: kiwoom_mcp 로 바꾸면 이 클래스가 쓰입니다).
"""

import pandas as pd
from .base_provider import BaseDataProvider


class KiwoomMCPProvider(BaseDataProvider):
    def __init__(self, config: dict):
        self.config = config

    def fetch_ohlcv(self, symbol: str, start_date: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "키움 MCP 연동이 아직 구현되지 않았습니다. "
            "MCP 서버가 준비되면 이 함수 안에서 MCP 클라이언트를 호출해 "
            "date, open, high, low, close, volume 컬럼을 가진 DataFrame을 반환하도록 채워주세요."
        )

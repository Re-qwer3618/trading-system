"""
[향후 구현 예정]
키움증권 MCP가 연결되면 이 클래스를 실제 MCP 호출로 채워넣으면 됩니다.
config에서 broker.provider: kiwoom_mcp 로 바꾸면 이 클래스가 쓰입니다.
실거래(live)로 전환하기 전 반드시 소액/모의계좌로 먼저 검증하세요.
"""

from .base_broker import BaseBroker


class KiwoomMCPBroker(BaseBroker):
    def __init__(self, config: dict):
        self.config = config
        raise NotImplementedError(
            "키움 MCP 연동이 아직 구현되지 않았습니다. "
            "MCP 서버가 준비되면 get_price/get_cash/get_total_deposit/get_position/place_order를 "
            "실제 MCP 호출로 채워주세요."
        )

    def get_total_deposit(self) -> float:
        raise NotImplementedError

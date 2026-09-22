"""
'주문을 넣는 곳'의 규격을 정의합니다.
키움증권 MCP가 준비되면 KiwoomMCPBroker 클래스 하나만 새로 만들면 되고,
strategy/risk 코드는 손댈 필요가 없습니다.
"""

from abc import ABC, abstractmethod


class BaseBroker(ABC):
    @abstractmethod
    def get_price(self, symbol: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_cash(self) -> float:
        """지금 당장 새 주문에 쓸 수 있는 현금 (미체결/미정산 주문 반영, 매매 중 출렁임)."""
        raise NotImplementedError

    @abstractmethod
    def get_total_deposit(self) -> float:
        """포지션 사이징의 고정 기준 총자본. get_cash()와 달리 미체결 주문 증거금 때문에
        출렁이지 않는, 세션 시작 시점에 한 번 읽어서 계속 같은 값으로 쓰는 용도."""
        raise NotImplementedError

    @abstractmethod
    def get_position(self, symbol: str) -> int:
        """현재 보유 수량"""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: int) -> dict:
        """side: 'BUY' 또는 'SELL'. 체결 결과를 dict로 반환."""
        raise NotImplementedError

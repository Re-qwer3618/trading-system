"""
'이 신호를 얼마나 살 것인가'를 결정합니다.
전략이 BUY를 내도, 리스크 관리자가 수량을 0으로 만들면 실제로는 매매되지 않습니다.
"""


class RiskManager:
    def __init__(self, max_position_pct: float, risk_limit_pct: float, starting_cash: float):
        """
        starting_cash: 종목당 매수 규모(max_position_pct%)를 계산하는 고정 기준 총자본.
        매매 중 바뀌는 '지금 남은 현금'이 아니라, 세션 시작 시점에 한 번 정해서 계속
        같은 값을 씁니다 — 그렇지 않으면 살수록 남은 현금이 줄어서 다음 매수 규모가
        점점 작아지는 의도치 않은 사이징이 됩니다 (실측 후 발견되어 수정됨).
        """
        self.max_position_pct = max_position_pct
        self.risk_limit_pct = risk_limit_pct
        self.starting_cash = starting_cash

    def calc_buy_quantity(self, cash: float, price: float) -> int:
        """목표 매수금액은 starting_cash의 max_position_pct%로 고정합니다. 다만 지금
        실제로 쓸 수 있는 현금(cash)을 넘어서 살 수는 없으니 그 한도 안에서 채웁니다."""
        if price <= 0:
            return 0
        target_budget = self.starting_cash * (self.max_position_pct / 100)
        budget = min(target_budget, cash)
        return int(budget // price)

    def stop_loss_price(self, entry_price: float) -> float:
        """1회 매매당 최대 손실 허용선."""
        return entry_price * (1 - self.risk_limit_pct / 100)

"""
실제 돈이 오가지 않는 모의투자 브로커.
데이터 저장소의 가격을 기준으로 가상의 체결을 만들고,
현금/보유수량은 로컬 JSON 파일에 저장해서 프로그램을 껐다 켜도 유지됩니다.
"""

import json
from pathlib import Path
from datetime import datetime

from .base_broker import BaseBroker
from data_layer.storage import MarketDataStore


class PaperBroker(BaseBroker):
    def __init__(self, data_store: MarketDataStore, state_path: str, starting_cash: float):
        self.data_store = data_store
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        if self.state_path.exists():
            with open(self.state_path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
            # 예전 상태 파일에는 없을 수 있음 — 없으면 지금 넘어온 값으로 채워서
            # get_total_deposit()이 항상 값을 반환하게 합니다.
            self.state.setdefault("starting_cash", starting_cash)
        else:
            self.state = {"cash": starting_cash, "starting_cash": starting_cash, "positions": {}, "history": []}
            self._save()

    def _save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def get_price(self, symbol: str) -> float:
        df = self.data_store.load(symbol)
        if df.empty:
            raise ValueError(f"{symbol}의 저장된 시세가 없습니다. 데이터 수집을 먼저 실행하세요.")
        return float(df.iloc[-1]["close"])

    def get_cash(self) -> float:
        return self.state["cash"]

    def get_total_deposit(self) -> float:
        return self.state["starting_cash"]

    def get_position(self, symbol: str) -> int:
        return self.state["positions"].get(symbol, 0)

    def place_order(self, symbol: str, side: str, quantity: int) -> dict:
        price = self.get_price(symbol)
        cost = price * quantity

        if side == "BUY":
            if cost > self.state["cash"]:
                return {"status": "REJECTED", "reason": "현금 부족"}
            self.state["cash"] -= cost
            self.state["positions"][symbol] = self.state["positions"].get(symbol, 0) + quantity
        elif side == "SELL":
            held = self.state["positions"].get(symbol, 0)
            if quantity > held:
                return {"status": "REJECTED", "reason": "보유 수량 부족"}
            self.state["cash"] += cost
            self.state["positions"][symbol] = held - quantity
        else:
            return {"status": "REJECTED", "reason": f"알 수 없는 side: {side}"}

        record = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol, "side": side, "quantity": quantity, "price": price,
        }
        self.state["history"].append(record)
        self._save()
        return {"status": "FILLED", **record}

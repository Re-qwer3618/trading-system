"""
kiwoom-client 라이브러리로 실제(모의) 주문을 넣는 브로커.

매수/매도, 예수금 조회는 키움 서버에서 직접 받아옵니다.
다만 '체결잔고'(kt00005) TR은 모의투자 서버에서 아예 제공되지 않아서
(RC9000: 모의투자에서는 해당업무가 제공되지 않습니다),
보유수량은 이 시스템이 주문을 넣을 때마다 로컬 파일에 직접 기록해서 관리합니다.
이 시스템을 통해서만 주문이 나간다는 전제이므로 정확도에 문제가 없습니다.
(실전투자로 전환하면 kt00005가 제공될 수 있으니, 그때는 실제 조회로 되돌리는 것도 고려하세요.)
"""

import json
from pathlib import Path

from kiwoom_client import KiwoomAPI, to_dataframe, round_to_tick
from kiwoom_client.base import KiwoomAPIError

from .base_broker import BaseBroker

# 확인 전 후보 필드명 (inspect_kiwoom_account.py 결과로 좁혀주세요)
_CASH_FIELD_CANDIDATES = ["entr", "dnca_tot_amt", "ord_alow_amt", "100"]


def _find_field(d: dict, candidates: list[str]):
    for c in candidates:
        if c in d:
            return d[c]
    raise KeyError(f"다음 후보 필드가 응답에 없습니다: {candidates}\n실제 키 목록: {list(d.keys())}")


class KiwoomRestBroker(BaseBroker):
    def __init__(self, config: dict, data_store):
        self.api = KiwoomAPI(
            app_key=config["_secrets"]["kiwoom_app_key"],
            app_secret=config["_secrets"]["kiwoom_app_secret"],
            is_mock=config.get("kiwoom", {}).get("is_mock", True),  # 실거래 전환은 반드시 충분한 검증 후.
        )
        self.data_store = data_store  # 시세 조회는 우리 저장소(수집된 데이터) 기준

        # 모의투자 서버가 체결잔고 조회를 지원하지 않아, 로컬에서 보유수량을 관리
        self._positions_path = Path(config["paths"]["data_dir"]) / "kiwoom_mock_positions.json"
        self._positions_path.parent.mkdir(parents=True, exist_ok=True)
        if self._positions_path.exists():
            with open(self._positions_path, "r", encoding="utf-8") as f:
                self._positions = json.load(f)
        else:
            self._positions = {}
            self._save_positions()

    def _save_positions(self):
        with open(self._positions_path, "w", encoding="utf-8") as f:
            json.dump(self._positions, f, ensure_ascii=False, indent=2)

    def get_price(self, symbol: str) -> float:
        df = self.data_store.load(symbol)
        if df.empty:
            raise ValueError(f"{symbol}의 저장된 시세가 없습니다. collect_data.py를 먼저 실행하세요.")
        return float(df.iloc[-1]["close"])

    def get_cash(self) -> float:
        raw = self.api.account.deposit_detail(qry_tp="2")
        data = raw if isinstance(raw, dict) else to_dataframe(raw).to_dict("records")[0]
        return float(_find_field(data, _CASH_FIELD_CANDIDATES))

    def get_position(self, symbol: str) -> int:
        return self._positions.get(symbol, 0)

    def place_order(self, symbol: str, side: str, quantity: int) -> dict:
        price = round_to_tick(self.get_price(symbol))
        try:
            if side == "BUY":
                result = self.api.order.buy_order(
                    dmst_stex_tp="01", stk_cd=symbol,
                    ord_qty=quantity, trde_tp="00", ord_uv=price,
                )
            elif side == "SELL":
                result = self.api.order.sell_order(
                    dmst_stex_tp="01", stk_cd=symbol,
                    ord_qty=quantity, trde_tp="00", ord_uv=price,
                )
            else:
                return {"status": "REJECTED", "reason": f"알 수 없는 side: {side}"}

            # 모의투자는 지정가 주문이 즉시 체결된다고 가정하고 로컬 보유수량을 갱신합니다.
            # (실제로는 미체결로 남을 수도 있으니, 나중에 unfilled_orders()로 검증하는 걸 추천)
            if side == "BUY":
                self._positions[symbol] = self._positions.get(symbol, 0) + quantity
            else:
                self._positions[symbol] = max(0, self._positions.get(symbol, 0) - quantity)
            self._save_positions()

            return {"status": "SUBMITTED", "response": result}
        except KiwoomAPIError as e:
            return {"status": "REJECTED", "reason": f"{e.code}: {e.message}"}

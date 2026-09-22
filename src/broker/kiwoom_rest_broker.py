"""
kiwoom-client 라이브러리로 실제(모의) 주문을 넣는 브로커.

매수/매도 호출부는 라이브러리 공식 문서에 명확히 나온 형태를 그대로 썼습니다.
다만 잔고/예수금 조회 응답의 정확한 필드명은 inspect_kiwoom_account.py로
먼저 확인해주세요 (kiwoom_provider.py와 같은 이유입니다).
"""

from kiwoom_client import KiwoomAPI, to_dataframe, round_to_tick
from kiwoom_client.base import KiwoomAPIError

from .base_broker import BaseBroker

# ord_alow_amt(주문가능금액)를 최우선으로 둡니다. entr(예수금)은 미체결/미정산
# 주문의 증거금이 얼마나 묶여 있는지를 반영하지 않는 총 예수금이라, 매매가 여러 건
# 쌓이면 entr는 그대로인데 실제 주문가능금액은 마이너스로 떨어질 수 있습니다
# (실측 확인됨: entr=10,000,000인데 ord_alow_amt=-8,042,765 — entr를 쓰면 매수
# 가능수량을 계속 양수로 잘못 계산해서 "모의투자 매수증거금이 부족합니다"로
# 거부되는 주문을 매 반복마다 다시 넣게 됩니다).
_CASH_FIELD_CANDIDATES = ["ord_alow_amt", "entr", "dnca_tot_amt", "100"]
# get_total_deposit()용. entr(예수금 총액)는 ord_alow_amt와 달리 미체결 주문
# 증거금 때문에 매매 중에 출렁이지 않아서, "고정된 총자본" 기준으로 적합합니다.
_TOTAL_DEPOSIT_FIELD_CANDIDATES = ["entr", "dnca_tot_amt", "100"]
_POSITION_QTY_FIELD_CANDIDATES = ["rmnd_qty", "hldn_qty", "trde_able_qty"]
_POSITION_SYMBOL_FIELD_CANDIDATES = ["stk_cd", "stck_shrn_iscd"]


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

    def get_price(self, symbol: str) -> float:
        df = self.data_store.load(symbol)
        if df.empty:
            raise ValueError(f"{symbol}의 저장된 시세가 없습니다. collect_data.py를 먼저 실행하세요.")
        return float(df.iloc[-1]["close"])

    def get_cash(self) -> float:
        raw = self.api.account.deposit_detail(qry_tp="2")
        data = raw if isinstance(raw, dict) else to_dataframe(raw).to_dict("records")[0]
        return float(_find_field(data, _CASH_FIELD_CANDIDATES))

    def get_total_deposit(self) -> float:
        raw = self.api.account.deposit_detail(qry_tp="2")
        data = raw if isinstance(raw, dict) else to_dataframe(raw).to_dict("records")[0]
        return float(_find_field(data, _TOTAL_DEPOSIT_FIELD_CANDIDATES))

    def get_position(self, symbol: str) -> int:
        # filled_position(kt00005)은 모의투자에서 지원되지 않아 account_evaluation(kt00018)으로 대체.
        # 보유종목 리스트는 'stk_acnt_evlt_prst' 안에 들어있음 (실제 응답으로 확인됨).
        raw = self.api.account.account_evaluation(qry_tp="1", dmst_stex_tp="01")
        holdings = raw.get("stk_acnt_evlt_prst", [])
        for item in holdings:
            sym_val = next((item[c] for c in _POSITION_SYMBOL_FIELD_CANDIDATES if c in item), None)
            if sym_val and symbol in str(sym_val):
                qty_val = next((item[c] for c in _POSITION_QTY_FIELD_CANDIDATES if c in item), None)
                if qty_val is None:
                    raise KeyError(f"수량 필드를 못 찾았습니다. 실제 키 목록: {list(item.keys())}")
                return int(qty_val)
        return 0

    def place_order(self, symbol: str, side: str, quantity: int) -> dict:
        # round_to_tick()은 정밀도를 지키려고 Decimal을 반환하는데, 그대로 요청 바디에
        # 넣으면 httpx의 기본 JSON 인코더가 못 읽어서 주문이 "Object of type Decimal
        # is not JSON serializable"로 매번 실패합니다 (실측 확인됨). 원화 주가는 항상
        # 정수 단위라 int로 바꿔도 정밀도 손실이 없습니다.
        price = int(round_to_tick(self.get_price(symbol)))
        # kt10000/kt10001 스펙 확인 결과(spec_show), ord_qty/ord_uv는 문자열 타입이어야
        # 하고(정수를 그대로 넣으면 "1517: 파라미터=ord_qty 원인=타입 불일치"로 거부됨,
        # 실측 확인됨), dmst_stex_tp는 "01"이 아니라 "KRX"/"NXT"/"SOR" 중 하나,
        # trde_tp는 "00"이 아니라 "0"(보통/지정가)이 유효한 값입니다.
        try:
            if side == "BUY":
                result = self.api.order.buy_order(
                    dmst_stex_tp="KRX", stk_cd=symbol,
                    ord_qty=str(quantity), trde_tp="0", ord_uv=str(price),
                )
            elif side == "SELL":
                result = self.api.order.sell_order(
                    dmst_stex_tp="KRX", stk_cd=symbol,
                    ord_qty=str(quantity), trde_tp="0", ord_uv=str(price),
                )
            else:
                return {"status": "REJECTED", "reason": f"알 수 없는 side: {side}"}
            return {"status": "SUBMITTED", "response": result}
        except KiwoomAPIError as e:
            return {"status": "REJECTED", "reason": f"{e.code}: {e.message}"}

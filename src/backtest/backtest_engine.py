"""
저장소에 쌓인 과거 데이터를 하루씩 따라가며 전략을 검증합니다.
실전 매매(main.py)와 완전히 같은 전략/리스크 코드를 그대로 재사용합니다.
(전략 코드를 백테스트용/실전용으로 따로 만들면 나중에 둘이 어긋나는
 가장 흔한 실수가 생깁니다. 그래서 여기서도 동일한 클래스를 씁니다.)

매수/매도에는 슬리피지·수수료·매도세를 반영하고, risk_manager의 손절선도
매 봉마다 실제로 체크합니다 (이전에는 stop_loss_price()가 정의만 되어 있고
백테스트에서 호출되지 않아, 전략이 스스로 SELL을 내기 전까지는 손실이
무한정 커질 수 있었습니다). 비용을 안 넣으면 백테스트 수익률이 실제보다
낙관적으로 나옵니다.
"""

import pandas as pd


class BacktestEngine:
    def __init__(
        self,
        data_store,
        strategy,
        risk_manager,
        starting_cash: float,
        fee_rate: float = 0.00015,
        tax_rate: float = 0.0018,
        slippage_rate: float = 0.001,
    ):
        """
        fee_rate: 매수/매도 각각에 붙는 위탁수수료율 (증권사·상품마다 다르므로 실제 값으로 맞추세요)
        tax_rate: 매도 시에만 붙는 증권거래세율 (현재 세율로 맞추세요)
        slippage_rate: 신호가 난 종가와 실제 체결가 사이의 가정 괴리율
        """
        self.data_store = data_store
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.starting_cash = starting_cash
        self.fee_rate = fee_rate
        self.tax_rate = tax_rate
        self.slippage_rate = slippage_rate

    def run(self, symbol: str, benchmark_code: str | None = None) -> dict:
        df = self.data_store.load(symbol)
        if df.empty:
            return {"symbol": symbol, "error": "저장된 데이터가 없습니다. 먼저 데이터를 수집하세요."}

        cash = self.starting_cash
        position = 0
        entry_price = 0.0
        trades = []

        for i in range(len(df)):
            window = df.iloc[: i + 1]
            price = float(window.iloc[-1]["close"])
            date = window.iloc[-1]["date"]

            # 손절선 체크가 전략 신호보다 우선합니다 — 실전에서도 손절은
            # "전략이 SELL을 낼 때까지 기다리지 않고" 즉시 나가는 게 정상 동작입니다.
            if position > 0 and price <= self.risk_manager.stop_loss_price(entry_price):
                cash += self._sell_proceeds(position, price)
                trades.append({"date": date, "side": "SELL", "price": price, "qty": position, "reason": "STOP_LOSS"})
                position = 0
                entry_price = 0.0
                continue

            signal = self.strategy.generate_signal(symbol, window)

            if signal == "BUY" and position == 0:
                qty = self.risk_manager.calc_buy_quantity(cash, price)
                if qty > 0:
                    cost = self._buy_cost(qty, price)
                    if cost <= cash:
                        cash -= cost
                        position = qty
                        entry_price = price
                        trades.append({"date": date, "side": "BUY", "price": price, "qty": qty, "reason": "SIGNAL"})

            elif signal == "SELL" and position > 0:
                cash += self._sell_proceeds(position, price)
                trades.append({"date": date, "side": "SELL", "price": price, "qty": position, "reason": "SIGNAL"})
                position = 0
                entry_price = 0.0

        last_price = float(df.iloc[-1]["close"])
        final_value = cash + position * last_price
        return_pct = round((final_value / self.starting_cash - 1) * 100, 2)

        result = {
            "symbol": symbol,
            "starting_cash": self.starting_cash,
            "final_value": round(final_value, 0),
            "return_pct": return_pct,
            "num_trades": len(trades),
            "trades": trades,
            "cost_assumptions": {
                "fee_rate": self.fee_rate,
                "tax_rate": self.tax_rate,
                "slippage_rate": self.slippage_rate,
            },
        }

        if benchmark_code:
            benchmark_return = self._benchmark_return(df, benchmark_code)
            if benchmark_return is not None:
                result["benchmark_code"] = benchmark_code
                result["benchmark_return_pct"] = benchmark_return
                result["alpha_pct"] = round(return_pct - benchmark_return, 2)

        return result

    def _buy_cost(self, qty: int, price: float) -> float:
        """매수 체결가(슬리피지 반영) 기준 총 지불액 (수수료 포함)."""
        exec_price = price * (1 + self.slippage_rate)
        return qty * exec_price * (1 + self.fee_rate)

    def _sell_proceeds(self, qty: int, price: float) -> float:
        """매도 체결가(슬리피지 반영) 기준 실수령액 (수수료+거래세 차감)."""
        exec_price = price * (1 - self.slippage_rate)
        return qty * exec_price * (1 - self.fee_rate - self.tax_rate)

    def _benchmark_return(self, df: pd.DataFrame, benchmark_code: str) -> float | None:
        """종목 백테스트와 같은 기간의 지수 등락률(%). 지수 데이터가 없으면 None
        (collect_index.py 미실행 등) — 이땐 그냥 alpha 없이 결과를 돌려줍니다."""
        if not hasattr(self.data_store, "load_index"):
            return None
        start_date, end_date = df.iloc[0]["date"], df.iloc[-1]["date"]
        bench = self.data_store.load_index(benchmark_code, start_date=start_date)
        if bench.empty:
            return None
        bench = bench[bench["date"] <= end_date]
        if bench.empty:
            return None
        first, last = float(bench.iloc[0]["close"]), float(bench.iloc[-1]["close"])
        if first <= 0:
            return None
        return round((last / first - 1) * 100, 2)

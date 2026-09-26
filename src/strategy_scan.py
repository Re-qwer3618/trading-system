"""
"전략에 따른 종목 관리": 저장된 일봉에 config의 전략(build_strategy)을 돌려서, 지금 어떤
종목이 BUY/SELL 신호 상태인지 훑어봅니다. 대시보드의 관심종목 화면에서 BUY 후보를
"전략 후보(source=strategy)"로 관심종목에 추가하는 데 씁니다.

live_trade.py가 하는 판단(전략.generate_signal)과 같은 함수를 쓰지만, 여기서는 실시간가 없이
마지막 저장된 일봉까지만 보므로 "어제 종가 기준 신호"입니다 — 후보를 고르는 용도이고,
실제 주문 판단은 live_trade.py가 실시간가로 다시 합니다.
"""

from datetime import datetime, timedelta

import pandas as pd

from data_layer.storage import MarketDataStore
from core.factory import build_strategy

_LOOKBACK_DAYS = 700  # 신호 판단에 충분한 최근 구간(달력일). 상장 이래 전체를 읽으면 종목당 1만 행이라 느림


def scan_signals(store: MarketDataStore, config: dict, symbols: list[str]) -> pd.DataFrame:
    """종목별 현재 전략 신호. 열: symbol, signal, last_date, last_close"""
    cutoff = (datetime.now() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    rows = []
    for symbol in symbols:
        df = store.load(symbol, start_date=cutoff)
        if df.empty:
            continue
        try:
            signal = build_strategy(config, symbol).generate_signal(symbol, df)
        except Exception:
            continue  # 종목 하나의 데이터 문제로 전체 스캔을 멈추지 않음
        rows.append({"symbol": symbol, "signal": signal,
                     "last_date": df.iloc[-1]["date"], "last_close": float(df.iloc[-1]["close"])})
    return pd.DataFrame(rows, columns=["symbol", "signal", "last_date", "last_close"])

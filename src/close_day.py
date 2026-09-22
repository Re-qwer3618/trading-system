"""
장 마감 후, 실시간 수집기(realtime/stream_collector.py)가 그날 쌓아둔 체결 틱
(realtime_ticks)을 종목별 1분봉으로 묶어 기존 저장소(intraday_ohlcv)에 합칩니다.
그날 공식 일봉(ohlcv)이 아직 없는 종목은 같은 틱으로 일봉도 근사해서 채웁니다.

이미 collect_data.py/collect_all.py로 그날 공식 일봉을 받아둔 종목은 건드리지
않습니다 (공식 API 데이터가 실시간 근사치보다 항상 우선). 실시간 수집기는 켜뒀지만
공식 수집을 아직 못 돌린 날에 "그래도 데이터가 하나도 없는 것보다는 낫게" 채워
넣는 안전망입니다 — 나중에 collect_data.py를 돌리면 이 근사 일봉은 공식 값으로
덮어써집니다 (같은 (symbol, date) 키에 upsert하므로).

호가(realtime_orderbook)는 캔들이 아니라 그대로 두고 씁니다 — 분/일봉으로 뭉개면
의미가 없는 스냅샷 데이터라서요.

실행:
    python src/close_day.py                        # 오늘, 실시간 틱이 있던 전 종목
    python src/close_day.py 005930 000660           # 특정 종목만
    python src/close_day.py --date 2026-09-22        # 다른 날짜 지정 (그날 놓쳤을 때)
"""

import sys
import logging
import pandas as pd
from datetime import datetime

from config_loader import load_config
from data_layer.storage import MarketDataStore

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# 실시간 틱에서 뽑아낸 1분봉 태그. collect_all.py --minute이 쓰는 "{scope}m"
# (기본 "5m")과 겹치지 않도록 고정값으로 둡니다 — 둘 다 intraday_ohlcv에
# 나란히 쌓이고, interval 컬럼으로 구분됩니다.
MINUTE_INTERVAL = "1m_rt"


def _ticks_to_minute_bars(ticks: pd.DataFrame, date: str) -> pd.DataFrame:
    """realtime_ticks(ts=HHMMSS 또는 그 이상 자리수 문자열, 날짜 없음)를 그날 날짜와
    합쳐 1분봉으로 묶습니다."""
    ts_str = ticks["ts"].astype(str).str.zfill(6).str.slice(-6)
    timestamps = pd.to_datetime(date + " " + ts_str, format="%Y-%m-%d %H%M%S", errors="coerce")

    df = pd.DataFrame({
        "timestamp": timestamps,
        "price": ticks["price"].astype(float),
        "volume": ticks["volume"].astype(int),
    }).dropna(subset=["timestamp"]).sort_values("timestamp")

    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    priced = df.set_index("timestamp")["price"]
    bars = priced.resample("1min").ohlc()
    bars["volume"] = df.set_index("timestamp")["volume"].resample("1min").sum()
    bars = bars.dropna(subset=["open"]).reset_index()  # 틱이 없던 분은 버림 (가짜 평봉 방지)
    bars["timestamp"] = bars["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return bars[["timestamp", "open", "high", "low", "close", "volume"]]


def _ticks_to_daily_bar(ticks: pd.DataFrame, date: str) -> pd.DataFrame:
    prices = ticks["price"].astype(float)
    return pd.DataFrame([{
        "date": date,
        "open": float(prices.iloc[0]),
        "high": float(prices.max()),
        "low": float(prices.min()),
        "close": float(prices.iloc[-1]),
        "volume": int(ticks["volume"].astype(int).sum()),
    }])


def close_day(symbols: list[str] | None = None, date: str | None = None):
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    date = date or datetime.now().strftime("%Y-%m-%d")

    targets = symbols or store.realtime_tick_symbols(date)
    if not targets:
        log.warning(f"{date}에 수집된 실시간 체결 데이터가 없습니다. "
                    f"그날 'run.bat realtime <종목>'을 켜뒀는지 확인하세요.")
        return

    for symbol in targets:
        ticks = store.ticks_for_date(symbol, date)
        if ticks.empty:
            log.warning(f"[{symbol}] {date} 실시간 체결 데이터가 없어 건너뜁니다.")
            continue

        minute_bars = _ticks_to_minute_bars(ticks, date)
        store.upsert_intraday(symbol, MINUTE_INTERVAL, minute_bars)
        log.info(f"[{symbol}] 실시간 틱 {len(ticks)}건 -> 1분봉 {len(minute_bars)}건, "
                 f"intraday_ohlcv(interval={MINUTE_INTERVAL})에 합쳤습니다.")

        if store.last_saved_date(symbol) == date:
            log.info(f"[{symbol}] {date} 일봉이 이미 있습니다 (공식 데이터 우선, 실시간 근사치로 덮어쓰지 않음).")
        else:
            daily_bar = _ticks_to_daily_bar(ticks, date)
            store.upsert(symbol, daily_bar)
            log.info(f"[{symbol}] {date} 공식 일봉이 아직 없어 실시간 틱으로 근사 채움 "
                     f"(나중에 collect_data.py를 돌리면 공식 값으로 덮어써집니다).")


if __name__ == "__main__":
    args = sys.argv[1:]
    date_arg = None
    if "--date" in args:
        idx = args.index("--date")
        date_arg = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    close_day(args or None, date=date_arg)

"""
실시간 체결가(0B) + 호가잔량(0D) 수집기.

kiwoom_client 라이브러리(REST 호출에도 쓰는 바로 그 라이브러리)의 KiwoomWebSocket을
직접 씁니다. 원래는 이미 검증된 공식 CLI `kwcli`를 서브프로세스 두 개(체결용/호가용)로
띄우는 방식으로 만들었었는데, 실측해보니 **같은 계정으로 실시간 웹소켓 로그인을
두 번 하면 키움 서버가 먼저 연결을 끊어버렸습니다** (계정당 실시간 세션은 하나만
유지되는 것으로 보임 — "received 1000 (OK) Bye"로 조용히 끊김, 순서를 늦춰서 열어도
동일). kiwoom_client의 KiwoomWebSocket.subscribe()는 한 세션 안에서 여러 타입(0B+0D)을
한 번에 등록할 수 있어서 이 문제를 근본적으로 피합니다 — 모의계좌로 직접 검증
완료(005930/000660 두 종목에 0B+0D 동시 구독, 10초간 체결 173건/호가 100건 정상 수신,
연결 끊김 없음).

KiwoomWebSocket이 콜백에 넘기는 `values`는 kwcli의 `--named` 출력과 달리 한글
필드명이 아니라 원본 FID(숫자 문자열) 키입니다. 아래 FID 상수는 공식 스펙
(kiwoom.realtime.schemas, 0B/0D)에서 그대로 가져왔습니다.

실행:
    python src/realtime/stream_collector.py 005930
    python src/realtime/stream_collector.py 005930 000660   # 여러 종목
    python src/realtime/stream_collector.py --watchlist     # 지금까지 수집된 전 종목(store.symbols())
    python src/realtime/stream_collector.py --no-orderbook 005930   # 체결만, 호가 제외
    python src/realtime/stream_collector.py --real 005930   # 모의 대신 실전 데이터 구독

Ctrl+C로 종료합니다.
"""

import sys
import asyncio
import logging

from config_loader import load_config
from data_layer.storage import MarketDataStore
from kiwoom_client import KiwoomAPI

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# 0B(주식체결) FID. kiwoom.realtime.schemas로 확인.
_FID_TRADE_PRICE = "10"    # 현재가
_FID_TRADE_VOLUME = "15"   # 거래량
_FID_TRADE_TS = "20"       # 체결시간

# 0D(주식호가잔량) FID. 레벨 i(1~10): 매도호가=40+i, 매도호가수량=60+i,
# 매수호가=50+i, 매수호가수량=70+i.
_FID_ORDERBOOK_TS = "21"          # 호가시간
_FID_TOTAL_ASK_QTY = "121"        # 매도호가총잔량
_FID_TOTAL_BID_QTY = "125"        # 매수호가총잔량
_ORDERBOOK_LEVELS = 10             # 국내 HTS 호가창 표준 10단


def _to_float(value) -> float | None:
    """키움 실시간 값(부호/콤마 포함 문자열일 수 있음)을 안전하게 float로."""
    if value is None or value == "":
        return None
    try:
        return abs(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


class RealtimeCollector:
    """웹소켓 콜백에서 받은 REAL 이벤트를 파싱해서 DB에 적재합니다."""

    def __init__(self, store: MarketDataStore):
        self.store = store
        self._trade_saved = 0
        self._orderbook_saved = 0
        self._warned_trade = False
        self._warned_orderbook = False

    def on_trade(self, item: dict):
        values = item.get("values", {})
        symbol = item.get("item")
        price = _to_float(values.get(_FID_TRADE_PRICE))
        volume = _to_int(values.get(_FID_TRADE_VOLUME)) or 0
        ts = values.get(_FID_TRADE_TS)

        if not symbol or price is None:
            if not self._warned_trade:
                log.warning(f"체결 이벤트 구조를 해석하지 못했습니다. values 키: {list(values.keys())}\n"
                            f"-> stream_collector.py의 _FID_TRADE_* 상수를 실제 FID로 맞춰주세요.")
                self._warned_trade = True
            return

        self.store.append_realtime_tick(symbol, str(ts) if ts else "", price, volume)
        self._trade_saved += 1
        if self._trade_saved % 20 == 0:
            log.info(f"실시간 체결 {self._trade_saved}건 적재됨 (최근: {symbol} {price:,.0f}원)")

    def on_orderbook(self, item: dict):
        values = item.get("values", {})
        symbol = item.get("item")
        ts = values.get(_FID_ORDERBOOK_TS)

        asks, bids = [], []
        for i in range(1, _ORDERBOOK_LEVELS + 1):
            ask_price = _to_float(values.get(str(40 + i)))
            if ask_price is not None:
                asks.append([ask_price, _to_int(values.get(str(60 + i))) or 0])
            bid_price = _to_float(values.get(str(50 + i)))
            if bid_price is not None:
                bids.append([bid_price, _to_int(values.get(str(70 + i))) or 0])

        if not symbol or (not asks and not bids):
            if not self._warned_orderbook:
                log.warning(f"호가 이벤트 구조를 해석하지 못했습니다. values 키: {list(values.keys())}\n"
                            f"-> stream_collector.py의 호가 FID 상수를 실제 값으로 맞춰주세요.")
                self._warned_orderbook = True
            return

        total_ask = _to_int(values.get(_FID_TOTAL_ASK_QTY))
        total_bid = _to_int(values.get(_FID_TOTAL_BID_QTY))
        self.store.append_realtime_orderbook(symbol, str(ts) if ts else "", asks, bids, total_ask, total_bid)
        self._orderbook_saved += 1
        if self._orderbook_saved % 20 == 0:
            best_ask = asks[0][0] if asks else None
            best_bid = bids[0][0] if bids else None
            log.info(f"실시간 호가 {self._orderbook_saved}건 적재됨 (최근: {symbol} 매도1호가 {best_ask} / 매수1호가 {best_bid})")


async def _run(symbols: list[str], mode: str, with_orderbook: bool):
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    api = KiwoomAPI(
        app_key=config["_secrets"]["kiwoom_app_key"],
        app_secret=config["_secrets"]["kiwoom_app_secret"],
        is_mock=(mode != "real"),
    )
    api.login()
    ws = api.create_websocket()

    collector = RealtimeCollector(store)
    ws.on("0B", collector.on_trade)
    if with_orderbook:
        ws.on("0D", collector.on_orderbook)

    await ws.connect()
    types = ["0B", "0D"] if with_orderbook else ["0B"]
    await ws.subscribe(types=types, items=symbols)
    log.info(f"[{len(symbols)}종목] 실시간 구독 시작 (mode={mode}, 호가={'포함' if with_orderbook else '제외'}, Ctrl+C로 종료)")

    try:
        await ws.listen()
    except asyncio.CancelledError:
        pass
    finally:
        await ws.disconnect()


def collect(symbols: list[str], mode: str = "demo", with_orderbook: bool = True):
    try:
        asyncio.run(_run(symbols, mode, with_orderbook))
    except KeyboardInterrupt:
        log.info("종료 신호를 받아 연결을 정리합니다...")


if __name__ == "__main__":
    args = sys.argv[1:]

    mode_arg = "demo"
    if "--real" in args:
        mode_arg = "real"
        args = [a for a in args if a != "--real"]

    with_orderbook_arg = "--no-orderbook" not in args
    args = [a for a in args if a != "--no-orderbook"]

    use_watchlist = "--watchlist" in args
    args = [a for a in args if a != "--watchlist"]

    if use_watchlist:
        # store.symbols()(수집해둔 전체 종목)이 아니라 watchlist(실제 감시 대상)를 씁니다 —
        # 전체 종목을 다 받아도 실시간 구독 개수가 그만큼 늘어나면 안 되기 때문입니다.
        _store = MarketDataStore(load_config()["data"]["db_path"])
        _store.seed_watchlist_if_empty(_store.symbols())  # 최초 실행이면 지금까지 수집분으로 시드
        symbols_arg = _store.get_watchlist()
        if not symbols_arg:
            log.error("관심종목이 비어 있습니다 (collect_data.py로 종목을 먼저 수집하세요).")
            sys.exit(1)
    else:
        symbols_arg = args or ["005930"]

    collect(symbols_arg, mode=mode_arg, with_orderbook=with_orderbook_arg)

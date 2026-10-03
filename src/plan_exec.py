"""
stock_analysis가 장 마감 후 만든 '다음 거래일 매매 계획'(plans_<기준일>.json)을 판단 없이 실행합니다.

역할 분담: 무엇을 언제 살지(전략·조건·매물대)는 stock_analysis가 정하고(rules/strategies.yaml, core/plans.py),
이 모듈은 계획에 적힌 숫자대로 주문만 넣고 결과를 plan_trades에 남깁니다. 복기는 다시 stock_analysis가
plan_trades를 읽기 전용으로 읽어서 합니다(review_trades.py). 계획 파일 형식은 stock_analysis/core/plans.py 맨 위 설명.

live_trade.py가 config의 plan_follow.enabled가 true일 때만 이 모듈을 씁니다 (기본 false). 켜져 있으면
계획 종목과 진행 중인 계획 매매 종목은 기존 전략(_check_symbol) 대신 여기서 처리합니다.

장중 가격은 키움 REST 시세(ka10001)를 직접 조회합니다 — 실시간 수집기를 안 켜도 되고, 계획의 '시가 조건'에
필요한 오늘 시가도 같이 옵니다. 주문 가격은 지금 현재가 기준(broker.place_order(price=...))이라 마지막 저장
일봉 종가(=장중에는 전일 종가)로 주문이 나가서 체결되지 않는 기존 한계를 피합니다.

비중(종목당 %), 장중 손절(%), 동시 보유 한도는 기존과 똑같이 대시보드 리스크 설정을 따릅니다. 계획의 손절가·
목표가·보유기간은 그와 별도로 함께 적용됩니다(먼저 닿는 쪽으로 매도).

계획 유효성: asof(기준일) < 오늘 인 가장 최근 파일만 쓰고, 그 asof가 DB의 '오늘 이전 마지막 거래일'과 같아야
합니다. 장 마감 후 계획 생성이 실패했으면 그 전날 계획이 남아 있어도 쓰지 않습니다(묵은 계획으로 매매 방지).
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np

from config_loader import PROJECT_ROOT

log = logging.getLogger(__name__)

PLAN_SOURCE = "plan"            # watchlist.source — 계획 때문에 들어온 종목 (다음 계획에 없으면 자동으로 빠짐)
PLAN_STRATEGY_TAG = "plan_follow"


def plans_dir(config: dict) -> Path:
    override = os.getenv("PLANS_DIR", "").strip()
    raw = override or config.get("plan_follow", {}).get("plans_dir", "../stock_analysis/data/plans")
    p = Path(raw)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def load_current_plans(config: dict, store, today: str) -> tuple[dict | None, str]:
    """(오늘 실행할 계획 문서, 설명). 쓸 계획이 없으면 (None, 이유)."""
    d = plans_dir(config)
    files = sorted(d.glob("plans_*.json"))
    cands = [f for f in files if f.stem.removeprefix("plans_") < today]
    if not cands:
        return None, f"계획 파일 없음 ({d})"
    path = cands[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"계획 파일 읽기 실패 {path.name}: {exc}"
    if doc.get("schema") != 1:
        return None, f"알 수 없는 계획 형식 {path.name}: schema={doc.get('schema')}"
    last_trading = store.last_index_date_before(today)
    if last_trading and doc["asof"] != last_trading:
        return None, (f"계획 기준일 {doc['asof']}이 직전 거래일 {last_trading}과 달라 쓰지 않음 "
                      f"(장 마감 후 계획 생성이 안 됐거나 DB 갱신이 안 됨)")
    max_age = int(config.get("plan_follow", {}).get("max_plan_age_days", 4))
    age = (datetime.fromisoformat(today) - datetime.fromisoformat(doc["asof"])).days
    if age > max_age:
        return None, f"계획이 {age}일 묵음 (한도 {max_age}일)"
    return doc, f"{path.name} ({doc['strategy']} {doc['version']}, {len(doc['plans'])}건)"


def sync_watchlist(store, doc: dict | None) -> tuple[list[str], list[str]]:
    """계획 종목을 관심종목에 넣고(source=plan), 지난 계획으로 들어왔다가 이번 계획에 없고 보유도 아닌 종목은 뺍니다.
    직접 추가한(source가 plan이 아닌) 관심종목은 건드리지 않습니다. (추가, 제거) 반환."""
    wanted = {p["symbol"]: p for p in (doc or {}).get("plans", [])}
    active = set(store.active_plan_symbols())
    detail = store.get_watchlist_detail()
    current = set(detail["symbol"])
    added, removed = [], []
    for sym, p in wanted.items():
        if sym not in current:
            store.add_to_watchlist(sym, source=PLAN_SOURCE, strategy=PLAN_STRATEGY_TAG, note=p["plan_id"])
            added.append(sym)
    for row in detail.itertuples():
        if row.source == PLAN_SOURCE and row.symbol not in wanted and row.symbol not in active:
            store.remove_from_watchlist(row.symbol)
            removed.append(row.symbol)
    return added, removed


def _hhmm(now: datetime) -> str:
    return now.strftime("%H:%M")


class PlanExecutor:
    def __init__(self, config: dict, store, broker, quote_fn):
        self.config = config
        self.store = store
        self.broker = broker
        self.quote_fn = quote_fn
        self.slippage_pct = float(config.get("plan_follow", {}).get("order_slippage_pct", 0.5))
        self.doc: dict | None = None
        self.plans: dict[str, dict] = {}
        self._loaded_for: str | None = None
        self._noted: set[str] = set()   # 같은 사유 로그를 하루 한 번만 남기려고

    # ---- 계획 읽기 ----------------------------------------------------------
    def refresh(self, today: str) -> list[str]:
        """하루 한 번(날짜가 바뀌면) 계획을 다시 읽고 관심종목을 맞춥니다. 이 실행기가 맡는 종목 목록 반환."""
        if self._loaded_for != today:
            self.doc, why = load_current_plans(self.config, self.store, today)
            self.plans = {p["symbol"]: p for p in (self.doc or {}).get("plans", [])}
            added, removed = sync_watchlist(self.store, self.doc)
            log.info(f"[계획] {why} — 관심종목 추가 {added}, 제외 {removed}")
            self._loaded_for = today
            self._noted.clear()
        return sorted(set(self.plans) | set(self.store.active_plan_symbols()))

    def handles(self, symbol: str) -> bool:
        return symbol in self.plans or self.store.active_plan_trade(symbol) is not None

    def _note_once(self, key: str, symbol: str, signal: str, detail: str) -> None:
        if key in self._noted:
            return
        self._noted.add(key)
        self.store.log_decision(symbol, signal, "NEUTRAL", "NONE", detail)
        log.info(f"[{symbol}] {signal} {detail}")

    # ---- 종목 하나 점검 -----------------------------------------------------
    def check(self, symbol: str, risk, entry_prices: dict[str, float], max_concurrent: int,
              now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        trade = self.store.active_plan_trade(symbol)
        position = self.broker.get_position(symbol)
        q = self.quote_fn(symbol)
        price = q.get("price")
        if not price:
            return

        if trade is not None:
            self._manage(trade, symbol, position, price, risk, entry_prices, now, today)
            return

        plan = self.plans.get(symbol)
        if plan is None or position > 0:
            return
        pid = plan["plan_id"]
        if self.store.plan_trade_exists(pid, today):
            return  # 오늘 이미 시도함(재시작 후 등)
        e = plan["entry"]
        if _hhmm(now) < e["after"]:
            return
        if q.get("open") is None or q["open"] < e["min_open"]:
            self._note_once(pid + ":open", symbol, "PLAN_SKIP",
                            f"{pid} 시가 {q.get('open') or 0:,.0f} < 조건 {e['min_open']:,} — 오늘 매수 안 함 (돌파 다음날 확인 실패)")
            return
        if not (e["min_price"] < price <= e["max_price"]):
            self._note_once(pid + ":price", symbol, "PLAN_WAIT",
                            f"{pid} 현재가 {price:,.0f}가 매수 구간({e['min_price']:,} 초과 ~ {e['max_price']:,} 이하) 밖 — 장 마감까지 계속 확인")
            return
        if len(entry_prices) >= max_concurrent:
            self._note_once(pid + ":full", symbol, "PLAN_SKIP", f"{pid} 동시 보유 한도 {len(entry_prices)}/{max_concurrent}")
            return
        qty = risk.calc_buy_quantity(self.broker.get_cash(), price)
        if qty <= 0:
            self._note_once(pid + ":qty", symbol, "PLAN_SKIP", f"{pid} 매수 수량 0 (현금 부족 또는 비중 한도)")
            return

        result = self.broker.place_order(symbol, "BUY", qty, price=price, **self._slip())
        ok = result.get("status") in ("SUBMITTED", "FILLED")
        detail = f"{pid} 계획 매수 {qty}주 @현재가 {price:,.0f} ({plan.get('basis', '')}): {result}"
        if ok:
            tid = self.store.add_plan_trade(plan, self.doc["strategy"], self.doc["version"], qty, price,
                                           now.isoformat(timespec="seconds"))
            if result.get("status") == "FILLED":  # 페이퍼 브로커는 즉시 체결
                self.store.update_plan_trade(tid, status="open", entry_date=today)
            entry_prices[symbol] = price
        else:
            tid = self.store.add_plan_trade(plan, self.doc["strategy"], self.doc["version"], qty, price,
                                           now.isoformat(timespec="seconds"))
            self.store.update_plan_trade(tid, status="rejected", note=str(result)[:500])
        self.store.log_decision(symbol, "PLAN_BUY", "NEUTRAL", "BUY" if ok else "BUY_FAILED", detail)
        log.info(f"[{symbol}] {detail}")

    def _slip(self) -> dict:
        # PaperBroker는 slippage 인자를 모릅니다 — 받는 브로커(키움)에만 넘깁니다.
        return {"slippage_pct": self.slippage_pct} if "slippage_pct" in inspect.signature(self.broker.place_order).parameters else {}

    def _manage(self, trade: dict, symbol: str, position: int, price: float, risk,
                entry_prices: dict[str, float], now: datetime, today: str) -> None:
        tid, st, plan = trade["id"], trade["status"], trade["plan"]
        if st == "submitted":
            if position > 0:
                self.store.update_plan_trade(tid, status="open", entry_date=trade["buy_submitted_at"][:10])
                entry_prices[symbol] = trade["entry_price"]
                log.info(f"[{symbol}] 계획 매수 체결 확인 ({position}주)")
            elif trade["buy_submitted_at"][:10] < today:
                self.store.update_plan_trade(tid, status="unfilled", note="매수 주문 후 다음 거래일까지 보유가 잡히지 않음")
                entry_prices.pop(symbol, None)
            return
        if st == "closing":
            if position == 0:
                self.store.update_plan_trade(tid, status="closed", exit_date=trade["exit_submitted_at"][:10])
                entry_prices.pop(symbol, None)
                log.info(f"[{symbol}] 계획 매도 체결 확인 — {trade['exit_reason']}")
                return
            if trade["exit_submitted_at"][:10] >= today:
                return  # 오늘 낸 매도 주문 체결 대기
            self.store.update_plan_trade(tid, status="open", note=(trade.get("note") or "") + " 매도 미체결 → 보유 유지;")
        # open
        if position == 0:
            self.store.update_plan_trade(tid, status="closed", exit_date=today, exit_price=price,
                                         exit_reason="계좌에 보유 없음 (수동 정리 등)")
            entry_prices.pop(symbol, None)
            return
        entry_prices[symbol] = trade["entry_price"]
        reason = self._exit_reason(trade, plan, price, risk, now, today)
        if not reason:
            return
        result = self.broker.place_order(symbol, "SELL", position, price=price, **self._slip())
        ok = result.get("status") in ("SUBMITTED", "FILLED")
        detail = f"{trade['plan_id']} {reason} {position}주 @현재가 {price:,.0f}: {result}"
        if ok:
            fields = dict(status="closing", exit_submitted_at=now.isoformat(timespec="seconds"),
                          exit_price=price, exit_reason=reason)
            if result.get("status") == "FILLED":
                fields.update(status="closed", exit_date=today)
                entry_prices.pop(symbol, None)
            self.store.update_plan_trade(tid, **fields)
        self.store.log_decision(symbol, "PLAN_EXIT", "NEUTRAL", "SELL" if ok else "SELL_FAILED", detail)
        log.info(f"[{symbol}] {detail}")

    @staticmethod
    def _exit_reason(trade: dict, plan: dict, price: float, risk, now: datetime, today: str) -> str:
        entry = trade["entry_price"]
        if price >= plan["target"]:
            return f"목표가 {plan['target']:,} 도달"
        risk_stop = risk.stop_loss_price(entry)
        if price <= risk_stop:
            return f"리스크 손절 (진입 {entry:,.0f} → 손절선 {risk_stop:,.0f}, 대시보드 {risk.risk_limit_pct}%)"
        hhmm = _hhmm(now)
        if hhmm >= plan["entry"]["after"] and price < plan["stop"]:
            return f"계획 손절 (장 후반 {price:,.0f} < 손절가 {plan['stop']:,})"
        held = int(np.busday_count(trade["entry_date"] or today, today)) + 1
        if held >= int(plan["max_hold_days"]) and hhmm >= "15:15":
            return f"보유기간 만료 ({held}거래일째)"
        return ""

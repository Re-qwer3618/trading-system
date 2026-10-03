"""
매매 계획(stock_analysis가 만든 plans_<기준일>.json) 상태 확인 + 관심종목 동기화.

    run.bat plans              # 다음 거래일에 쓸 계획이 무엇인지, 유효한지, 진행 중인 계획 매매
    run.bat plans --sync       # 계획 종목을 관심종목에 넣고(source=plan) 지난 계획 종목은 뺌
                               # (live_trade가 plan_follow.enabled일 때 시작하면서 알아서 하는 일과 같음 —
                               #  실시간 수집기를 관심종목 기준으로 켜기 전에 미리 맞춰두고 싶을 때)

'다음 거래일' 기준으로 보려고 오늘 날짜 대신 내일 날짜로 계획을 찾습니다(장 마감 후 확인용).
"""

import sys
from datetime import date, timedelta

import pandas as pd

from config_loader import load_config
from data_layer.storage import MarketDataStore
from plan_exec import load_current_plans, plans_dir, sync_watchlist

sys.stdout.reconfigure(encoding="utf-8")


def main():
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    target = (date.today() + timedelta(days=1)).isoformat()
    doc, why = load_current_plans(config, store, target)
    print(f"계획 폴더: {plans_dir(config)}")
    print(f"계획 실행(plan_follow.enabled): {config.get('plan_follow', {}).get('enabled', False)}")
    print(f"{target} 이후 첫 거래일용 계획: {why}")
    if doc:
        for p in doc["plans"]:
            e = p["entry"]
            print(f"  {p['rank']:>2}. {p['name']}({p['symbol']}) 시가≥{e['min_open']:,}, {e['after']} 이후 "
                  f"{e['min_price']:,}~{e['max_price']:,} / 손절 {p['stop']:,} 목표 {p['target']:,}")
    if "--sync" in sys.argv:
        added, removed = sync_watchlist(store, doc)
        print(f"관심종목 동기화: 추가 {added}, 제외 {removed}")

    trades = store.plan_trades(50)
    print(f"\n계획 매매 기록 (최근 {len(trades)}건)")
    if not trades.empty:
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(trades[["id", "plan_id", "name", "status", "qty", "entry_date", "entry_price",
                          "exit_date", "exit_price", "exit_reason"]].to_string(index=False))


if __name__ == "__main__":
    main()

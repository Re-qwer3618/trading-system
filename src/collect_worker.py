"""
대시보드("데이터 수집" 페이지)가 만든 수집 작업(collection_jobs)을 실제로 실행하는 백그라운드 프로세스.

대시보드(Streamlit) 안에서 직접 수집을 돌리면 화면을 새로고침하거나 브라우저를 닫는 순간
끊기고, 종목 수천 개 x 페이지 수십 개는 몇 시간이 걸립니다. 그래서 대시보드는 작업을
DB에 등록하고 이 워커를 별도 프로세스로 띄우기만 하며, 진행 상황은 DB(collection_jobs)를 통해
주고받습니다 — 브라우저를 닫아도 계속 돌고, 다시 열면 진행률이 그대로 보입니다.

실행 (보통은 대시보드가 알아서 실행하지만, 직접 돌릴 수도 있습니다):
    python src/collect_worker.py --job-id 12

중지: 대시보드의 [중지] 버튼이 작업 상태를 'cancelling'으로 바꾸면, 워커가 종목 사이(그리고
페이지를 받는 도중에도) 확인하고 멈춥니다. 그때까지 받은 데이터는 저장돼 있습니다.
"""

import os
import sys
import logging
from datetime import datetime

from config_loader import load_config
from data_layer.storage import MarketDataStore
from data_layer.collector import CollectOptions, collect_symbols
from data_layer.catalog import refresh_catalog
from core.factory import build_data_provider

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # 페이지마다 찍히는 HTTP 로그가 진행 로그를 묻어버림
log = logging.getLogger(__name__)

_CATALOG_FLUSH_EVERY = 20  # 이 종목 수마다 카탈로그를 중간 갱신


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_job(job_id: int) -> int:
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    job = store.get_job(job_id)
    if job is None:
        log.error(f"작업 {job_id}번을 찾을 수 없습니다.")
        return 1
    if job["status"] != "pending":
        log.error(f"작업 {job_id}번은 '{job['status']}' 상태라 시작하지 않습니다 (pending만 시작 가능).")
        return 1

    spec = job["spec"]
    symbols: list[str] = spec["symbols"]
    opts = CollectOptions.from_dict(spec.get("options", {}))
    opts.sleep_sec = config.get("data", {}).get("rate_limit_sleep_sec", opts.sleep_sec)

    store.update_job(job_id, status="running", started_at=_now(), heartbeat_at=_now(), pid=os.getpid(),
                     message="시작")
    log.info(f"작업 {job_id} 시작: {job['title']} ({len(symbols)}종목, 데이터 {opts.datasets})")

    def should_stop() -> bool:
        current = store.get_job(job_id)
        return current is not None and current["status"] == "cancelling"

    counters = {"done": 0, "ok": 0, "failed": 0}
    unrefreshed: list[str] = []  # 카탈로그에 아직 반영 안 된 종목들

    def flush_catalog():
        # 몇 시간~며칠 걸리는 작업이라, 끝날 때 한 번만 갱신하면 그동안 대시보드 현황이 낡고
        # 중간에 강제 종료되면 영영 반영이 안 됩니다. 일정 종목마다 해당 종목만 다시 집계합니다.
        if unrefreshed:
            refresh_catalog(store, list(unrefreshed))
            unrefreshed.clear()

    def on_symbol(index, symbol, status, detail):
        counters["done"] = index
        if status == "ok":
            counters["ok"] += 1
        elif status == "failed":
            counters["failed"] += 1
        unrefreshed.append(symbol)
        if len(unrefreshed) >= _CATALOG_FLUSH_EVERY:
            flush_catalog()
        store.log_job_item(job_id, symbol, status, detail)
        nxt = symbols[index] if index < len(symbols) else ""
        store.update_job(job_id, done=counters["done"], ok=counters["ok"], failed=counters["failed"],
                         current_symbol=nxt, heartbeat_at=_now(), message=f"[{symbol}] {detail}"[:300])

    try:
        provider = build_data_provider(config)
        # 페이지를 받을 때마다 heartbeat를 남기고 중지 요청을 확인 — 분봉 과거 확장은
        # 종목 하나에 2분 이상 걸릴 수 있어서 종목 단위로만 확인하면 중지가 너무 늦습니다.
        provider.should_stop = should_stop
        provider.on_page = lambda: store.touch_job(job_id)

        stats = collect_symbols(provider, store, symbols, opts, on_symbol=on_symbol, should_stop=should_stop)

        store.update_job(job_id, message="카탈로그 갱신 중...", heartbeat_at=_now())
        flush_catalog()

        status = "cancelled" if stats["cancelled"] else "done"
        store.update_job(job_id, status=status, finished_at=_now(), current_symbol="",
                         message=f"{'중지됨' if stats['cancelled'] else '완료'}: 성공 {stats['ok']}, 실패 {stats['failed']}")
        log.info(f"작업 {job_id} {status}: 성공 {stats['ok']}, 실패 {stats['failed']}")
        return 0
    except Exception as exc:  # 워커 자체가 죽는 경우도 화면에서 원인을 볼 수 있게 남김
        log.exception("수집 작업 실패")
        try:
            flush_catalog()  # 실패해도 그때까지 받은 데이터는 카탈로그에 반영
        except Exception:
            pass
        store.update_job(job_id, status="failed", finished_at=_now(), message=f"{type(exc).__name__}: {exc}"[:300])
        return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--job-id" not in args:
        print("사용법: python src/collect_worker.py --job-id N")
        sys.exit(2)
    sys.exit(run_job(int(args[args.index("--job-id") + 1])))

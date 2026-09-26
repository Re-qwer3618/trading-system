"""
대시보드가 수집 작업을 만들고(create_and_launch) 중지(request_cancel)하는 창구.

수집 자체는 별도 프로세스(collect_worker.py)가 하고, 진행 상황은 DB(collection_jobs)로
주고받습니다 — 대시보드를 새로고침하거나 브라우저를 닫아도 작업은 계속 돌아갑니다.
키움 API 호출 제한이 계정 단위라 수집 작업은 동시에 하나만 허용합니다.
"""

import os
import sys
import subprocess
from pathlib import Path

from data_layer.storage import MarketDataStore
from data_layer.collector import CollectOptions, DATASET_LABELS, prioritize

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class JobAlreadyRunning(RuntimeError):
    pass


def _launch_worker(job_id: int) -> int:
    """워커를 대시보드와 독립된 프로세스로 띄웁니다 (대시보드를 꺼도 계속 실행). pid를 돌려줍니다."""
    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = open(log_dir / f"collect_job_{job_id}.log", "ab")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [sys.executable, str(_PROJECT_ROOT / "src" / "collect_worker.py"), "--job-id", str(job_id)],
        cwd=str(_PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=flags, close_fds=True,
        env={**os.environ, "PYTHONUTF8": "1"},  # 로그 파일을 cp949가 아니라 UTF-8로 (한글이 안 깨지게)
    )
    return proc.pid


def describe(opts: CollectOptions, count: int) -> str:
    what = "+".join(DATASET_LABELS[d] for d in opts.datasets)
    extra = f", 분봉 {opts.minute_target_days}일 확장" if opts.minute_target_days else ""
    return f"{count}종목 수집 ({what}{extra})"


def create_and_launch(store: MarketDataStore, symbols: list[str], opts: CollectOptions,
                      title: str | None = None) -> int:
    """작업을 등록하고 워커를 띄웁니다. 이미 실행 중인 작업이 있으면 JobAlreadyRunning."""
    if store.active_job() is not None:
        raise JobAlreadyRunning("이미 실행 중인 수집 작업이 있습니다. 끝나거나 중지된 뒤에 시작하세요.")
    ordered = prioritize(store, list(dict.fromkeys(symbols)))  # 중복 제거 + 관심종목/시총 순
    job_id = store.create_job(
        title or describe(opts, len(ordered)),
        {"symbols": ordered, "options": opts.to_dict()},
        total=len(ordered),
    )
    try:
        pid = _launch_worker(job_id)
    except Exception as exc:
        store.update_job(job_id, status="failed", message=f"워커 실행 실패: {exc}")
        raise
    store.update_job(job_id, pid=pid)
    return job_id


def request_cancel(store: MarketDataStore, job_id: int) -> None:
    """실행 중인 작업에 중지를 요청합니다. 워커가 다음 페이지/종목 경계에서 멈춥니다."""
    job = store.get_job(job_id)
    if job and job["status"] in ("pending", "running"):
        store.update_job(job_id, status="cancelling", message="중지 요청됨 — 현재 페이지까지 마치고 멈춥니다")


def kill_worker(store: MarketDataStore, job_id: int) -> bool:
    """워커가 응답하지 않을 때의 강제 종료. 이미 저장된 데이터는 그대로 남습니다."""
    job = store.get_job(job_id)
    if not job or not job.get("pid"):
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(job["pid"]), "/T", "/F"], capture_output=True)
    else:
        os.kill(int(job["pid"]), 9)
    store.update_job(job_id, status="aborted", message="강제 종료됨")
    return True

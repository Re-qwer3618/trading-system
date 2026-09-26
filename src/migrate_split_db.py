"""
단일 파일 DB(data/market_data.db)를 도메인별 파일(data/db/*.db)로 나눕니다.
왜 나누는지/어떤 파일이 있는지는 data_layer/storage.py 상단 주석 참고.

안전장치:
- 원본은 절대 지우지 않습니다. 검증이 끝나면 market_data.legacy.db로 이름만 바꿔둡니다
  (몇 주 써보고 문제없으면 직접 삭제해서 공간을 되찾으세요).
- 새 파일은 임시 폴더(data/_split_tmp)에서 먼저 만들고, 행 수가 원본과 정확히 일치할 때만
  data/db/로 옮깁니다. 중간에 실패하면 원본은 그대로이고 임시 폴더만 남습니다(지워도 됨).
- 쓰는 프로세스가 있으면 복사본이 어긋나므로, 실행 전에 꼭 멈추세요:
    · 대시보드 "데이터 수집"의 진행 중인 수집 작업 (중지 버튼)
    · run.bat realtime (실시간 수집기), run.bat live-trade
  (대시보드 화면 자체는 켜둬도 됩니다.) 진행 중인 수집 작업이 있으면 시작하지 않습니다.

실행:
    python src/migrate_split_db.py            # 실제 이관
    python src/migrate_split_db.py --check    # 무엇을 어디로 옮길지와 행 수만 보여줌 (아무것도 안 바꿈)
"""

import sys
import shutil
import time
import sqlite3
from pathlib import Path

from config_loader import load_config
from data_layer.storage import MarketDataStore, DOMAINS, _TABLE_DOMAIN


def _old_conn(legacy: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{legacy.as_posix()}?mode=ro", uri=True, timeout=60)
    return conn


def _count(conn, ref: str, where: str = "") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {ref} {where}").fetchone()[0]


def migrate(db_path: str, check_only: bool = False) -> int:
    legacy = Path(db_path)
    final_dir = legacy.parent / "db"
    tmp_root = legacy.parent / "_split_tmp"
    tmp_dir = tmp_root / "db"

    if (final_dir / "daily.db").exists():
        print(f"이미 분리되어 있습니다: {final_dir}")
        return 0
    if not legacy.exists():
        print(f"원본 DB가 없습니다: {legacy}")
        return 1

    # 원본(호환 모드) 스키마를 최신으로 맞춰서 모든 테이블이 존재하게 함 + 작업 중인지 확인
    old_store = MarketDataStore(str(legacy))
    job = old_store.active_job()
    if job is not None:
        print(f"진행 중인 수집 작업 #{job['id']}이(가) 있습니다. 대시보드에서 중지한 뒤 다시 실행하세요.")
        return 1

    conn = _old_conn(legacy)
    plan = {}
    for table, domain in _TABLE_DOMAIN.items():
        if table == "intraday_ohlcv":
            plan["minute.intraday_ohlcv"] = _count(conn, "intraday_ohlcv", "WHERE interval NOT LIKE '%t'")
            plan["tick.intraday_ohlcv"] = _count(conn, "intraday_ohlcv", "WHERE interval LIKE '%t'")
        else:
            plan[f"{domain}.{table}"] = _count(conn, table)
    conn.close()
    print("이관 계획 (도메인.테이블: 행 수)")
    for k, v in plan.items():
        print(f"  {k:36s} {v:>13,}")
    if check_only:
        return 0

    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "daily.db").touch()  # 이 파일이 있어야 MarketDataStore가 분리 모드로 스키마를 만듭니다
    new_store = MarketDataStore(str(tmp_root / "market_data.db"))
    assert new_store.split

    started = time.time()
    conn = new_store._connect()
    try:
        conn.execute(f"ATTACH DATABASE 'file:{legacy.as_posix()}?mode=ro' AS old")
        for table, domain in _TABLE_DOMAIN.items():
            t0 = time.time()
            if table == "intraday_ohlcv":
                cols = ", ".join(r[1] for r in conn.execute("PRAGMA minute.table_info(intraday_ohlcv)"))
                conn.execute(f"INSERT INTO minute.intraday_ohlcv ({cols}) SELECT {cols} FROM old.intraday_ohlcv "
                             f"WHERE interval NOT LIKE '%t'")
                conn.commit()
                conn.execute(f"INSERT INTO tick.intraday_ohlcv ({cols}) SELECT {cols} FROM old.intraday_ohlcv "
                             f"WHERE interval LIKE '%t'")
                conn.commit()
            else:
                cols = ", ".join(r[1] for r in conn.execute(f"PRAGMA {domain}.table_info({table})"))
                conn.execute(f"INSERT INTO {domain}.{table} ({cols}) SELECT {cols} FROM old.{table}")
                conn.commit()
            print(f"  복사 {domain}.{table} ({time.time() - t0:.0f}초)", flush=True)

        # 검증: 새 파일의 행 수가 계획(원본 행 수)과 정확히 같아야 합니다.
        bad = []
        for ref, expected in plan.items():
            actual = _count(conn, ref)
            if actual != expected:
                bad.append((ref, expected, actual))
        conn.execute("DETACH DATABASE old")
    finally:
        conn.close()  # 파일 핸들을 확실히 놓아야 아래에서 폴더를 옮길 수 있습니다 (Windows)
    del new_store

    if bad:
        print("행 수가 일치하지 않아 중단합니다 (원본은 그대로, 임시 폴더는 지워도 됩니다):")
        for ref, exp, act in bad:
            print(f"  {ref}: 원본 {exp:,} / 복사본 {act:,}")
        return 1

    # 파일 교체: 원본 이름 변경(다른 프로세스가 쓰고 있으면 여기서 실패 -> 아무것도 안 바뀜) 후 새 폴더 이동
    legacy_renamed = legacy.with_name("market_data.legacy.db")
    try:
        legacy.rename(legacy_renamed)
    except OSError as exc:
        print(f"원본 파일 이름을 바꾸지 못했습니다(다른 프로그램이 DB를 쓰는 중일 수 있음): {exc}")
        return 1
    for suffix in ("-wal", "-shm"):
        side = Path(str(legacy) + suffix)
        if side.exists():
            side.rename(Path(str(legacy_renamed) + suffix))
    try:
        tmp_dir.rename(final_dir)
    except OSError as exc:
        legacy_renamed.rename(legacy)
        print(f"새 폴더를 옮기지 못해 원본을 되돌렸습니다: {exc}")
        return 1
    shutil.rmtree(tmp_root, ignore_errors=True)

    print(f"\n이관 완료 ({time.time() - started:.0f}초). 새 DB: {final_dir}")
    for d in DOMAINS:
        f = final_dir / f"{d}.db"
        print(f"  {f.name:14s} {f.stat().st_size / 1024 ** 3:6.2f} GB")
    print(f"원본은 {legacy_renamed.name}로 보관했습니다 (문제없으면 삭제해서 공간 확보).")
    return 0


if __name__ == "__main__":
    cfg = load_config()
    sys.exit(migrate(cfg["data"]["db_path"], check_only="--check" in sys.argv))

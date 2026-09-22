"""
브로커 종류(paper/kiwoom_rest)와 무관하게 '언제 무엇을 얼마에 샀는지'를
한 곳에 기록합니다. 대시보드가 이 기록을 읽어서 보여줍니다.
"""

import sqlite3
from pathlib import Path
from datetime import datetime


class TradeLog:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER,
                    price REAL,
                    status TEXT,
                    detail TEXT
                )
            """)

    def record(self, symbol: str, side: str, quantity: int, price: float, status: str, detail: str = ""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO trades (timestamp, symbol, side, quantity, price, status, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), symbol, side, quantity, price, status, detail),
            )

    def recent(self, limit: int = 50):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

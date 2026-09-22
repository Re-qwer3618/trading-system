"""
kiwoom_provider.py가 정확한 컬럼명을 쓰고 있는지 확인하기 전에,
실제 API 응답이 어떤 컬럼명을 쓰는지 눈으로 먼저 확인하는 스크립트입니다.

실행:
    python src/inspect_kiwoom_chart.py 005930
"""

import sys
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

from kiwoom_client import KiwoomAPI, to_dataframe

symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"

api = KiwoomAPI(
    app_key=os.getenv("KIWOOM_APP_KEY"),
    app_secret=os.getenv("KIWOOM_APP_SECRET"),
    is_mock=True,
)

raw = api.chart.stock_daily_chart(
    stk_cd=symbol,
    base_dt=datetime.now().strftime("%Y%m%d"),
    upd_stkpc_tp="0",  # 수정주가구분: 0=비수정주가(원주가). 키움/저장소 기준 통일용.
)
print("=== 원본 응답 (일부) ===")
print(raw)

df = to_dataframe(raw)
print("\n=== DataFrame 컬럼 목록 ===")
print(list(df.columns))
print("\n=== 최근 3행 ===")
print(df.head(3))

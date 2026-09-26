"""
실행:
    python src/inspect_kiwoom_account.py
"""

import os
from config_loader import load_env_files  # 프로젝트 .env + 중앙 .env
load_env_files()

from kiwoom_client import KiwoomAPI, to_dataframe

api = KiwoomAPI(
    app_key=os.getenv("KIWOOM_APP_KEY"),
    app_secret=os.getenv("KIWOOM_APP_SECRET"),
    is_mock=True,
)

print("=== 예수금 상세 (deposit_detail) 원본 ===")
# qry_tp: 조회구분. "2"=일반조회로 우선 시도 (확실하지 않으면 "3"=추정조회로도 시도해보세요)
deposit = api.account.deposit_detail(qry_tp="2")
print(deposit)

print("\n=== 계좌평가잔고 (account_evaluation) 원본 ===")
# filled_position(kt00005)은 모의투자 미지원으로 확인됨. 대신 이 TR로 시도.
# qry_tp 의미가 deposit_detail과 다를 수 있어 후보를 순서대로 시도.
for candidate in ["1", "2", "0"]:
    try:
        position = api.account.account_evaluation(qry_tp=candidate, dmst_stex_tp="01")
        print(f"[qry_tp={candidate}] 성공")
        print(position)
        df = to_dataframe(position)
        print("\n컬럼 목록:", list(df.columns))
        break
    except Exception as e:
        print(f"[qry_tp={candidate}] 실패: {e}")

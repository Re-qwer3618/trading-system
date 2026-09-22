"""
kiwoom_rest_provider.py의 fetch_universe/fetch_minute/fetch_tick이 쓰는
메서드 이름(_STKINFO_NAMESPACE_CANDIDATES, _UNIVERSE_METHOD_CANDIDATES,
_MINUTE_METHOD_CANDIDATES, _TICK_METHOD_CANDIDATES)이 실제 kiwoom-client
라이브러리와 맞는지 확인하기 전에, api 객체에 실제로 뭐가 달려있는지
먼저 눈으로 확인하는 스크립트입니다. (inspect_kiwoom_chart.py와 같은 패턴)

이미 fetch_universe()를 실행했는데 AttributeError가 났다면, 에러 메시지에
"실제 사용 가능한 것들" 목록이 같이 나옵니다 — 이 스크립트는 그걸 좀 더
자세히 훑어보고 싶을 때 씁니다.

실행:
    python src/inspect_kiwoom_universe.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

from kiwoom_client import KiwoomAPI, to_dataframe

api = KiwoomAPI(
    app_key=os.getenv("KIWOOM_APP_KEY"),
    app_secret=os.getenv("KIWOOM_APP_SECRET"),
    is_mock=True,
)

print("=== api 최상위 네임스페이스 ===")
top_level = [n for n in dir(api) if not n.startswith("_")]
print(top_level)

for ns_name in top_level:
    ns = getattr(api, ns_name, None)
    if ns is None or callable(ns):
        continue
    methods = [n for n in dir(ns) if not n.startswith("_") and callable(getattr(ns, n, None))]
    if methods:
        print(f"\n=== api.{ns_name} 안의 메서드들 ===")
        print(methods)

print("\n종목정보 리스트(ka10099)로 추정되는 네임스페이스를 찾았다면,")
print("아래처럼 직접 호출해서 응답 컬럼명도 확인해보세요:")
print('  raw = api.<네임스페이스>.<메서드>(mrkt_tp="0")')
print("  df = to_dataframe(raw)")
print("  print(list(df.columns)); print(df.head(3))")

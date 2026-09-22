"""
키움증권 REST API와 통신하는 가장 아래 계층입니다.
- 토큰 발급/자동 갱신
- TR(api-id) 호출을 위한 공통 POST 헬퍼

data_layer/providers/kiwoom_rest_provider.py 와
broker/kiwoom_rest_broker.py 가 이 클래스를 공유해서 씁니다.

주의: 모의투자(mockapi.kiwoom.com)와 실전(api.kiwoom.com)은
      URL만 다르고 요청/응답 구조는 동일합니다. is_mock 값만 바꾸면 됩니다.
"""

import time
import logging
import requests

log = logging.getLogger(__name__)


class KiwoomRestClient:
    def __init__(self, app_key: str, app_secret: str, is_mock: bool = True):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = "https://mockapi.kiwoom.com" if is_mock else "https://api.kiwoom.com"
        self._token = None
        self._token_expires_at = 0

    def _issue_token(self):
        """
        POST /oauth2/token 으로 접근토큰을 발급받습니다.
        [확인 필요] grant_type 등 정확한 body 필드명은 공식 가이드
        (https://openapi.kiwoom.com/m/guide/apiguide) 의 '접근토큰 발급 au10001' 항목에서
        한 번 대조해주세요. 여기서는 문서에 공통적으로 쓰이는 관례를 따랐습니다.
        """
        url = f"{self.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        headers = {"Content-Type": "application/json;charset=UTF-8"}

        resp = requests.post(url, json=body, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # [확인 필요] 응답의 토큰 필드명이 access_token/token 중 무엇인지 실제 응답으로 확인해주세요.
        self._token = data.get("token") or data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in - 60  # 60초 여유

        if not self._token:
            log.error(f"토큰 응답에서 토큰 값을 찾지 못했습니다. 응답 원문: {data}")
            raise RuntimeError("토큰 발급 실패 — 응답 형식을 확인해주세요 (위 로그 참고).")

        log.info("키움 REST API 토큰 발급 완료.")

    def get_token(self) -> str:
        if not self._token or time.time() >= self._token_expires_at:
            self._issue_token()
        return self._token

    def post_tr(self, path: str, api_id: str, body: dict, cont_yn: str = "N", next_key: str = "") -> dict:
        """
        TR(api-id) 하나를 호출하는 공통 함수.
        path 예: '/api/dostk/ordr' (주문), '/api/dostk/chart' (차트, [확인 필요])
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.get_token()}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": api_id,
        }
        resp = requests.post(url, json=body, headers=headers, timeout=10)

        if resp.status_code != 200:
            log.error(f"[{api_id}] 요청 실패 ({resp.status_code}): {resp.text}")
        resp.raise_for_status()
        return resp.json()

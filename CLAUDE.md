# trading-system

키움증권 REST API 기반 국내주식(코스피/코스닥) 자동매매 시스템. Python + SQLite + Streamlit.
자세한 내용(폴더 구조, 전체 기능 목록, 세팅 절차, 명령어 목록)은 **README.md가 원본**입니다.
이 파일은 README에 없거나 README만 봐서는 놓치기 쉬운 것만 적습니다 — 여기부터 읽고 필요하면 README로.

## 실행 환경 (매번 틀리기 쉬운 부분)

- **파이썬은 무조건 `run.bat`을 통해 실행하세요.** `run.bat <command> args` 형태이고, 안에서
  `TRADING_PYTHON` → 프로젝트 `.venv` → conda `agent-py313` 순으로 인터프리터를 스스로 찾습니다.
  PATH의 `python`은 이 PC에서 Windows 스토어 스텁이라 직접 부르면 실패합니다.
  직접 스크립트를 돌려야 하면 conda 환경 python을 명시하세요:
  `E:\dev\envs\miniconda3\envs\agent-py313\python.exe`(회사는 `D:\dev\envs\...`).
- 명령 목록은 `run.bat` 맨 위 주석 참고. 자주 쓰는 것: `collect-all`, `catalog`, `screen`, `research`,
  `backtest`, `live-trade`, `dashboard`/`app`.
- Windows PowerShell 5.1로 `.ps1`을 실행할 땐 한글이 든 파일은 **UTF-8 BOM으로 저장**해야
  파싱이 안 깨집니다. 오래 도는 python 출력을 `Select-Object -First N`으로 자르면 프로세스가
  일찍 죽습니다 — 파일로 리다이렉트하거나 background로 돌리세요.

## 데이터 = 신뢰의 원천, 안전 규칙

- **DB(`data/db/*.db` 또는 이관 전 `data/market_data.db`), `.env`, `logs/`는 절대 git에 올리지
  않습니다.** 커밋 전 `git diff --cached --name-only`로 확인하는 걸 습관화하세요.
- **시장 데이터(daily/minute/tick/orderbook)를 쓰는 코드는 수집 계층뿐입니다**
  (README "세 영역의 분리" 참고). 분석/전략검증/백테스트 코드를 새로 짤 때는
  `MarketDataStore(db_path, readonly=True)`로 열어서 실수로 못 바꾸게 하세요
  (`research_strategy.py`, `screen_candidates.py`가 이 패턴입니다).
- **수집 작업은 동시에 하나만 돕니다** (`collection_jobs` 테이블, `store.active_job()`으로 확인).
  대시보드 밖에서 별도 수집 스크립트를 새로 만들 땐 이 잠금을 거치세요.
- **실시간 웹소켓 세션은 계정당 하나뿐**입니다. `stream_collector.py`를 이미 어딘가에서 돌리고
  있으면 또 켜지 마세요(서버가 먼저 연결을 끊습니다). 장중에만 의미가 있습니다.
- 분봉 API가 정상 응답인데 빈 레코드만 주는 종목(거래정지/관리종목)은 `data_catalog.exhausted`
  표식으로 재시도를 막아둔 것입니다 — "왜 이 종목은 분봉이 없지"라고 다시 조사하기 전에
  `store.is_exhausted(symbol, "1m")`부터 확인하세요.

## 매매 로직 안전 규칙

- `config/base.yaml`의 `kiwoom.is_mock`은 기본 `true`(모의투자)입니다. **`false`로 바꾸는 건
  사용자가 명시적으로 요청했을 때만** 하고, 그 전에 충분한 기간의 백테스트+모의투자 검증을
  거쳤는지 사용자에게 먼저 확인하세요.
- `broker/`, `live_trade.py`, `main.py`의 주문 관련 코드를 고칠 땐 특히 신중하게: 실제 돈이
  걸린 코드입니다. 리스크 파라미터(`risk.*`)는 대시보드(`pages/3_리스크_설정.py`)에서
  코드 수정 없이 조절되므로, 값 자체를 코드에 하드코딩하지 마세요.
- 전략 검증(`research/`, `screen_candidates.py`)이 찾은 규칙은 **과거 데이터 기준 약한 통계적
  우위**일 뿐입니다. "검증됨"이라고 곧장 실전 강도를 올리거나 자동매매에 바로 연결하지 말고,
  관심종목 추가 정도로만 쓰세요(지금 `watchlist-candidates` 스킬이 하는 정확한 범위).
  생존편향(상장폐지 종목 제외)이 있다는 것도 사용자에게 매번 상기시키세요.

## 자주 하는 실수 (이번 개발 중 실제로 겪은 것)

- 종목/시각 필드가 통째로 빈 문자열인 API 응답을 그대로 저장하면 `date='nan'` 같은 쓰레기 행이
  생깁니다 — 새 provider 코드를 짤 때 `_clean_bar_rows` 패턴을 재사용하세요.
- `DATA_DIR`/`LOG_DIR`처럼 `.env`의 상대경로는 실행 위치가 아니라 **프로젝트 폴더 기준**으로
  고정돼 있습니다(`config_loader.py`). 다른 폴더에서 실행해도 엉뚱한 곳에 데이터가 안 생깁니다 —
  이 동작을 다시 "고치려" 하지 마세요, 의도된 것입니다.
- 관심종목(`watchlist`)과 전체 수집 종목(`store.symbols()`/`universe`)은 다른 개념입니다.
  실시간 매매/구독 대상은 항상 전자입니다.

## 참고

- README.md: 전체 기능, 폴더 구조, 세팅 절차, 명령어 목록.
- `E:\dev\CLAUDE.md`: 이 프로젝트가 속한 워크스페이스 공통 규칙(키 관리, 다른 프로젝트와의 관계).

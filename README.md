# trading-system

집(E드라이브)/회사(D드라이브) 어디서든 코드 수정 없이 돌아가도록 만든
확장형 자동매매 시스템입니다. 데이터/주문 모두 **키움증권 REST API 모의투자**를
기본으로 사용합니다.

## 진행 현황 (Status)

- ✅ **1단계 — 기본 시스템 구축**: 데이터/브로커/전략/리스크/LLM 슬롯을 인터페이스로 분리한 구조 완성
- ✅ **2단계 — 백테스팅 데이터 연결**: 키움 REST 실데이터로 수집~백테스트~모의매매 전체 파이프라인 검증 완료
- ✅ **대시보드(1차, 단일페이지)**: `src/dashboard.py` — 시세차트/계좌현황/판단이력을 한 화면에서 확인
- ✅ **대시보드(2차, 로그인+멀티페이지)**: `app.py` + `pages/` — 로그인 화면, 전략별 종목 아코디언, 캔들차트, 계좌현황 페이지 분리
- ✅ **실제 비밀번호 인증**: `.env`의 `DASHBOARD_PASSWORD`와 정확히 일치해야 로그인 통과 (5회 실패 시 60초 잠금)
- ✅ **포트포워딩 없는 원격 접속**: Tailscale 사설망을 통해 집/회사 컴퓨터가 각자 대시보드를 띄우고 서로/원격에서 접속
- ✅ **더블클릭 실행**: 아나콘다 프롬프트 없이 바탕화면 아이콘 하나로 대시보드 실행 (`start_dashboard.bat` + `make_shortcut.vbs`)
- ✅ **키움증권 MCP 연동**: `kiwoom-spec-mcp`(API 명세 검색), `kiwoom-exec-mcp`(조회 실행) — 대화 중에 API를 찾거나 계좌를 바로 조회 가능 (`D:\dev\mcp` 참고)
- ✅ **3단계 — 전체 종목/분봉·틱 수집**: `collect_universe.py`(전 종목 리스트) + `collect_all.py`(장 마감 후 전체 종목 일봉/분봉/틱 일괄 갱신)
- ✅ **3단계 — 실시간 데이터 수집**: `realtime/stream_collector.py`가 키움 웹소켓 체결가(0B)를 `realtime_ticks` 테이블에 적재
- ✅ **3단계 — 분석 에이전트 1차(규칙기반)**: `agents/history_analyst.py`(과거 차트: 추세/변동성/박스권/거래량), `agents/realtime_analyst.py`(실시간: 단기 모멘텀/거래량 급증)
- ✅ **3단계 — 전략 구성 1차**: `config/base.yaml`의 `strategy.assignments`로 종목별로 다른 전략 배정 가능 (지금은 `ma_cross`만 등록되어 있어 실질적으로는 파라미터만 다르게 줄 수 있음)
- ✅ **4단계 — 백테스트 데이터/엔진 신뢰도 보강**:
  - 일봉/분봉/틱 수집이 cont-yn/next-key로 연속조회하도록 바뀌어, 한 페이지 분량이 아니라 실제 장기 데이터를 받습니다
  - 수정주가(`upd_stkpc_tp=1`) 적용 — 액면분할/무상증자 종목의 권리락 가짜 급등락을 제거
  - 시장 벤치마크(코스피/코스닥) 지수 일봉 수집 (`collect_index.py`) + 백테스트 결과의 알파(초과수익) 계산
  - 백테스트 엔진에 수수료·매도세·슬리피지 반영, 손절선(`risk_manager.stop_loss_price`) 실제 체크 (이전엔 계산만 하고 호출은 안 했음)
  - 위 변경 전에 모아둔 데이터는 비수정주가·단일페이지이므로, `--full`로 한 번 재수집이 필요합니다 (아래 참고)
- ✅ **5단계 — 실시간 호가(호가창) 수집 + 장마감 병합**:
  - `realtime/stream_collector.py`가 체결(0B)뿐 아니라 호가잔량(0D, 매도/매수 10단)도 동시에 구독해 `realtime_orderbook` 테이블에 적재 (`--no-orderbook`으로 끌 수 있음, `--watchlist`로 수집된 전 종목을 한 번에 구독 가능)
  - `close_day.py` — 장 마감 후 그날 쌓인 실시간 체결 틱을 종목별 1분봉으로 묶어 `intraday_ohlcv`에 합치고, 그날 공식 일봉이 아직 없으면 `ohlcv`에도 근사치로 채움 (공식 데이터가 나중에 오면 그걸로 덮어써짐)
  - `live_trade.py` — 관심종목(기본: 지금까지 수집된 전 종목)을 장중 내내 반복 확인하며 실시간가로 전략 신호를 판단하고, 신호가 나오면 main.py와 동일한 브로커/리스크 코드로 자동 주문. 알려진 한계는 코드 상단 docstring 참고 (실제 체결가는 아직 broker.get_price()의 마지막 저장 종가 기준)
- ✅ **6단계 — 실거래 배관 버그 수정 + 리스크 관리 보강** (실제로 켜서 매매해보며 발견):
  - `KiwoomRestBroker.place_order()`가 매번 실패하던 버그 3개 수정 — `round_to_tick()`의 Decimal을 그대로 JSON에 넣어 죽던 것, `ord_qty`/`ord_uv`를 문자열로 안 보내던 것, `dmst_stex_tp="01"`(잘못된 값, "KRX"여야 함)
  - `get_cash()`가 `entr`(총 예수금, 안 바뀜)를 보고 있어서 실제 주문가능금액(`ord_alow_amt`)이 마이너스인데도 계속 매수를 시도하던 버그 수정
  - `live_trade.py`에 신호가 "바뀐" 시점에만 주문을 시도하도록 해서, 거부된 주문을 매 interval마다 재시도하던 걸 막음
  - `live_trade.py`에 손절매(risk.stop_loss_price) 로직 추가 — 원래 backtest_engine.py에만 있고 라이브에는 없어서, 전략이 SELL 신호를 낼 때까지 손실 방어가 전혀 없었음
  - 매수 비중을 "그때그때 남은 현금"이 아니라 세션 시작 시점의 고정 총자본(`broker.get_total_deposit()`) 기준으로 계산하도록 변경 (전엔 살수록 다음 매수 규모가 점점 작아졌음)
  - `risk.max_concurrent_positions`로 동시 보유 종목 수 제한 추가 (기본 5, 종목당 비중 10%와 곱하면 최대 총 노출 50%) — 여러 종목이 한꺼번에 신호를 낼 때 자금이 쏠리지 않게
- ✅ **7단계 — 대시보드에서 리스크 설정 조절**:
  - 새 페이지 `pages/3_리스크_설정.py` — 종목당 매수 비중/손절선/동시 보유 종목 수를 코드나 config 수정 없이 화면에서 슬라이더로 바로 조절
  - DB에 `settings` 테이블 추가 (`MarketDataStore.get_risk_settings`/`set_risk_settings`). `config/base.yaml`은 "최초 기본값"이고, 대시보드에서 바꾸면 그 값이 우선함
  - `live_trade.py`가 매 확인 주기(기본 60초)마다 이 설정을 다시 읽으므로, 대시보드에서 값을 바꿔도 **프로세스 재시작 없이** 다음 주기부터 바로 반영됨 (`main.py`도 실행될 때마다 반영). 백테스트(`run_backtest.py`)는 재현성을 위해 이 설정을 쓰지 않고 항상 config 값 그대로 사용
- ✅ **8단계 — 종목 기본정보 수집 + 관심종목(watchlist)을 전체 수집 종목과 분리**:
  - `collect_all.py --info`로 종목 기본정보(PER/PBR/EPS/BPS/ROE, 시가총액 등, ka10001) 수집 — `stock_basic_info` 테이블, 차트 페이지에 카드로 표시
  - **중요한 구조 변경**: `store.symbols()`(수집해둔 전체 종목)와 `store.get_watchlist()`(실시간 매매/구독이 실제로 지켜보는 종목)를 분리했습니다. 예전엔 둘이 같아서(수집한 것 = 감시하는 것) 문제가 없었는데, `collect_all.py`로 전체 종목(수천 개)을 받으면 `live_trade.py`/`stream_collector.py --watchlist`가 그대로 수천 종목을 실시간 구독/감시하려다 구독 한도 초과·감시 루프 한 바퀴에 몇 시간씩 걸리는 문제가 생깁니다. 지금은 `watchlist` 테이블에 있는 종목만 실시간 대상이고, 전체 수집은 그 범위에 영향을 주지 않습니다
  - 대시보드(차트 및 분석 페이지)의 종목 검색이 실제로 동작하도록 연결 — 코드/이름으로 전체 수집 종목을 찾아 바로 조회하거나 관심종목에 추가/제거 가능
- ✅ **9단계 — 전체 종목 리스트에서 ETF/ETN/우선주/스팩 제외 + 분봉 1분 단위로 변경**:
  - `collect_universe.py`가 ETF/ETN(별도 시장구분 mrkt_tp=8/60/70/90으로 대조 확인 — 코스피/코스닥 리스트에 섞여 나옴), 우선주(종목명 끝 "(숫자)우(영문)" 패턴), 스팩(종목명에 "스팩" 포함)을 걸러내고 저장. 실측 결과 코스피 2,486종목 중 1,653건, 코스닥 1,823종목 중 77건이 제외되어 전체 4,309 → **2,579종목**으로 줄었습니다
  - `MarketDataStore.sync_universe()` 추가 — 기존 `upsert_universe`는 새로 안 들어온 종목을 안 지웠는데(계속 남아있음), 이건 이번 실행에 없는 코드를 삭제까지 해서 필터가 진짜로 반영되게 함
  - `config.data.intraday.minute_scope`를 `"5"` → `"1"`로 변경 (5분봉 대신 1분봉)
  - **버그 수정**: `kiwoom_rest_provider.py`의 분봉/틱 파싱이 거래량 `None`을 그대로 `.astype(int)`에 넣어 죽는 버그가 있었습니다 (전체 수집 중 100종목이 이걸로 실패, `_to_int_volume()` 헬퍼로 NaN을 0으로 채우도록 수정, 재수집으로 100/100 복구 확인)
- ✅ **4-역할 위원회 (매매 최종 결정권자)**: `agents/decision_maker.py`가 과거(`history_analyst`)/현재(`realtime_analyst`)/비교(`comparison_analyst`, 신규) 3명의 의견을 가중합해 BUY/SELL/HOLD + 확신도를 산출. `config.decision.enabled`로 `main.py` 매매 판단에 거부권(veto) 형태로 연결 가능 (기본 꺼짐, 기존 동작 불변)
- ✅ **위원 판단 기준 config화**: 각 위원의 임계값(모멘텀%, 박스권%, 이동평균 기간 등)이 전부 `config/base.yaml`의 `analysts` 섹션으로 빠져서, 코드 수정 없이 조정 가능
- ✅ **백테스트 기반 자동 튜닝 + 성공/실패 이력**: `run.bat tune-history`가 분석가-1(과거)의 이동평균 파라미터를 그리드서치해 `tuning_runs`/`tuning_transitions` 테이블에 성공/실패와 성공↔실패 전환을 기록하고, `--apply`로 1위 조합을 config에 바로 반영. 기록은 `MarketDataStore.tuning_llm_context()`로 LLM 연결 시 그대로 재사용 가능하도록 설계
- ⏳ **다음 단계 후보**: 신규 전략 추가(예: 변동성 돌파, 눌림목), 실현손익/승률 계산, 실시간 급등락 알림→자동매매 연결, LLM(gemma-2-9b)이 각 위원(history/realtime/comparison)의 규칙기반 판단을 대체·보강, 위원회가 거부권을 넘어 직접 주문까지 내도록 확장, realtime/comparison 위원의 백테스트 튜닝(틱 데이터 축적 후)

## 왜 이런 구조인가

브로커(주문 실행)와 데이터 제공자(시세 수집)를 각각 "규격(인터페이스)"으로
분리해뒀습니다. 지금은 `kiwoom-client`(오픈소스 키움 REST API 파이썬 래퍼)를
통해 키움 모의투자 서버에 연결되어 있고, 실전투자로 넘어갈 때는
`config/base.yaml`의 `kiwoom.is_mock`을 `false`로 바꾸기만 하면 됩니다.
전략/리스크 관리 코드는 전혀 건드릴 필요가 없습니다.

```
데이터 제공자 → 데이터 저장소(SQLite, 증분저장) → 전략 → 리스크 관리자 → 브로커
  [kiwoom_rest, 기본]                                                    [kiwoom_rest, 기본]
  [dummy, 테스트용]                            (LLM 자문, 3단계부터 참여)  [paper, 배관 검증용]
```

## 폴더 구조
```
app.py              로그인 페이지 (멀티페이지 앱의 진입점, 실제 비밀번호 인증)
pages/
  1_차트_및_분석.py    전략별 종목 아코디언 + 캔들차트 + 실시간 분봉/호가창 + 시스템 로그
  2_계좌_현황.py        계좌 요약 + 보유종목 + 매매이력
  3_리스크_설정.py      매수 비중/손절선/동시 보유 종목 수를 화면에서 조절 (live_trade.py가 재시작 없이 반영)
.streamlit/
  config.toml         다크 테마 + 서버 바인딩(0.0.0.0) + 브라우저 자동 실행 설정
start_dashboard.bat   더블클릭 실행용 (가상환경 경로 직접 지정, git 미포함 — 컴퓨터별로 따로 생성)
make_shortcut.vbs     바탕화면에 "Trading Dashboard" 바로가기를 만들어주는 스크립트 (최초 1회만 실행)
config/              공통 설정 + 집/회사 환경별 override
src/
  config_loader.py   환경 자동감지 + 설정 병합 + 키움 앱키/시크릿 로드
  ui_common.py        페이지 공통 헤더/로그인가드/긴급정지 버튼
  dashboard.py         (구버전) 단일페이지 대시보드. app.py로 대체되었지만 그대로 동작함
  core/factory.py     config 문자열 → 실제 클래스로 조립하는 공장
  data_layer/
    storage.py         시세 데이터 저장(SQLite) + 매매판단 로그(decisions 테이블)
    providers/
      base_provider.py
      dummy_provider.py        가짜 데이터 (테스트용)
      kiwoom_rest_provider.py  키움 REST로 일봉 수집 (기본)
      kiwoom_mcp_provider.py   향후 MCP용 자리(스텁)
  broker/
    base_broker.py
    paper_broker.py         자체 가상 잔고 (배관만 검증하고 싶을 때)
    kiwoom_rest_broker.py   키움 모의투자로 실제 형식 주문 (기본)
    kiwoom_mcp_broker.py    향후 MCP용 자리(스텁)
  strategy/
    base_strategy.py
    ma_cross_strategy.py    1단계: 단순 이동평균 교차
  risk/risk_manager.py      포지션 크기, 손절 계산
  llm/advisor.py             LLM 자문 슬롯 (3단계 전까지는 항상 중립 응답)
  backtest/backtest_engine.py  실전과 동일한 전략/리스크 코드로 검증 (수수료·세금·슬리피지·손절 반영)
  collect_data.py           데이터 증분 수집 실행 (종목 지정, --full로 전체 재수집)
  collect_universe.py       전체 종목 리스트 수집 (ka10099, 코스피/코스닥)
  collect_all.py            universe의 전체 종목 일봉(+분봉/틱) 일괄 갱신 — 장 마감 후 스케줄 실행용
  collect_index.py          시장 벤치마크(코스피/코스닥) 지수 일봉 수집 — 백테스트 알파 계산용
  analyze_history.py        과거 차트 분석 에이전트 CLI (agents/history_analyst.py)
  analyze_realtime.py       실시간 분석 에이전트 감시 CLI (agents/realtime_analyst.py)
  analyze_decision.py       4-역할 위원회 전체 리포트 CLI (agents/decision_maker.py)
  tune_analysts.py          백테스트 기반 위원 파라미터 튜닝 CLI (tuning/analyst_tuner.py)
  agents/
    history_analyst.py      [차트 분석가-1: 과거] 추세/변동성/박스권/거래량 규칙기반 분석 (LLM 붙일 자리)
    realtime_analyst.py     [차트 분석가-2: 현재] 단기 모멘텀/거래량 급증 규칙기반 분석 (LLM 붙일 자리)
    comparison_analyst.py   [차트 분석가-3: 비교] 과거 추세 vs 현재 모멘텀의 정합성 판단
    decision_maker.py       [매매 최종 결정권자] 위 3명의 stance를 가중합해 BUY/SELL/HOLD 결정
  tuning/
    analyst_tuner.py         분석가-1 파라미터 그리드서치 + 성공/실패·전환 기록 + config 자동반영
  realtime/
    stream_collector.py     키움 웹소켓 체결가(0B)+호가잔량(0D) 한 세션에서 동시 수집 (kiwoom_client.KiwoomWebSocket 직접 사용)
  close_day.py               장마감 후 그날 실시간 틱을 종목별 1분봉/일봉으로 묶어 기존 저장소에 합침
  live_trade.py               장중 내내 관심종목을 반복 확인하며 실시간가 기준으로 자동 매매 (main.py의 연속 실행 버전)
  run_backtest.py           백테스트 실행
  main.py                    모의/실전 매매 실행 (하루 1회 신호 확인, 판단결과 자동 로깅)
  inspect_kiwoom_chart.py    [최초 1회] 일봉 응답 실제 컬럼명 확인용
  inspect_kiwoom_account.py  [최초 1회] 계좌 응답 실제 필드명 확인용
  inspect_kiwoom_universe.py [필요시] fetch_universe/fetch_minute/fetch_tick이 쓰는
                             kiwoom-client 메서드명이 실제와 맞는지 확인용
```

## 처음 세팅

1. 키움증권 REST API 포털(openapi.kiwoom.com)에서 **모의투자용** App Key/Secret 발급
2. `.env.example` → `.env` 복사 후 값 채우기:
   - `KIWOOM_APP_KEY`, `KIWOOM_APP_SECRET`, `DATA_DIR`, `LOG_DIR`, `ENV_NAME`
   - `DASHBOARD_PASSWORD` — 대시보드 로그인 비밀번호. **비워두면 대시보드가 실행을 거부합니다** (안전장치)
   (`.env`와 `data/` 폴더는 git에 올라가지 않으므로, **컴퓨터를 옮길 때마다 이 단계는 매번 새로 해야 합니다**)
3. `pip install -r requirements.txt` (conda 가상환경 활성화된 상태에서)
4. **필드명 확인 (최초 1회, 중요)**
   ```
   run.bat inspect-chart 005930
   run.bat inspect-account
   ```
   확인된 필드명은 이미 코드에 반영되어 있습니다 (`dt`/`cur_prc`/`open_pric`/`trde_qty` 등). 계정이나 키움 서버 정책이 바뀌면 다시 확인이 필요할 수 있습니다.
5. 데이터 채우기: `run.bat collect 005930 000660`
6. 벤치마크 지수 채우기(선택, 알파 계산용): `run.bat collect-index`
7. 백테스트: `run.bat backtest 005930`
8. 모의투자로 하루치 신호 확인/주문: `run.bat main 005930`
9. 대시보드 실행: `run.bat app` (로그인+멀티페이지, 추천) 또는 `run.bat dashboard` (단일페이지 구버전)

### 기존 데이터를 갖고 있다면 (연속조회/수정주가 적용 전에 수집한 경우)

`collect_data.py`/`collect_all.py`가 연속조회(cont-yn/next-key)와 수정주가(`upd_stkpc_tp=1`)를
쓰도록 바뀌었습니다. 이전에 모아둔 데이터는 비수정주가·한 페이지 분량뿐이라 액면분할/무상증자
종목에서 가짜 급등락이 남아있을 수 있고, 과거 구간도 짧습니다. 한 번은 전체 재수집하세요:

```
run.bat collect --full 005930 000660
run.bat collect-all --full
```

`upsert`가 같은 (종목, 날짜)를 덮어쓰므로 기존 행이 안전하게 교체됩니다.

### 백테스트 비용 가정치

`config/base.yaml`의 `backtest:` 블록(`fee_rate`/`tax_rate`/`slippage_rate`)이 매수/매도마다
수수료·세금·슬리피지를 반영합니다. 예시값이 들어있으니 본인 증권사 수수료와 현재 세율로
바꿔서 쓰세요. `benchmark_code`(기본 `"001"`=코스피)가 설정돼 있고 `collect-index`로 지수
데이터를 받아뒀으면, 백테스트 결과에 `benchmark_return_pct`/`alpha_pct`(시장 대비 초과수익)가
함께 나옵니다.

### 더블클릭으로 실행하고 싶다면 (아나콘다 프롬프트 없이)

컴퓨터마다 가상환경 경로가 다르므로(집=E드라이브, 회사=D드라이브), `start_dashboard.bat`은
git에 포함되지 않습니다 — 컴퓨터별로 한 번씩 아래처럼 만들어 두세요.

1. 프로젝트 폴더에 `start_dashboard.bat` 생성, 아래 내용에서 `ENV_DIR`만 그 컴퓨터의 실제
   가상환경 경로로 수정:
   ```bat
   @echo off
   set ENV_DIR=D:\dev\envs\miniconda3\envs\agent-py313
   set PATH=%ENV_DIR%;%ENV_DIR%\Scripts;%PATH%
   cd /d %~dp0
   call run.bat app
   pause
   ```
2. `make_shortcut.vbs`를 더블클릭 (최초 1회) → 바탕화면에 "Trading Dashboard" 아이콘 생성
3. 이후로는 그 아이콘만 더블클릭하면 실행됩니다. 관리자 권한, `conda activate` 불필요.

## 여러 컴퓨터에서 쓸 때 (Git 동기화 관련)

Git으로 옮겨가는 건 **코드뿐**입니다. 아래는 컴퓨터마다 별도로 해야 합니다.

- `.env` 새로 작성 (API 키, 경로, `ENV_NAME`, `DASHBOARD_PASSWORD`)
- `data/market_data.db` 재수집 (`run.bat collect`) — DB 파일 자체를 git에 올리지 않음
- `pip install -r requirements.txt` — 코드는 와도 패키지 설치는 별개
- conda 가상환경 활성화 (또는 위의 `start_dashboard.bat` 더블클릭 방식)
- `start_dashboard.bat` — 컴퓨터별 가상환경 경로가 달라 git에 포함하지 않음, 위 방법대로 새로 생성

## 대시보드 사용법

- `run.bat app` 실행 (또는 바탕화면 아이콘 더블클릭) → 브라우저가 자동으로 열리며 로그인 화면 표시
  → `.env`의 `DASHBOARD_PASSWORD`와 정확히 일치하는 비밀번호 입력 후 "투자 환경" 선택하고 로그인
  (5회 연속 실패 시 60초간 잠금)
- **차트 및 분석**: 왼쪽 사이드바에서 전략 그룹(아코디언)을 펼쳐 종목 선택 → 캔들차트(실데이터)와 시스템 로그 확인
- **계좌 현황**: 현금/보유수량은 실제 키움 모의투자 데이터. 실현손익/승률은 계산 로직이 아직 없어 "준비 중"으로 표시됩니다 (가짜 숫자를 보여주지 않기 위한 의도적인 선택입니다)

### 집/회사 어디서든 원격 접속 (Tailscale, 포트포워딩 불필요)

`.streamlit/config.toml`에서 `server.address = "0.0.0.0"`로 모든 네트워크 인터페이스에서
접속을 받도록 설정되어 있습니다. Tailscale이 설치되고 로그인된 상태라면, 공유기 포트포워딩
없이도 Tailscale 사설망을 통해 접속할 수 있습니다.

- `run.bat app` 실행 시 콘솔에 로컬 주소와 함께 Tailscale 주소가 자동으로 출력됩니다.
- 집/회사 컴퓨터 양쪽 다 각자 `run.bat app`(또는 바탕화면 아이콘)을 실행하면, 각자 독립적인
  대시보드(각자의 데이터/DB)를 돌리면서 서로의 Tailscale 주소로 접속할 수 있습니다.
- 접속이 안 되면 Windows 방화벽에서 8501 포트 인바운드 허용 여부를 확인하세요.

## 전체 종목 데이터 / 실시간 / 분석 에이전트 (3단계)

### 1) 전체 종목 리스트 + 일괄 수집

```
run.bat collect-universe          # 코스피/코스닥 전체 종목 리스트 → universe 테이블
run.bat collect-all                # universe 전체 종목의 일봉을 증분 수집
run.bat collect-all --minute       # + 분봉(기본 5분, config.data.intraday.minute_scope)
run.bat collect-all --tick         # + 틱
run.bat collect-all --info         # + 기본정보(PER/PBR/시가총액 등)
run.bat collect-all --limit 50     # 테스트용으로 앞 50종목만
```

**전체 종목을 일봉+분봉+틱+기본정보 다 받으면 종목당 API 호출이 여러 번씩 쌓여서
전체(수천 종목) 기준 몇 시간이 걸릴 수 있습니다** — 장 마감 후 스케줄러로 밤새
돌리는 용도로 설계됐습니다. 이렇게 전체 종목을 받아도 실시간 매매/구독 대상
(watchlist)은 늘어나지 않습니다 — `store.symbols()`(수집해둔 전체 종목)과
`store.get_watchlist()`(실제 감시 대상)가 분리되어 있기 때문입니다. `live_trade.py`/
`run.bat realtime --watchlist`는 항상 후자만 봅니다. 관심종목 추가/제거는 차트 페이지
사이드바 검색에서 하거나 `store.add_to_watchlist("종목코드")`를 직접 호출하세요.

`collect_universe.py`/`collect_all.py`는 `kiwoom_rest_provider.py`의 `fetch_universe`/
`fetch_minute`/`fetch_tick`을 씁니다. 이 메서드들이 부르는 `kiwoom-client` 라이브러리
메서드 이름(`_STKINFO_NAMESPACE_CANDIDATES` 등)은 기존 `fetch_ohlcv`처럼 "후보 목록 중
맞는 걸 자동으로 찾고, 없으면 실제 사용 가능한 이름을 보여주는" 방식으로 짜뒀습니다.
**처음 실행했을 때 `AttributeError`가 나면** 에러 메시지에 실제 사용 가능한
네임스페이스/메서드 목록이 함께 나오니, 그걸 보고 `kiwoom_rest_provider.py` 상단의
후보 리스트를 수정하거나 `run.bat inspect-universe`로 먼저 확인해보세요.

**장 마감 후 자동 실행(Windows 작업 스케줄러)**: 작업 스케줄러 → 새 작업 만들기 →
트리거 "매일 16:00" → 동작 "프로그램 시작" → `D:\dev\project\trading-system\run.bat` →
인수 `collect-all --minute`. (`run.bat main`도 같은 방식으로 매매 시간에 맞춰 등록 가능)

### 2) 실시간 데이터 수집 (체결 + 호가창)

키움 웹소켓 실시간 체결가(API ID `0B`)와 호가잔량(API ID `0D`, 매도/매수 10단)을
**한 세션에서** 동시에 구독합니다. `kiwoom-client` 라이브러리(REST 호출에도 쓰는 바로 그
라이브러리)의 `KiwoomWebSocket`을 직접 씁니다 — 별도 설치나 `kiwoomcli setup` 같은
사전 준비 없이 `.env`의 `KIWOOM_APP_KEY`/`SECRET`을 그대로 씁니다.

(처음엔 공식 CLI `kwcli`를 서브프로세스 두 개로 띄우는 방식으로 만들었는데, 실측해보니
같은 계정으로 실시간 웹소켓 로그인을 두 번 하면 키움 서버가 먼저 연결을 끊어버렸습니다
— 계정당 실시간 세션은 하나만 유지되는 것으로 보입니다. `KiwoomWebSocket.subscribe()`는
한 세션 안에서 여러 타입을 한 번에 등록할 수 있어 이 문제를 피합니다.)

실행:
```
run.bat realtime 005930                 # 삼성전자 체결+호가 동시 수집 (Ctrl+C로 종료)
run.bat realtime 005930 000660          # 여러 종목 동시
run.bat realtime 005930 --no-orderbook  # 체결만, 호가는 끄고 싶을 때
run.bat realtime 005930 --real          # 모의 대신 실전 데이터 구독
```

체결은 `realtime_ticks`, 호가는 `realtime_orderbook`(매도/매수 각 10단, JSON으로
저장)에 쌓입니다. `MarketDataStore.latest_orderbook(symbol)`로 최신 호가 한 장을
바로 가져올 수 있습니다.

프로그램을 켤 때 또는 장 시작 시 이 명령을 실행해두면 "실시간 데이터 취합" 요구사항을
채웁니다. 아직은 수동 실행이며, 자동 스케줄링(작업 스케줄러로 장 시작 시 자동 기동)은
TODO입니다.

### 2-1) 장마감 후 기존 데이터에 합치기

`realtime_ticks`는 종목/구간 구분 없이 계속 쌓이기만 하는 원시 로그입니다. 장이
끝나면 `close_day.py`가 그날 종목별로 모아 1분봉을 만들고, 기존 종목별 저장소에
합칩니다:

```
run.bat close-day                          # 오늘, 실시간 틱이 있던 전 종목
run.bat close-day 005930 000660            # 특정 종목만
run.bat close-day --date 2026-09-22        # 다른 날짜 지정 (그날 못 돌렸을 때)
```

- 1분봉은 `intraday_ohlcv`에 `interval="1m_rt"`로 합쳐집니다 (`collect_all.py --minute`이
  쓰는 `"5m"` 등 API 기반 분봉과 같은 테이블에 나란히 쌓이되, interval로 구분되어 섞이지 않습니다).
- 그날 `ohlcv`에 공식 일봉이 아직 없는 종목만 실시간 틱으로 만든 근사 일봉을 채워 넣습니다.
  이미 공식 일봉이 있으면 건드리지 않습니다 — 나중에 `collect_data.py`를 돌리면 그 근사치가
  공식 값으로 자연스럽게 덮어써집니다.
- 장 마감 후(예: 매일 15:40) Windows 작업 스케줄러에 등록해서 자동 실행할 수 있습니다
  (`collect-all` 등록 방법과 동일하게 인수만 `close-day`로 바꾸면 됩니다).

### 2-2) 관심종목 실시간 매매 (live_trade.py)

`main.py`는 하루 한 번만 신호를 확인하고 끝나는 반면, `live_trade.py`는 장중 내내
`interval`초(기본 60초)마다 관심종목을 반복 확인합니다. 관심종목은 별도로 등록할
필요 없이 기본값이 `store.symbols()`, 즉 **지금까지 collect_data.py/collect_all.py로
수집해둔 전 종목**입니다.

```
run.bat realtime --watchlist       # 1) 관심종목 전체를 체결+호가 실시간 구독 (별도 창)
run.bat live-trade                 # 2) 같은 관심종목을 60초 간격으로 감시하며 자동 매매
run.bat live-trade 005930 000660   # 특정 종목만 감시하고 싶을 때
run.bat live-trade --interval 30   # 확인 주기를 30초로
```

1)을 먼저 켜서 `realtime_ticks`를 채워두면 2)가 그 실시간가로 신호를 판단합니다.
1)을 안 켜두면 마지막 저장된 종가로 판단하므로 사실상 하루 내내 같은 신호만
반복됩니다. 전략/리스크/브로커는 `main.py`·`run_backtest.py`와 완전히 같은 코드를
씁니다. **알려진 한계**는 `src/live_trade.py` 상단 docstring에 정리해뒀습니다 —
특히 실제 주문 체결가는 아직 실시간가가 아니라 브로커가 반환하는 마지막 저장 종가
기준이라, 장중 변동이 큰 날은 신호 판단 시점 가격과 체결가가 다를 수 있습니다.

### 3) 분석 에이전트 (1차, 규칙기반)

지금은 LLM 없이 pandas로 계산하는 "1단계 규칙기반" 버전입니다. `llm/advisor.py`와
같은 패턴으로, 나중에 LLM(gemma-2-9b)을 붙일 때 이 두 클래스의 `analyze()` 반환값을
그대로 프롬프트 컨텍스트로 넘기면 됩니다.

```
run.bat analyze-history 005930         # 과거 차트: 추세/변동성/박스권/거래량 요약
run.bat analyze-realtime 005930        # 실시간: 단기 모멘텀/거래량 급증 감시 (5초 간격)
run.bat analyze-realtime 005930 10     # 10초 간격
```

`analyze-realtime`은 `realtime` 수집기가 먼저 켜져서 데이터를 쌓고 있어야 의미 있는
결과가 나옵니다 (데이터가 없으면 그렇다고 알려줍니다).

### 4) 전략 구성

`config/base.yaml`의 `strategy.assignments`에 종목코드별로 다른 전략/파라미터를
지정할 수 있습니다. 지정하지 않은 종목은 기본 `strategy.name`/`params`를 씁니다.
새 전략을 추가하려면 `src/strategy/`에 `BaseStrategy`를 상속한 클래스를 만들고
`core/factory.py`의 `_STRATEGY_REGISTRY`에 한 줄 등록하면 됩니다 (`ma_cross`가
등록된 방식과 동일).

### 5) 4-역할 위원회 (매매 최종 결정권자 + 차트 분석가 3명)

요청하신 역할 분담 구조입니다. 기존에 분리되어 있던 두 분석 에이전트를 "위원"으로
재사용하고, "비교 분석가"와 "최종 결정권자"를 새로 추가해서 하나의 위원회로 묶었습니다.

| 요청하신 역할 | 실제 구현 | 파일 |
|---|---|---|
| 차트 분석가-1 (과거 차트) | `HistoryAnalyst` (기존, 그대로 재사용) | `agents/history_analyst.py` |
| 차트 분석가-2 (현재 차트) | `RealtimeAnalyst` (기존, 그대로 재사용) | `agents/realtime_analyst.py` |
| 차트 분석가-3 (과거·현재 비교) | `ComparisonAnalyst` (신규) — 위 두 위원의 결과를 받아 "추세와 단기 흐름이 같은 얘기를 하는지"를 판단 | `agents/comparison_analyst.py` |
| 매매 최종 결정권자 | `DecisionMaker` (신규) — 3명의 stance(POSITIVE/NEGATIVE/NEUTRAL)를 `config.decision.weights`로 가중합해서 BUY/SELL/HOLD + 확신도(%)를 결정 | `agents/decision_maker.py` |

```
run.bat analyze-decision 005930     # 위원회 전체 리포트(JSON) + 최종 결정 출력, decision_committee 테이블에도 기록
```

대시보드의 "차트 및 분석" 페이지에도 "🧭 매매 위원회 판단" 섹션이 추가되어, 선택한
종목에 대해 버튼 한 번으로 4명의 의견을 카드 형태로 볼 수 있습니다.

`main.py`(자동 매매 진입점)에는 `config/base.yaml`의 `decision.enabled`로 켜고 끌 수
있는 안전장치로 연결해두었습니다. 기본값은 `false`라 지금까지의 동작(순수 규칙기반
`strategy.generate_signal()`)은 전혀 바뀌지 않습니다. `true`로 켜면:

- 기존 전략이 BUY 신호를 냈는데 위원회가 SELL 우세로 판단하면 → 이번엔 매수를 보류(HOLD)
- 반대로 전략이 SELL인데 위원회가 BUY 우세면 → 매도를 보류(HOLD)
- 위원회가 전략과 같은 방향이거나 중립이면 → 전략 신호 그대로 실행

즉 지금은 "위원회가 전략을 거부권(veto)으로 보완"하는 구조입니다. 나중에 위원회
쪽 확신도(`confidence`)가 충분히 높을 때는 위원회 판단만으로 직접 주문을 넣게
하거나, `llm/advisor.py`처럼 위원회 안에 LLM을 네 번째 위원으로 추가하는 식으로
확장할 수 있도록 `decide()`가 늘 같은 dict 형태(`action`, `confidence`, `votes`,
`reports`)를 반환하게 설계했습니다.

가중치/임계값은 `config/base.yaml`의 `decision` 섹션에서 조정합니다:

```yaml
decision:
  enabled: false
  buy_threshold: 0.4
  sell_threshold: -0.4
  weights:
    history: 1.0
    realtime: 1.0
    comparison: 1.5
```

### 6) 위원 "교육" — 각 분석가의 판단 기준 조정

규칙기반 시스템이라 ML/LLM식 학습은 아니고, 각 분석가가 쓰던 임계값(모멘텀 %,
박스권 %, 이동평균 기간 등)을 코드에서 `config/base.yaml`의 `analysts` 섹션으로
전부 빼뒀습니다. 즉 "교육"은 이 숫자들을 조정하는 것으로 합니다 — 코드 수정도,
재배포도 필요 없습니다.

```yaml
analysts:
  history:                        # 차트 분석가-1 (과거)
    sma_short: 5
    sma_mid: 20
    sma_long: 60
    volatility_window: 20
    box_window: 60
    volume_recent_days: 5
    volume_prior_days: 20
    min_bars_required: 20
  realtime:                       # 차트 분석가-2 (현재)
    lookback: 200
    momentum_up_pct: 0.5          # 이 값(%)보다 오르면 "단기 급등" — 낮출수록 예민해짐
    momentum_down_pct: -0.5
    volume_spike_window: 20
    volume_spike_alert_ratio: 2.0
  comparison:                     # 차트 분석가-3 (비교)
    box_breakout_high_pct: 90     # 박스권 상단 몇 %부터 "돌파 시도"로 볼지
    box_breakout_low_pct: 10
```

값을 생략하면 각 `agents/*.py` 파일에 있던 기존 기본값이 그대로 쓰여서 하위호환이
유지됩니다. 예:
- 분석가-2가 잔파도에 너무 자주 반응한다 → `momentum_up_pct`/`momentum_down_pct`의
  절대값을 0.5 → 0.8 등으로 올려서 둔감하게.
- 분석가-1이 너무 늦게 추세 전환을 알아챈다 → `sma_long`을 60 → 40으로 줄여서
  더 짧은 호흡으로 보게.
- 분석가-3의 "박스권 돌파" 판정이 너무 자주 뜬다 → `box_breakout_high_pct`를
  90 → 95로 올려서 더 보수적으로.

진짜 "학습"(과거 데이터로 최적 임계값을 자동으로 찾는 것)은 아래 7번에서
"차트 분석가-1 (과거)"에 한해 구현했습니다. LLM에게 자연어로 판단 성향을
지시하는 것은 아직 없고, `llm/advisor.py` 슬롯에 실제 로컬 LLM을 연결하는
작업을 별도로 진행해야 합니다 (TODO 참고). 다만 아래 튜닝 이력은 그 LLM이
붙었을 때 바로 참고자료로 쓸 수 있도록 이미 준비해뒀습니다.

### 7) 백테스트 기반 자동 튜닝 + 성공/실패 이력

지금은 "차트 분석가-1 (과거, `history_analyst`)"의 추세 판정 파라미터
(`sma_short`/`sma_mid`/`sma_long`)만 자동 튜닝됩니다. **왜 이 세 개만인가**:
이 값들만 실제로 매수/매도 시점을 바꿔서 백테스트로 검증할 수 있고, 나머지
(변동성/박스권/거래량 관련, 그리고 실시간·비교 분석가의 파라미터)는 과거
틱 데이터가 아직 충분히 없어서 백테스트로 검증할 방법이 없습니다. 실시간
수집기(`run.bat realtime`)로 틱 데이터가 쌓이면 같은 방식으로 확장할 수
있게 `tuning/analyst_tuner.py`를 분석가별로 나눠뒀습니다.

```
run.bat tune-history 005930                              # 기본 그리드로 백테스트, 결과만 출력
run.bat tune-history 005930 --start 2025-01-01 --end 2025-09-01
run.bat tune-history 005930 --apply                       # 1위 조합을 config/base.yaml에 바로 반영
run.bat tune-history 005930 --top 10                       # 콘솔에 상위 몇 개까지 보여줄지
```

**어떻게 판단하나**: `(sma_short, sma_mid, sma_long)` 조합마다 "상승 정배열일
때만 보유"하는 단순 롱온리 규칙으로 백테스트해서 수익률을 계산하고, 매수 후
보유(벤치마크)보다 잘했으면 `SUCCESS`, 아니면(또는 거래가 아예 없었으면)
`FAILURE`로 판정합니다. 모든 조합의 결과가 `tuning_runs` 테이블에 기록됩니다.

**성공↔실패 전환 기록**: 같은 파라미터 조합을 나중에 다른 기간으로 다시
튜닝했을 때 판정이 뒤집히면(예: 예전엔 SUCCESS였는데 이번엔 FAILURE)
`tuning_transitions` 테이블에 별도로 기록합니다. 이건 "시장 국면이 바뀌어서
예전에 통하던 설정이 더는 안 통한다"는 신호입니다. 그리드서치가 순위를 매길
때도 이 뒤집힌 횟수(`flip_count`)만큼 감점(`score = 수익률 - flip_count × 2`)
해서, 어쩌다 한 번 잘 맞았을 뿐인 불안정한 설정보다 꾸준히 통하는 설정을
우선하도록 했습니다.

**조정 방법**: `--apply` 옵션을 주면 1위(SUCCESS 중 점수가 가장 높은) 조합을
`config/base.yaml`의 `analysts.history`에 자동으로 반영합니다(주석/서식은
그대로 보존). 옵션 없이 실행하면 결과만 보여주고 config는 건드리지 않으니,
먼저 결과를 보고 판단한 뒤 `--apply`로 확정하는 흐름을 권장합니다. 대시보드의
"차트 및 분석" 페이지에도 "🧪 위원 튜닝 이력" 섹션에서 최근 성공/실패와
전환 기록을 바로 볼 수 있습니다.

**LLM 연결 대비**: 이 모든 기록(성공/실패, 전환)은 `MarketDataStore.
tuning_llm_context(symbol, "history")`로 사람이 읽는 텍스트 한 덩어리로
뽑을 수 있고, `main.py`는 이미 이 텍스트를 `llm_advisor.get_opinion()`의
context에 같이 넘기고 있습니다. 지금은 `NoOpAdvisor`라 무시되지만, 나중에
`llm/advisor.py`에 실제 LLM을 연결하면 "이 종목/이 설정은 과거에 이랬다"는
걸 코드 변경 없이 바로 참고하게 됩니다.

## 키움증권 MCP (API 탐색 · 조회 보조 도구)

`D:\dev\mcp\`에 별도로 관리 중인 Claude 데스크탑 확장 프로그램(.mcpb) 두 개입니다.
이 프로젝트 코드와는 독립적으로 동작하며, 대화 중에 필요할 때 바로 활용합니다.

- **kiwoom-spec-mcp**: 자격증명 없이 키움 REST/WebSocket API 명세를 검색하고 예제 코드를 가져옴
  (예: "일봉 차트 관련 API 찾아줘")
- **kiwoom-exec-mcp**: 실제 키움 계좌 조회를 실행 (App Key/Secret 필요, 주문 도구는 기본 비활성화)
  (예: "지금 계좌 예수금 확인해줘")

재설치가 필요하면 Claude 데스크탑 설정 → Extensions에서 `D:\dev\mcp\kiwoom-exec-mcp-1.0.0.mcpb`,
`D:\dev\mcp\kiwoom-spec-mcp-1.0.0.mcpb` 파일로 다시 설치하면 됩니다. `uv`(파이썬 실행 도구)가
설치되어 있어야 합니다.

## 앞으로 채워야 할 부분 (TODO)

- 실현손익/승률 계산 로직 (체결 단가 기반)
- `collect-all`/`realtime`의 Windows 작업 스케줄러 자동 등록 (지금은 수동 등록 안내만 있음)
- `agents/history_analyst.py`/`realtime_analyst.py`의 규칙기반 판단을 LLM(gemma-2-9b)으로 대체·보강
- 신규 매매 전략 추가 (지금은 `ma_cross` 하나뿐 — `strategy.assignments` 구조는 준비됨)
- 4-역할 위원회(`decision.enabled`)를 실제 계좌로 실행하기 전, 모의투자로 며칠 관찰해서
  `buy_threshold`/`sell_threshold`/`weights`가 합리적인지 검증 필요 (아직 실거래로 검증 안 됨)
- 위원회가 거부권(veto)만 행사하는 지금 구조를, 확신도가 높을 때 위원회가 직접 주문을
  내는 구조로 확장할지 결정 (`agents/decision_maker.py`의 `decide()` 반환값은 이미 대비되어 있음)
- `run.bat tune-history`는 아직 분석가-1(과거)만 튜닝합니다. 실시간 틱 데이터가
  `realtime_ticks`에 충분히 쌓이면 분석가-2(현재)/분석가-3(비교)도 같은 방식(그리드서치
  + `tuning_runs`/`tuning_transitions` 기록)으로 확장
- `tuning_llm_context()`로 준비해둔 성공/실패 이력을, `llm/advisor.py`에 실제 LLM을
  연결한 뒤 판단 성향 조정에 실제로 활용해보고 효과 검증
- 코스피/코스닥 실시간 지수 연동 (현재 Mock)
- `realtime/stream_collector.py`의 필드 매핑(`_PRICE_KEYS` 등)이 실제 `kiwoomcli --named` 출력과
  맞는지 실사용 후 확인 (처음 실행 시 경고 로그로 원본 키 목록이 나오면 그걸 보고 조정)

## 주의

- `config/base.yaml`의 `kiwoom.is_mock`은 기본값 `true`(모의투자)입니다.
  실거래(`false`) 전환은 반드시 충분한 기간의 백테스트 + 모의투자 검증 후, 신중하게 진행하세요.
- pykrx 등 다른 출처로 받은 과거 데이터와 섞어 쓰지 마세요 — 수정주가/비수정주가
  기준이 달라 백테스트 결과가 왜곡될 수 있습니다. 이 시스템은 처음부터
  키움 데이터 하나로 통일해서 이 문제를 피했습니다.
- `DASHBOARD_PASSWORD`를 반드시 설정하고, 외부(인터넷)에 노출할 계획이라면 포트포워딩보다
  Tailscale 같은 사설망 경유를 권장합니다.
- 이 시스템은 투자 조언을 제공하지 않으며, 모든 매매 결정과 그 결과의 책임은
  사용자 본인에게 있습니다.

@echo off
REM Usage: run.bat backtest 005930
REM        run.bat main 005930
REM        run.bat collect 005930 000660
REM        run.bat collect-universe [0 10]
REM        run.bat collect-all [--minute] [--tick] [--info] [--limit N] [--full]
REM                            [--minute-days N|max] [--missing] [--watchlist] [--symbols 005930 000660]
REM        run.bat catalog [--purge-orphans [--apply]]   (잔재 종목 데이터 정리: universe/관심종목에도 없고
REM                          일봉·기본정보도 없는 채로 남은 옛 실시간 틱/분봉만 지움 — 미리보기 후 --apply)
REM        run.bat migrate-db [--check]       (단일 DB 파일을 도메인별 파일로 분리, 1회)
REM        run.bat research [--scope top_cap^|watchlist^|random] [--n 300] [--start 2021-01-01] [--horizon 5] [--stop 2] [--minute]
REM        run.bat screen [--scope top_cap^|watchlist^|random] [--n 300] [--top 10] [--add]
REM                        (research로 검증된 규칙으로 오늘 상승 후보를 찾아 관심종목에 추가 — 기본은 미리보기, --add로 실제 추가)
REM        run.bat collect-index [001 101]
REM        run.bat realtime 005930 [000660 ...] [--real] [--no-orderbook]
REM        run.bat realtime --watchlist [--real] [--no-orderbook]
REM        run.bat close-day [005930 000660 ...] [--date YYYY-MM-DD] [--force]
REM        run.bat plans [--sync]              (다음 거래일 매매 계획 확인·관심종목 동기화, plan_trades 기록)
REM        run.bat live-trade [005930 000660 ...] [--interval SEC]
REM        run.bat analyze-history 005930
REM        run.bat analyze-realtime 005930 [interval_sec]
REM        run.bat analyze-decision 005930
REM        run.bat tune-history 005930 [--apply] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
REM        run.bat inspect-chart 005930
REM        run.bat inspect-account
REM        run.bat inspect-universe
REM        run.bat dashboard
REM        run.bat app

cd /d %~dp0

REM 키는 프로젝트 .env 또는 상위 폴더의 중앙 .env(E:\dev\.env / D:\dev\.env) 중 하나에 있으면 됩니다.
set "ENV_FOUND="
if exist ".env" set "ENV_FOUND=1"
if exist "..\.env" set "ENV_FOUND=1"
if exist "..\..\.env" set "ENV_FOUND=1"
if defined DEV_ENV_FILE if exist "%DEV_ENV_FILE%" set "ENV_FOUND=1"
if not defined ENV_FOUND (
    echo No .env found. Create the central key file: ..\..\_setup\keys.bat set KIWOOM_APP_KEY  ^(and KIWOOM_APP_SECRET^)
    echo Then copy .env.example to .env for project-specific values ^(DATA_DIR, DASHBOARD_PASSWORD, ...^).
    exit /b 1
)

set PYTHONPATH=%~dp0src

REM ---- 파이썬 인터프리터 결정 (PATH의 python에 의존하지 않음) ----
REM PATH의 'python'은 Windows 스토어 스텁일 수 있어 쓰지 않습니다. 우선순위:
REM   1) TRADING_PYTHON 환경변수  2) 프로젝트 .venv (uv)  3) conda agent-py313 (상위 폴더 기준, E:/D: 모두 동작)
set "PY="
if defined TRADING_PYTHON if exist "%TRADING_PYTHON%" set "PY=%TRADING_PYTHON%"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0..\..\envs\miniconda3\envs\agent-py313\python.exe" set "PY=%~dp0..\..\envs\miniconda3\envs\agent-py313\python.exe"
if not defined PY (
    echo Python interpreter not found. Expected one of:
    echo   .venv\Scripts\python.exe  ^(uv^)  or  ..\..\envs\miniconda3\envs\agent-py313\python.exe
    echo Or set TRADING_PYTHON to a python.exe path.
    exit /b 1
)

if "%1"=="backtest" (
    "%PY%" src\run_backtest.py %2
) else if "%1"=="main" (
    "%PY%" src\main.py %2
) else if "%1"=="collect" (
    "%PY%" src\collect_data.py %2 %3 %4 %5
) else if "%1"=="collect-universe" (
    "%PY%" src\collect_universe.py %2 %3
) else if "%1"=="collect-all" (
    "%PY%" src\collect_all.py %2 %3 %4 %5 %6 %7 %8 %9
) else if "%1"=="collect-index" (
    "%PY%" src\collect_index.py %2 %3
) else if "%1"=="realtime" (
    "%PY%" src\realtime\stream_collector.py %2 %3 %4 %5
) else if "%1"=="close-day" (
    "%PY%" src\close_day.py %2 %3 %4 %5 %6 %7
) else if "%1"=="catalog" (
    "%PY%" src\data_layer\catalog.py
) else if "%1"=="migrate-db" (
    "%PY%" src\migrate_split_db.py %2
) else if "%1"=="research" (
    "%PY%" src\research_strategy.py %2 %3 %4 %5 %6 %7 %8 %9
) else if "%1"=="screen" (
    "%PY%" src\screen_candidates.py %2 %3 %4 %5 %6 %7 %8
) else if "%1"=="plans" (
    "%PY%" src\plans_status.py %2
) else if "%1"=="live-trade" (
    "%PY%" src\live_trade.py %2 %3 %4 %5
) else if "%1"=="analyze-history" (
    "%PY%" src\analyze_history.py %2
) else if "%1"=="analyze-realtime" (
    "%PY%" src\analyze_realtime.py %2 %3
) else if "%1"=="analyze-decision" (
    "%PY%" src\analyze_decision.py %2
) else if "%1"=="tune-history" (
    "%PY%" src\tune_analysts.py %2 %3 %4 %5 %6 %7 %8 %9
) else if "%1"=="inspect-chart" (
    "%PY%" src\inspect_kiwoom_chart.py %2
) else if "%1"=="inspect-account" (
    "%PY%" src\inspect_kiwoom_account.py
) else if "%1"=="inspect-universe" (
    "%PY%" src\inspect_kiwoom_universe.py
) else if "%1"=="dashboard" (
    "%PY%" -m streamlit run src\dashboard.py
) else if "%1"=="app" (
    echo.
    echo ============================================================
    echo  Local access   : http://localhost:8501
    for /f "tokens=*" %%i in ('tailscale ip -4 2^>nul') do echo  Tailscale access: http://%%i:8501
    echo  ^(No port forwarding needed. Use the Tailscale address from
    echo   your other device, e.g. home PC, as long as Tailscale is
    echo   running there too.^)
    echo ============================================================
    echo.
    "%PY%" -m streamlit run app.py
) else (
    echo Usage: run.bat [backtest^|main^|collect^|collect-universe^|collect-all^|catalog^|collect-index^|realtime^|close-day^|live-trade^|analyze-history^|analyze-realtime^|analyze-decision^|tune-history^|inspect-chart^|inspect-account^|inspect-universe^|dashboard^|app] args
)

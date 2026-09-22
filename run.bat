@echo off
REM Usage: run.bat backtest 005930
REM        run.bat main 005930
REM        run.bat collect 005930 000660
REM        run.bat collect-universe [0 10]
REM        run.bat collect-all [--minute] [--tick] [--info] [--limit N] [--full]
REM        run.bat collect-index [001 101]
REM        run.bat realtime 005930 [000660 ...] [--real] [--no-orderbook]
REM        run.bat realtime --watchlist [--real] [--no-orderbook]
REM        run.bat close-day [005930 000660 ...] [--date YYYY-MM-DD]
REM        run.bat live-trade [005930 000660 ...] [--interval SEC]
REM        run.bat analyze-history 005930
REM        run.bat analyze-realtime 005930 [interval_sec]
REM        run.bat inspect-chart 005930
REM        run.bat inspect-account
REM        run.bat inspect-universe
REM        run.bat dashboard
REM        run.bat app

cd /d %~dp0

if not exist ".env" (
    echo .env file not found. Copy .env.example to .env and fill in values.
    exit /b 1
)

set PYTHONPATH=%~dp0src

if "%1"=="backtest" (
    python src\run_backtest.py %2
) else if "%1"=="main" (
    python src\main.py %2
) else if "%1"=="collect" (
    python src\collect_data.py %2 %3 %4 %5
) else if "%1"=="collect-universe" (
    python src\collect_universe.py %2 %3
) else if "%1"=="collect-all" (
    python src\collect_all.py %2 %3 %4 %5
) else if "%1"=="collect-index" (
    python src\collect_index.py %2 %3
) else if "%1"=="realtime" (
    python src\realtime\stream_collector.py %2 %3 %4 %5
) else if "%1"=="close-day" (
    python src\close_day.py %2 %3 %4 %5
) else if "%1"=="live-trade" (
    python src\live_trade.py %2 %3 %4 %5
) else if "%1"=="analyze-history" (
    python src\analyze_history.py %2
) else if "%1"=="analyze-realtime" (
    python src\analyze_realtime.py %2 %3
) else if "%1"=="inspect-chart" (
    python src\inspect_kiwoom_chart.py %2
) else if "%1"=="inspect-account" (
    python src\inspect_kiwoom_account.py
) else if "%1"=="inspect-universe" (
    python src\inspect_kiwoom_universe.py
) else if "%1"=="dashboard" (
    streamlit run src\dashboard.py
) else if "%1"=="app" (
    echo.
    echo ============================================================
    echo  Local access   : http://localhost:8501
    for /f "tokens=*" %%i in ('tailscale ip -4 2^>nul') do echo  Tailscale access: http://%%i:8501
    echo  (No port forwarding needed. Use the Tailscale address from
    echo   your other device, e.g. home PC, as long as Tailscale is
    echo   running there too.)
    echo ============================================================
    echo.
    streamlit run app.py
) else (
    echo Usage: run.bat [backtest^|main^|collect^|collect-universe^|collect-all^|collect-index^|realtime^|close-day^|live-trade^|analyze-history^|analyze-realtime^|inspect-chart^|inspect-account^|inspect-universe^|dashboard^|app] args
)

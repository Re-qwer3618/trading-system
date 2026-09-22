@"
@echo off
REM Usage: run.bat backtest 005930
REM        run.bat main 005930
REM        run.bat collect 005930 000660
REM        run.bat inspect-chart 005930
REM        run.bat inspect-account

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
) else if "%1"=="inspect-chart" (
    python src\inspect_kiwoom_chart.py %2
) else if "%1"=="inspect-account" (
    python src\inspect_kiwoom_account.py
) else (
    echo Usage: run.bat [backtest^|main^|collect^|inspect-chart^|inspect-account] symbol
)
"@ | Set-Content -Path run.bat -Encoding ASCII
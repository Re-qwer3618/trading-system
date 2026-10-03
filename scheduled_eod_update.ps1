# 장 마감 후 자동 갱신. Windows 작업 스케줄러(평일 16:00, register_eod_task.ps1로 등록)가 이 스크립트를 실행합니다.
#
# 하는 일 (순서대로):
#   1. close-day  : 오늘 실시간 수집기가 쌓은 체결 틱을 종목별 공식 분봉의 빈 구간에 이어붙이고,
#                   공식 일봉이 아직 없는 종목은 정규장 틱으로 근사 일봉을 채웁니다.
#   2. collect-index : 코스피/코스닥 지수 일봉 (대시보드 시장 차트/상대강도용)
#   3. collect-all --minute --tick : universe+관심종목 전체의 일봉/분봉(증분)/틱봉을 키움 서버에서
#                   다시 받아 옵니다(이미 있는 구간은 건드리지 않고 새 날짜만 이어붙임). 분봉은
#                   과거로 더 파고들지 않고(--minute-days 생략 = 증분만) 오늘 하루치만 채웁니다 —
#                   깊은 과거 확장은 이미 한 번 해뒀고(run.bat collect-all --minute-days max),
#                   매일 또 하면 시간만 오래 걸리고 얻는 게 없습니다.
#   4. plans (stock_analysis) : 갱신된 일봉으로 다음 거래일 매매 계획(stock_analysis\data\plans\plans_<기준일>.json)을
#                   만들고, run.bat plans --sync로 계획 종목을 관심종목에 맞춥니다. live_trade(plan_follow)가 다음 날 이 계획을
#                   실행합니다. 일봉이 다 갱신된 뒤여야 해서 collect-all 다음에 둡니다 (형제 폴더 stock_analysis가 없으면 건너뜀).
#   전체 종목(수천 개) x 일봉/분봉/틱봉이라 보통 1~3시간 걸립니다 — 다음날 새벽에도 계속 돌고
#   있어도 정상입니다. 대시보드에서 수집 작업이 이미 돌고 있으면(collect_all.py 안의 안전장치)
#   이번 실행은 조용히 건너뛰고 다음 스케줄에 다시 시도합니다.
#
# 로그: logs\eod_update_YYYYMMDD.log (매일 새 파일)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("eod_update_" + (Get-Date -Format "yyyyMMdd") + ".log")

function Write-Log($text) {
    $text | Out-File -Append -Encoding utf8 $log
}

Write-Log "=== 장마감 갱신 시작 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

try {
    & "$root\run.bat" close-day 2>&1 | Out-File -Append -Encoding utf8 $log
    Write-Log "--- close-day 완료 ---"

    # 코스피/코스닥 지수 일봉 (대시보드 시장 차트·상대강도, 백테스트 알파 계산용). 몇 초면 끝남.
    & "$root\run.bat" collect-index 2>&1 | Out-File -Append -Encoding utf8 $log
    Write-Log "--- collect-index 완료 ---"

    & "$root\run.bat" collect-all --minute --tick 2>&1 | Out-File -Append -Encoding utf8 $log
    Write-Log "--- collect-all 완료 ---"

    # 다음 거래일 매매 계획 (stock_analysis가 전략 판단, 여기서는 만들어진 계획을 다음 날 실행만 함)
    $sa = Join-Path (Split-Path -Parent $root) "stock_analysis"
    $py = $env:TRADING_PYTHON
    if (-not $py -or -not (Test-Path $py)) {
        $py = Join-Path $root "..\..\envs\miniconda3\envs\agent-py313\python.exe"
    }
    if ((Test-Path (Join-Path $sa "plans.py")) -and (Test-Path $py)) {
        Push-Location $sa
        $env:PYTHONIOENCODING = "utf-8"
        & $py plans.py 2>&1 | Out-File -Append -Encoding utf8 $log
        Pop-Location
        & "$root\run.bat" plans --sync 2>&1 | Out-File -Append -Encoding utf8 $log
        Write-Log "--- plans (매매 계획) 완료 ---"
    } else {
        Write-Log "--- plans 건너뜀: stock_analysis 또는 python 없음 ($sa) ---"
    }
} catch {
    Write-Log ("!!! 오류: " + $_.Exception.Message)
}

Write-Log "=== 장마감 갱신 종료 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

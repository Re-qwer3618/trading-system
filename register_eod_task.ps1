# scheduled_eod_update.ps1을 Windows 작업 스케줄러에 등록합니다 (평일 16:00, 1회 실행).
# 다시 실행하면 같은 이름의 작업을 덮어씁니다(시간/내용을 바꿨을 때 재등록 용도).
#
# 확인:   Get-ScheduledTask -TaskName "TradingSystem-EODUpdate" | Get-ScheduledTaskInfo
# 수동 실행(테스트): Start-ScheduledTask -TaskName "TradingSystem-EODUpdate"
# 삭제:   Unregister-ScheduledTask -TaskName "TradingSystem-EODUpdate" -Confirm:$false

$TaskName = "TradingSystem-EODUpdate"
$ScriptPath = Join-Path $PSScriptRoot "scheduled_eod_update.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 4:00PM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew
# MultipleInstances IgnoreNew: 어제 실행이 아직 안 끝났으면(전체 종목 수집은 몇 시간 걸릴 수 있음)
# 오늘 새 실행을 또 겹쳐 시작하지 않습니다 (scheduled_eod_update.ps1 안의 active_job() 안전장치와
# 별개로, 이 작업 자체가 중복 실행되는 것도 막습니다).

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "장 마감 후(평일 16:00) close-day + collect-all --minute --tick로 전체 종목 일봉/분봉/틱봉 증분 갱신" `
    -Force | Out-Null

Write-Host "등록 완료: $TaskName (평일 16:00)"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State

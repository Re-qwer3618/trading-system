# 다른 프로젝트에 원본이 있는 Claude 스킬을 이 프로젝트의 .claude\skills 아래로 연결(디렉터리 정션)합니다.
#
# chart-psychology(차트 심리분석) 스킬의 원본은 형제 프로젝트 stock_analysis에 있습니다.
# 예전엔 이 저장소에 사본을 두었는데 원본만 계속 업데이트돼 사본이 낡았습니다(v1, 규칙 번호·오류기록·
# 정량조건 없음). 그래서 사본 대신 원본을 가리키는 정션을 둡니다 — 원본을 고치면 여기서도 바로 반영됩니다.
#
# 정션은 PC마다 만들어야 합니다(git으로 옮겨지지 않음, .gitignore에 등록됨). 새 PC에서 한 번 실행하세요.
# 관리자 권한 필요 없음. 드라이브 문자와 무관(이 파일 위치 기준 상대경로).
#
#   powershell -ExecutionPolicy Bypass -File link_shared_skills.ps1

$ErrorActionPreference = 'Stop'
$skills = Join-Path $PSScriptRoot ".claude\skills"
$links = @{
    "chart-psychology" = Join-Path $PSScriptRoot "..\stock_analysis\.claude\skills\chart-psychology"
}

New-Item -ItemType Directory -Force $skills | Out-Null
foreach ($name in $links.Keys) {
    $target = [IO.Path]::GetFullPath($links[$name])
    $link = Join-Path $skills $name
    if (-not (Test-Path (Join-Path $target "SKILL.md"))) {
        Write-Host "[$name] 원본이 없습니다: $target (stock_analysis 저장소를 형제 폴더로 클론하세요)" -ForegroundColor Yellow
        continue
    }
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType -eq 'Junction') {
            Write-Host "[$name] 이미 연결됨 -> $($item.Target)"
            continue
        }
        Write-Host "[$name] 연결이 아닌 실제 폴더(옛 사본)가 있어 연결하지 않았습니다: $link" -ForegroundColor Yellow
        Write-Host "        안의 내용을 확인한 뒤 지우고 다시 실행하세요." -ForegroundColor Yellow
        continue
    }
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Host "[$name] 연결 완료 -> $target"
}

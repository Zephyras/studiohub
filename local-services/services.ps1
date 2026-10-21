<#
.SYNOPSIS
    로컬 서비스 기동/중지/상태. 사용: .\services.ps1 up|down|status|logs|pull [immich|appflowy|all]
#>
param(
    [Parameter(Position = 0)][ValidateSet("up", "down", "status", "logs", "pull")][string]$Action = "status",
    [Parameter(Position = 1)][ValidateSet("immich", "appflowy", "all")][string]$Service = "all"
)
# docker compose warnings go to stderr; with Stop, PowerShell 5.1 aborts on the first one -> Continue + exit-code check
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$dockerBin = "C:/Program Files/Docker/Docker/resources/bin"
if (-not (Get-Command docker -ErrorAction SilentlyContinue) -and (Test-Path $dockerBin)) { $env:Path += ";$dockerBin" }  # 설치 직후 새 로그온 전
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "docker 가 없다. Docker Desktop(WSL2) 을 먼저 설치: README.md §0" -ForegroundColor Red
    exit 1
}

$stacks = [ordered]@{
    immich   = @{ dir = (Join-Path $here "immich");               env = (Join-Path $here "immich\.env");               setup = ".\immich\setup.ps1" }
    appflowy = @{ dir = (Join-Path $here "appflowy\AppFlowy-Cloud"); env = (Join-Path $here "appflowy\AppFlowy-Cloud\.env"); setup = ".\appflowy\setup.ps1" }
}
$targets = if ($Service -eq "all") { @($stacks.Keys) } else { @($Service) }

foreach ($name in $targets) {
    $s = $stacks[$name]
    Write-Host "=== $name ($Action)" -ForegroundColor Cyan
    if (-not (Test-Path $s.env)) {
        Write-Host "  .env 없음 → 먼저 $($s.setup)" -ForegroundColor Yellow
        continue
    }
    Push-Location $s.dir
    try {
        switch ($Action) {
            "up"     { docker compose up -d --remove-orphans }
            "down"   { docker compose down }
            "status" { docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" }
            "logs"   { docker compose logs --tail 100 }
            "pull"   { docker compose pull }
        }
        if ($LASTEXITCODE -ne 0) { Write-Host "  ! $name $Action 실패 (exit $LASTEXITCODE)" -ForegroundColor Red; $failed = $true }
    } finally { Pop-Location }
}
if ($failed) { exit 1 }

if ($Action -eq "up") {
    Write-Host ""
    Write-Host "Immich  : http://localhost:2283" -ForegroundColor Green
    Write-Host "AppFlowy: http://localhost:8070  (콘솔 /console)" -ForegroundColor Green
    Write-Host "점검    : python studiohub.py doctor" -ForegroundColor Green
}

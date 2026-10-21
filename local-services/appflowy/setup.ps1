<#
.SYNOPSIS
    AppFlowy-Cloud 저장소를 클론하고 로컬용 .env 를 만든다 (재실행 시 .env 보존).
.PARAMETER Ref
    git 태그/브랜치 (기본 main). 운영 고정은 릴리스 태그 권장.
.PARAMETER Port
    nginx HTTP 포트 (기본 8070). 80 은 다른 로컬 서버와 겹치기 쉬워 피한다.
.PARAMETER AdminEmail / AdminPassword
    GoTrue 관리자 계정. 비밀번호를 안 주면 무작위 생성해 출력한다.
#>
param(
    [string]$Ref = "main",
    [int]$Port = 8070,
    [int]$TlsPort = 8443,
    [string]$AdminEmail = "admin@example.com",
    [string]$AdminPassword = "",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Join-Path $here "AppFlowy-Cloud"

if (-not (Test-Path $repo)) {
    Write-Host "▶ git clone AppFlowy-Cloud ($Ref)" -ForegroundColor Cyan
    git clone --depth 1 --branch $Ref https://github.com/AppFlowy-IO/AppFlowy-Cloud.git $repo
} else {
    Write-Host "• 클론이 이미 있다: $repo (갱신은 git -C `"$repo`" pull)" -ForegroundColor Yellow
}

Copy-Item (Join-Path $here "docker-compose.override.yml") (Join-Path $repo "docker-compose.override.yml") -Force   # MinIO 대체 이미지 등 (매번 최신으로)

$envPath = Join-Path $repo ".env"
if ((Test-Path $envPath) -and -not $Force) {
    Write-Host "• .env 가 이미 있어 그대로 둔다 (-Force 로 다시 작성)" -ForegroundColor Yellow
    exit 0
}

function New-Secret([int]$n = 32) { -join ((48..57 + 65..90 + 97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ }) }
if (-not $AdminPassword) { $AdminPassword = New-Secret 20 }

$base = "http://localhost:$Port"
$overrides = [ordered]@{
    FQDN                         = "localhost"
    SCHEME                       = "http"
    WS_SCHEME                    = "ws"
    APPFLOWY_BASE_URL            = $base
    APPFLOWY_WEBSOCKET_BASE_URL  = "ws://localhost:$Port/ws/v2"
    APPFLOWY_WEB_URL             = $base
    NGINX_PORT                   = "$Port"
    NGINX_TLS_PORT               = "$TlsPort"
    POSTGRES_PASSWORD            = (New-Secret 24)
    GOTRUE_JWT_SECRET            = (New-Secret 48)
    GOTRUE_ADMIN_EMAIL           = $AdminEmail
    GOTRUE_ADMIN_PASSWORD        = $AdminPassword
    GOTRUE_MAILER_AUTOCONFIRM    = "true"     # SMTP 없이 가입 즉시 확인
    GOTRUE_DISABLE_SIGNUP        = "false"
    AWS_ACCESS_KEY               = (New-Secret 20)
    AWS_SECRET                   = (New-Secret 40)
    PGADMIN_DEFAULT_EMAIL        = $AdminEmail
    PGADMIN_DEFAULT_PASSWORD     = (New-Secret 20)
    RUST_LOG                     = "info"
}

$lines = Get-Content (Join-Path $repo "deploy.env") -Encoding UTF8
$seen = @{}
$out = foreach ($line in $lines) {
    $m = [regex]::Match($line, '^\s*([A-Z0-9_]+)=')
    if ($m.Success -and $overrides.Contains($m.Groups[1].Value)) {
        $k = $m.Groups[1].Value; $seen[$k] = $true
        "$k=$($overrides[$k])"
    } else { $line }
}
$missing = $overrides.Keys | Where-Object { -not $seen[$_] }
if ($missing) { $out += ""; $out += "# studiohub 추가"; $out += ($missing | ForEach-Object { "$_=$($overrides[$_])" }) }
[IO.File]::WriteAllLines($envPath, $out, (New-Object Text.UTF8Encoding $false))

Write-Host "✓ .env 작성: $envPath" -ForegroundColor Green
Write-Host "  관리자: $AdminEmail / $AdminPassword  (studiohub.env 의 APPFLOWY_EMAIL/PASSWORD 로도 쓴다)" -ForegroundColor Green
Write-Host "다음: ..\services.ps1 up appflowy  →  $base (콘솔 $base/console)" -ForegroundColor Green

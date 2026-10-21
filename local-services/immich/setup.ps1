<#
.SYNOPSIS
    Immich 릴리스 compose 파일을 내려받고 .env 를 작성한다 (한 번만, 재실행 시 .env 는 보존).
.PARAMETER Library
    업로드 원본 저장 위치. 기본 <이 폴더>\data\library — 용량이 큰 드라이브의 절대 경로 권장
.PARAMETER Version
    IMMICH_VERSION (기본 release = 최신). 고정하려면 v3.x.y
.PARAMETER Force
    .env 를 다시 쓴다 (DB 비밀번호가 바뀌면 기존 DB 볼륨과 안 맞으니 주의)
#>
param(
    [string]$Library = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "data\library"),
    [string]$Version = "release",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$base = "https://github.com/immich-app/immich/releases/latest/download"
foreach ($f in "docker-compose.yml", "example.env", "hwaccel.ml.yml") {
    Write-Host "▶ 내려받기 $f" -ForegroundColor Cyan
    Invoke-WebRequest -Uri "$base/$f" -OutFile (Join-Path $here $f) -UseBasicParsing
}

if ((Test-Path ".env") -and -not $Force) {
    Write-Host "• .env 가 이미 있어 그대로 둔다 (-Force 로 다시 작성)" -ForegroundColor Yellow
} else {
    New-Item -ItemType Directory -Force -Path $Library | Out-Null
    $pw = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    $lib = $Library -replace '\\', '/'
    $env_text = @"
# studiohub 가 만든 Immich .env — 원본 변수 설명: https://docs.immich.app/install/environment-variables
UPLOAD_LOCATION=$lib
# DB 는 docker-compose.override.yml 이 볼륨(immich-pgdata)으로 바꾼다. 아래 값은 자리만 채운다.
DB_DATA_LOCATION=./postgres
TZ=Asia/Seoul
IMMICH_VERSION=$Version
DB_PASSWORD=$pw
DB_USERNAME=postgres
DB_DATABASE_NAME=immich
"@
    [IO.File]::WriteAllText((Join-Path $here ".env"), $env_text, (New-Object Text.UTF8Encoding $false))
    Write-Host "✓ .env 작성 (라이브러리 $Library)" -ForegroundColor Green
}
Write-Host "다음: ..\services.ps1 up immich  →  http://localhost:2283 에서 관리자 계정·API 키" -ForegroundColor Green

# local-services — Immich · AppFlowy Cloud 를 Docker 로 띄우기

Windows + Docker Desktop(WSL2) 기준 스크립트입니다. Linux/macOS 는 각 프로젝트 공식 문서의 compose 절차를 그대로 쓰면 되고, 덮어쓰기 파일(`docker-compose.override.yml`)만 가져다 쓰면 됩니다.

| 폴더 | 서비스 | 주소 |
|---|---|---|
| `immich/` | Immich — 그림 라이브러리, CLIP 검색, GPU(CUDA) 가속 | http://localhost:2283 |
| `appflowy/` | AppFlowy Cloud — 그리드·문서·웹 | http://localhost:8070 (관리 콘솔 `/console`) |

## 전제

- Docker Desktop 4.x, WSL2 백엔드. GPU 를 쓰려면 Windows NVIDIA 드라이버 545 이상(컨테이너 툴킷은 Docker Desktop 에 포함).
- 메모리: AppFlowy 스택 약 3~4 GB, Immich(ML 포함) 2~4 GB.
- PowerShell 5.1 에서는 스크립트를 UTF-8 BOM 으로 저장해야 한글이 깨지지 않습니다(이 저장소 파일은 BOM 포함).

## 설치·실행

```powershell
cd local-services
.\immich\setup.ps1 -Library D:\immich-library   # 릴리스 compose·env 내려받고 .env 작성
.\appflowy\setup.ps1 -AdminEmail you@example.com # AppFlowy-Cloud 클론 + .env 생성 (관리자 비밀번호는 출력됨)
.\services.ps1 up          # 둘 다 (up immich | up appflowy)
.\services.ps1 status
.\services.ps1 logs appflowy
.\services.ps1 down
```

첫 기동 뒤

- **Immich**: 웹에서 관리자 계정 생성 → 계정 설정 → API 키. 한국어 검색은 관리 → 설정 → 머신러닝 → 스마트 검색 모델을 다국어 모델로 바꾸고 작업 → Smart Search 재실행.
- **AppFlowy**: GoTrue **시스템 관리자(setup 의 AdminEmail)는 워크스페이스를 받지 못합니다.** 일반 계정을 하나 만들어 쓰세요(`POST /gotrue/signup {email,password}` 또는 웹 가입). 무료 셀프호스팅은 사용자 1명 제한입니다.
- AppFlowy 웹 로그인은 기본이 이메일 매직링크라 SMTP 가 없으면 실패합니다. 로그인 화면의 "Sign in with password" 를 쓰거나, `studiohub appflowy login-link` 로 일회용 링크를 만드세요. 실제 메일을 쓰려면 `.env` 의 `GOTRUE_SMTP_*` 를 채우고 재기동.

## 데이터 위치

| 무엇 | 위치 |
|---|---|
| Immich 업로드 원본 | `.env` 의 `UPLOAD_LOCATION` |
| Immich DB | Docker 볼륨 `immich-pgdata` (Windows 바인드 마운트는 Postgres 권한 문제가 있어 볼륨) |
| AppFlowy 코드·compose | `appflowy/AppFlowy-Cloud/` (클론, 태그 고정은 `setup.ps1 -Ref`) |
| AppFlowy DB·MinIO | Docker 볼륨 `postgres_data`, `minio_data` |

## 알려진 문제

- `minio/minio` 공개 이미지가 2026-09 에 Docker Hub·quay.io 에서 사라졌습니다(pull access denied). `appflowy/docker-compose.override.yml` 이 Chainguard 빌드(`cgr.dev/chainguard/minio`)로 바꿉니다. `setup.ps1` 이 클론 안으로 복사합니다.
- `docker compose` 는 경고를 stderr 로 쓰므로 PowerShell `$ErrorActionPreference = "Stop"` 이면 첫 경고에서 끊깁니다. `services.ps1` 은 Continue + 종료 코드 검사로 돕니다.
- 설치 직후 새 로그온 전에는 `docker` 가 PATH 에 없을 수 있습니다. `services.ps1` 은 Docker Desktop 설치 경로를 스스로 찾습니다.

## 포트

| 포트 | 서비스 |
|---|---|
| 2283 | Immich |
| 8070 | AppFlowy nginx (웹 `/`, API `/api`, GoTrue `/gotrue`, WS `/ws`, 콘솔 `/console`) |
| 8443 | AppFlowy TLS (미사용) |

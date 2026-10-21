# 구조와 API 메모

## 모듈

| 파일 | 역할 |
|---|---|
| `studiohub/http_client.py` | urllib 기반 JSON·multipart·다운로드 |
| `studiohub/config.py` | `studiohub.env` → 환경 변수 우선 설정, 자산 레지스트리 |
| `studiohub/immich_client.py` | Immich REST (`x-api-key`) — 업로드·중복 검사·앨범·태그·검색·공유 링크 |
| `studiohub/appflowy_client.py` | AppFlowy Cloud REST — GoTrue 로그인·워크스페이스·그리드 필드/행·페이지·블록 |
| `studiohub/assets.py` | 자산 폴더 스캔, 사이드카, 태그 규칙, sha1 장부 |
| `studiohub/markdown_blocks.py` | Markdown → AppFlowy 블록 |
| `studiohub/directives.py` | 지시 로그 Markdown 표 파서 |
| `studiohub/dex.py`, `dex_cli.py` | 도감 프로필 (게임 데이터 파서, 항목 조립, 앨범 분류, 그리드·그림판) |
| `tests/fakes.py` | 가짜 Immich·AppFlowy HTTP 서버 — 실제 경로·봉투 형식만 흉내 |

## Immich (OpenAPI v3.3 기준)

- 인증 헤더 `x-api-key`.
- 업로드 `POST /api/assets` (multipart, 헤더 `x-immich-checksum` = SHA1 hex). 중복이면 200 `duplicate`. 미리 거를 때 `POST /api/assets/bulk-upload-check`.
- 앨범 `GET/POST /api/albums`, `PUT /api/albums/{id}/assets`, `PATCH /api/albums/{id}`. 태그 `PUT /api/tags`(경로형 이름 `a/b` 는 계층), `PUT /api/tags/assets`.
- 검색 `POST /api/search/smart`(CLIP 자연어, `queryAssetId` 로 유사 이미지), `POST /api/search/metadata`.
- 공유 링크 `POST /api/shared-links`(INDIVIDUAL + assetIds), `PUT /api/shared-links/{id}/assets`. `…/thumbnail?size=preview&key=<key>` 는 로그인 없이 열린다.

## AppFlowy Cloud

- 로그인 `POST /gotrue/token?grant_type=password` → Bearer JWT. 첫 로그인 뒤 `GET /api/user/verify/{token}` 을 불러야 사용자가 등록된다.
- 응답 봉투 `{code, message, data}`, `code == 0` 성공.
- 그리드: `GET /api/workspace/{ws}/database`, `…/database/{db}/fields`(POST 로 필드 추가, `field_type` 숫자: 0 텍스트·1 숫자·6 URL·14 미디어), `…/row` (POST 생성, PUT `pre_hash` upsert), `…/row/detail?ids&with_doc`.
- 페이지: `POST /api/workspace/{ws}/page-view {parent_view_id, layout(0 문서·1 그리드), name, page_data}` — `page_data` 는 `{"type":"page","children":[SerdeBlock…]}`. 블록 `{"type":"paragraph","data":{"delta":[{"insert":"…"}]},"children":[]}`, 외부 이미지 `{"type":"image","data":{"url":…,"image_type":2}}`.
- API 로 만든 그리드의 데이터베이스 id 는 `GET …/page-view/{view}` 응답의 `view.extra.database_id`.
- 미디어 셀 값: `{"files":[{"id","name","url","upload_type":1,"file_type":1}]}` (1 = 외부 URL, 1 = 이미지).
- 행 문서 `with_doc` 는 서식 없는 글만 돌려준다.

### 행 id 와 웹 클라이언트

서버 upsert 는 행 id 를 `sha256(workspace_id ‖ database_id ‖ pre_hash)[:16]` 그대로 쓴다. 버전 비트가 없어 UUID v4 가 아니고, AppFlowy Web 0.19.2x 는 `Invalid Y.Doc guid … must be a valid UUID v4` 로 그 행을 동기화하지 않는다(무한 로딩). `appflowy_client.v4_safe_pre_hash` 가 같은 해시를 로컬에서 계산해 v4 모양이 나올 때까지 `key#n` 을 붙인다(평균 60여 번, 결정적).

## 로컬 서비스 스택

`local-services/` 의 PowerShell 스크립트는 Docker Desktop(WSL2) 에서 두 스택을 내려받아 띄운다. MinIO 공개 이미지가 2026-09 에 레지스트리에서 사라져 AppFlowy 스택은 Chainguard 빌드로 대체한다(`appflowy/docker-compose.override.yml`).

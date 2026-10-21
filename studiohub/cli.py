"""studiohub CLI.

  studiohub doctor
  studiohub scan <project> [--slot s …]
  studiohub immich sync <project> [--slot s …] [--dry-run] [--no-description]
  studiohub immich find "<자연어>" [--project P] [--slot S] [-n 20] [--pull]
  studiohub immich similar <asset-id> [-n 20] [--project P]
  studiohub immich favorites <project> [--slot S]           # Immich 에서 ♥/별점 준 것 = 승격 후보
  studiohub appflowy whoami | databases | fields <db> | pages
  studiohub appflowy push-directives <project> --db "<이름>"
  studiohub appflowy push-assets <project> --db "<이름>" [--slot library]
  studiohub appflowy page --parent <뷰 이름|id> --title "<제목>" --from <md 파일>
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .appflowy_client import AppFlowyClient, AppFlowyError
from .assets import LocalAsset, SyncLedger, scan
from .config import Settings
from .directives import load_directives
from . import dex_cli
from .http_client import ApiError
from .immich_client import ImmichClient
from .markdown_blocks import markdown_to_blocks


class CliError(Exception):
    pass


def out(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 공통
def immich(s: Settings) -> ImmichClient:
    if not s.immich_api_key:
        raise CliError("IMMICH_API_KEY 가 없다. studiohub.env 또는 환경 변수로 준다 (Immich 웹 → 계정 설정 → API 키)")
    return ImmichClient(s.immich_url, s.immich_api_key)


def appflowy(s: Settings) -> tuple[AppFlowyClient, str]:
    c = AppFlowyClient(s.appflowy_url, s.appflowy_email, s.appflowy_password, token_cache=s.state_dir / "appflowy_token.json")
    return c, c.workspace_id(s.appflowy_workspace)


def ledger(s: Settings) -> SyncLedger:
    return SyncLedger(s.state_dir / "immich_sync.json")


def fmt_row(cols: list[str], widths: list[int]) -> str:
    return "  ".join(str(c)[:w].ljust(w) for c, w in zip(cols, widths))


# ---------------------------------------------------------------- doctor
def cmd_doctor(a, s: Settings) -> int:
    out(f"studiohub {__version__}")
    out(f"registry   : {s.registry_path} ({'있음' if s.registry_path.exists() else '없음'})")
    out(f"state_dir  : {s.state_dir}")
    docker = shutil.which("docker")
    if not docker:  # 설치 직후 새 로그온 전에는 PATH 에 없다
        for cand in (Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),):
            if cand.exists():
                docker = str(cand)
                break
    out(f"docker     : {docker or '없음 — Docker Desktop(WSL2) 설치 필요: local-services/README.md'}")
    if docker:
        try:
            r = subprocess.run([docker, "ps", "--format", "{{.Names}}\t{{.Status}}"], capture_output=True, text=True, timeout=20)
            names = [ln for ln in r.stdout.splitlines() if ln]
            out(f"containers : {len(names)}개 실행 중" + ("".join("\n             " + n for n in names if "immich" in n or "appflowy" in n) if names else ""))
        except Exception as e:  # noqa: BLE001
            out(f"containers : 확인 실패 ({e})")
    out(f"immich     : {s.immich_url} (key {'있음' if s.immich_api_key else '없음'})")
    try:
        c = ImmichClient(s.immich_url, s.immich_api_key)
        if c.ping():
            out(f"             ping OK · v{c.version()}" + (f" · 사용자 {c.me().get('email')}" if s.immich_api_key else ""))
        else:
            out("             응답 없음 (서비스 내려감?)")
    except ApiError as e:
        out(f"             오류 {e.status}: {e.body[:120]}")
    out(f"appflowy   : {s.appflowy_url} (계정 {s.appflowy_email or '없음'})")
    if s.appflowy_email and s.appflowy_password:
        try:
            c, ws = appflowy(s)
            out(f"             로그인 OK · workspace {ws}")
        except (ApiError, AppFlowyError) as e:
            out(f"             로그인 실패: {e}")
    return 0


# ---------------------------------------------------------------- scan
def cmd_scan(a, s: Settings) -> int:
    project = s.resolve_project(a.project)
    assets = scan(s.assets_root(), project, a.slot or s.slots(), include=a.include)
    led = ledger(s)
    widths = [12, 48, 9, 6]
    out(fmt_row(["slot", "rel", "KB", "sync"], widths))
    for x in assets:
        synced = "O" if (x.sha1 and led.get(x.sha1)) else "-"
        out(fmt_row([x.slot, x.rel, f"{x.size // 1024}", synced], widths))
    out(f"\n{project}: {len(assets)}개 (태그 예: {assets[0].tags() if assets else '—'})")
    return 0


# ---------------------------------------------------------------- immich
def cmd_immich_sync(a, s: Settings) -> int:
    project = s.resolve_project(a.project)
    slots = a.slot or s.slots()
    assets = scan(s.assets_root(), project, slots, include=a.include)
    if not assets:
        out(f"{project}: 올릴 파일이 없다 (슬롯 {slots})")
        return 0
    if a.max_files and len(assets) > a.max_files:
        raise CliError(
            f"{project}: 파일 {len(assets)}개가 --max-files {a.max_files} 를 넘는다. "
            "--slot 으로 좁히거나 --include '<글롭>'(예: '*montage*') 으로 고르거나 --max-files 0 으로 해제"
        )
    led = ledger(s)
    for x in assets:
        x.ensure_sha1()
    todo = [x for x in assets if not led.get(x.sha1) or a.force]
    out(f"{project}: 스캔 {len(assets)} · 장부에 없음 {len(todo)}")
    if a.dry_run:
        for x in todo[:50]:
            out(f"  + {x.slot}/{x.rel}  {x.tags()}")
        if len(todo) > 50:
            out(f"  … {len(todo) - 50}개 더")
        return 0
    c = immich(s)
    uploaded = dup = failed = 0
    by_album: dict[str, list[str]] = {}
    by_tag: dict[str, list[str]] = {}
    descriptions: list[tuple[str, str]] = []
    batch = 100
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        check = c.bulk_upload_check((x.device_asset_id, x.sha1) for x in chunk)
        for x in chunk:
            r = check.get(x.device_asset_id, {})
            asset_id = r.get("assetId") if r.get("action") == "reject" and r.get("reason") == "duplicate" else None
            if r.get("action") == "reject" and not asset_id:
                out(f"  ! 거부 {x.rel}: {r.get('reason')}")
                failed += 1
                continue
            try:
                if asset_id:
                    dup += 1
                    status = "duplicate"
                else:
                    res = c.upload(x.path, device_asset_id=x.device_asset_id, sha1_hex=x.sha1)
                    asset_id, status = res["id"], res.get("status", "created")
                    uploaded += 1
            except ApiError as e:
                out(f"  ! 업로드 실패 {x.rel}: {e.status} {e.body[:100]}")
                failed += 1
                continue
            led.put(x, asset_id, status)
            by_album.setdefault(x.album, []).append(asset_id)
            for t in x.tags():
                by_tag.setdefault(t, []).append(asset_id)
            if not a.no_description:
                descriptions.append((asset_id, x.description()))
        led.save()
        out(f"  … {min(i + batch, len(todo))}/{len(todo)} (새 {uploaded} · 중복 {dup} · 실패 {failed})")
    # 앨범·태그·설명
    for album, ids in by_album.items():
        alb = c.ensure_album(album, description=f"studiohub · 70_ASSETS/{album}")
        c.add_to_album(alb["id"], ids)
    tag_ids = c.upsert_tags(by_tag)
    for t, ids in by_tag.items():
        if t in tag_ids:
            c.tag_assets([tag_ids[t]], ids)
    for asset_id, desc in descriptions:
        try:
            c.update_asset(asset_id, description=desc)
        except ApiError:
            pass
    out(f"완료: 새 업로드 {uploaded} · 서버 중복 {dup} · 실패 {failed} · 앨범 {len(by_album)} · 태그 {len(by_tag)}")
    return 1 if failed else 0


def _print_hits(items: list[dict], s: Settings, led: SyncLedger) -> None:
    local = led.by_asset_id()
    widths = [38, 28, 10, 44]
    out(fmt_row(["asset id", "파일", "♥/별점", "로컬(장부)"], widths))
    for it in items:
        loc = local.get(it["id"])
        loc_s = f"{loc['project']}/{loc['slot']}/{loc['rel']}" if loc else "-"
        fav = ("♥" if it.get("isFavorite") else "") + (str((it.get("exifInfo") or {}).get("rating") or ""))
        out(fmt_row([it["id"], it.get("originalFileName", ""), fav, loc_s], widths))


def _filters(a, s: Settings, c: ImmichClient) -> dict:
    kw: dict = {}
    if getattr(a, "project", None):
        project = s.resolve_project(a.project)
        names = [f"project/{project}"] + ([f"slot/{a.slot}"] if a.slot else [])
        tag_ids = c.upsert_tags(names)  # 이미 있으면 그대로 돌아온다
        kw["tag_ids"] = [tag_ids[n] for n in names if n in tag_ids]
    return kw


def _pull(c: ImmichClient, items: list[dict], s: Settings, project: str | None) -> None:
    if not project:
        out("--pull 은 --project 가 필요하다 (inbox/from-immich/ 에 받는다)")
        return
    dest = s.project_dir(s.resolve_project(project)) / "inbox" / "from-immich"
    for it in items:
        p = c.download_original(it["id"], dest / it.get("originalFileName", it["id"]))
        out(f"  ↓ {p}")


def cmd_immich_find(a, s: Settings) -> int:
    c = immich(s)
    res = c.search_smart(a.query, size=a.n, **_filters(a, s, c))
    items = res.get("items", [])
    out(f"'{a.query}' → {len(items)}개 (전체 {res.get('total', '?')})")
    _print_hits(items, s, ledger(s))
    if a.pull:
        _pull(c, items, s, a.project)
    return 0


def cmd_immich_similar(a, s: Settings) -> int:
    c = immich(s)
    res = c.search_smart(query_asset_id=a.asset_id, size=a.n, **_filters(a, s, c))
    items = [it for it in res.get("items", []) if it["id"] != a.asset_id]
    out(f"{a.asset_id} 와 비슷한 {len(items)}개")
    _print_hits(items, s, ledger(s))
    return 0


def cmd_immich_favorites(a, s: Settings) -> int:
    c = immich(s)
    kw = _filters(a, s, c)
    items = list(c.iter_metadata(isFavorite=True, tagIds=kw.get("tag_ids"), withExif=True))
    out(f"♥ {len(items)}개 — library 로 승격할 후보")
    _print_hits(items, s, ledger(s))
    return 0


def cmd_immich_share(a, s: Settings) -> int:
    """프로젝트의 동기화된 자산 전부를 담은 Immich 공유 링크(로그인 없이 열림). 키는 .state/immich_share.json 에 둔다."""
    import json
    project = s.resolve_project(a.project)
    c = immich(s)
    ids = [v["asset_id"] for _, v in ledger(s).by_project(project) if v.get("asset_id")]
    if not ids:
        raise CliError(f"장부에 {project} 자산이 없다 — 먼저 `studiohub immich sync {project}`")
    res = c.ensure_shared_link(f"studiohub share {project}", ids)
    p = s.state_dir / "immich_share.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[project] = {"id": res["id"], "key": res["key"], "count": res["count"]}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    out(f"{project}: 공유 링크 자산 {res['count']}개 (이번에 추가 {res['added']}) · 예: {c.thumb_url(ids[0], res['key'], 'thumbnail')}")
    out("이 키는 그 자산들을 로그인 없이 보여 준다(localhost 전용). AppFlowy 도감 그림은 `dex board`·`dex gallery` 가 이 키로 넣는다.")
    return 0


# ---------------------------------------------------------------- appflowy
def cmd_af_whoami(a, s: Settings) -> int:
    c, ws = appflowy(s)
    for w in c.workspaces():
        mark = "*" if w["workspace_id"] == ws else " "
        out(f"{mark} {w.get('workspace_name')}  {w['workspace_id']}  (owner {w.get('owner_name')})")
    return 0


def cmd_af_databases(a, s: Settings) -> int:
    c, ws = appflowy(s)
    for db in c.databases(ws):
        names = ", ".join(v.get("name", "?") for v in db.get("views") or [])
        out(f"{db['id']}  {names}")
    return 0


def cmd_af_fields(a, s: Settings) -> int:
    c, ws = appflowy(s)
    db = c.database_id(ws, a.db)
    for f in c.fields(ws, db):
        out(f"{'*' if f.get('is_primary') else ' '} {f.get('name')}  [{f.get('field_type')}]  {f.get('id')}")
    return 0


def cmd_af_pages(a, s: Settings) -> int:
    c, ws = appflowy(s)

    def walk(v: dict, depth: int = 0):
        out(f"{'  ' * depth}{v.get('name') or '(이름 없음)'}  [{v.get('layout')}] {v.get('view_id')}")
        for ch in v.get("children") or []:
            walk(ch, depth + 1)

    walk(c.folder(ws, depth=a.depth))
    return 0


FIELD_KIND = {"번호": "number", "Immich": "url", "앨범": "url", "대표": "url", "그림": "media"}  # 자동 생성 열 타입 (나머지는 text)


def _cells_for(c: AppFlowyClient, ws: str, db: str, wanted: dict[str, object], primary: str, create: bool) -> tuple[dict, list[str]]:
    """wanted 의 키를 그리드 열에 맞춘다. primary 키는 그리드의 기본 열 이름으로 바꾸고,
    없는 열은 create 면 만들고(텍스트·번호·URL) 아니면 건너뛴다."""
    fields = c.fields(ws, db)
    names = {f["name"] for f in fields}
    pname = c.primary_field(fields) or primary
    cells: dict[str, object] = {}
    missing: list[str] = []
    for k, v in wanted.items():
        if k == primary:
            cells[pname] = v
        elif k in names:
            cells[k] = v
        elif create:
            c.add_field(ws, db, k, FIELD_KIND.get(k, "text"))
            names.add(k)
            cells[k] = v
        else:
            missing.append(k)
    return cells, missing


def cmd_af_push_directives(a, s: Settings) -> int:
    project = a.project
    ds = load_directives(s.vault_dir, project)
    c, ws = appflowy(s)
    db = c.database_id(ws, a.db)
    missing_all: set[str] = set()
    n = 0
    for d in ds:
        wanted = {"지시": d.text, "번호": d.number, "프로젝트": project, "날짜": d.date, "상태": d.status, "결과": d.result}
        cells, missing = _cells_for(c, ws, db, wanted, "지시", create=not a.no_create_fields and not a.dry_run)
        missing_all |= set(missing)
        if a.dry_run:
            out(f"  {project}#{d.number}: {cells}")
            continue
        c.upsert_row(ws, db, pre_hash=f"directive:{project}:{d.number}", cells=cells)
        n += 1
    if missing_all:
        out(f"그리드에 없어서 건너뛴 열: {sorted(missing_all)}")
    out(f"{project}: 지시 {len(ds)}개 중 {n}개 upsert → AppFlowy '{a.db}'")
    return 0


def cmd_af_push_assets(a, s: Settings) -> int:
    project = s.resolve_project(a.project)
    led = ledger(s)
    rows = led.by_project(project, a.slot)
    if not rows:
        out(f"장부에 {project} 자산이 없다 — 먼저 `studiohub immich sync {project}`")
        return 1
    c, ws = appflowy(s)
    db = c.database_id(ws, a.db)
    status_by_slot = {"library": "승인", "inbox": "후보", "calibration": "실험", "concept": "컨셉", "export": "내보냄"}
    from .appflowy_client import media_cell
    from .dex_cli import share_key
    key = share_key(s, project)
    if not key:
        out(f"(그림 열은 건너뜀 — `studiohub immich share {project}` 뒤에 다시 push 하면 썸네일이 들어간다)")
    n = 0
    missing_all: set[str] = set()
    for sha1, r in rows:
        wanted = {
            "자산": r["rel"],
            "프로젝트": project,
            "슬롯": r["slot"],
            "상태": status_by_slot.get(r["slot"], r["slot"]),
            "Immich": f"{s.immich_url}/photos/{r['asset_id']}",
            "sha1": sha1,
        }
        if key:
            wanted["그림"] = media_cell([(r["rel"].rsplit("/", 1)[-1], f"{s.immich_url}/api/assets/{r['asset_id']}/thumbnail?size=preview&key={key}")])
        cells, missing = _cells_for(c, ws, db, wanted, "자산", create=not a.no_create_fields and not a.dry_run)
        missing_all |= set(missing)
        if a.dry_run:
            out(f"  {cells}")
            continue
        c.upsert_row(ws, db, pre_hash=f"asset:{project}:{r['slot']}:{r['rel']}", cells=cells)
        n += 1
    if missing_all:
        out(f"그리드에 없어서 건너뛴 열: {sorted(missing_all)}")
    out(f"{project}: 자산 {len(rows)}개 중 {n}개 upsert → AppFlowy '{a.db}'")
    return 0


def _appflowy_env(s: Settings) -> dict[str, str]:
    import os
    from .config import WORKSPACE_ROOT, load_env_file
    path = Path(os.environ.get("APPFLOWY_ENV_FILE") or s.extra.get("APPFLOWY_ENV_FILE") or (WORKSPACE_ROOT / "40_TOOLS" / "local-services" / "appflowy" / "AppFlowy-Cloud" / ".env"))
    env = load_env_file(path)
    if "GOTRUE_JWT_SECRET" not in env:
        raise CliError(f"AppFlowy .env 에서 GOTRUE_JWT_SECRET 을 못 읽음: {path} (APPFLOWY_ENV_FILE 로 지정)")
    return env


def service_jwt(secret: str, ttl: int = 600) -> str:
    """GoTrue 관리자 API 용 JWT (HS256, role=supabase_admin) — 메일 서버 없이 로그인 링크를 만들 때 쓴다."""
    import base64
    import hashlib
    import hmac
    import json as _json
    import time

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    head = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(_json.dumps({"role": "supabase_admin", "sub": "studiohub", "exp": int(time.time()) + ttl}).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def cmd_af_login_link(a, s: Settings) -> int:
    """웹 로그인용 일회용 매직링크 — SMTP 가 없어도 된다. 기본 계정 = APPFLOWY_EMAIL."""
    from .http_client import Http

    email = a.email or s.appflowy_email
    if not email:
        raise CliError("계정이 없다: --email 또는 APPFLOWY_EMAIL")
    env = _appflowy_env(s)
    http = Http(s.appflowy_url, {"Authorization": f"Bearer {service_jwt(env['GOTRUE_JWT_SECRET'])}"})
    res = http.post_json("/gotrue/admin/generate_link", {"type": "magiclink", "email": email, "redirect_to": s.appflowy_url + "/login"})
    link = res.get("action_link") if isinstance(res, dict) else None
    if not link:
        raise CliError(f"링크를 못 받음: {str(res)[:200]}")
    out(f"{email} 로그인 링크 (일회용, 브라우저에서 연다):")
    out(link)
    if a.open:
        import webbrowser
        webbrowser.open(link)
    return 0


def cmd_af_trash(a, s: Settings) -> int:
    c, ws = appflowy(s)
    n = 0
    for name in a.views:
        v = c.find_view(ws, name)
        if not v:
            out(f"  못 찾음: {name}")
            continue
        c.trash_page(ws, v["view_id"])
        out(f"  휴지통으로: {v.get('name')} ({v['view_id']})")
        n += 1
    out(f"{n}개 페이지를 휴지통으로 옮김 (복구는 AppFlowy 휴지통)")
    return 0


def cmd_af_grid(a, s: Settings) -> int:
    c, ws = appflowy(s)
    parent = c.find_view(ws, a.parent)
    if not parent:
        raise CliError(f"상위 뷰를 못 찾음: {a.parent} (`studiohub appflowy pages` 로 확인)")
    fields: list[tuple[str, str]] = []
    for spec in (a.fields.split(",") if a.fields else []):
        name, _, kind = spec.strip().partition(":")
        if name:
            fields.append((name, kind or "text"))
    view_id, db = c.create_grid(ws, parent["view_id"], a.title, fields)
    out(f"그리드 생성: {a.title} (db {db}) → {c.web_url(ws, view_id)}")
    out("열: " + ", ".join(f"{f['name']}{'*' if f.get('is_primary') else ''}" for f in c.fields(ws, db)))
    return 0


def cmd_af_page(a, s: Settings) -> int:
    md = Path(a.src).read_text(encoding="utf-8")
    blocks = markdown_to_blocks(md)
    if a.dry_run:
        out(f"블록 {len(blocks)}개: " + ", ".join(b["type"] for b in blocks[:20]))
        return 0
    c, ws = appflowy(s)
    parent = c.find_view(ws, a.parent)
    if not parent:
        raise CliError(f"상위 뷰를 못 찾음: {a.parent} (`studiohub appflowy pages` 로 확인)")
    view_id = c.create_page(ws, parent["view_id"], a.title, blocks)
    out(f"페이지 생성: {a.title} → {c.web_url(ws, view_id)}")
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="studiohub", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env-file", help="studiohub.env 대신 쓸 파일")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sc = sub.add_parser("scan")
    sc.add_argument("project")
    sc.add_argument("--slot", action="append")
    sc.add_argument("--include", action="append", help="상대 경로 글롭 (여러 번 가능)")
    sc.set_defaults(fn=cmd_scan)

    im = sub.add_parser("immich").add_subparsers(dest="sub", required=True)
    x = im.add_parser("sync")
    x.add_argument("project")
    x.add_argument("--slot", action="append")
    x.add_argument("--include", action="append", help="상대 경로 글롭 (여러 번 가능)")
    x.add_argument("--max-files", type=int, default=5000, help="이보다 많으면 중단 (0 = 제한 없음)")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--force", action="store_true", help="장부를 무시하고 다시 확인·업로드")
    x.add_argument("--no-description", action="store_true")
    x.set_defaults(fn=cmd_immich_sync)
    x = im.add_parser("find")
    x.add_argument("query")
    x.add_argument("--project")
    x.add_argument("--slot")
    x.add_argument("-n", type=int, default=20)
    x.add_argument("--pull", action="store_true", help="결과 원본을 <project>/inbox/from-immich/ 에 받는다")
    x.set_defaults(fn=cmd_immich_find)
    x = im.add_parser("similar")
    x.add_argument("asset_id")
    x.add_argument("--project")
    x.add_argument("--slot")
    x.add_argument("-n", type=int, default=20)
    x.set_defaults(fn=cmd_immich_similar)
    x = im.add_parser("share", help="프로젝트 자산 공유 링크(로그인 없이 그림 보기) 만들기·갱신")
    x.add_argument("project")
    x.set_defaults(fn=cmd_immich_share)
    x = im.add_parser("favorites")
    x.add_argument("project")
    x.add_argument("--slot")
    x.set_defaults(fn=cmd_immich_favorites)

    af = sub.add_parser("appflowy").add_subparsers(dest="sub", required=True)
    af.add_parser("whoami").set_defaults(fn=cmd_af_whoami)
    af.add_parser("databases").set_defaults(fn=cmd_af_databases)
    x = af.add_parser("fields")
    x.add_argument("db")
    x.set_defaults(fn=cmd_af_fields)
    x = af.add_parser("pages")
    x.add_argument("--depth", type=int, default=3)
    x.set_defaults(fn=cmd_af_pages)
    x = af.add_parser("push-directives")
    x.add_argument("project")
    x.add_argument("--db", default="지시 로그")
    x.add_argument("--no-create-fields", action="store_true", help="없는 열을 만들지 않고 건너뛴다")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_af_push_directives)
    x = af.add_parser("push-assets")
    x.add_argument("project")
    x.add_argument("--db", default="자산 보드")
    x.add_argument("--slot")
    x.add_argument("--no-create-fields", action="store_true", help="없는 열을 만들지 않고 건너뛴다")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_af_push_assets)
    dex_cli.register(sub, {"out": out, "immich": immich, "appflowy": appflowy, "ledger": ledger, "cells_for": _cells_for, "CliError": CliError})
    x = af.add_parser("trash", help="페이지(그리드 포함)를 휴지통으로 — 이름 또는 view id 여러 개")
    x.add_argument("views", nargs="+")
    x.set_defaults(fn=cmd_af_trash)
    x = af.add_parser("login-link", help="웹 로그인용 일회용 링크 (SMTP 없이)")
    x.add_argument("--email")
    x.add_argument("--open", action="store_true", help="기본 브라우저로 바로 연다")
    x.set_defaults(fn=cmd_af_login_link)
    x = af.add_parser("grid", help="Grid 페이지 생성 (--fields '번호:number,상태,Immich:url')")
    x.add_argument("--parent", required=True)
    x.add_argument("--title", required=True)
    x.add_argument("--fields", default="")
    x.set_defaults(fn=cmd_af_grid)
    x = af.add_parser("page")
    x.add_argument("--parent", required=True)
    x.add_argument("--title", required=True)
    x.add_argument("--from", dest="src", required=True)
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_af_page)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    a = build_parser().parse_args(argv)
    s = Settings.load(Path(a.env_file) if a.env_file else None)
    try:
        return int(a.fn(a, s) or 0)
    except (ApiError, AppFlowyError, CliError, FileNotFoundError, KeyError) as e:
        out(f"오류: {e}")
        return 2

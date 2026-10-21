"""`studiohub dex …` — dragon-yuhui 도감 명령 (tag · board · pull). 공용 헬퍼는 cli 에서 주입받는다."""
from __future__ import annotations

import os
from pathlib import Path

from . import dex as dexmod
from .appflowy_client import AppFlowyError
from .assets import SyncLedger, scan
from .config import WORKSPACE_ROOT, Settings
from .http_client import ApiError

DEX_FIELDS = [("ID", "text"), ("종류", "text"), ("등급", "text"), ("속성", "text"), ("숨결", "text"), ("성향", "text"), ("서식지", "text"),
              ("성격", "text"), ("색", "text"), ("부모", "text"), ("드래곤", "text"), ("알비노", "text"), ("초상", "text"), ("누락", "text"),
              ("상태", "text"), ("앨범", "url"), ("대표", "url")]


def share_key(s: Settings, project: str) -> str | None:
    """`studiohub immich share <project>` 가 저장한 공유 링크 키 (.state/immich_share.json)."""
    p = s.state_dir / "immich_share.json"
    if not p.exists():
        return None
    import json
    return (json.loads(p.read_text(encoding="utf-8")).get(project) or {}).get("key")


def gallery_blocks(entries, immich_url: str, key: str) -> list[dict]:
    """도감 그림판 페이지 — 항목마다 제목 + 단계 4장(기본 포즈) + 잡종은 알비노 성체. 외부 이미지 블록(image_type 2)."""
    def img(a: dict) -> dict:
        return {"type": "image", "data": {"url": f"{immich_url}/api/assets/{a['asset_id']}/thumbnail?size=preview&key={key}", "image_type": 2, "align": "center"}, "children": []}
    blocks: list[dict] = []
    for e in entries:
        m = e.meta
        blocks.append({"type": "heading", "data": {"level": 2, "delta": [{"insert": f"{m['name']} ({e.id})"}]}, "children": []})
        cap = f"{'잡종' if e.is_hybrid else '순종'} · {dexmod.GRADE_KO.get(m['grade'], m['grade'])} · {dexmod.ELEMENT_KO.get(m['element'], m['element'])} · {e.status()}"
        blocks.append({"type": "paragraph", "data": {"delta": [{"insert": cap}]}, "children": []})
        for st in dexmod.STAGES:
            a = e.slots.get(("normal", st, "default"))
            if a and a.get("asset_id"):
                blocks.append({"type": "paragraph", "data": {"delta": [{"insert": dexmod.STAGE_KO[st]}]}, "children": []})
                blocks.append(img(a))
        if e.is_hybrid:
            a = e.slots.get(("albino", "adult", "default"))
            if a and a.get("asset_id"):
                blocks.append({"type": "paragraph", "data": {"delta": [{"insert": "알비노 성체"}]}, "children": []})
                blocks.append(img(a))
    return blocks


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def data_dir(s: Settings, project: str) -> Path:
    override = os.environ.get("STUDIOHUB_DEX_DATA") or s.extra.get("STUDIOHUB_DEX_DATA")
    return Path(override) if override else dexmod.data_dir(WORKSPACE_ROOT, project)


def local_assets(s: Settings, project: str, ledger: SyncLedger) -> list[dict]:
    """로컬 스캔(모든 슬롯) + 장부의 asset_id. rel 은 슬롯 기준 상대 경로."""
    by_rel = {(v["slot"], v["rel"]): v["asset_id"] for _, v in ledger.by_project(project)}
    return [{"rel": x.rel, "slot": x.slot, "asset_id": by_rel.get((x.slot, x.rel))} for x in scan(s.assets_root(), project, s.slots())]


def entries_for(s: Settings, project: str, ledger: SyncLedger):
    species, tables = dexmod.load_game_data(data_dir(s, project))
    return dexmod.build_entries(species, tables, local_assets(s, project, ledger))


def register(sub, h: dict) -> None:
    """h = cli 의 헬퍼: out · immich · appflowy · ledger · cells_for · CliError"""
    out, CliError = h["out"], h["CliError"]

    def cmd_tag(a, s: Settings) -> int:
        project = s.resolve_project(a.project)
        c = h["immich"](s)
        assets = [x for x in local_assets(s, project, h["ledger"](s)) if x.get("asset_id")]
        by_tag: dict[str, list[str]] = {}
        by_album: dict[str, list[str]] = {}
        skipped = 0
        for x in assets:
            info = dexmod.parse_key(x["rel"])
            if not info:
                skipped += 1
                continue
            for t in dexmod.tags_for(info):
                by_tag.setdefault(t, []).append(x["asset_id"])
            by_album.setdefault(f"{project}/dex/{info['species']}", []).append(x["asset_id"])
        if a.dry_run:
            out(f"{project}: 태그 {len(by_tag)}종 · 앨범 {len(by_album)}개 · 해석 못 한 파일 {skipped} (dry-run)")
            return 0
        tag_ids = c.upsert_tags(by_tag)
        for t, ids in by_tag.items():
            c.tag_assets([tag_ids[t]], ids)
        for name, ids in by_album.items():
            alb = c.ensure_album(name, description=f"studiohub 도감 · {name}")
            c.add_to_album(alb["id"], ids)
        out(f"{project}: 동기화된 자산 {len(assets)}개에 태그 {len(by_tag)}종 · 종별 앨범 {len(by_album)}개 (해석 못 한 파일 {skipped})")
        return 0

    def cmd_albums(a, s: Settings) -> int:
        """Immich 분류 앨범 — 종류·단계·포즈·속성·등급·서식지·종별. 옛 '<project>/dex/<id>' 앨범은 종별 이름으로 바꾼다."""
        project = s.resolve_project(a.project)
        c = h["immich"](s)
        species, tables = dexmod.load_game_data(data_dir(s, project))
        by_id = {sp["id"]: sp for sp in species}
        metas: dict[str, dict] = dict(by_id)
        assets = [x for x in local_assets(s, project, h["ledger"](s)) if x.get("asset_id")]
        plan: dict[str, tuple[str, list[str]]] = {}  # 앨범 이름 -> (설명, asset ids)
        unparsed = 0
        for x in assets:
            info = dexmod.parse_key(x["rel"])
            if not info:
                unparsed += 1
                continue
            sid = info["species"]
            if sid not in metas and sid.startswith("h_"):
                hd = dexmod.hybrid_def(sid, by_id, tables)
                if hd:
                    metas[sid] = hd
            for name, desc in dexmod.category_albums(project, info, metas.get(sid)):
                plan.setdefault(name, (desc, []))[1].append(x["asset_id"])
        if a.dry_run:
            for name, (desc, ids) in sorted(plan.items()):
                out(f"  {name}: {len(ids)}장")
            out(f"{project}: 앨범 {len(plan)}개 (dry-run) · 해석 못 한 파일 {unparsed}")
            return 0
        existing = {alb["albumName"]: alb for alb in c.albums()}
        # 옛 종별 앨범 이름 바꾸기
        renamed = 0
        for sid, meta in metas.items():
            old = existing.get(f"{project}/dex/{sid}")
            new_name = dexmod.species_album_name(project, meta)
            if old and new_name not in existing:
                c.rename_album(old["id"], new_name, f"{meta['name']} 의 모든 그림")
                existing[new_name] = old
                renamed += 1
        created = 0
        for name, (desc, ids) in sorted(plan.items()):
            alb = existing.get(name)
            if not alb:
                alb = c.ensure_album(name, description=desc)
                existing[name] = alb
                created += 1
            c.add_to_album(alb["id"], ids)
        out(f"{project}: 분류 앨범 {len(plan)}개 (새로 {created} · 이름 바꿈 {renamed}) · 자산 {len(assets)}장 · 해석 못 한 파일 {unparsed}")
        for name, (_, ids) in sorted(plan.items()):
            out(f"  {name}: {len(ids)}장")
        return 0

    def cmd_board(a, s: Settings) -> int:
        project = s.resolve_project(a.project)
        entries = entries_for(s, project, h["ledger"](s))
        c, ws = h["appflowy"](s)
        try:
            db = c.database_id(ws, a.db)
        except AppFlowyError:
            parent = c.find_view(ws, a.parent)
            if not parent:
                raise CliError(f"상위 뷰를 못 찾음: {a.parent} (`studiohub appflowy pages` 로 확인)")
            if a.dry_run:
                out(f"(dry-run) 그리드 '{a.db}' 를 {a.parent} 아래 만들 것")
                db = None
            else:
                _, db = c.create_grid(ws, parent["view_id"], a.db, DEX_FIELDS)
                out(f"그리드 생성: {a.db}")
        albums: dict[str, str] = {}
        try:
            albums = {alb["albumName"]: alb["id"] for alb in h["immich"](s).albums()}
        except (ApiError, CliError):
            pass

        def album_for(e) -> str | None:  # 종별 앨범: 새 이름(용의 유희 › 종 › …) 우선, 없으면 옛 이름
            return albums.get(dexmod.species_album_name(project, e.meta)) or albums.get(f"{project}/dex/{e.id}")
        seen = SyncLedger(s.state_dir / "dex_rows.json")  # pre_hash -> row id. 문서는 처음 만들 때만 보낸다(사용자 글 보존)
        docs_dir = Path(a.docs) if a.docs else None
        key = share_key(s, project)
        if not key:
            out("(그림은 링크로만 들어간다 — `studiohub immich share " + project + "` 뒤에 다시 board 하면 이미지 블록이 된다)")
        n_new = n_upd = n_doc = 0
        for e in entries:
            alb_id = album_for(e)
            cells = dexmod.entry_cells(e, s.immich_url, f"{s.immich_url}/albums/{alb_id}" if alb_id else None, key)
            if a.dry_run or db is None:
                out(f"  {e.id}: 드래곤 {cells['드래곤']} 초상 {cells['초상'] or '-'} 알비노 {cells['알비노'] or '-'} · {cells['상태']}")
                continue
            cells, _ = h["cells_for"](c, ws, db, cells, "이름", create=True)
            row_key = f"dex:{project}:{e.id}"
            doc_file = docs_dir / f"{e.id}.md" if docs_dir else None
            if doc_file and doc_file.exists():
                # --docs: 파일(정본)의 본문으로 행 문서를 **덮어쓴다** — frontmatter 는 떼고, 그림 자리표시자에 공유 키를 붙인다
                doc = dexmod.resolve_placeholders(strip_frontmatter(doc_file.read_text(encoding="utf-8")), s.immich_url, key)
                rid = c.upsert_row(ws, db, row_key, cells, document=doc)
                seen.data[row_key] = {"row_id": rid, "project": project, "doc": str(doc_file)}
                n_doc += 1
            elif seen.data.get(row_key):
                c.upsert_row(ws, db, row_key, cells)
                n_upd += 1
            else:
                doc = dexmod.resolve_placeholders(dexmod.entry_document(e, s.immich_url), s.immich_url, key)
                rid = c.upsert_row(ws, db, row_key, cells, document=doc)
                seen.data[row_key] = {"row_id": rid, "project": project}
                n_new += 1
        if not a.dry_run and db is not None:
            seen.save()
        out(f"{project}: 도감 항목 {len(entries)} → AppFlowy '{a.db}' 새 {n_new} · 셀 갱신 {n_upd} · 문서 덮어씀(--docs) {n_doc}")
        return 0

    def cmd_gallery(a, s: Settings) -> int:
        """'도감 그림판' 페이지 — 항목마다 단계별 그림을 외부 이미지 블록으로. 같은 제목의 옛 페이지는 휴지통으로."""
        project = s.resolve_project(a.project)
        key = share_key(s, project)
        if not key:
            raise CliError(f"공유 링크 키가 없다 — 먼저 `studiohub immich share {project}`")
        entries = entries_for(s, project, h["ledger"](s))
        c, ws = h["appflowy"](s)
        parent = c.find_view(ws, a.parent)
        if not parent:
            raise CliError(f"상위 뷰를 못 찾음: {a.parent}")
        old = c.find_view(ws, a.title)
        if old and old.get("view_id") != parent["view_id"]:
            c.trash_page(ws, old["view_id"])
        blocks = gallery_blocks(entries, s.immich_url, key)
        vid = c.create_page(ws, parent["view_id"], a.title, blocks)
        out(f"{a.title}: 항목 {len(entries)} · 블록 {len(blocks)} (그림 {sum(1 for b in blocks if b['type'] == 'image')}) → {c.web_url(ws, vid)}")
        return 0

    def cmd_pull(a, s: Settings) -> int:
        project = s.resolve_project(a.project)
        c, ws = h["appflowy"](s)
        db = c.database_id(ws, a.db)
        primary = c.primary_field(c.fields(ws, db)) or "이름"
        rows = c.row_details(ws, db, c.row_ids(ws, db), with_doc=True)
        out_dir = Path(a.out) if a.out else WORKSPACE_ROOT / "10_PROJECTS" / "active" / project / "docs" / "dex"
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for r in rows:
            cells = r.get("cells") or {}
            rid = str(cells.get("ID") or "").strip()
            if not rid:
                continue
            doc = (r.get("doc") or "").rstrip()
            front = [
                "---", f"id: {rid}", f"name: {cells.get(primary, '')}", f"kind: {cells.get('종류', '')}", f"status: {cells.get('상태', '')}",
                f"source: appflowy '{a.db}' (studiohub dex pull)", "---", "", doc, "",
            ]
            (out_dir / f"{rid}.md").write_text("\n".join(front), encoding="utf-8")
            n += 1
        out(f"{project}: 도감 행 {n}개 → {out_dir}")
        return 0

    dx = sub.add_parser("dex", help="dragon-yuhui 도감: tag(Immich 종·단계 태그·앨범) · board(AppFlowy 도감 그리드) · pull(행 문서 → md)").add_subparsers(dest="sub", required=True)
    x = dx.add_parser("tag")
    x.add_argument("project")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_tag)
    x = dx.add_parser("albums", help="Immich 분류 앨범 (종류·단계·포즈·속성·등급·서식지·종별)")
    x.add_argument("project")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_albums)
    x = dx.add_parser("board")
    x.add_argument("project")
    x.add_argument("--db", default="도감")
    x.add_argument("--parent", default="General")
    x.add_argument("--docs", help="<폴더>/<id>.md 가 있으면 그 본문으로 행 문서를 덮어쓴다 (정본 = 파일)")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_board)
    x = dx.add_parser("gallery", help="'도감 그림판' 페이지 — 단계별 그림을 이미지 블록으로 (공유 링크 키 필요)")
    x.add_argument("project")
    x.add_argument("--title", default="도감 그림판")
    x.add_argument("--parent", default="General")
    x.set_defaults(fn=cmd_gallery)
    x = dx.add_parser("pull")
    x.add_argument("project")
    x.add_argument("--db", default="도감")
    x.add_argument("--out")
    x.set_defaults(fn=cmd_pull)

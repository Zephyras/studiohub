"""AppFlowy Cloud REST 클라이언트 (AppFlowy-Docs openapi + AppFlowy-Cloud 소스 기준, 확인일 2026-10-20).

인증: POST /gotrue/token?grant_type=password {email,password} → Bearer JWT (refresh_token 으로 갱신).
응답 봉투: {"code": 0, "message": "...", "data": ...} — code 0 이 성공.
쓰는 엔드포인트
  GET  /api/workspace · /api/workspace/{ws}/folder?depth=
  GET  /api/workspace/{ws}/database · .../database/{db}/fields · .../row · .../row/detail?ids=&with_doc=
  POST .../database/{db}/row {cells, document}  ·  PUT .../row {pre_hash, cells, document}  (upsert)
  POST /api/workspace/{ws}/page-view {parent_view_id, layout, name, page_data}
  POST /api/workspace/{ws}/page-view/{view}/append-block {blocks:[SerdeBlock]}
SerdeBlock = {"type": "paragraph", "data": {"delta": [{"insert": "..."}]}, "children": []}
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .http_client import ApiError, Http


def derive_row_id(ws: str, db: str, pre_hash: str) -> uuid.UUID:
    """서버(src/api/workspace.rs upsert_database_row_handler)와 같은 계산: sha256(ws 16바이트 + db 16바이트 + pre_hash)[:16]."""
    def raw(x: str) -> bytes:
        try:
            return uuid.UUID(x).bytes
        except ValueError:  # 가짜 서버의 'db-1' 같은 id — 글자 그대로
            return x.encode("utf-8")
    h = hashlib.sha256(raw(ws) + raw(db) + pre_hash.encode("utf-8")).digest()
    return uuid.UUID(bytes=h[:16])


def v4_safe_pre_hash(ws: str, db: str, key: str, max_tries: int = 4096) -> str:
    """해시 그대로의 행 id 는 UUID v4 모양이 아니어서 AppFlowy 웹(0.19.2x)이 'Invalid Y.Doc guid' 로 행을 영원히 로딩한다
    (2026-10-22 확인). version=4·variant=RFC4122 가 나올 때까지 '#n' 을 붙여 결정적으로 고른다(평균 60여 번, 로컬 계산)."""
    for n in range(max_tries):
        cand = key if n == 0 else f"{key}#{n}"
        u = derive_row_id(ws, db, cand)
        if u.version == 4 and u.variant == uuid.RFC_4122:
            return cand
    raise AppFlowyError(f"v4 모양 행 id 를 못 찾음: {key}")


class AppFlowyError(Exception):
    pass


# collab-database FieldType id (AppFlowy-Collab, 2026-10 확인): 0 RichText 1 Number 2 DateTime 3 SingleSelect
# 4 MultiSelect 5 Checkbox 6 URL 7 Checklist 8 LastEditedTime 9 CreatedTime 10 Relation 13 Time 14 Media
FIELD_TYPES = {"text": 0, "number": 1, "date": 2, "select": 3, "multiselect": 4, "checkbox": 5, "url": 6, "checklist": 7, "media": 14}


def media_cell(files: list[tuple[str, str]]) -> dict:
    """Media(14) 셀 값 — collab-database MediaCellData: files[{id,name,url,upload_type 1=Network,file_type 1=Image}].
    files = [(이름, 외부 URL)]. 그리드에 썸네일이 보이고 갤러리 커버 열로 고를 수 있다."""
    return {"files": [{"id": f"img{i}", "name": name, "url": url, "upload_type": 1, "file_type": 1} for i, (name, url) in enumerate(files)]}
# shared-entity ViewLayout 은 serde_repr(u8): 0 Document 1 Grid 2 Board 3 Calendar 4 Chat (폴더 응답도 같은 숫자)
VIEW_LAYOUTS = {"Document": 0, "Grid": 1, "Board": 2, "Calendar": 3, "Chat": 4}


class AppFlowyClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        token_cache: Path | None = None,
        http: Http | None = None,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.email, self.password = email, password
        self.token_cache = token_cache
        self.http = http or Http(self.base_url, timeout=timeout)
        self._token: dict[str, Any] = {}
        if token_cache and token_cache.exists():
            try:
                cached = json.loads(token_cache.read_text(encoding="utf-8"))
                if cached.get("email") == email:
                    self._token = cached
            except Exception:
                self._token = {}

    # ---- 인증 ----
    def login(self) -> dict:
        if not self.email or not self.password:
            raise AppFlowyError("APPFLOWY_EMAIL / APPFLOWY_PASSWORD 가 비어 있다")
        tok = self.http.post_json(
            "/gotrue/token", {"email": self.email, "password": self.password}, params={"grant_type": "password"}
        )
        self._store(tok)
        self.verify()  # GoTrue 사용자를 AppFlowy(af_user)에 등록 — 안 하면 /api/workspace 가 "user not found"
        return tok

    def verify(self) -> bool:
        """GET /api/user/verify/{access_token} — 처음 로그인한 사용자를 AppFlowy 에 만든다. is_new 를 돌려준다."""
        data = self._call("GET", f"/api/user/verify/{self._token['access_token']}", retry=False)
        return bool(data.get("is_new")) if isinstance(data, dict) else False

    def refresh(self) -> dict:
        rt = self._token.get("refresh_token")
        if not rt:
            return self.login()
        try:
            tok = self.http.post_json("/gotrue/token", {"refresh_token": rt}, params={"grant_type": "refresh_token"})
        except ApiError:
            return self.login()
        return self._store(tok)

    def _store(self, tok: dict) -> dict:
        if "access_token" not in tok:
            raise AppFlowyError(f"토큰 응답이 이상함: {str(tok)[:200]}")
        tok = dict(tok)
        tok.setdefault("expires_at", int(time.time()) + int(tok.get("expires_in", 3600)))
        tok["email"] = self.email
        self._token = tok
        if self.token_cache:
            self.token_cache.parent.mkdir(parents=True, exist_ok=True)
            self.token_cache.write_text(json.dumps({k: tok[k] for k in ("access_token", "refresh_token", "expires_at", "email") if k in tok}), encoding="utf-8")
        return tok

    def _auth(self) -> dict[str, str]:
        if not self._token.get("access_token") or int(self._token.get("expires_at", 0)) - 60 < time.time():
            self.refresh()
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    def _call(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None, retry: bool = True) -> Any:
        try:
            raw = self.http.request(method, path, json_body=json_body, params=params, headers=self._auth()).json()
        except ApiError as e:
            if e.status == 401 and retry:
                self.refresh()
                return self._call(method, path, json_body=json_body, params=params, retry=False)
            raise
        if isinstance(raw, dict) and "code" in raw:
            if raw.get("code") not in (0, None):
                msg = str(raw.get("message"))
                if "af_user" in msg and retry:  # 캐시된 토큰으로 첫 호출: AppFlowy 쪽 사용자 등록이 안 된 경우
                    self.verify()
                    return self._call(method, path, json_body=json_body, params=params, retry=False)
                raise AppFlowyError(f"{method} {path}: code={raw.get('code')} {msg}")
            return raw.get("data")
        return raw

    # ---- 워크스페이스·폴더 ----
    def workspaces(self) -> list[dict]:
        return self._call("GET", "/api/workspace") or []

    def workspace_id(self, name_or_id: str = "") -> str:
        wss = self.workspaces()
        if not wss:
            raise AppFlowyError("워크스페이스가 없다")
        if not name_or_id:
            return wss[0]["workspace_id"]
        for ws in wss:
            if name_or_id in (ws.get("workspace_id"), ws.get("workspace_name")):
                return ws["workspace_id"]
        raise AppFlowyError(f"워크스페이스 없음: {name_or_id} (있는 것: {[w.get('workspace_name') for w in wss]})")

    def folder(self, ws: str, depth: int = 2, root_view_id: str | None = None) -> dict:
        return self._call("GET", f"/api/workspace/{ws}/folder", params={"depth": depth, "root_view_id": root_view_id})

    def find_view(self, ws: str, name_or_id: str, depth: int = 4) -> dict | None:
        """폴더 트리에서 이름(또는 view_id)이 같은 첫 뷰를 찾는다."""
        def walk(v: dict):
            if name_or_id in (v.get("view_id"), v.get("name")):
                return v
            for c in v.get("children") or []:
                hit = walk(c)
                if hit:
                    return hit
            return None
        return walk(self.folder(ws, depth=depth))

    # ---- 데이터베이스 ----
    def databases(self, ws: str) -> list[dict]:
        return self._call("GET", f"/api/workspace/{ws}/database") or []

    def database_id(self, ws: str, name_or_id: str) -> str:
        """데이터베이스 id · 뷰 id · 뷰 이름 · 페이지(폴더) 이름 어느 것으로든 데이터베이스 id 를 찾는다.
        API 로 만든 Grid 페이지는 database 목록의 뷰 이름이 'Grid' 라 폴더 이름(페이지 제목)으로 찾아야 한다."""
        dbs = self.databases(ws)
        for db in dbs:
            if db.get("id") == name_or_id:
                return db["id"]
            for v in db.get("views") or []:
                if v.get("name") == name_or_id or v.get("view_id") == name_or_id:
                    return db["id"]
        view = self.find_view(ws, name_or_id)
        if view and view.get("layout") in (1, 2, 3, "Grid", "Board", "Calendar"):
            try:
                return self.database_for_view(ws, view["view_id"])
            except AppFlowyError:
                pass
        names = [v.get("name") for db in dbs for v in (db.get("views") or [])]
        raise AppFlowyError(f"데이터베이스 없음: {name_or_id} (있는 것: {names}; 페이지 이름은 `appflowy pages`)")

    def fields(self, ws: str, db: str) -> list[dict]:
        return self._call("GET", f"/api/workspace/{ws}/database/{db}/fields") or []

    def add_field(self, ws: str, db: str, name: str, field_type: int | str = "text", type_option: dict | None = None) -> str:
        """POST .../fields {name, field_type: 숫자, type_option_data}. 새 필드 id 를 돌려준다."""
        ft = FIELD_TYPES[field_type] if isinstance(field_type, str) else int(field_type)
        body: dict[str, Any] = {"name": name, "field_type": ft}
        if type_option is not None:
            body["type_option_data"] = type_option
        return self._call("POST", f"/api/workspace/{ws}/database/{db}/fields", json_body=body)

    @staticmethod
    def primary_field(fields: list[dict]) -> str | None:
        for f in fields:
            if f.get("is_primary"):
                return f.get("name")
        return fields[0].get("name") if fields else None

    def database_for_view(self, ws: str, view_id: str) -> str:
        """page-view 응답의 view.extra.database_id (API 로 만든 그리드는 database 목록에 늦게 나타난다)."""
        page = self.page(ws, view_id) or {}
        view = page.get("view") or {}
        db = (view.get("extra") or {}).get("database_id")
        if db:
            return db
        for d in self.databases(ws):
            for v in d.get("views") or []:
                if v.get("view_id") == view_id:
                    return d["id"]
        raise AppFlowyError(f"뷰 {view_id} 의 데이터베이스를 못 찾음")

    def row_ids(self, ws: str, db: str) -> list[str]:
        rows = self._call("GET", f"/api/workspace/{ws}/database/{db}/row") or []
        return [r["id"] if isinstance(r, dict) else r for r in rows]

    def row_details(self, ws: str, db: str, ids: Iterable[str], with_doc: bool = False) -> list[dict]:
        ids = list(ids)
        if not ids:
            return []
        return self._call(
            "GET", f"/api/workspace/{ws}/database/{db}/row/detail", params={"ids": ",".join(ids), "with_doc": with_doc}
        ) or []

    def create_row(self, ws: str, db: str, cells: dict[str, Any], document: str | None = None) -> str:
        body: dict[str, Any] = {"cells": cells}
        if document is not None:
            body["document"] = document
        return self._call("POST", f"/api/workspace/{ws}/database/{db}/row", json_body=body)

    def upsert_row(self, ws: str, db: str, pre_hash: str, cells: dict[str, Any], document: str | None = None, v4_safe: bool = True) -> str:
        """같은 pre_hash 면 같은 행. v4_safe 면 웹이 읽을 수 있는 id 가 나오는 변형 pre_hash 를 쓴다 (v4_safe_pre_hash)."""
        if v4_safe:
            pre_hash = v4_safe_pre_hash(ws, db, pre_hash)
        body: dict[str, Any] = {"pre_hash": pre_hash, "cells": cells}
        if document is not None:
            body["document"] = document
        return self._call("PUT", f"/api/workspace/{ws}/database/{db}/row", json_body=body)

    # ---- 페이지(문서) ----
    def create_grid(self, ws: str, parent_view_id: str, name: str, fields: list[tuple[str, str]] | None = None) -> tuple[str, str]:
        """Grid 페이지를 만들고 (view_id, database_id) 를 돌려준다. fields = [(이름, 타입)] 는 기본 열 뒤에 추가한다."""
        view_id = self.create_page(ws, parent_view_id, name, layout="Grid")
        db = self.database_for_view(ws, view_id)
        for fname, ftype in fields or []:
            self.add_field(ws, db, fname, ftype)
        return view_id, db

    def create_page(self, ws: str, parent_view_id: str, name: str, blocks: list[dict] | None = None, layout: str = "Document") -> str:
        body: dict[str, Any] = {"parent_view_id": parent_view_id, "layout": VIEW_LAYOUTS.get(layout, layout), "name": name}
        if blocks is not None:
            body["page_data"] = {"type": "page", "data": {}, "children": blocks}
        data = self._call("POST", f"/api/workspace/{ws}/page-view", json_body=body)
        return data["view_id"] if isinstance(data, dict) else data

    def append_blocks(self, ws: str, view_id: str, blocks: list[dict]) -> None:
        if blocks:
            self._call("POST", f"/api/workspace/{ws}/page-view/{view_id}/append-block", json_body={"blocks": blocks})

    def page(self, ws: str, view_id: str) -> dict:
        return self._call("GET", f"/api/workspace/{ws}/page-view/{view_id}")

    def trash_page(self, ws: str, view_id: str) -> None:
        """휴지통으로 (복구는 AppFlowy UI 또는 restore-from-trash)."""
        self._call("POST", f"/api/workspace/{ws}/page-view/{view_id}/move-to-trash", json_body={})

    def web_url(self, ws: str, view_id: str) -> str:
        return f"{self.base_url}/app/{ws}/{view_id}"

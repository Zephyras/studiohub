"""테스트용 가짜 Immich·AppFlowy 서버 (http.server, 스레드). 실제 API 의 경로·봉투 형식만 흉내 낸다."""
from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class _State:
    def __init__(self):
        self.assets: dict[str, dict] = {}  # id -> asset
        self.by_checksum: dict[str, str] = {}
        self.albums: dict[str, dict] = {}
        self.tags: dict[str, dict] = {}  # value -> tag
        self.asset_tags: dict[str, set[str]] = {}
        self.uploads: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        # appflowy
        self.logins = 0
        self.workspace = "11111111-1111-1111-1111-111111111111"
        self.databases = {"db-1": {"name": "지시 로그", "fields": ["지시", "번호", "프로젝트", "상태", "결과"], "rows": {}},
                          "db-2": {"name": "자산 보드", "fields": ["자산", "프로젝트", "슬롯", "상태", "Immich"], "rows": {}}}
        self.pages: dict[str, dict] = {}


class _Handler(BaseHTTPRequestHandler):
    state: _State
    api_key = "test-key"

    def log_message(self, *a):  # 조용히
        pass

    # ---- helpers
    def _json(self, code: int, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _dispatch(self, method: str):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.state.requests.append((method, u.path))
        if u.path.startswith("/api/") and not u.path.startswith(("/api/workspace", "/api/user/")):
            return self._immich(method, u.path[4:], q)
        return self._appflowy(method, u.path, q)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    # ---- Immich
    def _immich(self, m: str, p: str, q: dict):
        st = self.state
        if self.headers.get("x-api-key") != self.api_key:
            return self._json(401, {"message": "Invalid API key"})
        if (m, p) == ("GET", "/server/ping"):
            return self._json(200, {"res": "pong"})
        if (m, p) == ("GET", "/server/version"):
            return self._json(200, {"major": 3, "minor": 3, "patch": 0})
        if (m, p) == ("GET", "/users/me"):
            return self._json(200, {"id": "u1", "email": "me@test"})
        if (m, p) == ("GET", "/albums"):
            albs = list(st.albums.values())
            if "name" in q:
                albs = [a for a in albs if a["albumName"] == q["name"]]
            return self._json(200, albs)
        if (m, p) == ("POST", "/albums"):
            b = json.loads(self._body())
            a = {"id": str(uuid.uuid4()), "albumName": b["albumName"], "description": b.get("description", ""), "assetIds": list(b.get("assetIds", [])), "assetCount": 0}
            st.albums[a["id"]] = a
            return self._json(201, a)
        if m == "PATCH" and (mm := re.fullmatch(r"/albums/([^/]+)", p)):
            b = json.loads(self._body())
            st.albums[mm.group(1)].update({k: v for k, v in {"albumName": b.get("albumName"), "description": b.get("description")}.items() if v is not None})
            return self._json(200, st.albums[mm.group(1)])
        if m == "PUT" and (mm := re.fullmatch(r"/albums/([^/]+)/assets", p)):
            ids = json.loads(self._body())["ids"]
            st.albums[mm.group(1)]["assetIds"] += ids
            return self._json(200, [{"id": i, "success": True} for i in ids])
        if (m, p) == ("PUT", "/tags"):
            res = []
            for name in json.loads(self._body())["tags"]:
                t = st.tags.setdefault(name, {"id": str(uuid.uuid4()), "name": name.split("/")[-1], "value": name})
                res.append(t)
            return self._json(200, res)
        if (m, p) == ("PUT", "/tags/assets"):
            b = json.loads(self._body())
            for a in b["assetIds"]:
                st.asset_tags.setdefault(a, set()).update(b["tagIds"])
            return self._json(200, {"count": len(b["assetIds"]) * len(b["tagIds"])})
        if (m, p) == ("POST", "/assets/bulk-upload-check"):
            res = []
            for it in json.loads(self._body())["assets"]:
                if it["checksum"] in st.by_checksum:
                    res.append({"id": it["id"], "action": "reject", "reason": "duplicate", "assetId": st.by_checksum[it["checksum"]]})
                else:
                    res.append({"id": it["id"], "action": "accept"})
            return self._json(200, {"results": res})
        if (m, p) == ("POST", "/assets"):
            raw = self._body()
            ctype = self.headers.get("Content-Type", "")
            assert ctype.startswith("multipart/form-data"), ctype
            checksum = self.headers.get("x-immich-checksum", "")
            fields = _parse_multipart(raw, ctype.split("boundary=")[1])
            if checksum in st.by_checksum:
                return self._json(200, {"id": st.by_checksum[checksum], "status": "duplicate"})
            aid = str(uuid.uuid4())
            st.assets[aid] = {"id": aid, "originalFileName": fields["assetData"][0], "checksum": checksum, "isFavorite": fields.get("isFavorite", ["false"])[1] == "true", "type": "IMAGE", "deviceAssetId": fields["deviceAssetId"][1], "description": ""}
            st.by_checksum[checksum] = aid
            st.uploads.append({"fields": {k: v[1] for k, v in fields.items() if k != "assetData"}, "size": len(fields["assetData"][1])})
            return self._json(201, {"id": aid, "status": "created"})
        if (m, p) == ("GET", "/shared-links"):
            return self._json(200, getattr(st, "shared_links", []))
        if (m, p) == ("POST", "/shared-links"):
            b = json.loads(self._body())
            link = {"id": str(uuid.uuid4()), "key": "k" + uuid.uuid4().hex[:12], "type": b["type"], "description": b.get("description", ""), "assets": [{"id": i} for i in b.get("assetIds", [])], "allowDownload": b.get("allowDownload", False)}
            st.shared_links = getattr(st, "shared_links", []) + [link]
            return self._json(201, link)
        if m == "PUT" and (mm := re.fullmatch(r"/shared-links/([^/]+)/assets", p)):
            link = next(l for l in st.shared_links if l["id"] == mm.group(1))
            ids = json.loads(self._body())["assetIds"]
            link["assets"] += [{"id": i} for i in ids]
            return self._json(200, [{"assetId": i, "success": True} for i in ids])
        if m == "GET" and (mm := re.fullmatch(r"/assets/([^/]+)/original", p)):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"PNGDATA-" + mm.group(1).encode())
            return
        if m == "GET" and (mm := re.fullmatch(r"/assets/([^/]+)", p)):
            return self._json(200, st.assets[mm.group(1)])
        if m == "PUT" and (mm := re.fullmatch(r"/assets/([^/]+)", p)):
            st.assets[mm.group(1)].update(json.loads(self._body()))
            return self._json(200, st.assets[mm.group(1)])
        if m == "POST" and p in ("/search/smart", "/search/metadata"):
            b = json.loads(self._body())
            items = list(st.assets.values())
            if b.get("tagIds"):
                items = [a for a in items if set(b["tagIds"]) & st.asset_tags.get(a["id"], set())]
            if b.get("isFavorite") is not None:
                items = [a for a in items if a["isFavorite"] == b["isFavorite"]]
            if b.get("query"):
                items = [a for a in items if b["query"].lower() in (a["originalFileName"] + a.get("description", "")).lower()] or items
            size = b.get("size", 20)
            page = b.get("page", 1)
            chunk = items[(page - 1) * size : page * size]
            return self._json(200, {"albums": {"items": [], "total": 0, "count": 0, "facets": []}, "assets": {"items": chunk, "total": len(items), "count": len(chunk), "facets": [], "nextPage": str(page + 1) if page * size < len(items) else None}})
        return self._json(404, {"message": f"fake immich: {m} {p}"})

    # ---- AppFlowy
    def _appflowy(self, m: str, p: str, q: dict):
        st = self.state
        if p == "/gotrue/token" and m == "POST":
            b = json.loads(self._body())
            if q.get("grant_type") == "password":
                if b.get("email") != "user@test" or b.get("password") != "pw":
                    return self._json(400, {"error": "invalid_grant"})
                st.logins += 1
                return self._json(200, {"access_token": f"tok{st.logins}", "refresh_token": "rt", "expires_in": 3600, "token_type": "Bearer"})
            if q.get("grant_type") == "refresh_token":
                st.logins += 1
                return self._json(200, {"access_token": f"tok{st.logins}", "refresh_token": "rt", "expires_in": 3600})
        auth = self.headers.get("Authorization", "")
        if p == "/gotrue/admin/generate_link" and m == "POST":
            # 서비스 JWT(HS256, role=supabase_admin) — 서명 검증은 생략, 형태만 본다
            if auth.count(".") != 2 or "supabase_admin" not in __import__("base64").urlsafe_b64decode(auth.split()[1].split(".")[1] + "==").decode("utf-8", "replace"):
                return self._json(401, {"code": 401, "msg": "bad service token"})
            b = json.loads(self._body())
            st.magic_links = getattr(st, "magic_links", []) + [b]
            return self._json(200, {"action_link": f"{'http://fake'}/gotrue/verify?token=abc&type={b['type']}&redirect_to={b['redirect_to']}", "hashed_token": "abc", "verification_type": b["type"]})
        if not auth.startswith("Bearer tok"):
            return self._json(401, {"code": 401, "message": "unauthorized"})
        ok = lambda data: self._json(200, {"code": 0, "message": "ok", "data": data})  # noqa: E731
        ws = st.workspace
        if m == "GET" and p.startswith("/api/user/verify/"):
            st.verified = getattr(st, "verified", 0) + 1
            return ok({"is_new": st.verified == 1})
        if not getattr(st, "verified", 0):
            return self._json(200, {"code": -2, "message": "Record not found:user not found in af_user", "data": None})
        if (m, p) == ("GET", "/api/workspace"):
            return ok([{"workspace_id": ws, "workspace_name": "게임 스튜디오", "owner_name": "me"}])
        if (m, p) == ("GET", f"/api/workspace/{ws}/folder"):
            return ok({"view_id": ws, "name": "root", "children": [{"view_id": "space-1", "name": "기획", "layout": 0, "children": [{"view_id": "page-1", "name": "GDD", "layout": 0, "children": []}] + getattr(st, "folder_extra", [])}]})
        if (m, p) == ("GET", f"/api/workspace/{ws}/database"):
            return ok([{"id": k, "views": [{"view_id": v.get("view_id", f"view-{k}"), "name": v["name"], "layout": "Grid"}]} for k, v in st.databases.items()])
        if m == "GET" and (mm := re.fullmatch(rf"/api/workspace/{ws}/database/([^/]+)/fields", p)):
            db = st.databases[mm.group(1)]
            return ok([{"id": f"f{i}", "name": n, "field_type": "RichText", "is_primary": i == 0} for i, n in enumerate(db["fields"])])
        if m == "POST" and (mm := re.fullmatch(rf"/api/workspace/{ws}/database/([^/]+)/fields", p)):
            b = json.loads(self._body())
            assert isinstance(b["field_type"], int), b
            db = st.databases[mm.group(1)]
            db["fields"].append(b["name"])
            db.setdefault("field_types", {})[b["name"]] = b["field_type"]
            return ok(f"f{len(db['fields']) - 1}")
        if m == "GET" and (mm := re.fullmatch(rf"/api/workspace/{ws}/database/([^/]+)/row", p)):
            return ok([{"id": r} for r in st.databases[mm.group(1)]["rows"]])
        if m == "GET" and (mm := re.fullmatch(rf"/api/workspace/{ws}/database/([^/]+)/row/detail", p)):
            db = st.databases[mm.group(1)]
            ids = q.get("ids", "").split(",")
            with_doc = q.get("with_doc") == "true"
            # 실서버는 with_doc 일 때 문서를 서식 없는 글(plain text)로 돌려준다 (2026-10-21 확인) — 여기선 그대로
            return ok([{"id": i, "cells": db["rows"][i]["cells"], "has_doc": bool(db["rows"][i].get("document")),
                        **({"doc": db["rows"][i].get("document")} if with_doc else {})} for i in ids if i in db["rows"]])
        if m in ("POST", "PUT") and (mm := re.fullmatch(rf"/api/workspace/{ws}/database/([^/]+)/row", p)):
            db = st.databases[mm.group(1)]
            b = json.loads(self._body())
            if m == "PUT":
                from studiohub.appflowy_client import derive_row_id  # 서버와 같은 해시
                u = derive_row_id(ws, mm.group(1), b["pre_hash"])
                rid = str(u)
                # 웹 0.19.2x 가 거부하는 모양이면 서버처럼 저장은 하되 기록해 둔다 (테스트가 잡아낸다)
                if not (u.version == 4 and u.variant == uuid.RFC_4122):
                    st.bad_row_ids = getattr(st, "bad_row_ids", []) + [rid]
            else:
                rid = str(uuid.uuid4())
            prev = db["rows"].get(rid) or {}
            # document 를 안 보내면 기존 문서를 유지한다 (실서버 2026-10-21 확인)
            db["rows"][rid] = {"cells": b["cells"], "document": b["document"] if "document" in b else prev.get("document")}
            return ok(rid)
        if (m, p) == ("POST", f"/api/workspace/{ws}/page-view"):
            b = json.loads(self._body())
            vid = str(uuid.uuid4())
            st.pages[vid] = b
            assert isinstance(b.get("layout"), int), b  # serde_repr(u8)
            if b.get("layout") == 1:  # Grid — 실제 AppFlowy 기본 그리드 열: Name(기본)·Type·Done, database 목록의 뷰 이름은 'Grid'
                st.databases[f"db-{vid[:8]}"] = {"name": "Grid", "fields": ["Name", "Type", "Done"], "rows": {}, "view_id": vid}
            st.folder_extra = getattr(st, "folder_extra", []) + [{"view_id": vid, "name": b.get("name"), "layout": b.get("layout"), "children": []}]
            return ok({"view_id": vid})
        if m == "GET" and (mm := re.fullmatch(rf"/api/workspace/{ws}/page-view/([^/]+)", p)):
            vid = mm.group(1)
            b = st.pages.get(vid)
            if not b:
                return self._json(200, {"code": -2, "message": "Record not found"})
            extra = {}
            for k, v in st.databases.items():
                if v.get("view_id") == vid:
                    extra = {"database_id": k, "is_database_container": True}
            return ok({"view": {"view_id": vid, "name": b.get("name"), "layout": b.get("layout"), "extra": extra, "children": []}, "data": {}})
        if m == "POST" and (mm := re.fullmatch(rf"/api/workspace/{ws}/page-view/([^/]+)/move-to-trash", p)):
            vid = mm.group(1)
            st.trashed = getattr(st, "trashed", []) + [vid]
            st.folder_extra = [v for v in getattr(st, "folder_extra", []) if v["view_id"] != vid]
            for k in [k for k, v in st.databases.items() if v.get("view_id") == vid]:
                del st.databases[k]
            return ok(None)
        if m == "POST" and (mm := re.fullmatch(rf"/api/workspace/{ws}/page-view/([^/]+)/append-block", p)):
            st.pages[mm.group(1)].setdefault("appended", []).extend(json.loads(self._body())["blocks"])
            return ok(None)
        return self._json(404, {"code": 404, "message": f"fake appflowy: {m} {p}"})


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


def _parse_multipart(raw: bytes, boundary: str) -> dict[str, tuple[str | None, bytes]]:
    out: dict[str, tuple[str | None, bytes]] = {}
    for part in raw.split(b"--" + boundary.encode()):
        part = part.strip()
        if not part or part == b"--":
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        h = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]+)"', h).group(1)
        fn = re.search(r'filename="([^"]+)"', h)
        out[name] = (fn.group(1) if fn else None, body.rstrip(b"\r\n") if fn else body.rstrip(b"\r\n"))
    # 텍스트 필드는 (None, bytes) → (None, str) 로
    return {k: (v[0], v[1] if v[0] else v[1].decode()) for k, v in out.items()}


class FakeServer:
    def __init__(self):
        self.state = _State()
        handler = type("H", (_Handler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

"""Immich REST 클라이언트 (OpenAPI 3.3 기준, 확인일 2026-10-20).

인증: 헤더 ``x-api-key`` (Immich 웹 → 계정 설정 → API 키).
쓰는 엔드포인트
  GET  /server/ping · /server/version · /users/me
  GET  /albums?name= · POST /albums · PUT /albums/{id}/assets
  PUT  /tags (upsert, "a/b" 계층) · PUT /tags/assets (bulk)
  POST /assets/bulk-upload-check · POST /assets (multipart, 헤더 x-immich-checksum = SHA1 hex)
  POST /search/smart (CLIP 자연어·유사 이미지 queryAssetId) · POST /search/metadata
  GET  /assets/{id} · PUT /assets/{id} · GET /assets/{id}/original · GET /assets/{id}/thumbnail
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .http_client import Http, encode_multipart

DEVICE_ID = "studiohub"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, http: Http | None = None, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.http = http or Http(self.base_url + "/api", {"x-api-key": api_key}, timeout=timeout)

    # ---- 서버 ----
    def ping(self) -> bool:
        try:
            return self.http.get_json("/server/ping").get("res") == "pong"
        except Exception:
            return False

    def version(self) -> str:
        v = self.http.get_json("/server/version")
        return f"{v['major']}.{v['minor']}.{v['patch']}"

    def me(self) -> dict:
        return self.http.get_json("/users/me")

    # ---- 앨범 ----
    def albums(self) -> list[dict]:
        return self.http.get_json("/albums")

    def album_by_name(self, name: str) -> dict | None:
        hits = self.http.get_json("/albums", params={"name": name})
        return hits[0] if hits else None

    def ensure_album(self, name: str, description: str = "") -> dict:
        found = self.album_by_name(name)
        if found:
            return found
        return self.http.post_json("/albums", {"albumName": name, "description": description, "assetIds": []})

    def rename_album(self, album_id: str, name: str, description: str | None = None) -> dict:
        body: dict[str, Any] = {"albumName": name}
        if description is not None:
            body["description"] = description
        return self.http.request("PATCH", f"/albums/{album_id}", json_body=body).json()

    def add_to_album(self, album_id: str, asset_ids: Iterable[str]) -> list[dict]:
        ids = list(asset_ids)
        if not ids:
            return []
        return self.http.put_json(f"/albums/{album_id}/assets", {"ids": ids})

    # ---- 태그 ----
    def upsert_tags(self, names: Iterable[str]) -> dict[str, str]:
        """'project/dragon-yuhui' 같은 경로형 이름을 받아 {value: id} 를 돌려준다."""
        names = sorted(set(n for n in names if n))
        if not names:
            return {}
        tags = self.http.put_json("/tags", {"tags": names})
        return {t["value"]: t["id"] for t in tags}

    def tag_assets(self, tag_ids: Iterable[str], asset_ids: Iterable[str]) -> int:
        tag_ids, asset_ids = list(tag_ids), list(asset_ids)
        if not tag_ids or not asset_ids:
            return 0
        return int(self.http.put_json("/tags/assets", {"tagIds": tag_ids, "assetIds": asset_ids}).get("count", 0))

    # ---- 업로드 ----
    def bulk_upload_check(self, items: Iterable[tuple[str, str]]) -> dict[str, dict]:
        """items = [(id, sha1_hex)]. 결과 {id: {action: accept|reject, reason, assetId}}."""
        payload = [{"id": i, "checksum": c} for i, c in items]
        if not payload:
            return {}
        res = self.http.post_json("/assets/bulk-upload-check", {"assets": payload})
        return {r["id"]: r for r in res.get("results", [])}

    def upload(
        self,
        path: Path,
        *,
        device_asset_id: str,
        sha1_hex: str,
        is_favorite: bool = False,
        visibility: str | None = None,
    ) -> dict:
        st = path.stat()
        fields = {
            "deviceAssetId": device_asset_id,
            "deviceId": DEVICE_ID,
            "fileCreatedAt": iso(st.st_ctime),
            "fileModifiedAt": iso(st.st_mtime),
            "isFavorite": "true" if is_favorite else "false",
            "filename": path.name,
        }
        if visibility:
            fields["visibility"] = visibility
        body, ctype = encode_multipart(fields, {"assetData": (path.name, path.read_bytes(), None)})
        resp = self.http.request(
            "POST", "/assets", data=body, content_type=ctype, headers={"x-immich-checksum": sha1_hex}
        )
        return resp.json()  # {id, status: created|duplicate|replaced}

    # ---- 검색 ----
    def search_smart(
        self,
        query: str | None = None,
        *,
        query_asset_id: str | None = None,
        size: int = 20,
        page: int = 1,
        album_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        asset_type: str | None = None,
        is_favorite: bool | None = None,
        with_exif: bool = False,
    ) -> dict:
        body: dict[str, Any] = {"size": size, "page": page, "withExif": with_exif}
        if query:
            body["query"] = query
        if query_asset_id:
            body["queryAssetId"] = query_asset_id
        if album_ids:
            body["albumIds"] = album_ids
        if tag_ids:
            body["tagIds"] = tag_ids
        if asset_type:
            body["type"] = asset_type
        if is_favorite is not None:
            body["isFavorite"] = is_favorite
        return self.http.post_json("/search/smart", body)["assets"]

    def search_metadata(self, **filters: Any) -> dict:
        body = {k: v for k, v in filters.items() if v is not None}
        return self.http.post_json("/search/metadata", body)["assets"]

    def iter_metadata(self, page_size: int = 250, **filters: Any):
        page = 1
        while True:
            res = self.search_metadata(size=page_size, page=page, **filters)
            yield from res.get("items", [])
            if not res.get("nextPage"):
                return
            page += 1

    # ---- 자산 ----
    def asset(self, asset_id: str) -> dict:
        return self.http.get_json(f"/assets/{asset_id}")

    def update_asset(self, asset_id: str, **fields: Any) -> dict:
        body = {k: v for k, v in fields.items() if v is not None}
        return self.http.put_json(f"/assets/{asset_id}", body)

    def download_original(self, asset_id: str, dest: Path) -> Path:
        return self.http.download(f"/assets/{asset_id}/original", dest)

    def thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        return self.http.request("GET", f"/assets/{asset_id}/thumbnail", params={"size": size}, headers={"Accept": "*/*"}).body

    def web_url(self, asset_id: str) -> str:
        return f"{self.base_url}/photos/{asset_id}"

    # ---- 공유 링크 — 로그인 없이 그림을 보여 줄 때 (AppFlowy 이미지 블록 등). key 를 쿼리로 붙이면 썸네일·원본이 열린다 ----
    def shared_links(self) -> list[dict]:
        return self.http.get_json("/shared-links")

    def create_shared_link(self, asset_ids: Iterable[str], description: str, allow_download: bool = True) -> dict:
        return self.http.post_json("/shared-links", {"type": "INDIVIDUAL", "assetIds": list(asset_ids), "description": description, "allowDownload": allow_download, "showMetadata": True})

    def add_shared_link_assets(self, link_id: str, asset_ids: Iterable[str]) -> list[dict]:
        ids = list(asset_ids)
        return self.http.put_json(f"/shared-links/{link_id}/assets", {"assetIds": ids}) if ids else []

    def ensure_shared_link(self, description: str, asset_ids: Iterable[str]) -> dict:
        """description 으로 찾아 없으면 만들고, 있으면 빠진 자산만 더한다. {id, key, count, added}."""
        wanted = list(dict.fromkeys(asset_ids))
        link = next((l for l in self.shared_links() if l.get("description") == description), None)
        if not link:
            link = self.create_shared_link(wanted, description)
            return {"id": link["id"], "key": link["key"], "count": len(link.get("assets", [])) or len(wanted), "added": len(wanted)}
        have = {a["id"] for a in link.get("assets", [])}
        missing = [i for i in wanted if i not in have]
        self.add_shared_link_assets(link["id"], missing)
        return {"id": link["id"], "key": link["key"], "count": len(have) + len(missing), "added": len(missing)}

    def thumb_url(self, asset_id: str, key: str, size: str = "preview") -> str:
        """size = thumbnail(작음) · preview(중간) · original(원본)."""
        if size == "original":
            return f"{self.base_url}/api/assets/{asset_id}/original?key={key}"
        return f"{self.base_url}/api/assets/{asset_id}/thumbnail?size={size}&key={key}"

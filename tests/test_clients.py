import json
import tempfile
import unittest
from pathlib import Path

from studiohub.appflowy_client import AppFlowyClient, AppFlowyError
from studiohub.http_client import ApiError
from studiohub.immich_client import ImmichClient
from tests.fakes import FakeServer


class ImmichClientTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer().__enter__()
        self.c = ImmichClient(self.srv.url, "test-key")
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.__exit__(None, None, None)

    def test_ping_version_me(self):
        self.assertTrue(self.c.ping())
        self.assertEqual(self.c.version(), "3.3.0")
        self.assertEqual(self.c.me()["email"], "me@test")

    def test_bad_key_raises(self):
        bad = ImmichClient(self.srv.url, "nope")
        self.assertFalse(bad.ping())
        with self.assertRaises(ApiError) as cm:
            bad.me()
        self.assertEqual(cm.exception.status, 401)

    def test_upload_album_tags_search_download(self):
        f = self.tmp / "hero.png"
        f.write_bytes(b"\x89PNG fake")
        res = self.c.upload(f, device_asset_id="p/library/hero.png", sha1_hex="abc")
        self.assertEqual(res["status"], "created")
        aid = res["id"]
        up = self.srv.state.uploads[0]
        self.assertEqual(up["fields"]["deviceId"], "studiohub")
        self.assertEqual(up["fields"]["deviceAssetId"], "p/library/hero.png")
        self.assertTrue(up["fields"]["fileCreatedAt"].endswith("Z"))
        self.assertEqual(up["size"], len(b"\x89PNG fake"))
        # 중복은 서버가 200 duplicate
        self.assertEqual(self.c.upload(f, device_asset_id="x", sha1_hex="abc")["status"], "duplicate")
        self.assertEqual(self.c.bulk_upload_check([("k", "abc"), ("new", "zzz")])["k"]["assetId"], aid)
        alb = self.c.ensure_album("p/library")
        self.assertEqual(self.c.ensure_album("p/library")["id"], alb["id"])
        self.c.add_to_album(alb["id"], [aid])
        tags = self.c.upsert_tags(["project/p", "slot/library"])
        self.assertEqual(self.c.tag_assets(tags.values(), [aid]), 2)
        hits = self.c.search_smart("hero", tag_ids=[tags["project/p"]])
        self.assertEqual([h["id"] for h in hits["items"]], [aid])
        self.assertEqual(self.c.search_smart("hero", tag_ids=["unknown"])["items"], [])
        self.c.update_asset(aid, description="desc", isFavorite=True)
        self.assertEqual([h["id"] for h in self.c.iter_metadata(isFavorite=True)], [aid])
        dest = self.c.download_original(aid, self.tmp / "dl" / "hero.png")
        self.assertEqual(dest.read_bytes(), b"PNGDATA-" + aid.encode())
        self.assertEqual(self.c.web_url(aid), f"{self.srv.url}/photos/{aid}")


class RowIdTest(unittest.TestCase):
    WS, DB = "0878d128-fef8-4f5b-9c49-dc9b2ed72082", "28303e32-f4b3-4c02-9d9f-8ec8c81f3d01"

    def test_derive_matches_live_server(self):
        from studiohub.appflowy_client import derive_row_id, v4_safe_pre_hash
        # 2026-10-22 실서버에서 받은 값 (upsert 응답)
        self.assertEqual(str(derive_row_id(self.WS, self.DB, "dex:project-game-dragon-yuhui:white")), "e577fa6d-0a3a-ab14-882a-13ff85b47a09")
        self.assertEqual(str(derive_row_id(self.WS, self.DB, "dex:project-game-dragon-yuhui:black")), "8174841a-5aa5-72e5-be1f-b656031aba03")
        safe = v4_safe_pre_hash(self.WS, self.DB, "dex:project-game-dragon-yuhui:white")
        u = derive_row_id(self.WS, self.DB, safe)
        self.assertTrue(safe.startswith("dex:project-game-dragon-yuhui:white"))
        self.assertEqual((u.version, u.variant), (4, __import__("uuid").RFC_4122))
        self.assertEqual(v4_safe_pre_hash(self.WS, self.DB, "dex:project-game-dragon-yuhui:white"), safe)  # 결정적


class AppFlowyClientTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer().__enter__()
        self.tmp = Path(tempfile.mkdtemp())
        self.c = AppFlowyClient(self.srv.url, "user@test", "pw", token_cache=self.tmp / "tok.json")

    def tearDown(self):
        self.srv.__exit__(None, None, None)

    def test_login_cache_and_workspace(self):
        ws = self.c.workspace_id("게임 스튜디오")
        self.assertEqual(ws, self.srv.state.workspace)
        self.assertEqual(self.srv.state.logins, 1)
        self.assertEqual(self.srv.state.verified, 1)  # 로그인 직후 /api/user/verify
        cached = json.loads((self.tmp / "tok.json").read_text())
        self.assertEqual(cached["access_token"], "tok1")
        # 캐시를 쓰는 두 번째 클라이언트는 다시 로그인하지 않는다
        c2 = AppFlowyClient(self.srv.url, "user@test", "pw", token_cache=self.tmp / "tok.json")
        c2.workspaces()
        self.assertEqual(self.srv.state.logins, 1)
        with self.assertRaises(AppFlowyError):
            self.c.workspace_id("없는 워크스페이스")

    def test_bad_password(self):
        bad = AppFlowyClient(self.srv.url, "user@test", "wrong")
        with self.assertRaises(ApiError):
            bad.workspaces()

    def test_databases_rows_pages(self):
        ws = self.c.workspace_id()
        db = self.c.database_id(ws, "지시 로그")
        self.assertEqual(db, "db-1")
        names = [f["name"] for f in self.c.fields(ws, db)]
        self.assertIn("지시", names)
        rid = self.c.create_row(ws, db, {"지시": "첫 지시"}, document="# 본문")
        rid2 = self.c.upsert_row(ws, db, "directive:p:1", {"지시": "둘"})
        rid3 = self.c.upsert_row(ws, db, "directive:p:1", {"지시": "둘(수정)"})
        self.assertEqual(rid2, rid3)
        self.assertEqual(set(self.c.row_ids(ws, db)), {rid, rid2})
        det = {d["id"]: d for d in self.c.row_details(ws, db, [rid, rid2])}
        self.assertEqual(det[rid2]["cells"]["지시"], "둘(수정)")
        self.assertTrue(det[rid]["has_doc"])
        parent = self.c.find_view(ws, "기획")
        self.assertEqual(parent["view_id"], "space-1")
        vid = self.c.create_page(ws, parent["view_id"], "새 문서", [{"type": "paragraph", "data": {"delta": [{"insert": "hi"}]}, "children": []}])
        page = self.srv.state.pages[vid]
        self.assertEqual(page["layout"], 0)  # Document (serde_repr)
        self.assertEqual(page["page_data"]["type"], "page")
        self.c.append_blocks(ws, vid, [{"type": "paragraph", "data": {"delta": []}, "children": []}])
        self.assertEqual(len(page["appended"]), 1)
        # upsert 로 만든 행은 모두 웹이 읽는 v4 모양이어야 한다
        self.assertEqual(getattr(self.srv.state, "bad_row_ids", []), [])
        gid, gdb = self.c.create_grid(ws, parent["view_id"], "임시", [("A", "text")])
        self.assertEqual(self.c.database_id(ws, "임시"), gdb)
        self.c.trash_page(ws, gid)
        self.assertIn(gid, self.srv.state.trashed)
        with self.assertRaises(AppFlowyError):
            self.c.database_id(ws, "임시")


if __name__ == "__main__":
    unittest.main()

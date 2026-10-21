"""CLI 통합 — 임시 70_ASSETS 레지스트리 + 가짜 서버로 sync/find/favorites/push-* 를 끝까지 돌린다."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from studiohub.cli import main
from tests.fakes import FakeServer

DIRECTIVES_MD = """---
type: directive-log
---
| # | 날짜 | 지시 | 상태 | 결과 |
|---|---|---|---|---|
| 1 | 2026-10-18 | 첫 지시 \\| 파이프 포함 | 완료 | abc123 |
| 2 | 2026-10-19 | 둘째 지시 | 진행 | |
"""


class CliSyncTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer().__enter__()
        self.tmp = Path(tempfile.mkdtemp())
        root = self.tmp / "70_ASSETS"
        (root / "project-game-demo" / "library" / "dragons").mkdir(parents=True)
        (root / "project-game-demo" / "inbox" / "done").mkdir(parents=True)
        (root / "project-game-demo" / "library" / "history").mkdir()
        (root / "project-game-demo" / "library" / "dragons" / "black.png").write_bytes(b"png-black")
        (root / "project-game-demo" / "library" / "dragons" / "black.json").write_text(json.dumps({"schema": "asset-meta/v1", "key": "dragons/black", "kind": "dragon", "note": "검은 용"}), encoding="utf-8")
        (root / "project-game-demo" / "library" / "history" / "old.png").write_bytes(b"old")  # 건너뜀
        (root / "project-game-demo" / "inbox" / "cand1.webp").write_bytes(b"webp-1")
        (root / "project-game-demo" / "inbox" / "done" / "cand0.webp").write_bytes(b"webp-0")
        (root / "project-game-demo" / "inbox" / "notes.txt").write_text("x")  # 미디어 아님
        (root / "registry.json").write_text(json.dumps({"root": str(root), "slots": {"concept": "", "calibration": "", "library": "", "inbox": "", "export": ""}, "projects": {"project-game-demo": {"kind": "game"}}}), encoding="utf-8")
        vault = self.tmp / "vault" / "10_PROJECTS"
        vault.mkdir(parents=True)
        (vault / "project-game-demo.directives.md").write_text(DIRECTIVES_MD, encoding="utf-8")
        env = self.tmp / "studiohub.env"
        env.write_text(
            f"IMMICH_URL={self.srv.url}\nIMMICH_API_KEY=test-key\nAPPFLOWY_URL={self.srv.url}\nAPPFLOWY_EMAIL=user@test\nAPPFLOWY_PASSWORD=pw\n"
            f"STUDIOHUB_ASSETS_REGISTRY={root / 'registry.json'}\nSTUDIOHUB_STATE_DIR={self.tmp / 'state'}\nSTUDIOHUB_VAULT={self.tmp / 'vault'}\n",
            encoding="utf-8",
        )
        self.env = str(env)
        # 실제 환경 변수가 끼어들지 않게
        self._saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(("IMMICH_", "APPFLOWY_", "STUDIOHUB_"))}

    def tearDown(self):
        os.environ.update(self._saved)
        self.srv.__exit__(None, None, None)

    def run_cli(self, *args) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--env-file", self.env, *args])
        return code, buf.getvalue()

    def test_scan_and_sync(self):
        code, text = self.run_cli("scan", "demo")
        self.assertEqual(code, 0)
        self.assertIn("dragons/black.png", text)
        self.assertNotIn("history", text)
        self.assertNotIn("notes.txt", text)
        self.assertIn("3개", text)

        code, text = self.run_cli("immich", "sync", "demo", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("장부에 없음 3", text)
        self.assertEqual(self.srv.state.uploads, [])

        code, text = self.run_cli("immich", "sync", "demo")
        self.assertEqual(code, 0, text)
        st = self.srv.state
        self.assertEqual(len(st.uploads), 3)
        self.assertEqual(sorted(a["albumName"] for a in st.albums.values()), ["project-game-demo/inbox", "project-game-demo/library"])
        self.assertIn("project/project-game-demo", st.tags)
        self.assertIn("kind/dragon", st.tags)
        self.assertIn("dir/dragons", st.tags)
        black = next(a for a in st.assets.values() if a["originalFileName"] == "black.png")
        self.assertIn("key: dragons/black", black["description"])
        self.assertIn("검은 용", black["description"])
        ledger = json.loads((self.tmp / "state" / "immich_sync.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger), 3)

        # 두 번째 sync 는 장부 덕에 아무것도 안 올린다; --force 는 서버 중복 검사로 걸러진다
        code, text = self.run_cli("immich", "sync", "demo")
        self.assertIn("장부에 없음 0", text)
        self.assertEqual(len(st.uploads), 3)
        code, text = self.run_cli("immich", "sync", "demo", "--force")
        self.assertIn("서버 중복 3", text)
        self.assertEqual(len(st.uploads), 3)

        # find / similar / favorites / pull
        code, text = self.run_cli("immich", "find", "black", "--project", "demo", "--slot", "library")
        self.assertEqual(code, 0)
        self.assertIn("black.png", text)
        self.assertIn("project-game-demo/library/dragons/black.png", text)
        self.assertNotIn("cand1.webp", text)
        black["isFavorite"] = True
        code, text = self.run_cli("immich", "favorites", "demo")
        self.assertIn("♥ 1개", text)
        self.assertIn("black.png", text)
        code, text = self.run_cli("immich", "find", "black", "--project", "demo", "--pull", "-n", "1")
        pulled = list((self.tmp / "70_ASSETS" / "project-game-demo" / "inbox" / "from-immich").iterdir())
        self.assertEqual(len(pulled), 1)
        code, text = self.run_cli("immich", "similar", black["id"], "--project", "demo")
        self.assertEqual(code, 0)
        self.assertNotIn(black["id"], text.splitlines()[-1])

        # AppFlowy 로 지시·자산 push
        code, text = self.run_cli("appflowy", "push-directives", "project-game-demo", "--db", "지시 로그")
        self.assertEqual(code, 0, text)
        rows = st.databases["db-1"]["rows"]
        self.assertEqual(len(rows), 2)
        texts = sorted(r["cells"]["지시"] for r in rows.values())
        self.assertEqual(texts[0], "둘째 지시")
        self.assertEqual(texts[1], "첫 지시 | 파이프 포함")
        self.assertIn("날짜", st.databases["db-1"]["fields"])  # 없던 열은 자동 생성
        self.assertEqual(st.databases["db-1"]["field_types"]["날짜"], 0)  # RichText (번호 열은 이미 있었음)
        code, text = self.run_cli("appflowy", "push-directives", "project-game-demo", "--db", "지시 로그")
        self.assertEqual(len(rows), 2)  # upsert → 같은 수
        code, text = self.run_cli("appflowy", "push-directives", "project-game-demo", "--db", "자산 보드", "--no-create-fields", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("건너뛴 열", text)

        code, text = self.run_cli("appflowy", "push-assets", "demo", "--db", "자산 보드", "--slot", "library")
        self.assertEqual(code, 0, text)
        arows = st.databases["db-2"]["rows"]
        self.assertEqual(len(arows), 1)
        cells = next(iter(arows.values()))["cells"]
        self.assertEqual(cells["상태"], "승인")
        self.assertTrue(cells["Immich"].startswith(self.srv.url + "/photos/"))
        self.assertEqual(st.databases["db-2"]["field_types"]["sha1"], 0)

        # 새 그리드: 기본 열 이름(Name)이 달라도 기본 열로 매핑된다
        code, text = self.run_cli("appflowy", "grid", "--parent", "기획", "--title", "새 보드", "--fields", "번호:number,상태")
        self.assertEqual(code, 0, text)
        self.assertIn("Name*", text)
        self.assertIn("번호", text)
        code, text = self.run_cli("appflowy", "push-directives", "project-game-demo", "--db", "새 보드")
        self.assertEqual(code, 0, text)
        newdb = next(v for v in st.databases.values() if v.get("view_id") and v["name"] == "Grid")  # 페이지 이름으로 찾음
        self.assertEqual(sorted(r["cells"]["Name"] for r in newdb["rows"].values())[0], "둘째 지시")
        self.assertIn("프로젝트", newdb["fields"])

    def test_page_from_markdown(self):
        md = self.tmp / "gdd.md"
        md.write_text("# 제목\n\n문단 **굵게** 글\n\n- 항목\n- [x] 완료\n\n```python\nprint(1)\n```\n", encoding="utf-8")
        code, text = self.run_cli("appflowy", "page", "--parent", "기획", "--title", "GDD 초안", "--from", str(md), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("heading, paragraph, bulleted_list, todo_list, code", text)
        code, text = self.run_cli("appflowy", "page", "--parent", "기획", "--title", "GDD 초안", "--from", str(md))
        self.assertEqual(code, 0, text)
        page = next(iter(self.srv.state.pages.values()))
        self.assertEqual(page["parent_view_id"], "space-1")
        self.assertEqual(page["name"], "GDD 초안")
        kids = page["page_data"]["children"]
        self.assertEqual(kids[0]["data"]["level"], 1)
        self.assertEqual(kids[1]["data"]["delta"][1], {"insert": "굵게", "attributes": {"bold": True}})
        self.assertTrue(kids[3]["data"]["checked"])
        self.assertEqual(kids[4]["data"]["language"], "python")
        code, text = self.run_cli("appflowy", "page", "--parent", "없음", "--title", "x", "--from", str(md))
        self.assertNotEqual(code, 0)

    def test_include_and_max_files(self):
        code, text = self.run_cli("scan", "demo", "--include", "*.WEBP")
        self.assertEqual(code, 0)
        self.assertIn("cand1.webp", text)
        self.assertNotIn("black.png", text)
        code, text = self.run_cli("scan", "demo", "--include", "dragons/*", "--include", "cand1*")
        self.assertIn("2개", text)
        code, text = self.run_cli("immich", "sync", "demo", "--max-files", "2", "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("--max-files", text)
        self.assertEqual(self.srv.state.uploads, [])
        code, text = self.run_cli("immich", "sync", "demo", "--max-files", "0", "--include", "dragons/*")
        self.assertEqual(code, 0, text)
        self.assertEqual(len(self.srv.state.uploads), 1)

    def test_login_link(self):
        envf = self.tmp / "appflowy.env"
        envf.write_text("GOTRUE_JWT_SECRET=topsecret\nOTHER=1\n", encoding="utf-8")
        with open(self.env, "a", encoding="utf-8") as f:
            f.write(f"APPFLOWY_ENV_FILE={envf}\n")
        code, text = self.run_cli("appflowy", "login-link")
        self.assertEqual(code, 0, text)
        self.assertIn("/gotrue/verify?token=abc&type=magiclink", text)
        req = self.srv.state.magic_links[-1]
        self.assertEqual(req["email"], "user@test")
        self.assertEqual(req["redirect_to"], self.srv.url + "/login")
        code, text = self.run_cli("appflowy", "login-link", "--email", "other@test")
        self.assertEqual(self.srv.state.magic_links[-1]["email"], "other@test")
        from studiohub.cli import service_jwt
        import base64, json as _json
        tok = service_jwt("s")
        payload = _json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "==").decode())
        self.assertEqual(payload["role"], "supabase_admin")

    def test_doctor_and_errors(self):
        code, text = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("ping OK", text)
        self.assertIn("로그인 OK", text)
        code, text = self.run_cli("scan", "없는프로젝트")
        self.assertEqual(code, 2)
        self.assertIn("오류", text)


if __name__ == "__main__":
    unittest.main()

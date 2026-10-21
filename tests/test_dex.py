"""도감 프로필 — 게임 데이터 파서·키 해석·항목 조립 + CLI(dex tag/board/pull) 를 가짜 서버로."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from studiohub import dex
from studiohub.cli import main
from tests.fakes import FakeServer

SPECIES_TS = """export const SPECIES: readonly SpeciesDef[] = [
  { id: 'white', kind: 'pure', element: 'frost', name: '화이트 드래곤', grade: 'common', breath: '냉기', alignment: 'evil', habitat: 'snowfield',
    personality: ['야생적', '단순함', '먹보'], color: '#EAF2F8',
    promptTokens: {
      body: 'snow-white sleek icy scales', eyes: 'icy blue eyes', accent: 'tiny frost crystals on tail tip',
      elder: 'icicle spikes', avoid: 'beak, feathers' } },
  { id: 'black', kind: 'pure', element: 'dark', name: '블랙 드래곤', grade: 'common', breath: '산성', alignment: 'evil', habitat: 'swamp',
    personality: ['음침함', '고집', '늪 사랑'], color: '#2B2A33',
    promptTokens: { body: 'wet obsidian black scales', eyes: 'acid green eyes', accent: 'drip spots' } },
  { id: 'gem', kind: 'pure', element: 'light', name: '젬 드래곤', grade: 'legendary', breath: '빛', alignment: 'good', habitat: null,
    personality: ['고요함'], color: '#A0F',
    promptTokens: { body: 'crystal', eyes: 'violet', accent: 'gems' } },
];
"""
HYBRIDS_TS = """export const SIG_PART_LABEL: Record<SignatureSetIndex, string> = { 0: '날개', 1: '눈', 2: '뿔' };
export const SIG_COLOR_WORD: Record<SpeciesId, string> = {
  white: '서리', black: '칠흑', gem: '자수정',
};
export const HYBRID_FIRST_RELEASE: readonly HybridId[] = [
  'h_white_black_0', 'h_gem_white_0',
];
"""


class DexParseTest(unittest.TestCase):
    def test_species_and_hybrids(self):
        sp = dex.parse_species_ts(SPECIES_TS)
        self.assertEqual([s["id"] for s in sp], ["white", "black", "gem"])
        self.assertEqual(sp[0]["personality"], ["야생적", "단순함", "먹보"])
        self.assertIsNone(sp[2]["habitat"])
        self.assertEqual(sp[0]["tokens"]["elder"], "icicle spikes")
        self.assertEqual(sp[1]["tokens"]["body"], "wet obsidian black scales")
        t = dex.parse_hybrid_tables(HYBRIDS_TS)
        self.assertEqual(t["first_release"], ["h_white_black_0", "h_gem_white_0"])
        h = dex.hybrid_def("h_white_black_0", {s["id"]: s for s in sp}, t)
        self.assertEqual(h["name"], "화이트 칠흑날개 드래곤")
        self.assertEqual(h["element2"], "dark")
        self.assertEqual(h["element"], "frost")  # 냉기·어둠은 상극이 아님
        self.assertEqual(dex.hybrid_def("h_gem_black_0", {s["id"]: s for s in sp}, t)["element"], "chaos")  # 결정→어둠 상극 → 혼원
        self.assertEqual(dex.element_matchups("frost"), (["storm"], ["fire"]))
        self.assertIsNone(dex.hybrid_def("h_white_white_0", {s["id"]: s for s in sp}, t))
        self.assertIsNone(dex.hybrid_def("h_white_red_0", {s["id"]: s for s in sp}, t))

    def test_parse_key(self):
        self.assertEqual(dex.parse_key("dragons/black/adult_sleep.webp"), {"kind": "dragon", "species": "black", "stage": "adult", "pose": "sleep", "variant": "normal"})
        self.assertEqual(dex.parse_key("portraits/black/premium_3.webp")["n"], 3)
        h = dex.parse_key("done/dragons__h_black_brass_0__albino_egg_default.webp")
        self.assertEqual((h["species"], h["variant"], h["stage"]), ("h_black_brass_0", "albino", "egg"))
        self.assertIsNone(dex.parse_key("montage.png"))
        # library 로 옮긴 잡종 규칙: dragons/h_<a>_<b>_<k>/[albino_]<stage>_<pose>
        h2 = dex.parse_key("dragons/h_black_brass_0/albino_elder_sleep.webp")
        self.assertEqual((h2["species"], h2["variant"], h2["stage"], h2["pose"]), ("h_black_brass_0", "albino", "elder", "sleep"))
        self.assertEqual(dex.parse_key("dragons/h_black_brass_0/egg_default.webp")["variant"], "normal")
        self.assertIn("variant/albino", dex.tags_for(h))
        self.assertIn("hybrid", dex.tags_for(h))

    def test_entries_cells_document(self):
        sp = dex.parse_species_ts(SPECIES_TS)
        t = dex.parse_hybrid_tables(HYBRIDS_TS)
        assets = [{"rel": "dragons/white/adult_default.webp", "asset_id": "a1"}, {"rel": "dragons/white/egg_default.webp"},
                  {"rel": "portraits/white/premium_1.webp", "asset_id": "p1"}, {"rel": "done/dragons__h_black_gem_1__adult_default.webp", "asset_id": "h1"}]
        entries = {e.id: e for e in dex.build_entries(sp, t, assets)}
        self.assertEqual(set(entries), {"white", "black", "gem", "h_white_black_0", "h_gem_white_0", "h_black_gem_1"})
        w = entries["white"]
        self.assertEqual(w.count("normal"), 2)
        self.assertEqual(len(w.missing()), 5 + 7)
        cells = dex.entry_cells(w, "http://im", "http://im/albums/x")
        self.assertEqual(cells["드래곤"], "2/7")
        self.assertEqual(cells["초상"], "1/8")
        self.assertEqual(cells["대표"], "http://im/photos/a1")
        self.assertEqual(cells["상태"], "자산 부족")
        doc = dex.entry_document(w, "http://im")
        self.assertIn("## 도감 설명", doc)
        self.assertIn("[보기](http://im/photos/a1)", doc)
        self.assertIn("(미동기화)", doc)
        self.assertIn("- body: snow-white", doc)
        self.assertIn("![화이트 드래곤](immich://asset/a1/preview)", doc)
        self.assertIn("![성체](immich://asset/a1/thumbnail)", doc)
        resolved = dex.resolve_placeholders(doc, "http://im", "KEY")
        self.assertIn("![성체](http://im/api/assets/a1/thumbnail?size=thumbnail&key=KEY)", resolved)
        self.assertNotIn("immich://", resolved)
        nokey = dex.resolve_placeholders(doc, "http://im", None)
        self.assertIn("[성체](http://im/photos/a1)", nokey)
        self.assertNotIn("![성체]", nokey)
        hb = entries["h_black_gem_1"]
        self.assertEqual(hb.meta["name"], "블랙 자수정눈 드래곤")
        self.assertEqual(dex.entry_cells(hb, "http://im", None)["알비노"], "0/7")
        self.assertEqual(dex.entry_cells(hb, "http://im", None)["초상"], "")


class DexCliTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer().__enter__()
        self.tmp = Path(tempfile.mkdtemp())
        root = self.tmp / "70_ASSETS"
        lib = root / "project-game-dy" / "library"
        (lib / "dragons" / "white").mkdir(parents=True)
        (lib / "portraits" / "white").mkdir(parents=True)
        (root / "project-game-dy" / "inbox" / "done").mkdir(parents=True)
        for rel in ["dragons/white/adult_default.webp", "dragons/white/egg_default.webp", "portraits/white/premium_1.webp"]:
            (lib / rel).write_bytes(b"img-" + rel.encode())
        (root / "project-game-dy" / "inbox" / "done" / "dragons__h_white_black_0__albino_adult_sleep.webp").write_bytes(b"hyb")
        (root / "registry.json").write_text(json.dumps({"root": str(root), "slots": {"concept": "", "calibration": "", "library": "", "inbox": "", "export": ""}, "projects": {"project-game-dy": {"kind": "game"}}}), encoding="utf-8")
        data = self.tmp / "data"
        data.mkdir()
        (data / "species.ts").write_text(SPECIES_TS, encoding="utf-8")
        (data / "hybrids.ts").write_text(HYBRIDS_TS, encoding="utf-8")
        env = self.tmp / "studiohub.env"
        env.write_text(
            f"IMMICH_URL={self.srv.url}\nIMMICH_API_KEY=test-key\nAPPFLOWY_URL={self.srv.url}\nAPPFLOWY_EMAIL=user@test\nAPPFLOWY_PASSWORD=pw\n"
            f"STUDIOHUB_ASSETS_REGISTRY={root / 'registry.json'}\nSTUDIOHUB_STATE_DIR={self.tmp / 'state'}\nSTUDIOHUB_DEX_DATA={data}\n",
            encoding="utf-8",
        )
        self.env = str(env)
        self._saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(("IMMICH_", "APPFLOWY_", "STUDIOHUB_"))}

    def tearDown(self):
        os.environ.update(self._saved)
        self.srv.__exit__(None, None, None)

    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--env-file", self.env, *args])
        return code, buf.getvalue()

    def test_tag_board_pull(self):
        code, text = self.run_cli("dex", "board", "dy")
        self.assertEqual(code, 2)  # 먼저 sync 가 필요하진 않지만 그리드는 생성 가능 — 여기선 부모 뷰 없음
        self.assertIn("상위 뷰", text)
        code, text = self.run_cli("immich", "sync", "dy")
        self.assertEqual(code, 0, text)
        code, text = self.run_cli("dex", "tag", "dy")
        self.assertEqual(code, 0, text)
        st = self.srv.state
        self.assertIn("species/white", st.tags)
        self.assertIn("variant/albino", st.tags)
        self.assertEqual(sorted(a["albumName"] for a in st.albums.values() if "/dex/" in a["albumName"]), ["project-game-dy/dex/h_white_black_0", "project-game-dy/dex/white"])
        # 분류 앨범: 종류·단계·포즈·속성·등급·서식지·종별, 옛 dex/<id> 앨범은 종별 이름으로
        code, text = self.run_cli("dex", "albums", "dy", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("project-game-dy › 단계 › 성체: 2장", text)  # white adult + 잡종 albino adult_sleep
        self.assertEqual(len([a for a in st.albums.values() if "›" in a["albumName"]]), 0)
        code, text = self.run_cli("dex", "albums", "dy")
        self.assertEqual(code, 0, text)
        names = {a["albumName"] for a in st.albums.values()}
        self.assertIn("project-game-dy › 종류 › 순종", names)
        self.assertIn("project-game-dy › 종류 › 알비노", names)
        self.assertIn("project-game-dy › 종류 › 초상 (폴리모프)", names)
        self.assertIn("project-game-dy › 속성 › 냉기", names)
        self.assertIn("project-game-dy › 종 › 화이트 드래곤 (white)", names)
        self.assertNotIn("project-game-dy/dex/white", names)  # 이름 바꿈
        self.assertIn("이름 바꿈 2", text)
        code, text = self.run_cli("dex", "albums", "dy")  # 멱등
        self.assertIn("새로 0 · 이름 바꿈 0", text)

        code, text = self.run_cli("dex", "board", "dy", "--parent", "기획", "--db", "도감")
        self.assertEqual(code, 0, text)
        self.assertIn("항목 5", text)
        db = next(v for v in st.databases.values() if v.get("view_id") and v["name"] == "Grid")
        self.assertIn("누락", db["fields"])
        rows = {r["cells"]["Name"]: r for r in db["rows"].values()}
        self.assertEqual(rows["화이트 드래곤"]["cells"]["드래곤"], "2/7")
        self.assertIn("## 도감 설명", rows["화이트 드래곤"]["document"])
        self.assertTrue(rows["화이트 드래곤"]["cells"]["앨범"].startswith(self.srv.url + "/albums/"))
        self.assertEqual(rows["화이트 칠흑날개 드래곤"]["cells"]["알비노"], "1/7")
        # 두 번째 board: 행 수 그대로, 문서는 다시 보내지 않는다(사용자 글 보존)
        for r in db["rows"].values():
            r["document"] = "사용자가 쓴 설명"
        code, text = self.run_cli("dex", "board", "dy", "--parent", "기획", "--db", "도감")
        self.assertEqual(code, 0, text)
        self.assertEqual(len(db["rows"]), 5)
        self.assertTrue(all(r["document"] == "사용자가 쓴 설명" for r in db["rows"].values()))
        # --docs: 파일 본문이 행 문서를 덮어쓴다 (frontmatter 제거)
        dd = self.tmp / "docs_dex"
        dd.mkdir()
        (dd / "white.md").write_text("---\nid: white\n---\n\n# 화이트\n\n파일에서 온 설명\n", encoding="utf-8")
        code, text = self.run_cli("dex", "board", "dy", "--parent", "기획", "--db", "도감", "--docs", str(dd))
        self.assertEqual(code, 0, text)
        self.assertIn("문서 덮어씀(--docs) 1", text)
        rows = {r["cells"]["Name"]: r for r in db["rows"].values()}  # 가짜 서버가 행 객체를 새로 만든다
        self.assertEqual(rows["화이트 드래곤"]["document"], "# 화이트\n\n파일에서 온 설명\n")
        self.assertEqual(rows["화이트 칠흑날개 드래곤"]["document"], "사용자가 쓴 설명")
        for r in db["rows"].values():
            r["document"] = "사용자가 쓴 설명"
        # 공유 링크 + 그림: share → board --docs 는 자리표시자에 키를 붙이고, gallery 는 이미지 블록 페이지를 만든다
        (dd / "white.md").write_text("---\nid: white\n---\n\n# 화이트\n\n![성체](immich://asset/%s/preview)\n\n설명\n" % next(a["id"] for a in st.assets.values() if a["originalFileName"] == "adult_default.webp"), encoding="utf-8")
        code, text = self.run_cli("immich", "share", "dy")
        self.assertEqual(code, 0, text)
        self.assertIn("공유 링크 자산 4개", text)
        link = st.shared_links[0]
        self.assertEqual(len(link["assets"]), 4)
        code, text = self.run_cli("immich", "share", "dy")  # 두 번째: 추가 0
        self.assertIn("이번에 추가 0", text)
        self.assertEqual(len(st.shared_links), 1)
        code, text = self.run_cli("dex", "board", "dy", "--parent", "기획", "--db", "도감", "--docs", str(dd))
        self.assertEqual(code, 0, text)
        rows = {r["cells"]["Name"]: r for r in db["rows"].values()}
        self.assertEqual(st.databases[[k for k, v in st.databases.items() if v.get("view_id") and v["name"] == "Grid"][0]]["field_types"]["그림"], 14)  # Media
        pic = rows["화이트 드래곤"]["cells"]["그림"]
        self.assertEqual([f["name"] for f in pic["files"]], ["성체", "알"])
        self.assertTrue(all(f["upload_type"] == 1 and f["file_type"] == 1 and f"key={link['key']}" in f["url"] for f in pic["files"]))
        self.assertNotIn("그림", rows["화이트 칠흑날개 드래곤"]["cells"])  # 그림 없는 항목은 셀 없음
        self.assertIn(f"/api/assets/", rows["화이트 드래곤"]["document"])
        self.assertIn(f"key={link['key']}", rows["화이트 드래곤"]["document"])
        self.assertNotIn("immich://", rows["화이트 드래곤"]["document"])
        code, text = self.run_cli("dex", "gallery", "dy", "--parent", "기획")
        self.assertEqual(code, 0, text)
        gal = next(p for p in st.pages.values() if p.get("name") == "도감 그림판")
        imgs = [b for b in gal["page_data"]["children"] if b["type"] == "image"]
        self.assertEqual(len(imgs), 2)  # white 성체·알 (잡종은 알비노 수면만 있어 그림 없음)
        self.assertTrue(all(b["data"]["image_type"] == 2 and "key=" in b["data"]["url"] for b in imgs))
        code, text = self.run_cli("dex", "gallery", "dy", "--parent", "기획")  # 같은 제목은 옛 것을 휴지통으로
        self.assertEqual(sum(1 for p in st.pages.values() if p.get("name") == "도감 그림판"), 2)
        self.assertEqual(len(st.trashed), 1)
        for r in db["rows"].values():
            r["document"] = "사용자가 쓴 설명"
        # pull → md 파일
        out = self.tmp / "dexdocs"
        code, text = self.run_cli("dex", "pull", "dy", "--db", "도감", "--out", str(out))
        self.assertEqual(code, 0, text)
        files = sorted(p.name for p in out.iterdir())
        self.assertIn("white.md", files)
        self.assertIn("h_white_black_0.md", files)
        self.assertIn("사용자가 쓴 설명", (out / "white.md").read_text(encoding="utf-8"))
        self.assertIn("id: white", (out / "white.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

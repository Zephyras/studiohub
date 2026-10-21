import os
import tempfile
import unittest
from pathlib import Path

from studiohub.config import Settings
from studiohub.directives import parse_directives
from studiohub.http_client import encode_multipart
from studiohub.markdown_blocks import delta, markdown_to_blocks


class MarkdownBlocksTest(unittest.TestCase):
    def test_types(self):
        md = "## 둘째\n\n첫 줄\n둘째 줄\n\n1. 하나\n2) 둘\n\n> 인용\n\n---\n\n| a | b |\n"
        types = [b["type"] for b in markdown_to_blocks(md)]
        self.assertEqual(types, ["heading", "paragraph", "numbered_list", "numbered_list", "quote", "divider", "paragraph"])
        para = markdown_to_blocks(md)[1]
        self.assertEqual(para["data"]["delta"], [{"insert": "첫 줄 둘째 줄"}])

    def test_heading_level_cap_and_inline(self):
        b = markdown_to_blocks("###### 깊은 `코드` 제목")[0]
        self.assertEqual(b["data"]["level"], 3)
        self.assertEqual(b["data"]["delta"][1], {"insert": "코드", "attributes": {"code": True}})
        self.assertEqual(delta(""), [{"insert": ""}])

    def test_unclosed_code_fence(self):
        blocks = markdown_to_blocks("```\nx = 1\n")
        self.assertEqual(blocks[0]["type"], "code")
        self.assertEqual(blocks[0]["data"]["delta"][0]["insert"], "x = 1")


class DirectivesTest(unittest.TestCase):
    def test_parse(self):
        md = "| # | 날짜 | 지시 | 상태 | 결과 |\n|---|---|---|---|---|\n| 3 | 2026-10-19 | 가 | 진행 | |\n| x | - | 무시 | | |\n| 4 | 2026-10-19 | 나 \\| 다 | 완료 | r |\n"
        ds = parse_directives(md)
        self.assertEqual([d.number for d in ds], [3, 4])
        self.assertEqual(ds[1].text, "나 | 다")
        self.assertEqual(ds[0].result, "")
        self.assertEqual(ds[1].as_dict()["status"], "완료")

    def test_no_table(self):
        self.assertEqual(parse_directives("아무것도 없음"), [])


class MultipartTest(unittest.TestCase):
    def test_encode(self):
        body, ctype = encode_multipart({"a": "1"}, {"f": ("x.png", b"\x00\x01", None)})
        boundary = ctype.split("boundary=")[1]
        self.assertTrue(body.startswith(b"--" + boundary.encode()))
        self.assertIn(b'name="f"; filename="x.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertTrue(body.endswith(b"--" + boundary.encode() + b"--\r\n"))


class SettingsTest(unittest.TestCase):
    def test_precedence(self):
        tmp = Path(tempfile.mkdtemp())
        env = tmp / "s.env"
        env.write_text("IMMICH_URL=http://file:1/\nIMMICH_API_KEY='k'\n# 주석\nAPPFLOWY_EMAIL=a@b\n", encoding="utf-8")
        s = Settings.load(env, environ={"IMMICH_URL": "http://env:2"})
        self.assertEqual(s.immich_url, "http://env:2")
        self.assertEqual(s.immich_api_key, "k")
        self.assertEqual(s.appflowy_email, "a@b")
        self.assertEqual(s.sources["IMMICH_URL"], "env")
        self.assertEqual(s.sources["IMMICH_API_KEY"], "file")
        self.assertEqual(s.sources["APPFLOWY_URL"], "default")
        self.assertEqual(s.appflowy_url, "http://localhost:8070")

    def test_real_registry_resolves(self):
        s = Settings.load(Path(tempfile.mkdtemp()) / "none.env", environ={})
        if not s.registry_path.exists():
            self.skipTest("작업공간 registry 없음")
        self.assertIn("library", s.slots())
        self.assertEqual(s.resolve_project("dotkit"), "project-tool-dotkit")
        with self.assertRaises(KeyError):
            s.resolve_project("project-game")  # 여러 개 일치


if __name__ == "__main__":
    unittest.main()

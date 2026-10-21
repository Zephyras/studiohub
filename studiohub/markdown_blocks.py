"""Markdown(부분집합) → AppFlowy SerdeBlock 목록.

지원: # 제목(1~3), - / * 글머리, 1. 번호, - [ ] / - [x] 할 일, > 인용, ``` 코드 펜스, --- 구분선, 일반 문단.
인라인 서식은 **굵게** 와 `코드` 만 delta attributes 로 옮기고 나머지는 글자 그대로 둔다.
"""
from __future__ import annotations

import re

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")


def delta(text: str) -> list[dict]:
    ops: list[dict] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            ops.append({"insert": part[2:-2], "attributes": {"bold": True}})
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            ops.append({"insert": part[1:-1], "attributes": {"code": True}})
        else:
            ops.append({"insert": part})
    return ops or [{"insert": ""}]


def block(ty: str, text: str = "", **data) -> dict:
    d = dict(data)
    if ty != "divider":
        d["delta"] = delta(text)
    return {"type": ty, "data": d, "children": []}


def markdown_to_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush():
        nonlocal para
        if para:
            blocks.append(block("paragraph", " ".join(s.strip() for s in para)))
            para = []

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            lang = s[3:].strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append({"type": "code", "data": {"language": lang or "plain", "delta": [{"insert": "\n".join(code)}]}, "children": []})
            i += 1
            continue
        if not s:
            flush()
        elif s in ("---", "***", "___"):
            flush()
            blocks.append(block("divider"))
        elif (m := re.match(r"^(#{1,6})\s+(.*)$", s)):
            flush()
            blocks.append(block("heading", m.group(2), level=min(len(m.group(1)), 3)))
        elif (m := re.match(r"^[-*+]\s+\[([ xX])\]\s+(.*)$", s)):
            flush()
            blocks.append(block("todo_list", m.group(2), checked=m.group(1).lower() == "x"))
        elif (m := re.match(r"^[-*+]\s+(.*)$", s)):
            flush()
            blocks.append(block("bulleted_list", m.group(1)))
        elif (m := re.match(r"^\d+[.)]\s+(.*)$", s)):
            flush()
            blocks.append(block("numbered_list", m.group(1)))
        elif s.startswith(">"):
            flush()
            blocks.append(block("quote", s.lstrip("> ").strip()))
        elif s.startswith("|"):
            flush()
            blocks.append(block("paragraph", s))  # 표는 AppFlowy 표 블록 구조가 복잡해 글자로 둔다
        else:
            para.append(line)
        i += 1
    flush()
    return blocks

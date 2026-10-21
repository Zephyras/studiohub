"""지식창고 지시 로그(vault/10_PROJECTS/<project>.directives.md) 표 파서.

kb.py 가 만드는 표: | # | 날짜 | 지시 | 상태 | 결과 |
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Directive:
    number: int
    date: str
    text: str
    status: str
    result: str

    def as_dict(self) -> dict:
        return asdict(self)


def _split_row(line: str) -> list[str]:
    # 셀 안의 이스케이프된 \| 는 보존
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [p.replace("\\|", "|").strip() for p in parts]


def parse_directives(md: str) -> list[Directive]:
    out: list[Directive] = []
    header_seen = False
    for line in md.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _split_row(line)
        if not header_seen:
            if cells and cells[0] in ("#", "번호"):
                header_seen = True
            continue
        if cells and set(cells[0]) <= set("-: "):
            continue
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        cells += [""] * (5 - len(cells))
        out.append(Directive(int(cells[0]), cells[1], cells[2], cells[3], cells[4]))
    return out


def directives_path(vault_dir: Path, project: str) -> Path:
    return vault_dir / "10_PROJECTS" / f"{project}.directives.md"


def load_directives(vault_dir: Path, project: str) -> list[Directive]:
    p = directives_path(vault_dir, project)
    if not p.exists():
        raise FileNotFoundError(f"지시 로그 없음: {p}")
    return parse_directives(p.read_text(encoding="utf-8"))

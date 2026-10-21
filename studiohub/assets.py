"""70_ASSETS/<project>/<slot>/ 스캔 → Immich 에 올릴 로컬 자산 목록·태그·설명.

규칙
  앨범  = "<project>/<slot>"  (예: project-game-dragon-yuhui/library)
  태그  = project/<p> · slot/<slot> · kind/<meta.kind> · run/<calibration 런 폴더> · dir/<첫 하위 폴더>
  설명  = 같은 이름 .json 사이드카(asset-meta/v1)의 key·note + 상대 경로 — Immich 메타데이터 검색(description)용
동기화 장부 = <state_dir>/immich_sync.json  {sha1: {asset_id, project, slot, rel, uploaded_at}}
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
SKIP_DIRS = {"history", "__pycache__", ".git", "from-immich"}


def sha1_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass
class LocalAsset:
    project: str
    slot: str
    path: Path
    rel: str  # 슬롯 기준 상대 경로 (posix)
    size: int
    sha1: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def device_asset_id(self) -> str:
        return f"{self.project}/{self.slot}/{self.rel}"

    @property
    def album(self) -> str:
        return f"{self.project}/{self.slot}"

    def tags(self) -> list[str]:
        tags = [f"project/{self.project}", f"slot/{self.slot}"]
        parts = self.rel.split("/")
        if len(parts) > 1:
            tags.append(f"run/{parts[0]}" if self.slot == "calibration" else f"dir/{parts[0]}")
        kind = self.meta.get("kind")
        if kind:
            tags.append(f"kind/{kind}")
        return tags

    def description(self) -> str:
        bits = [f"{self.project}/{self.slot}/{self.rel}"]
        for k in ("key", "note", "prompt"):
            v = self.meta.get(k)
            if v:
                bits.append(f"{k}: {v}")
        return "\n".join(bits)

    def ensure_sha1(self) -> str:
        if not self.sha1:
            self.sha1 = sha1_of(self.path)
        return self.sha1


def _sidecar(path: Path) -> dict:
    sc = path.with_suffix(".json")
    if sc.exists():
        try:
            d = json.loads(sc.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def scan(
    assets_root: Path,
    project: str,
    slots: Iterable[str],
    exts: set[str] = MEDIA_EXTS,
    max_bytes: int = 500 << 20,
    include: Iterable[str] | None = None,
) -> list[LocalAsset]:
    """include = 슬롯 기준 상대 경로에 대한 글롭 목록(fnmatch, 대소문자 무시). 하나라도 맞으면 포함."""
    patterns = [g.lower() for g in (include or []) if g]
    out: list[LocalAsset] = []
    for slot in slots:
        base = assets_root / project / slot
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            rel_parts = p.relative_to(base).parts
            if SKIP_DIRS & set(rel_parts[:-1]):
                continue
            if patterns and not any(fnmatch.fnmatch("/".join(rel_parts).lower(), g) for g in patterns):
                continue
            size = p.stat().st_size
            if size == 0 or size > max_bytes:
                continue
            out.append(LocalAsset(project, slot, p, p.relative_to(base).as_posix(), size, meta=_sidecar(p)))
    return out


class SyncLedger:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get(self, sha1: str) -> dict | None:
        return self.data.get(sha1)

    def put(self, asset: LocalAsset, asset_id: str, status: str) -> None:
        self.data[asset.sha1] = {
            "asset_id": asset_id,
            "project": asset.project,
            "slot": asset.slot,
            "rel": asset.rel,
            "status": status,
            "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def by_project(self, project: str, slot: str | None = None) -> list[tuple[str, dict]]:
        return [(k, v) for k, v in self.data.items() if v.get("project") == project and (slot is None or v.get("slot") == slot)]

    def by_asset_id(self) -> dict[str, dict]:
        return {v["asset_id"]: {"sha1": k, **v} for k, v in self.data.items() if v.get("asset_id")}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

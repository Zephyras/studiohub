"""설정 — 환경 변수 > studiohub.env 파일 > 기본값. 자산 경로의 진실은 70_ASSETS/registry.json.

읽는 키
  IMMICH_URL (기본 http://localhost:2283) · IMMICH_API_KEY
  APPFLOWY_URL (기본 http://localhost:8070) · APPFLOWY_EMAIL · APPFLOWY_PASSWORD · APPFLOWY_WORKSPACE(이름 또는 id)
  STUDIOHUB_ASSETS_REGISTRY (기본 <작업공간>/70_ASSETS/registry.json) · STUDIOHUB_STATE_DIR (기본 <프로젝트>/.state)
  STUDIOHUB_VAULT (기본 <작업공간>/20_KNOWLEDGE/vault) — 지시 로그 위치
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parents[2]  # 기본값: 저장소가 <작업공간>/10_PROJECTS/active/<이름> 에 있다고 가정. 환경 변수로 전부 바꿀 수 있다
ENV_FILE = PROJECT_ROOT / "studiohub.env"


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


@dataclass
class Settings:
    immich_url: str = "http://localhost:2283"
    immich_api_key: str = ""
    appflowy_url: str = "http://localhost:8070"
    appflowy_email: str = ""
    appflowy_password: str = ""
    appflowy_workspace: str = ""
    registry_path: Path = WORKSPACE_ROOT / "70_ASSETS" / "registry.json"
    state_dir: Path = PROJECT_ROOT / ".state"
    vault_dir: Path = WORKSPACE_ROOT / "20_KNOWLEDGE" / "vault"
    sources: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)  # env 파일의 그 밖 키 (STUDIOHUB_DEX_DATA 등)

    @classmethod
    def load(cls, env_file: Path | None = None, environ: dict[str, str] | None = None) -> "Settings":
        environ = dict(os.environ if environ is None else environ)
        file_vals = load_env_file(env_file or ENV_FILE)
        s = cls()

        def pick(key: str, default: str) -> str:
            if key in environ and environ[key] != "":
                s.sources[key] = "env"
                return environ[key]
            if key in file_vals:
                s.sources[key] = "file"
                return file_vals[key]
            s.sources[key] = "default"
            return default

        s.immich_url = pick("IMMICH_URL", s.immich_url).rstrip("/")
        s.immich_api_key = pick("IMMICH_API_KEY", "")
        s.appflowy_url = pick("APPFLOWY_URL", s.appflowy_url).rstrip("/")
        s.appflowy_email = pick("APPFLOWY_EMAIL", "")
        s.appflowy_password = pick("APPFLOWY_PASSWORD", "")
        s.appflowy_workspace = pick("APPFLOWY_WORKSPACE", "")
        s.registry_path = Path(pick("STUDIOHUB_ASSETS_REGISTRY", str(s.registry_path)))
        s.state_dir = Path(pick("STUDIOHUB_STATE_DIR", str(s.state_dir)))
        s.vault_dir = Path(pick("STUDIOHUB_VAULT", str(s.vault_dir)))
        s.extra = {k: v for k, v in file_vals.items() if k not in s.sources}
        return s

    # ---- 70_ASSETS registry ----
    def registry(self) -> dict:
        reg = json.loads(self.registry_path.read_text(encoding="utf-8"))
        root = reg.get("root")
        reg["_root"] = Path(root).resolve() if root else self.registry_path.parent.resolve()
        return reg

    def assets_root(self) -> Path:
        return self.registry()["_root"]

    def project_names(self) -> list[str]:
        return list(self.registry()["projects"])

    def slots(self) -> list[str]:
        return list(self.registry()["slots"])

    def project_dir(self, project: str) -> Path:
        reg = self.registry()
        if project not in reg["projects"]:
            raise KeyError(f"registry 에 없는 프로젝트: {project} (등록: {', '.join(reg['projects'])})")
        return reg["_root"] / project

    def resolve_project(self, name: str) -> str:
        """정확한 이름 또는 부분 일치(유일할 때)로 프로젝트 이름을 돌려준다."""
        names = self.project_names()
        if name in names:
            return name
        hits = [n for n in names if name in n]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"프로젝트를 특정할 수 없음: {name} → {hits or names}")

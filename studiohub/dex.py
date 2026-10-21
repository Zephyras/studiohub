"""dragon-yuhui 도감(codex) 프로필 — 게임의 종 표(packages/shared/src/data)를 읽고, 자산 키를 해석해 도감 항목을 조립한다.

도감 항목 = 순종 12종 + 잡종(1차 출시 12종 + 자산이 있는 잡종). 항목마다
  드래곤 자산 7칸(알 기본 · 해츨링/성체/고룡 × 기본/수면) · 알비노 7칸(잡종) · 초상 8칸(순종)
자산 키(70_ASSETS 상대 경로, 슬롯 기준)
  dragons/<species>/<stage>_<pose>.webp        순종 드래곤
  portraits/<species>/premium_<n>.webp         순종 초상
  …/dragons__h_<base>_<sig>_<k>__[albino_]<stage>_<pose>.webp   잡종(inbox/done 이름 규칙)
정본은 게임 코드(species.ts·hybrids.ts)와 70_ASSETS 파일이다. 이 모듈은 읽기만 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ["egg", "hatchling", "adult", "elder"]
STAGE_KO = {"egg": "알", "hatchling": "해츨링", "adult": "성체", "elder": "고룡"}
POSE_KO = {"default": "기본", "sleep": "수면"}
SLOTS = [("egg", "default")] + [(s, p) for s in STAGES[1:] for p in ("default", "sleep")]  # 7
PORTRAIT_COUNT = 8
# elements.ts ELEMENT_LABEL · ELEMENT_CHART 와 같은 값 (잡종 혼원 판정은 설정이 아니라 이 상수 — 코드 주석 그대로)
ELEMENT_KO = {"fire": "화염", "frost": "냉기", "storm": "폭풍", "current": "해류", "poison": "독", "crystal": "결정", "dark": "어둠", "light": "빛", "ancient": "고대", "chaos": "혼원"}
ELEMENT_CHART = {"fire": ["frost"], "frost": ["storm"], "storm": ["current"], "current": ["fire"], "poison": ["crystal"], "crystal": ["dark"], "dark": ["light"], "light": ["dark"]}
GRADE_KO = {"common": "일반", "uncommon": "고급", "rare": "희귀", "epic": "영웅", "legendary": "전설"}
ALIGN_KO = {"good": "선", "evil": "악", "neutral": "중립"}
HABITAT_KO = {"volcano": "화산", "desert": "사막", "forest": "삼림", "swamp": "늪지", "snowfield": "설원", "coast": "해안"}


def is_opposed(a: str, b: str) -> bool:
    return a != b and (b in ELEMENT_CHART.get(a, []) or a in ELEMENT_CHART.get(b, []))


def element_matchups(e: str) -> tuple[list[str], list[str]]:
    """(강한 상대, 약한 상대) — 상성표 기준."""
    strong = ELEMENT_CHART.get(e, [])
    weak = [k for k, v in ELEMENT_CHART.items() if e in v]
    return strong, weak

_SPECIES_RE = re.compile(
    r"\{\s*id:\s*'(?P<id>\w+)',\s*kind:\s*'pure',\s*element:\s*'(?P<element>\w+)',\s*name:\s*'(?P<name>[^']+)',\s*grade:\s*'(?P<grade>\w+)',"
    r"\s*breath:\s*'(?P<breath>[^']+)',\s*alignment:\s*'(?P<alignment>\w+)',\s*habitat:\s*(?P<habitat>null|'\w+'),"
    r"\s*personality:\s*\[(?P<personality>[^\]]*)\],\s*color:\s*'(?P<color>[^']+)'"
    r"(?P<rest>.*?)(?=\n\s*\{\s*id:|\n\];)",
    re.S,
)
_TOKEN_RE = re.compile(r"\b(body|eyes|accent|elder|avoid):\s*'([^']*)'")


def parse_species_ts(text: str) -> list[dict]:
    out = []
    for m in _SPECIES_RE.finditer(text):
        d = m.groupdict()
        d["habitat"] = None if d["habitat"] == "null" else d["habitat"].strip("'")
        d["personality"] = [p.strip().strip("'\"") for p in d["personality"].split(",") if p.strip()]
        tokens: dict[str, str] = {}
        for k, v in _TOKEN_RE.findall(d.pop("rest")):
            tokens.setdefault(k, v)  # 첫 등장만 (portraitTags 안의 eyes 는 제외)
        d["tokens"] = tokens
        d["kind"] = "pure"
        out.append(d)
    return out


def parse_hybrid_tables(text: str) -> dict:
    def table(name: str) -> dict[str, str]:
        m = re.search(name + r"[^=]*=\s*\{(.*?)\};", text, re.S)
        return dict(re.findall(r"(\w+):\s*'([^']*)'", m.group(1))) if m else {}
    first = re.search(r"HYBRID_FIRST_RELEASE[^=]*=\s*\[(.*?)\];", text, re.S)
    return {
        "color_word": table("SIG_COLOR_WORD"),
        "part_label": table("SIG_PART_LABEL"),
        "first_release": re.findall(r"'(h_\w+)'", first.group(1)) if first else [],
    }


_HYBRID_ID = re.compile(r"^h_([a-z]+)_([a-z]+)_([0-2])$")


def hybrid_def(hid: str, species_by_id: dict[str, dict], tables: dict) -> dict | None:
    m = _HYBRID_ID.match(hid)
    if not m:
        return None
    base, sig, k = m.group(1), m.group(2), m.group(3)
    a, b = species_by_id.get(base), species_by_id.get(sig)
    if not a or not b or base == sig:
        return None
    short = re.sub(r" 드래곤.*$", "", a["name"])
    name = f"{short} {tables['color_word'].get(sig, sig)}{tables['part_label'].get(k, k)} 드래곤"
    return {
        "id": hid, "kind": "hybrid", "name": name, "grade": a["grade"],
        "element": "chaos" if is_opposed(a["element"], b["element"]) else a["element"], "base_element": a["element"],
        "element2": b["element"] if b["element"] != a["element"] else None, "breath": a["breath"], "alignment": a["alignment"],
        "habitat": a["habitat"], "personality": list(a["personality"]), "color": a["color"], "parents": {"base": base, "sig": sig, "k": int(k)},
        "tokens": {},
    }


# ---- 자산 키 ----
# library/dragons/<종>/[albino_]<단계>_<포즈> — 종은 순종(black) 또는 잡종(h_black_brass_0, 2026-10-21 저녁 library 로 이동한 규칙)
_PURE = re.compile(r"^dragons/(?P<species>[a-z]+|h_[a-z]+_[a-z]+_[0-2])/(?P<albino>albino_)?(?P<stage>egg|hatchling|adult|elder)_(?P<pose>default|sleep)\.\w+$")
_PORTRAIT = re.compile(r"^portraits/(?P<species>[a-z]+)/premium_(?P<n>\d+)\.\w+$")
_HYB = re.compile(r"(?:^|/)dragons__(?P<species>h_[a-z]+_[a-z]+_[0-2])__(?P<albino>albino_)?(?P<stage>egg|hatchling|adult|elder)_(?P<pose>default|sleep)\.\w+$")


def parse_key(rel: str) -> dict | None:
    """슬롯 기준 상대 경로 → {kind, species, stage, pose, variant, n}. 모르는 파일은 None."""
    rel = rel.replace("\\", "/")
    if m := _PURE.match(rel):
        return {"kind": "dragon", "species": m["species"], "stage": m["stage"], "pose": m["pose"], "variant": "albino" if m["albino"] else "normal"}
    if m := _PORTRAIT.match(rel):
        return {"kind": "portrait", "species": m["species"], "n": int(m["n"])}
    if m := _HYB.search(rel):
        return {"kind": "dragon", "species": m["species"], "stage": m["stage"], "pose": m["pose"], "variant": "albino" if m["albino"] else "normal"}
    return None


def tags_for(info: dict) -> list[str]:
    tags = [f"species/{info['species']}", f"dexkind/{info['kind']}"]
    if info["kind"] == "dragon":
        tags += [f"stage/{info['stage']}", f"pose/{info['pose']}", f"variant/{info['variant']}"]
        if info["species"].startswith("h_"):
            tags.append("hybrid")
    return tags


# ---- 도감 항목 ----
@dataclass
class DexEntry:
    meta: dict
    slots: dict[tuple[str, str, str], dict] = field(default_factory=dict)  # (variant, stage, pose) -> {rel, asset_id?}
    portraits: dict[int, dict] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def is_hybrid(self) -> bool:
        return self.meta["kind"] == "hybrid"

    def count(self, variant: str) -> int:
        return sum(1 for (v, s, p) in self.slots if v == variant)

    def missing(self) -> list[str]:
        out = [f"{STAGE_KO[s]} {POSE_KO[p]}" for s, p in SLOTS if ("normal", s, p) not in self.slots]
        if self.is_hybrid:
            out += [f"알비노 {STAGE_KO[s]} {POSE_KO[p]}" for s, p in SLOTS if ("albino", s, p) not in self.slots]
        else:
            out += [f"초상 {n}" for n in range(1, PORTRAIT_COUNT + 1) if n not in self.portraits]
        return out

    def status(self) -> str:
        return "자산 완료" if not self.missing() else "자산 부족"


def build_entries(species: list[dict], tables: dict, assets: list[dict], extra_hybrids: list[str] | None = None) -> list[DexEntry]:
    """assets = [{rel, asset_id?}] (sl�롯 안 상대 경로). 순종 전부 + (1차 출시 ∪ 자산에 있는) 잡종."""
    by_id = {s["id"]: s for s in species}
    entries: dict[str, DexEntry] = {s["id"]: DexEntry(s) for s in species}
    parsed = [(a, parse_key(a["rel"])) for a in assets]
    hybrid_ids = list(tables.get("first_release", [])) + list(extra_hybrids or [])
    hybrid_ids += [p["species"] for _, p in parsed if p and p["species"].startswith("h_")]
    for hid in dict.fromkeys(hybrid_ids):
        if hid not in entries:
            h = hybrid_def(hid, by_id, tables)
            if h:
                entries[hid] = DexEntry(h)
    for a, p in parsed:
        if not p or p["species"] not in entries:
            continue
        e = entries[p["species"]]
        if p["kind"] == "dragon":
            e.slots.setdefault((p["variant"], p["stage"], p["pose"]), a)
        else:
            e.portraits.setdefault(p["n"], a)
    return list(entries.values())


PLACEHOLDER = re.compile(r"immich://asset/([^/\s)]+)/(thumbnail|preview|original)")


def img_placeholder(a: dict | None, size: str = "thumbnail") -> str | None:
    """문서(정본)에는 공유 키 대신 자리표시자 `immich://asset/<id>/<size>` 를 둔다. 밀어 넣을 때 키가 붙는다."""
    return f"immich://asset/{a['asset_id']}/{size}" if a and a.get("asset_id") else None


def resolve_placeholders(text: str, immich_url: str, key: str | None) -> str:
    """자리표시자 → 공유 링크 URL. 키가 없으면 이미지 문법을 보기 링크로 낮춘다(깨진 그림 대신 링크)."""
    def url(m: re.Match) -> str:
        aid, size = m.group(1), m.group(2)
        if not key:
            return f"{immich_url}/photos/{aid}"
        return f"{immich_url}/api/assets/{aid}/original?key={key}" if size == "original" else f"{immich_url}/api/assets/{aid}/thumbnail?size={size}&key={key}"
    if not key:
        text = re.sub(r"!\[([^\]]*)\]\((immich://asset/[^)]+)\)", r"[\1](\2)", text)
    return PLACEHOLDER.sub(url, text)


def _link(immich_url: str, a: dict | None) -> str:
    if not a:
        return "—"
    if a.get("asset_id"):
        return f"[보기]({immich_url}/photos/{a['asset_id']})"
    return "(미동기화)"


def entry_images(e: DexEntry, immich_url: str, key: str) -> list[tuple[str, str]]:
    """그림(미디어) 셀에 넣을 (이름, URL) — 성체 기본을 맨 앞(갤러리 커버), 그다음 알·해츨링·고룡, 잡종은 알비노 성체."""
    order = [("normal", "adult", "default"), ("normal", "egg", "default"), ("normal", "hatchling", "default"), ("normal", "elder", "default")]
    if e.is_hybrid:
        order.append(("albino", "adult", "default"))
    out = []
    for v, s, p in order:
        a = e.slots.get((v, s, p))
        if a and a.get("asset_id"):
            label = ("알비노 " if v == "albino" else "") + STAGE_KO[s]
            out.append((label, f"{immich_url}/api/assets/{a['asset_id']}/thumbnail?size=preview&key={key}"))
    return out


def entry_cells(e: DexEntry, immich_url: str, album_url: str | None, key: str | None = None) -> dict:
    m = e.meta
    rep = e.slots.get(("normal", "adult", "default")) or next(iter(e.slots.values()), None)
    cells = {
        "이름": m["name"],
        "ID": m["id"],
        "종류": "잡종" if e.is_hybrid else "순종",
        "등급": m["grade"],
        "속성": ELEMENT_KO.get(m["element"], m["element"]) + (f" + {ELEMENT_KO.get(m['element2'], m['element2'])}" if m.get("element2") else ""),
        "숨결": m["breath"],
        "성향": m["alignment"],
        "서식지": m["habitat"] or "",
        "성격": ", ".join(m["personality"]),
        "색": m["color"],
        "부모": f"{m['parents']['base']} × {m['parents']['sig']} (세트 {m['parents']['k']})" if e.is_hybrid else "",
        "드래곤": f"{e.count('normal')}/{len(SLOTS)}",
        "알비노": f"{e.count('albino')}/{len(SLOTS)}" if e.is_hybrid else "",
        "초상": "" if e.is_hybrid else f"{len(e.portraits)}/{PORTRAIT_COUNT}",
        "누락": ", ".join(e.missing()),
        "상태": e.status(),
        "앨범": album_url or "",
        "대표": f"{immich_url}/photos/{rep['asset_id']}" if rep and rep.get("asset_id") else "",
    }
    if key:
        from .appflowy_client import media_cell
        imgs = entry_images(e, immich_url, key)
        if imgs:
            cells["그림"] = media_cell(imgs)
    return cells


def entry_document(e: DexEntry, immich_url: str) -> str:
    m = e.meta
    lines = [f"# {m['name']} ({m['id']})", ""]
    head = f"{'잡종' if e.is_hybrid else '순종'} · 등급 {m['grade']} · 속성 {ELEMENT_KO.get(m['element'], m['element'])}"
    if m.get("element2"):
        head += f" + {ELEMENT_KO.get(m['element2'], m['element2'])}"
    head += f" · 숨결 {m['breath']} · 성향 {m['alignment']} · 서식지 {m['habitat'] or '—'} · 성격 {', '.join(m['personality'])}"
    if e.is_hybrid:
        head += f" · 부모 {m['parents']['base']} × {m['parents']['sig']} (특이점 세트 {m['parents']['k']})"
    lines += [head, ""]
    rep = e.slots.get(("normal", "adult", "default")) or next(iter(e.slots.values()), None)
    if img_placeholder(rep, "preview"):
        lines += [f"![{m['name']}]({img_placeholder(rep, 'preview')})", ""]
    lines += ["## 도감 설명", "", "(여기에 쓴다. `studiohub dex pull` 이 docs/dex/<id>.md 로 가져온다. 이 문단은 지워도 된다.)", ""]
    lines += ["## 자산", ""]
    strip = [img_placeholder(e.slots.get(("normal", s, "default"))) for s in STAGES]
    if any(strip):
        lines += [" ".join(f"![{STAGE_KO[s]}]({p})" for s, p in zip(STAGES, strip) if p), ""]
    for variant in (["normal", "albino"] if e.is_hybrid else ["normal"]):
        lines.append(f"**{'알비노' if variant == 'albino' else '드래곤'}**")
        for s in STAGES:
            poses = ["default"] if s == "egg" else ["default", "sleep"]
            cells = [f"{POSE_KO[p]} {_link(immich_url, e.slots.get((variant, s, p)))}" for p in poses]
            lines.append(f"- {STAGE_KO[s]}: " + " · ".join(cells))
        lines.append("")
    if not e.is_hybrid:
        lines.append("**초상** " + " ".join(f"{n} {_link(immich_url, e.portraits.get(n))}" for n in range(1, PORTRAIT_COUNT + 1)))
        lines.append("")
    if e.missing():
        lines += ["누락: " + ", ".join(e.missing()), ""]
    if m.get("tokens"):
        lines += ["## 프롬프트 토큰 (species.ts, 참고)", ""]
        for k in ("body", "eyes", "accent", "elder", "avoid"):
            if m["tokens"].get(k):
                lines.append(f"- {k}: {m['tokens'][k]}")
    return "\n".join(lines) + "\n"


ALBUM_TITLE = {"project-game-dragon-yuhui": "용의 유희"}  # Immich 앨범 이름 앞머리 (프로젝트 id → 표시 이름; 없으면 id 그대로)
SEP = " › "


def album_prefix(project: str) -> str:
    return ALBUM_TITLE.get(project, project)


def species_album_name(project: str, meta: dict) -> str:
    return f"{album_prefix(project)}{SEP}종{SEP}{meta['name']} ({meta['id']})"


def category_albums(project: str, info: dict, meta: dict | None) -> list[tuple[str, str]]:
    """자산 하나가 들어갈 분류 앨범 [(이름, 설명)]. Immich 앨범은 평면이라 ' › ' 로 계층을 이름에 적는다."""
    p = album_prefix(project)
    out: list[tuple[str, str]] = []
    if info["kind"] == "portrait":
        out.append((f"{p}{SEP}종류{SEP}초상 (폴리모프)", "인간 모습 사전 초상 — portraits/<종>/premium_N"))
    else:
        hybrid = info["species"].startswith("h_")
        out.append((f"{p}{SEP}종류{SEP}{'잡종' if hybrid else '순종'}", "드래곤 7칸(알 기본 · 해츨링/성체/고룡 × 기본/수면)"))
        if info["variant"] == "albino":
            out.append((f"{p}{SEP}종류{SEP}알비노", "교배 3% 흰 비늘 변종"))
        out.append((f"{p}{SEP}단계{SEP}{STAGE_KO[info['stage']]}", "진화 단계별"))
        out.append((f"{p}{SEP}포즈{SEP}{POSE_KO[info['pose']]}", "기본 포즈 · 수면 포즈(둥지)"))
    if meta:
        out.append((species_album_name(project, meta), f"{meta['name']} 의 모든 그림 — 단계·포즈·알비노·초상"))
        el = ELEMENT_KO.get(meta["element"], meta["element"])
        out.append((f"{p}{SEP}속성{SEP}{el}", "우성 속성별 (잡종은 혼원 포함)"))
        out.append((f"{p}{SEP}등급{SEP}{GRADE_KO.get(meta['grade'], meta['grade'])}", "뽑기 등급별"))
        hab = HABITAT_KO.get(meta["habitat"], "없음") if meta.get("habitat") else "없음"
        out.append((f"{p}{SEP}서식지{SEP}{hab}", "인간계 서식지별 (없음 = 젬·골드)"))
    return out


def data_dir(workspace_root: Path, project: str) -> Path:
    return workspace_root / "10_PROJECTS" / "active" / project / "packages" / "shared" / "src" / "data"


def load_game_data(dir_: Path) -> tuple[list[dict], dict]:
    species = parse_species_ts((dir_ / "species.ts").read_text(encoding="utf-8"))
    tables = parse_hybrid_tables((dir_ / "hybrids.ts").read_text(encoding="utf-8"))
    if not species:
        raise ValueError(f"species.ts 에서 종을 못 읽음: {dir_}")
    return species, tables

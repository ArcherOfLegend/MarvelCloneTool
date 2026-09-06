from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import sound_bank
from .arc import Arc, read_arc, write_arc
from .rename import (
    Refusal, Replacement, _field_end, _slack, component_pattern,
    rename_components, replace,
)

CHR_ARCHIVE = Path("nativePCx64/chr/archive")
SOUND_ARCHIVE_CANDIDATES = [
    Path("nativePCx64/sound/se/chr/archive"),
    Path("sound/se/chr/archive"),
]
SOUND_ARCHIVE = SOUND_ARCHIVE_CANDIDATES[0]


SOUND_EVENT_CANDIDATES = [
    Path("nativePCx64/sound/event"),
    Path("sound/event"),
]


def sound_event_dir(game_dir: Path) -> Path:
    """Relative path of the streamed event audio folder in this install."""
    for candidate in SOUND_EVENT_CANDIDATES:
        if (Path(game_dir) / candidate).is_dir():
            return candidate
    return SOUND_EVENT_CANDIDATES[0]


def sound_archive_dir(game_dir: Path) -> Path:
    """Relative path of the voice bank folder in this install."""
    for candidate in SOUND_ARCHIVE_CANDIDATES:
        if (Path(game_dir) / candidate).is_dir():
            return candidate
    for hit in Path(game_dir).rglob("se/chr/archive"):
        if hit.is_dir():
            return hit.relative_to(game_dir)
    return SOUND_ARCHIVE_CANDIDATES[0]
UI_NAME_DIR = Path("nativePCx64/ui/chs/chs_b1p/chs_as_n")
UI_BODY_DIR = Path("nativePCx64/ui/chs/chs_b1p/chs_body")

# suffix -> content search term, as a format string over {name}
PASSES = {
    "cmn": "\\{name}",
    "param": "\\{name}",
    "costume": "\\{name}",
    "sound": None,      # handled by SOUND_PASSES, see clone_sound
}

SOUND_PASSES = [
    ("\\{base}\\", "\\{new}\\"),                                # sound\se\chr\IronMan\...
    ("sound\\event\\{base_sid}\\", "sound\\event\\{new_sid}\\"),  # sound\event\iro\...
    ("{base_sid}_", "{new_sid}_"),                              # iro_vo_en, iro_001e, iro_en
]

SOUND_ID_LENGTH = 3

SOUND_ID_PATTERN = re.compile(r"[\\/]([a-z0-9]{2,5})_vo_", re.IGNORECASE)

UI_NAME_TEMPLATE = "n_{name}_BM_HQ_NOMIP_typeB_other"
UI_BODY_TEMPLATE = "b_{name}99_BM_HQ_NOMIP"


@dataclass
class CloneSpec:
    game_dir: Path
    out_dir: Path
    char_id: str                  # "0033"
    base_name: str                # "IronMan", case sensitive
    new_name: str                 # "PwrSuit"
    costumes: list[str] = field(default_factory=list)   # ["00", "01", ...]
    sound_lang: str = "01"
    sound_id: str = ""
    base_sound_id: str = ""
    new_sound_id: str = ""
    num_colors: int = 0           # 0 derives it from the costume list
    include_sound: bool = True
    include_ui: bool = True
    fan_out_ui: bool = False        # duplicate a numbered UI texture across costumes
    underscore_names: bool = True   # also rename IronMan_l0, n_IronMan_BM_HQ

    def __post_init__(self):
        self.game_dir = Path(self.game_dir)
        self.out_dir = Path(self.out_dir)
        if not self.num_colors:
            self.num_colors = len(self.costumes)


@dataclass
class ArcResult:
    label: str
    source: Path
    dest: Path
    entries: int
    content_hits: int
    path_renames: int
    refused: list[Refusal] = field(default_factory=list)


@dataclass
class CloneReport:
    arcs: list[ArcResult] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    ini_block: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(a.refused for a in self.arcs)

    @property
    def total_refusals(self) -> int:
        return sum(len(a.refused) for a in self.arcs)


def find_sources(game_dir: Path, char_id: str, sound_lang: str = "01") -> dict[str, Path]:
    """Locate every archive belonging to a character ID."""
    game_dir = Path(game_dir)
    found: dict[str, Path] = {}

    chr_dir = game_dir / CHR_ARCHIVE
    if chr_dir.is_dir():
        for f in sorted(chr_dir.glob(f"{char_id}_*.arc")):
            found[f.stem.split("_", 1)[1]] = f

    snd = game_dir / sound_archive_dir(game_dir) / f"{char_id}_{sound_lang}.arc"
    if snd.is_file():
        found["sound"] = snd

    return found


def detect_base_name(arc: Arc) -> str | None:
    counts: dict[str, int] = {}
    for e in arc.entries:
        parts = re.split(r"[\\/]", e.path)
        for i, p in enumerate(parts[:-1]):
            if p.lower() == "chr" and i + 1 < len(parts) - 1:
                counts[parts[i + 1]] = counts.get(parts[i + 1], 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def kind_for_suffix(suffix: str) -> str:
    if suffix in ("cmn", "param", "sound"):
        return suffix
    return "costume"


def rename_sound_payload(data: bytes, pairs: list[tuple[str, str]]):
    rebuilt = sound_bank.rename(data, pairs)
    if rebuilt is not None:
        return rebuilt[0], rebuilt[1], []

    rebuilt = sound_bank.srqr_rename(data, pairs)
    if rebuilt is not None:
        return rebuilt[0], rebuilt[1], []

    hits, refused = 0, []
    for old, new in pairs:
        if old == new:
            continue
        res = replace(data, old, new)
        data = res.data
        hits += res.count
        refused.extend(res.refused)
    return data, hits, refused


def is_sound_entry(entry) -> bool:
    return re.split(r"[\\/]", entry.path)[0].lower() == "sound"


def clone_archive(spec: CloneSpec, suffix: str, src: Path, log=print) -> ArcResult:
    kind = kind_for_suffix(suffix)
    term = PASSES[kind]
    arc = read_arc(src)

    targets = list(arc.entries)
    skipped = 0

    content_hits = 0
    refused: list[Refusal] = []

    if term is not None:
        old_term = term.format(name=spec.base_name)
        new_term = term.format(name=spec.new_name)
        for e in targets:
            if is_sound_entry(e) and sound_bank.is_bank(e.data):
                # Rebuilt rather than patched, so the name may change length.
                e.data, hit, ref = rename_sound_payload(
                    e.data, [(f"\\{spec.base_name}\\", f"\\{spec.new_name}\\")])
                content_hits += hit
                for r in ref:
                    r.context = f"{e.filename}: {r.context}"
                refused.extend(ref)
                continue
            res = replace(e.data, old_term, new_term, whole_component=True,
                          underscores=spec.underscore_names)
            if res.count or res.refused:
                e.data = res.data
                content_hits += res.count
                for r in res.refused:
                    r.context = f"{e.filename}: {r.context}"
                refused.extend(res.refused)

    base_sid, new_sid = spec.base_sound_id, spec.new_sound_id
    if base_sid and new_sid and base_sid != new_sid:
        sid_passes = [(f"{base_sid}_se", f"{new_sid}_se")]
        for e in targets:
            if not is_sound_entry(e):
                continue
            e.data, hit, ref = rename_sound_payload(e.data, sid_passes)
            content_hits += hit
            for r in ref:
                r.context = f"{e.filename}: {r.context}"
            refused.extend(ref)

    path_renames = 0
    cap = arc.path_len - 1
    for e in targets:
        if is_sound_entry(e) and base_sid and new_sid and base_sid != new_sid:
            renamed = rename_components(e.path, spec.base_name, spec.new_name,
                                        underscores=spec.underscore_names)
            renamed = renamed.replace(f"{base_sid}_se", f"{new_sid}_se")
        elif re.split(r"[\\/]", e.path)[0].lower() == "ui":
            parts = re.split(r"([\\/])", e.path)
            parts[-1] = rename_ui_leaf(parts[-1], spec.base_name, spec.new_name)
            renamed = "".join(parts)
        else:
            renamed = rename_components(e.path, spec.base_name, spec.new_name,
                                        underscores=spec.underscore_names)
        if renamed == e.path:
            continue
        if len(renamed.encode("ascii", "replace")) > cap:
            refused.append(Refusal(
                -1, "path",
                f"internal path would be {len(renamed)} bytes, the header field holds {cap}",
                renamed,
            ))
            continue
        e.path = renamed
        path_renames += 1

    if refused:
        if all("packed" in r.reason for r in refused):
            log(f"{src.name}: {len(refused)} refusals, all in the packed sound bank. "
                f"'{spec.new_name}' is {len(spec.new_name)} characters and "
                f"'{spec.base_name}' is {len(spec.base_name)}; they have to match "
                f"for the clone to have sound.")
        else:
            log(f"{src.name}: {len(refused)} refusals, not writing an output")
        return ArcResult(
            label=suffix, source=src, dest=Path(), entries=len(arc.entries),
            content_hits=content_hits, path_renames=path_renames, refused=refused,
        )

    if suffix == "sound":
        dest_name = f"{spec.new_name}.arc"
        dest = spec.out_dir / sound_archive_dir(spec.game_dir) / dest_name
    else:
        dest = spec.out_dir / CHR_ARCHIVE / f"{spec.new_name}_{suffix}.arc"

    write_arc(arc, dest)
    log(f"{src.name} -> {dest.name}  "
        f"{content_hits} in contents, {path_renames} paths"
        + (f", {skipped} sound entries left on the base character" if skipped else "")
        + (f", {len(refused)} REFUSED" if refused else ""))

    return ArcResult(
        label=suffix, source=src, dest=dest, entries=len(arc.entries),
        content_hits=content_hits, path_renames=path_renames, refused=refused,
    )


def detect_sound_id(arc: Arc) -> str | None:
    for e in arc.entries:
        m = SOUND_ID_PATTERN.search(e.path)
        if m:
            return m.group(1)
    return None


def clone_sound(spec: CloneSpec, src: Path, log=print) -> ArcResult:
    arc = read_arc(src)
    base_sid = spec.base_sound_id or detect_sound_id(arc) or ""
    new_sid = spec.new_sound_id or base_sid

    if not base_sid and new_sid:
        raise ValueError(
            f"asked for sound ID {new_sid!r} but could not find the current one "
            f"in {src.name}")
    if not base_sid:
        base_sid = new_sid = ""

    terms = {
        "base": spec.base_name, "new": spec.new_name,
        "base_sid": base_sid, "new_sid": new_sid,
    }

    hits = 0
    refused: list[Refusal] = []
    pairs = [(o.format(**terms), n.format(**terms)) for o, n in SOUND_PASSES]
    pairs = [(o, n) for o, n in pairs if o != n]
    for e in arc.entries:
        e.data, hit, ref = rename_sound_payload(e.data, pairs)
        hits += hit
        for r in ref:
            r.context = f"{e.filename}: {r.context}"
        refused.extend(ref)

    path_renames = 0
    cap = arc.path_len - 1
    for e in arc.entries:
        renamed = e.path
        for old_t, new_t in SOUND_PASSES:
            renamed = renamed.replace(old_t.format(**terms), new_t.format(**terms))
        if renamed == e.path:
            continue
        if len(renamed.encode("ascii", "replace")) > cap:
            refused.append(Refusal(
                -1, "path",
                f"internal path would be {len(renamed)} bytes, the field holds {cap}",
                renamed))
            continue
        e.path = renamed
        path_renames += 1

    dest = spec.out_dir / sound_archive_dir(spec.game_dir) / f"{spec.new_name}.arc"
    if refused:
        log(f"{src.name}: {len(refused)} refusals, voice bank not written")
        return ArcResult("sound", src, Path(), len(arc.entries), hits, path_renames, refused)

    write_arc(arc, dest)
    log(f"{src.name} -> {dest.name}  sound ID {base_sid} to {new_sid}, "
        f"{hits} in contents, {path_renames} paths")
    return ArcResult("sound", src, dest, len(arc.entries), hits, path_renames)


def max_name_length(spec: CloneSpec, log=print) -> tuple[int, str]:
    sources = find_sources(spec.game_dir, spec.char_id, spec.sound_lang)
    base_len = len(spec.base_name)
    content_cap, path_cap = 10**6, 10**6
    reason = "nothing found"

    for suffix, src in sources.items():
        arc = read_arc(src)
        term = PASSES[kind_for_suffix(suffix)] or "\\{name}"
        cap = arc.path_len - 1
        for e in arc.entries:
            probe = spec.base_name + "\x01"
            if re.split(r"[\\/]", e.path)[0].lower() == "ui":
                parts = re.split(r"([\\/])", e.path)
                parts[-1] = rename_ui_leaf(parts[-1], spec.base_name, probe)
                renamed = "".join(parts)
            else:
                renamed = rename_components(e.path, spec.base_name, probe,
                                            underscores=spec.underscore_names)
            hits = renamed.count("\x01")
            if hits:
                fixed = len(e.path) - hits * base_len
                path_cap = min(path_cap, (cap - fixed) // hits)
            if term is None:
                continue
            if (sound_bank.parse(e.data) is not None
                    or sound_bank.srqr_rename(e.data, []) is not None):
                # Rebuilt from scratch, so its strings impose no limit.
                continue
            old_term = term.format(name=spec.base_name)
            for m in component_pattern(
                    spec.base_name, underscores=spec.underscore_names).finditer(e.data):
                room = _slack(e.data, _field_end(e.data, m.end(), 1), 1)
                content_cap = min(content_cap, base_len + room)

    limit = min(content_cap, path_cap)
    reason = "the archive path field" if path_cap <= content_cap else "string padding"
    log(f"longest workable name is {limit} characters, bound by {reason}")
    return limit, reason


def embedded_name_report(spec: CloneSpec) -> list[str]:
    out = []
    for suffix, src in find_sources(spec.game_dir, spec.char_id, spec.sound_lang).items():
        if suffix == "sound":
            continue
        for e in read_arc(src).entries:
            parts = re.split(r"[\\/]", e.path)
            for part in parts:
                if spec.base_name in part and part != spec.base_name:
                    renamed = rename_components(part, spec.base_name, spec.new_name,
                                                underscores=spec.underscore_names)
                    verb = "renamed" if renamed != part else "left alone"
                    out.append(f"{suffix}: {part} -> {verb}")
    return sorted(set(out))


def clone_sound_events(spec: CloneSpec, base_sid: str, new_sid: str, log=print
                       ) -> tuple[list[Path], list[str]]:
    written: list[Path] = []
    warnings: list[str] = []

    if base_sid == new_sid:
        return written, warnings

    event_root = sound_event_dir(spec.game_dir)
    src_dir = spec.game_dir / event_root / base_sid
    if not src_dir.is_dir():
        warnings.append(
            f"no event audio at {src_dir}."
        )
        return written, warnings

    for src in sorted(src_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        leaf = src.name.replace(f"{base_sid}_", f"{new_sid}_")
        dest = spec.out_dir / event_root / new_sid / rel.parent / leaf
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        written.append(dest)

    log(f"event audio -> {len(written)} files copied from {base_sid} to {new_sid}")
    return written, warnings


def find_ui_arc(game_dir: Path) -> Path | None:
    hits = list(Path(game_dir).rglob("mnchs_en.arc"))
    return hits[0] if hits else None


UI_NAME_RE_TEMPLATE = r"(?:(?<=^)|(?<=_)){name}(?=_|\d|$)"


def rename_ui_leaf(leaf: str, base: str, new: str) -> str:
    return re.sub(UI_NAME_RE_TEMPLATE.format(name=re.escape(base)), new, leaf)


UI_SLOT_RE_TEMPLATE = r"(?:(?<=^)|(?<=_)){name}(\d+)(?=_|$)"


def costume_variants(leaf: str, base: str, new: str, costumes: list[str],
                     fan_out: bool = False) -> list[str]:
    renamed = rename_ui_leaf(leaf, base, new)
    if not fan_out:
        return [renamed]
    match = re.search(UI_SLOT_RE_TEMPLATE.format(name=re.escape(base)), leaf)
    if not match:
        return [renamed]

    original = match.group(1)
    width = len(original)
    slots = list(dict.fromkeys(list(costumes) + [original]))
    out = []
    for slot in slots:
        slot = slot.zfill(width)
        out.append(re.sub(
            UI_SLOT_RE_TEMPLATE.format(name=re.escape(new)),
            new + slot, renamed, count=1))
    return list(dict.fromkeys(out))


def find_ui_elements(game_dir: Path, base_name: str) -> list[tuple[str, Path | None, str]]:
    game_dir = Path(game_dir)
    found: list[tuple[str, Path | None, str]] = []
    seen: set[tuple[str, str]] = set()

    ui_root = game_dir / "nativePCx64" / "ui"
    if ui_root.is_dir():
        for f in ui_root.rglob("*.tex"):
            leaf = f.stem
            if rename_ui_leaf(leaf, base_name, "?") == leaf:
                continue
            rel = f.parent.relative_to(game_dir).as_posix()
            if (rel, leaf) not in seen:
                seen.add((rel, leaf))
                found.append((rel, None, leaf))

    ui_arcs = sorted(ui_root.rglob("*.arc")) if ui_root.is_dir() else []
    for pattern in ("mnchs*.arc", "mngame*.arc"):
        ui_arcs += [p for p in game_dir.rglob(pattern) if p not in ui_arcs]

    if True:
        for arc_file in sorted(set(ui_arcs)):
            try:
                arc = read_arc(arc_file)
            except ValueError:
                continue
            for e in arc.entries:
                parts = re.split(r"[\\/]", e.path)
                leaf = parts[-1]
                if rename_ui_leaf(leaf, base_name, "?") == leaf:
                    continue
                rel = "nativePCx64/" + "/".join(parts[:-1])
                if (rel, leaf) not in seen:
                    seen.add((rel, leaf))
                    found.append((rel, arc_file, leaf))

    return found


def copy_ui_elements(spec: CloneSpec, log=print) -> tuple[list[Path], list[str]]:
    written: list[Path] = []
    warnings: list[str] = []

    elements = find_ui_elements(spec.game_dir, spec.base_name)
    if not elements:
        warnings.append(
            "no UI textures found for this character"
            )
        return written, warnings

    by_arc: dict[Path, list[tuple[str, str]]] = {}
    for rel, arc_file, leaf in elements:
        if arc_file is None:
            src = spec.game_dir / rel / f"{leaf}.tex"
            for new_leaf in costume_variants(
                    leaf, spec.base_name, spec.new_name, spec.costumes,
                    spec.fan_out_ui):
                dest = spec.out_dir / rel / f"{new_leaf}.tex"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                written.append(dest)
        else:
            by_arc.setdefault(arc_file, []).append((rel, leaf))

    for arc_file, wanted in by_arc.items():
        arc = read_arc(arc_file)
        index = {re.split(r"[\\/]", e.path)[-1]: e for e in arc.entries}
        for rel, leaf in wanted:
            entry = index.get(leaf)
            if entry is None:
                warnings.append(f"{leaf} vanished from {arc_file.name}")
                continue
            for new_leaf in costume_variants(
                    leaf, spec.base_name, spec.new_name, spec.costumes,
                    spec.fan_out_ui):
                dest = spec.out_dir / rel / f"{new_leaf}.tex"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(entry.data)
                written.append(dest)

    log(f"{len(written)} UI textures written")
    for w in written:
        log(f"   {w.parent.name}/{w.name}")
    return written, warnings


def next_character_index(ini_path: Path) -> int:
    if not ini_path.is_file():
        return 1
    text = ini_path.read_text(errors="replace")
    used = [int(m) for m in re.findall(r"^\s*\[Character(\d+)\]", text, re.MULTILINE)]
    return max(used) + 1 if used else 1


def existing_character_ids(ini_path: Path) -> dict[str, int]:
    """CharacterID -> block number, for every entry already in the ini."""
    if not ini_path.is_file():
        return {}
    text = ini_path.read_text(errors="replace")
    found = {}
    for index, body in re.findall(
            r"\[Character(\d+)\](.*?)(?=\[Character\d+\]|\Z)", text, re.S):
        m = re.search(r"^\s*CharacterID\s*=\s*(.*?)\s*$", body, re.M)
        if m and m.group(1):
            found[m.group(1)] = int(index)
    return found


def build_ini_block(spec: CloneSpec, index: int) -> str:
    return (
        f"[Character{index}]\n"
        f"CharacterID={spec.new_name}\n"
        f"BaseCharacter={spec.base_name}\n"
        f"SoundID={spec.sound_id}\n"
        f"NumColors={spec.num_colors}\n"
        f"Child1=\n"
    )


def run(spec: CloneSpec, log=print) -> CloneReport:
    report = CloneReport()
    sources = find_sources(spec.game_dir, spec.char_id, spec.sound_lang)

    if not spec.base_sound_id and "sound" in sources:
        spec.base_sound_id = detect_sound_id(read_arc(sources["sound"])) or ""

    if not spec.sound_id:
        spec.sound_id = spec.base_sound_id

    # Three characters exactly. The game breaks on anything longer.
    if spec.sound_id and len(spec.sound_id) != SOUND_ID_LENGTH:
        raise ValueError(
            f"SoundID {spec.sound_id!r} is {len(spec.sound_id)} characters. "
            f"It has to be exactly {SOUND_ID_LENGTH}."
        )
    if spec.sound_id and spec.base_sound_id and spec.sound_id != spec.base_sound_id:
        spec.new_sound_id = spec.sound_id
        log(f"custom sound ID: {spec.base_sound_id} becomes {spec.sound_id}")
    elif spec.base_sound_id:
        log(f"sound ID {spec.base_sound_id}, shared with the base character")
    if not sources:
        raise FileNotFoundError(
            f"no archives for character {spec.char_id} under {spec.game_dir / CHR_ARCHIVE}"
        )

    # The name goes inside the archives as well as in characters.ini, and some
    # resources are loaded by CharacterID, so it cannot be shortened internally.
    # Stop here rather than emit a half-renamed clone.
    limit, bound_by = max_name_length(spec, log=lambda _m: None)
    if len(spec.new_name) > limit:
        raise ValueError(
            f"{spec.new_name!r} is {len(spec.new_name)} characters. "
            f"{spec.base_name} allows at most {limit} ({bound_by}). "
            f"Pick a shorter name."
        )
    log(f"name limit for {spec.base_name} is {limit} characters ({bound_by})")

    wanted = set(spec.costumes) | {"cmn", "param"}
    if spec.include_sound:
        wanted.add("sound")

    for suffix, src in sources.items():
        if suffix not in wanted:
            continue
        if suffix == "sound":
            result = clone_sound(spec, src, log)
            report.arcs.append(result)
            if not result.refused:
                base_sid = spec.base_sound_id or detect_sound_id(read_arc(src)) or ""
                new_sid = spec.new_sound_id or base_sid
                files, warns = clone_sound_events(spec, base_sid, new_sid, log)
                report.files.extend(files)
                report.warnings.extend(warns)
            continue

        report.arcs.append(clone_archive(spec, suffix, src, log))

    missing = wanted - set(sources)
    for m in sorted(missing):
        if m == "sound":
            looked = spec.game_dir / sound_archive_dir(spec.game_dir)
            report.warnings.append(
                f"no voice bank at {looked}, so the clone has no sound of its own")
        else:
            report.warnings.append(f"{spec.char_id}_{m}.arc not found, skipped")

    if spec.include_ui:
        written, warns = copy_ui_elements(spec, log)
        report.files.extend(written)
        report.warnings.extend(warns)

    ini_path = spec.game_dir / "nativePCx64" / "characters.ini"
    # A duplicate CharacterID silently shadows an installed clone. With dozens
    # of entries in a real ini that is easy to do and hard to spot afterwards.
    taken = existing_character_ids(ini_path)
    if spec.new_name in taken:
        raise ValueError(
            f"characters.ini already has CharacterID={spec.new_name} at "
            f"[Character{taken[spec.new_name]}]. Pick a different name."
        )
    index = next_character_index(ini_path)
    report.ini_block = build_ini_block(spec, index)
    block_file = spec.out_dir / "characters.ini.append.txt"
    block_file.parent.mkdir(parents=True, exist_ok=True)
    block_file.write_text(report.ini_block)
    report.files.append(block_file)

    if not spec.sound_id:
        report.warnings.append("SoundID is empty.")

    return report


def install(spec: CloneSpec, report: CloneReport, log=print) -> list[Path]:
    copied = []
    for root, _, files in __import__("os").walk(spec.out_dir):
        for f in files:
            src = Path(root) / f
            rel = src.relative_to(spec.out_dir)
            if rel.name == "characters.ini.append.txt":
                continue
            dest = spec.game_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            copied.append(dest)
            log(f"installed {rel}")

    ini = spec.game_dir / "nativePCx64" / "characters.ini"
    if ini.is_file():
        backup = ini.with_suffix(".ini.bak")
        if not backup.exists():
            shutil.copyfile(ini, backup)
            log(f"backed up characters.ini to {backup.name}")
        with ini.open("a") as fh:
            fh.write("\n" + report.ini_block)
        log("appended the characters.ini block")
    else:
        log("characters.ini not found.")

    return copied
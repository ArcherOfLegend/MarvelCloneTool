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

# The base roster. Display names only; the archives are found by the numeric
# ID and the codename is still read out of the archive itself, so a wrong
# label here cannot affect a clone.
ROSTER = [
    ("0001", "Ryu"),
    ("0002", "Chun-Li"),
    ("0003", "Gouki (Akuma)"),
    ("0004", "Chris"),
    ("0005", "Wesker"),
    ("0006", "Viewtiful Joe"),
    ("0007", "Dante"),
    ("0008", "Trish"),
    ("0009", "Frank West"),
    ("0010", "Spencer"),
    ("0011", "Sir Arthur"),
    ("0012", "Amaterasu"),
    ("0013", "Zero"),
    ("0014", "Tron Bonne"),
    ("0015", "Morrigan"),
    ("0016", "Lei-Lei (Hsien-Ko)"),
    ("0017", "Felicia"),
    ("0018", "Crimson Viper"),
    ("0019", "Haggar"),
    ("0020", "Jill"),
    ("0021", "Strider Hiryu"),
    ("0022", "Vergil"),
    ("0023", "Naruhodo (Phoenix Wright)"),
    ("0024", "Red Aremer (Firebrand)"),
    ("0025", "Nemesis"),
    ("0026", "Spider Man"),
    ("0027", "Captain America"),
    ("0028", "Wolverine"),
    ("0029", "Magneto"),
    ("0030", "Hulk"),
    ("0031", "She Hulk"),
    ("0032", "Taskmaster"),
    ("0033", "Iron Man"),
    ("0034", "Thor"),
    ("0035", "Doctor Doom"),
    ("0036", "Phoenix"),
    ("0037", "Shuma Gorath"),
    ("0038", "Modok"),
    ("0039", "Dormammu"),
    ("0040", "Deadpool"),
    ("0041", "Storm"),
    ("0042", "Super Skrull"),
    ("0043", "Sentinel"),
    ("0044", "X-23"),
    ("0045", "Nova"),
    ("0046", "Rocket Racoon"),
    ("0047", "Ghost Rider"),
    ("0048", "Iron Fist"),
    ("0049", "Dr Strange"),
    ("0050", "Hawkeye"),
    ("0051", "Galactus"),
]

CHILDREN = [
    ("0052", "ZeroSh"),
    ("0053", "MorriganSh"),
    ("0054", "FeliciaF"),
    ("0055", "FeliciaC"),
    ("0056", "Zombie"),
    ("0057", "Mayoi"),
    ("0058", "RedArremerSh"),
    ("0059", "DrStrangeSh"),
]

CLONE_SLOT_BASE = 59

BGM_STREAM_BASE = 109

BGM_EVENT_BASE = 138


def bgm_stream_plan(hints) -> list[tuple[int, str]]:
    """(stream index, CharacterID) for every playable character, table or not."""
    if isinstance(hints, (str, Path)):
        hints = [hints]
    playable = [c for c in character_list(find_characters_ini(*hints)) if c[3]]
    return [(BGM_STREAM_BASE + position, cid)
            for position, (_index, cid, _base, _sid) in enumerate(playable, start=1)]


def bgm_stream_owners(hints, stream_count: int) -> dict[int, str]:
    return {slot: cid for slot, cid in bgm_stream_plan(hints) if slot < stream_count}


def bgm_stream_index(hints, character: str) -> int:
    """The stream a character's music occupies, or -1 if not found."""
    for slot, cid in bgm_stream_plan(hints):
        if cid == character:
            return slot
    return -1


def bgm_event_index(hints, character: str) -> int:
    for slot, cid in bgm_stream_plan(hints):
        if cid == character:
            return BGM_EVENT_BASE + (slot - BGM_STREAM_BASE)
    return -1


def bgm_event_plan(hints) -> list[tuple[int, str]]:
    return [(BGM_EVENT_BASE + (slot - BGM_STREAM_BASE), cid)
            for slot, cid in bgm_stream_plan(hints)]


def assist_slot_names(hints, slot_count: int) -> dict[int, str]:
    """Who owns each assist slot: base roster, then children, then clones."""
    if isinstance(hints, (str, Path)):
        hints = [hints]
    names = {int(cid): label for cid, label in ROSTER}
    names.update({int(cid): label for cid, label in CHILDREN})
    for index, cid, _base, _sid in character_list(find_characters_ini(*hints)):
        names[CLONE_SLOT_BASE + index] = cid
    return {k: v for k, v in names.items() if k < slot_count}


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
    ui_255: bool = False            # also emit a 255 copy of every 99 texture
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


ENDING_DIR = Path("nativePCx64/ui/ending")


def find_ending_arc(game_dir: Path, char_id: str, base_name: str = "") -> Path | None:
    names = []
    if char_id.isdigit():
        names.append(f"ending_{int(char_id):02d}.arc")
    if base_name:
        names.append(f"ending_{base_name}.arc")

    for name in names:
        for candidate in Path(game_dir).rglob(name):
            return candidate
    return None


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
    if suffix == "ending":
        return "cmn"
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
            renamed = rename_components(e.path, spec.base_name, spec.new_name,
                                        underscores=spec.underscore_names)
            parts = re.split(r"([\\/])", renamed)
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

    if suffix == "ending":
        dest = spec.out_dir / ENDING_DIR / f"ending_{spec.new_name}.arc"
    elif suffix == "sound":
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
                renamed = rename_components(e.path, spec.base_name, probe,
                                            underscores=spec.underscore_names)
                parts = re.split(r"([\\/])", renamed)
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

UI_OUTPUTS = [
    # (destination folder, leaf template, what {slot} takes)
    ("nativePCx64/ui/chs/chs_b1p/chs_as_n",
     "n_{name}{slot}_BM_HQ_NOMIP_typeB_other", "none"),
    ("nativePCx64/ui/chs/chs_b1p/chs_body",
     "b_{name}{slot}_BM_HQ_NOMIP", "select"),
    ("nativePCx64/ui/res/res_stgm/stgm_p",
     "p_{name}{slot}_BM_HQ_NOMIP_typeB", "costume"),
    ("nativePCx64/ui/res/res_stgm/stgm_p",
     "p_{name}{slot}_BM_HQ_NOMIP_typeB_other", "costume"),
]


def ui_sources(game_dir: Path) -> dict[str, tuple[Path | None, bytes]]:
    """Every ui texture the game has, by leaf name, loose files and archives."""
    game_dir = Path(game_dir)
    found: dict[str, tuple[Path | None, bytes]] = {}

    ui_root = game_dir / "nativePCx64" / "ui"
    if ui_root.is_dir():
        for f in sorted(ui_root.rglob("*.tex")):
            found.setdefault(f.stem, (f, b""))

    arcs = sorted(ui_root.rglob("*.arc")) if ui_root.is_dir() else []
    for pattern in ("mnchs*.arc", "mngame*.arc", "mnmain*.arc"):
        arcs += [p for p in game_dir.rglob(pattern) if p not in arcs]
    for arc_file in sorted(set(arcs)):
        try:
            arc = read_arc(arc_file)
        except ValueError:
            continue
        for e in arc.entries:
            leaf = re.split(r"[\\/]", e.path)[-1]
            found.setdefault(leaf, (None, e.data))
    return found


def copy_ui_elements(spec: CloneSpec, log=print) -> tuple[list[Path], list[str]]:
    """Write the loose textures the readme asks for, and nothing else."""
    written: list[Path] = []
    warnings: list[str] = []

    library = ui_sources(spec.game_dir)
    if not library:
        warnings.append("no ui textures found, so the select screen art needs doing by hand")
        return written, warnings

    for folder, template, kind in UI_OUTPUTS:
        if kind == "costume":
            pairs = [(slot, slot) for slot in spec.costumes]
        elif kind == "select":
            # The select screen texture is always 99 in the base game. Optional
            # extras write the same image under other numbers.
            pairs = [("99", "99")]
            if spec.fan_out_ui:
                pairs += [("99", slot) for slot in spec.costumes]
            if spec.ui_255:
                pairs.append(("99", "255"))
        else:
            pairs = [("", "")]

        for source_slot, dest_slot in pairs:
            source_leaf = template.format(name=spec.base_name, slot=source_slot)
            entry = library.get(source_leaf)
            if entry is None:
                warnings.append(f"{source_leaf}.tex not found")
                continue
            path, blob = entry
            data = path.read_bytes() if path is not None else blob

            dest_leaf = template.format(name=spec.new_name, slot=dest_slot)
            dest = spec.out_dir / folder / f"{dest_leaf}.tex"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            written.append(dest)

    log(f"{len(written)} UI textures written")
    for w in written:
        log(f"   {w.relative_to(spec.out_dir).as_posix()}")
    return written, warnings


def next_character_index(ini_path: Path) -> int:
    if not ini_path.is_file():
        return 1
    text = ini_path.read_text(errors="replace")
    used = [int(m) for m in re.findall(r"^\s*\[Character(\d+)\]", text, re.MULTILINE)]
    return max(used) + 1 if used else 1


def find_characters_ini(*hints: Path) -> Path:
    names = ("Characters.ini", "characters.ini")
    for hint in hints:
        if not hint:
            continue
        start = Path(hint)
        start = start if start.is_dir() else start.parent
        for folder in (start, *start.parents):
            for name in names:
                if (folder / name).is_file():
                    return folder / name
            for name in names:
                if (folder / "nativePCx64" / name).is_file():
                    return folder / "nativePCx64" / name
    first = Path(hints[0]) if hints and hints[0] else Path(".")
    return first / "Characters.ini"


def character_list(ini_path: Path) -> list[tuple[int, str, str, str]]:
    if not Path(ini_path).is_file():
        return []
    text = Path(ini_path).read_text(errors="replace")
    out = []
    for index, body in re.findall(
            r"\[Character(\d+)\](.*?)(?=\[Character\d+\]|\Z)", text, re.S):
        fields = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", body, re.M))
        cid = fields.get("CharacterID", "")
        if cid:
            out.append((int(index), cid, fields.get("BaseCharacter", ""),
                        fields.get("SoundID", "")))
    return sorted(out)


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


def verify_output(report: CloneReport, log=print) -> list[str]:
    problems = []
    for result in report.arcs:
        if not result.dest or not result.dest.is_file():
            continue

        name = result.dest.name
        if len(name.encode("ascii", "replace")) > 0x3F:
            problems.append(f"{name}: filename is {len(name)} bytes, the buffer holds 63")

        arc = read_arc(result.dest)
        cap = arc.path_len - 1
        for e in arc.entries:
            n = len(e.path.encode("ascii", "replace"))
            if n > cap:
                problems.append(f"{name}: internal path is {n} bytes, the field holds {cap}"
                                f"  {e.path}")

    for path in report.files:
        if path.suffix.lower() == ".tex" and len(path.name) > 0x3F:
            problems.append(f"{path.name}: filename is {len(path.name)} bytes")

    for p in problems:
        log(f"OVERRUN  {p}")
    return problems


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

    ending = find_ending_arc(spec.game_dir, spec.char_id, spec.base_name)
    if ending is not None:
        result = clone_archive(spec, "ending", ending, log)
        report.arcs.append(result)
    else:
        report.warnings.append(
            f"no ending archive found for {spec.char_id}, so the clone has no "
            f"arcade ending")

    if spec.include_ui:
        written, warns = copy_ui_elements(spec, log)
        report.files.extend(written)
        report.warnings.extend(warns)

    ini_path = find_characters_ini(spec.game_dir)
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

    overruns = verify_output(report, log)
    if overruns:
        report.warnings.append(
            f"{len(overruns)} strings overran their 0x40 buffer. This clone will "
            f"not load correctly. Use a shorter name.")

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

    ini = find_characters_ini(spec.game_dir)
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
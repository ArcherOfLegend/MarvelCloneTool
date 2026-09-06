"""
The port workflow from Yoshi's guide, as a job you can preview before running.

Source layout in the game install:

    nativePCx64/chr/archive/0033_00.arc  ... _07.arc   costumes
    nativePCx64/chr/archive/0033_cmn.arc                shared assets
    nativePCx64/chr/archive/0033_param.arc              shot files, shotlist
    sound/se/chr/archive/0033_01.arc                    English voice bank
    nativePCx64/ui/.../mnchs_en.arc                     select screen art

Output, named after the clone instead of the numeric ID:

    PwrSuit_00.arc ... PwrSuit_cmn.arc, PwrSuit_param.arc   -> chr/archive/
    PwrSuit.arc                                             -> sound/se/chr/archive/
    n_PwrSuit_BM_HQ_NOMIP_typeB_other.tex                   -> ui/chs/chs_b1p/chs_as_n/
    b_PwrSuit99_BM_HQ_NOMIP.tex                             -> ui/chs/chs_b1p/chs_body/
    a characters.ini block

The search term differs per archive, and that is deliberate. cmn and the costume
arcs use a leading backslash only, so material and effect references get caught
along with folder references. param uses backslashes on both sides so only whole
folder references in the shot files and shotlist are touched. Dropping the
leading backslash anywhere would start hitting class names.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .arc import Arc, read_arc, write_arc
from .rename import (
    Refusal, Replacement, _field_end, _slack, component_pattern,
    rename_components, replace,
)

CHR_ARCHIVE = Path("nativePCx64/chr/archive")
SOUND_ARCHIVE = Path("sound/se/chr/archive")
UI_NAME_DIR = Path("nativePCx64/ui/chs/chs_b1p/chs_as_n")
UI_BODY_DIR = Path("nativePCx64/ui/chs/chs_b1p/chs_body")

# suffix -> content search term, as a format string over {name}
PASSES = {
    "cmn": "\\{name}",
    "param": "\\{name}",
    "costume": "\\{name}",
    "sound": None,      # handled by SOUND_PASSES, see clone_sound
}

# The voice bank carries two identifiers, not one: the character folder and a
# short sound ID. Both are matched with delimiters, because the two collide in
# real cases. Iron Man's ID is "iro", which is also the first three letters of
# "IronMan". Chris's is "chr", which is also the name of the tree the character
# folders live in, so the ID's own folder is anchored to sound\event\ rather
# than matched as a bare path component. Checked against real archives: the
# bare ID folder never appears anywhere except under sound\event\.
SOUND_PASSES = [
    ("\\{base}\\", "\\{new}\\"),                                # sound\se\chr\IronMan\...
    ("sound\\event\\{base_sid}\\", "sound\\event\\{new_sid}\\"),  # sound\event\iro\...
    ("{base_sid}_", "{new_sid}_"),                              # iro_vo_en, iro_001e, iro_en
]

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
    sound_id: str = ""            # goes in characters.ini, e.g. "iro"
    base_sound_id: str = ""       # detected from the bank when left blank
    new_sound_id: str = ""        # only for a fully independent voice bank
    num_colors: int = 0           # 0 derives it from the costume list
    include_sound: bool = True
    include_ui: bool = True
    rename_sound_contents: bool = False
    underscore_names: bool = True   # also rename IronMan_l0, n_IronMan_BM_HQ

    def __post_init__(self):
        self.game_dir = Path(self.game_dir)
        self.out_dir = Path(self.out_dir)
        # NumColors is the palette count on the select screen, and each palette
        # is one costume arc. Claiming more colours than you shipped points the
        # game at files that are not there, so this is derived, never typed.
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

    snd = game_dir / SOUND_ARCHIVE / f"{char_id}_{sound_lang}.arc"
    if snd.is_file():
        found["sound"] = snd

    return found


def detect_base_name(arc: Arc) -> str | None:
    """
    Read the character's codename out of the archive's own path table.

    Internal paths look like chr\\IronMan\\model\\1p\\... so the component after
    chr is the name, spelled exactly the way the game spells it. Beats trusting
    a character ID list that may not match your install.
    """
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


def is_sound_entry(entry) -> bool:
    return re.split(r"[\\/]", entry.path)[0].lower() == "sound"


def clone_archive(spec: CloneSpec, suffix: str, src: Path, log=print) -> ArcResult:
    kind = kind_for_suffix(suffix)
    term = PASSES[kind]
    arc = read_arc(src)

    # Character archives carry a handful of sound entries, and every string
    # inside an embedded sbkr is packed with no padding at all. Those are the
    # only references here that pin the name to a fixed length. Leaving them
    # pointing at the base character costs nothing, since SoundID points there
    # too, and it is what frees the name length everywhere else.
    share_sound = not spec.rename_sound_contents
    targets = [e for e in arc.entries if not (share_sound and is_sound_entry(e))]
    skipped = len(arc.entries) - len(targets)

    content_hits = 0
    refused: list[Refusal] = []

    if term is not None:
        old_term = term.format(name=spec.base_name)
        new_term = term.format(name=spec.new_name)
        for e in targets:
            res = replace(e.data, old_term, new_term, whole_component=True,
                          underscores=spec.underscore_names)
            if res.count or res.refused:
                e.data = res.data
                content_hits += res.count
                for r in res.refused:
                    r.context = f"{e.filename}: {r.context}"
                refused.extend(res.refused)

    path_renames = 0
    cap = arc.path_len - 1
    for e in targets:
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
        log(f"{src.name}: {len(refused)} refusals, not writing an output")
        return ArcResult(
            label=suffix, source=src, dest=Path(), entries=len(arc.entries),
            content_hits=content_hits, path_renames=path_renames, refused=refused,
        )

    if suffix == "sound":
        dest_name = f"{spec.new_name}.arc"
        dest = spec.out_dir / SOUND_ARCHIVE / dest_name
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
    """Read the short sound ID out of a voice bank's paths, e.g. iro_vo_en."""
    for e in arc.entries:
        m = SOUND_ID_PATTERN.search(e.path)
        if m:
            return m.group(1)
    return None


def clone_sound(spec: CloneSpec, src: Path, log=print) -> ArcResult:
    """
    Rebuild the voice bank under the clone's own name and sound ID.

    Only worth doing if the clone needs its own voice lines. If it shares the
    base character's voice, copy the file instead and point SoundID at the base
    ID, which is what the guide does and why it works.

    Every string in the sbkr, srqr and stqr is packed with no padding and the
    record stride tracks the string length, so any length change here has to
    rebuild the whole table. Until someone writes that, both names must keep
    their original lengths.
    """
    arc = read_arc(src)
    base_sid = spec.base_sound_id or detect_sound_id(arc) or ""
    new_sid = spec.new_sound_id or base_sid

    if not base_sid:
        raise ValueError(f"could not find a sound ID in {src.name}")

    terms = {
        "base": spec.base_name, "new": spec.new_name,
        "base_sid": base_sid, "new_sid": new_sid,
    }

    hits = 0
    refused: list[Refusal] = []
    for e in arc.entries:
        for old_t, new_t in SOUND_PASSES:
            old_s, new_s = old_t.format(**terms), new_t.format(**terms)
            if old_s == new_s:
                continue
            res = replace(e.data, old_s, new_s)
            e.data = res.data
            hits += res.count
            for r in res.refused:
                r.context = f"{e.filename}: {r.context}"
            refused.extend(res.refused)

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

    dest = spec.out_dir / SOUND_ARCHIVE / f"{spec.new_name}.arc"
    if refused:
        log(f"{src.name}: {len(refused)} refusals, voice bank not written")
        return ArcResult("sound", src, Path(), len(arc.entries), hits, path_renames, refused)

    write_arc(arc, dest)
    log(f"{src.name} -> {dest.name}  sound ID {base_sid} to {new_sid}, "
        f"{hits} in contents, {path_renames} paths")
    return ArcResult("sound", src, dest, len(arc.entries), hits, path_renames)


def max_name_length(spec: CloneSpec, log=print) -> tuple[int, str]:
    """
    Longest clone name that fits, given the archives on disk.

    Two ceilings. Content strings can only grow into whatever padding sits
    behind them, and internal paths cannot exceed the header's field width.
    Returns the smaller, plus which one bound it.
    """
    sources = find_sources(spec.game_dir, spec.char_id, spec.sound_lang)
    share_sound = not spec.rename_sound_contents
    base_len = len(spec.base_name)
    content_cap, path_cap = 10**6, 10**6
    reason = "nothing found"

    for suffix, src in sources.items():
        if suffix == "sound":
            continue
        arc = read_arc(src)
        term = PASSES[kind_for_suffix(suffix)]
        cap = arc.path_len - 1
        for e in arc.entries:
            if share_sound and is_sound_entry(e):
                continue
            if spec.base_name in re.split(r"[\\/]", e.path):
                grown = len(e.path) - base_len
                path_cap = min(path_cap, cap - grown)
            if term is None:
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
    """
    Paths where the name is not a standalone folder, for eyeballing.

    These are the judgement calls. A leaf like IronMan_l0 clearly belongs to the
    character and has to move with it. Something like toon_Storm_BM_HQ might be
    shared with other characters, in which case renaming the clone's copy is
    harmless but worth knowing about. Anything with no delimiter at all, the
    StormSword and GenmuZero shape, is a move name and is never touched.
    """
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


def find_ui_arc(game_dir: Path) -> Path | None:
    hits = list(Path(game_dir).rglob("mnchs_en.arc"))
    return hits[0] if hits else None


def copy_ui_elements(spec: CloneSpec, log=print) -> tuple[list[Path], list[str]]:
    """Pull the silhouette and name plate out of mnchs_en.arc and drop loose
    .tex copies into the select screen directories under the clone's name."""
    written: list[Path] = []
    warnings: list[str] = []

    ui_arc_path = find_ui_arc(spec.game_dir)
    if ui_arc_path is None:
        warnings.append("mnchs_en.arc not found, select screen art skipped")
        return written, warnings

    ui_arc = read_arc(ui_arc_path)
    wanted = {
        UI_NAME_TEMPLATE.format(name=spec.base_name).lower():
            (UI_NAME_DIR, UI_NAME_TEMPLATE.format(name=spec.new_name)),
        UI_BODY_TEMPLATE.format(name=spec.base_name).lower():
            (UI_BODY_DIR, UI_BODY_TEMPLATE.format(name=spec.new_name)),
    }

    for e in ui_arc.entries:
        leaf = re.split(r"[\\/]", e.path)[-1].lower()
        if leaf in wanted:
            out_dir, new_leaf = wanted.pop(leaf)
            dest = spec.out_dir / out_dir / f"{new_leaf}.tex"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(e.data)
            written.append(dest)
            log(f"select screen art -> {dest.name}")

    for missing in wanted:
        warnings.append(f"{missing} not in mnchs_en.arc, that element needs doing by hand")

    return written, warnings


def next_character_index(ini_path: Path) -> int:
    if not ini_path.is_file():
        return 1
    text = ini_path.read_text(errors="replace")
    used = [int(m) for m in re.findall(r"^\s*\[Character(\d+)\]", text, re.MULTILINE)]
    return max(used) + 1 if used else 1


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
    if not sources:
        raise FileNotFoundError(
            f"no archives for character {spec.char_id} under {spec.game_dir / CHR_ARCHIVE}"
        )

    wanted = set(spec.costumes) | {"cmn", "param"}
    if spec.include_sound:
        wanted.add("sound")

    for suffix, src in sources.items():
        if suffix not in wanted:
            continue
        if suffix == "sound" and spec.rename_sound_contents:
            report.arcs.append(clone_sound(spec, src, log))
            continue
        if suffix == "sound":
            dest = spec.out_dir / SOUND_ARCHIVE / f"{spec.new_name}.arc"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            log(f"{src.name} -> {dest.name}  copied verbatim")
            report.arcs.append(ArcResult("sound", src, dest, 0, 0, 0))
            continue
        report.arcs.append(clone_archive(spec, suffix, src, log))

    missing = wanted - set(sources) - {"sound"}
    for m in sorted(missing):
        report.warnings.append(f"{spec.char_id}_{m}.arc not found, skipped")

    if spec.include_ui:
        written, warns = copy_ui_elements(spec, log)
        report.files.extend(written)
        report.warnings.extend(warns)

    ini_path = spec.game_dir / "nativePCx64" / "characters.ini"
    index = next_character_index(ini_path)
    report.ini_block = build_ini_block(spec, index)
    block_file = spec.out_dir / "characters.ini.append.txt"
    block_file.parent.mkdir(parents=True, exist_ok=True)
    block_file.write_text(report.ini_block)
    report.files.append(block_file)

    if not spec.sound_id:
        report.warnings.append("SoundID is empty, the clone will be silent until you set it")

    return report


def install(spec: CloneSpec, report: CloneReport, log=print) -> list[Path]:
    """Copy the staged output over the live install. Backs up characters.ini."""
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
        log("characters.ini not found, append the block yourself")

    return copied
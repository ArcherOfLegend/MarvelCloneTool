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
from .rename import Refusal, Replacement, rename_path, replace

CHR_ARCHIVE = Path("nativePCx64/chr/archive")
SOUND_ARCHIVE = Path("sound/se/chr/archive")
UI_NAME_DIR = Path("nativePCx64/ui/chs/chs_b1p/chs_as_n")
UI_BODY_DIR = Path("nativePCx64/ui/chs/chs_b1p/chs_body")

# suffix -> content search term, as a format string over {name}
PASSES = {
    "cmn": "\\{name}",
    "param": "\\{name}\\",
    "costume": "\\{name}",
    "sound": None,      # copied verbatim, contents untouched
}

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
    sound_id: str = ""            # 3 letter code for characters.ini, e.g. "iro"
    num_colors: int = 8
    include_sound: bool = True
    include_ui: bool = True
    rename_sound_contents: bool = False

    def __post_init__(self):
        self.game_dir = Path(self.game_dir)
        self.out_dir = Path(self.out_dir)


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


def clone_archive(spec: CloneSpec, suffix: str, src: Path, log=print) -> ArcResult:
    kind = kind_for_suffix(suffix)
    term = PASSES[kind]
    arc = read_arc(src)

    content_hits = 0
    refused: list[Refusal] = []

    if term is not None:
        old_term = term.format(name=spec.base_name)
        new_term = term.format(name=spec.new_name)
        for e in arc.entries:
            res = replace(e.data, old_term, new_term)
            if res.count or res.refused:
                e.data = res.data
                content_hits += res.count
                for r in res.refused:
                    r.context = f"{e.filename}: {r.context}"
                refused.extend(res.refused)

    path_renames = 0
    cap = arc.path_len - 1
    for e in arc.entries:
        renamed = rename_path(e.path, spec.base_name, spec.new_name)
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
        + (f", {len(refused)} REFUSED" if refused else ""))

    return ArcResult(
        label=suffix, source=src, dest=dest, entries=len(arc.entries),
        content_hits=content_hits, path_renames=path_renames, refused=refused,
    )


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
    # SOundID is spelled the way the guide spells it. If the Clone Engine parser
    # turns out to be case sensitive on keys, this is the line to look at first.
    return (
        f"[Character{index}]\n"
        f"CharacterID={spec.new_name}\n"
        f"BaseCharacter={spec.base_name}\n"
        f"SOundID={spec.sound_id}\n"
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
        if suffix == "sound" and not spec.rename_sound_contents:
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
        report.warnings.append("SOundID is empty, the clone will be silent until you set it")

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

"""
Headless check. Builds a synthetic install, clones it at several name lengths,
and asserts the things that would silently corrupt an archive if they broke.

    python tests/roundtrip.py

No PyQt needed, so CI can run it on Linux.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mvcclone.arc import Arc, ArcEntry, hash_for_ext, read_arc, verify_roundtrip, write_arc
from mvcclone.clone import CloneSpec, detect_base_name, find_sources, run
from mvcclone.rename import replace

BASE = "IronMan"
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def padded(text, size):
    raw = text.encode()
    assert len(raw) < size, f"{text!r} does not fit in {size}"
    return raw + b"\x00" * (size - len(raw))


def make_arc(entries, dest):
    write_arc(Arc(version=7, entries=entries, path_len=64, header_pad=0), dest)


def build_install(root: Path):
    cmn = [
        ArcEntry(r"chr\IronMan\effect\eftpathlist", hash_for_ext("efl"), 0, 0, 0, 0,
                 padded(r"\IronMan\effect\hit", 64) + padded(r"\IronMan\effect\beam", 64)),
        ArcEntry(r"chr\IronMan\model\IronMan", hash_for_ext("mod"), 0, 0, 0, 0,
                 padded(r"\IronMan\model\1p", 48)),
    ]
    param = [
        ArcEntry(r"chr\IronMan\shot\shot2CMcsle", hash_for_ext("xfs"), 0, 0, 0, 0,
                 padded(r"\IronMan\shot\x", 40) + b"ClassIronManThing\x00"),
    ]
    costume = [
        ArcEntry(r"chr\IronMan\model\1p\Ironman_tex1_BM", hash_for_ext("tex"), 0, 0, 0, 0,
                 b"TEXDATA"),
        ArcEntry(r"chr\IronMan\model\1p\IronMan", hash_for_ext("mrl"), 0, 0, 0, 0,
                 padded(r"\IronMan\model\1p\mat", 56)),
    ]
    ui = [
        ArcEntry(r"ui\chs\b_IronMan99_BM_HQ_NOMIP", hash_for_ext("tex"), 0, 0, 0, 0, b"SIL"),
        ArcEntry(r"ui\chs\n_IronMan_BM_HQ_NOMIP_typeB_other", hash_for_ext("tex"),
                 0, 0, 0, 0, b"NAME"),
    ]
    sound = [ArcEntry(r"sound\se\iro\voice", hash_for_ext("sngw"), 0, 0, 0, 0, b"VOICE")]

    chr_dir = root / "nativePCx64/chr/archive"
    chr_dir.mkdir(parents=True)
    make_arc(cmn, chr_dir / "0033_cmn.arc")
    make_arc(param, chr_dir / "0033_param.arc")
    for slot in ("00", "01"):
        make_arc(costume, chr_dir / f"0033_{slot}.arc")

    (root / "sound/se/chr/archive").mkdir(parents=True)
    make_arc(sound, root / "sound/se/chr/archive/0033_01.arc")

    (root / "nativePCx64/ui").mkdir(parents=True, exist_ok=True)
    make_arc(ui, root / "nativePCx64/ui/mnchs_en.arc")
    (root / "nativePCx64/characters.ini").write_text("[Character1]\nCharacterID=Yun\n")

    return cmn


def main():
    root = Path(tempfile.mkdtemp(prefix="mvcclone_test_")) / "game"
    originals = build_install(root)
    chr_dir = root / "nativePCx64/chr/archive"

    print("archives round trip through a repack")
    for arc_file in sorted(chr_dir.glob("*.arc")):
        ok, detail = verify_roundtrip(arc_file)
        check(arc_file.name, ok, detail)

    print("\ncodename comes off the path table")
    check("detect_base_name", detect_base_name(read_arc(chr_dir / "0033_cmn.arc")) == BASE)
    check("find_sources", len(find_sources(root, "0033")) == 5)

    print("\nreplacement never changes a file's length")
    for name in ("Bob", "PwrSuit", "IronManJr"):
        src = padded(r"\IronMan\effect\hit", 64)
        out = replace(src, f"\\{BASE}", f"\\{name}")
        check(f"{name} keeps 64 bytes", len(out.data) == len(src), f"got {len(out.data)}")

    print("\nclone at several name lengths")
    for name, expect_ok in (("Bob", True), ("PwrSuit", True), ("IronManJr", True),
                            ("SuperLongExtendedSuitNameHere", False)):
        out_dir = Path(tempfile.mkdtemp(prefix=f"out_{name}_"))
        shutil.rmtree(out_dir, ignore_errors=True)
        spec = CloneSpec(root, out_dir, "0033", BASE, name, ["00", "01"], sound_id="iro")
        report = run(spec, log=lambda _m: None)
        check(f"{name} ({len(name)} chars) ok={expect_ok}", report.ok is expect_ok,
              f"{report.total_refusals} refusals")

        if not expect_ok:
            continue

        cloned = read_arc(out_dir / "nativePCx64/chr/archive" / f"{name}_cmn.arc")
        check(f"{name} payload size held",
              len(cloned.entries[0].data) == len(originals[0].data))
        # Not "BASE is absent" — a name like IronManJr legitimately contains it.
        check(f"{name} paths renamed",
              all(name in e.path for e in cloned.entries if "\\" in e.path))
        check(f"{name} new name in payload",
              f"\\{name}".encode() in cloned.entries[0].data)

        param = read_arc(out_dir / "nativePCx64/chr/archive" / f"{name}_param.arc")
        check(f"{name} class reference untouched",
              b"ClassIronManThing" in param.entries[0].data)

        check(f"{name} sound arc written",
              (out_dir / "sound/se/chr/archive" / f"{name}.arc").is_file())
        check(f"{name} select screen art written",
              (out_dir / "nativePCx64/ui/chs/chs_b1p/chs_body"
               / f"b_{name}99_BM_HQ_NOMIP.tex").is_file())
        check(f"{name} ini block numbered past the existing one",
              "[Character2]" in report.ini_block)

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
        ArcEntry(r"ui\chs\chs_b1p\chs_body\b_IronMan99_BM_HQ_NOMIP",
                 hash_for_ext("tex"), 0, 0, 0, 0, b"SIL"),
        ArcEntry(r"ui\chs\chs_b1p\chs_as_n\n_IronMan_BM_HQ_NOMIP_typeB_other",
                 hash_for_ext("tex"), 0, 0, 0, 0, b"NAME"),
        # the one the guide never mentions, whose absence is a fatal error
        ArcEntry(r"ui\game\ga_hp_f\f_IronMan00_BM_HQ_NOMIP",
                 hash_for_ext("tex"), 0, 0, 0, 0, b"FACE0"),
        ArcEntry(r"ui\game\ga_hp_f\f_IronMan01_BM_HQ_NOMIP",
                 hash_for_ext("tex"), 0, 0, 0, 0, b"FACE1"),
    ]
    sound = [ArcEntry(r"sound\se\iro\voice", hash_for_ext("sngw"), 0, 0, 0, 0, b"VOICE")]

    chr_dir = root / "nativePCx64/chr/archive"
    chr_dir.mkdir(parents=True)
    make_arc(cmn, chr_dir / "0033_cmn.arc")
    make_arc(param, chr_dir / "0033_param.arc")
    for slot in ("00", "01"):
        make_arc(costume, chr_dir / f"0033_{slot}.arc")

    (root / "nativePCx64/sound/se/chr/archive").mkdir(parents=True)
    make_arc(sound, root / "nativePCx64/sound/se/chr/archive/0033_01.arc")

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
              (out_dir / "nativePCx64/sound/se/chr/archive" / f"{name}.arc").is_file())
        check(f"{name} select screen silhouette written",
              (out_dir / "nativePCx64/ui/chs/chs_b1p/chs_body"
               / f"b_{name}99_BM_HQ_NOMIP.tex").is_file())
        check(f"{name} select screen name plate written",
              (out_dir / "nativePCx64/ui/chs/chs_b1p/chs_as_n"
               / f"n_{name}_BM_HQ_NOMIP_typeB_other.tex").is_file())
        for slot in ["00", "01"]:
            check(f"{name} HP portrait {slot} written",
                  (out_dir / "nativePCx64/ui/game/ga_hp_f"
                   / f"f_{name}{slot}_BM_HQ_NOMIP.tex").is_file())
            check(f"{name} silhouette {slot} written",
                  (out_dir / "nativePCx64/ui/chs/chs_b1p/chs_body"
                   / f"b_{name}{slot}_BM_HQ_NOMIP.tex").is_file())
        check(f"{name} ini block numbered past the existing one",
              "[Character2]" in report.ini_block)
        check(f"{name} ini uses SoundID", "SoundID=iro" in report.ini_block,
              report.ini_block.replace("\n", " | "))

    print("\nUI texture leaf renaming")
    from mvcclone.clone import costume_variants, rename_ui_leaf, sound_archive_dir

    slots = [f"{i:02d}" for i in range(8)]
    check("b_Ryu99 fans out to every costume plus 99",
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", slots)
          == [f"b_RyA{s}_BM_HQ_NOMIP" for s in slots] + ["b_RyA99_BM_HQ_NOMIP"])
    check("f_Ryu00 fans out to every costume",
          costume_variants("f_Ryu00_BM_HQ_NOMIP", "Ryu", "RyA", slots)
          == [f"f_RyA{s}_BM_HQ_NOMIP" for s in slots])
    check("unnumbered name plate stays single",
          costume_variants("n_Ryu_BM_HQ_NOMIP_typeB_other", "Ryu", "RyA", slots)
          == ["n_RyA_BM_HQ_NOMIP_typeB_other"])
    ui_cases = [
        ("b_IronMan99_BM_HQ_NOMIP", BASE, "b_NEW99_BM_HQ_NOMIP"),
        ("n_IronMan_BM_HQ_NOMIP_typeB_other", BASE, "n_NEW_BM_HQ_NOMIP_typeB_other"),
        ("f_Ryu00_BM_HQ_NOMIP", "Ryu", "f_NEW00_BM_HQ_NOMIP"),
        ("f_Ryu07_BM_HQ_NOMIP", "Ryu", "f_NEW07_BM_HQ_NOMIP"),
        ("StormSword", "Storm", "StormSword"),
        ("MajinVergil_Tex01_BM", "Vergil", "MajinVergil_Tex01_BM"),
        ("Ironman_tex1_BM", BASE, "Ironman_tex1_BM"),
    ]
    for leaf, base, want in ui_cases:
        got = rename_ui_leaf(leaf, base, "NEW")
        check(f"ui leaf {leaf}", got == want, got)

    print("\nevent audio files match the references the rewrite produces")
    from mvcclone.clone import clone_sound_events

    ev_game = Path(tempfile.mkdtemp()) / "g"
    ev_dir = ev_game / "nativePCx64/sound/event/iro/source"
    ev_dir.mkdir(parents=True)
    stream_names = ("iro_038e", "iro_039e_pu", "2iro_018ce", "2iro_036be")
    for stream in stream_names:
        (ev_dir / f"{stream}.sngw").write_bytes(b"AUDIO")

    ev_out = Path(tempfile.mkdtemp())
    ev_spec = CloneSpec(ev_game, ev_out, "0033", BASE, "PwrSuit", ["00"])
    copied, _ = clone_sound_events(ev_spec, "iro", "pws", log=lambda _m: None)
    on_disk = {f.name for f in copied}
    check("every event stream copied", len(copied) == len(stream_names), str(on_disk))
    for stream in stream_names:
        ref = f"sound\\event\\iro\\source\\{stream}".encode() + b"\x00" * 20
        rewritten = replace(ref, "iro_", "pws_").data.rstrip(b"\x00").decode()
        leaf = rewritten.split("\\")[-1]
        check(f"{stream} reference resolves", f"{leaf}.sngw" in on_disk, leaf)

    print("\na missing voice bank is reported, not swallowed")
    bare = Path(tempfile.mkdtemp()) / "g"
    (bare / "nativePCx64/chr/archive").mkdir(parents=True)
    make_arc([ArcEntry(r"chr\IronMan\model\x", hash_for_ext("mod"), 0, 0, 0, 0, b"X")],
             bare / "nativePCx64/chr/archive/0033_cmn.arc")
    silent = run(CloneSpec(bare, Path(tempfile.mkdtemp()), "0033", BASE, "PwrSuit",
                           ["00"], sound_id="iro"), log=lambda _m: None)
    check("missing voice bank warns",
          any("voice bank" in w for w in silent.warnings), str(silent.warnings))

    print("\nsound folder is discovered, not assumed")
    for layout in ("nativePCx64/sound/se/chr/archive", "sound/se/chr/archive"):
        probe = Path(tempfile.mkdtemp())
        (probe / layout).mkdir(parents=True)
        check(f"finds {layout}", sound_archive_dir(probe).as_posix() == layout,
              sound_archive_dir(probe).as_posix())

    print("\nnames embedded in asset names")
    from mvcclone.rename import rename_components as rc
    embedded = [
        # (path, base, component-only result, underscore-tier result)
        (r"chr\Storm\model\1p\Storm", "Storm", "NEW", "NEW"),
        (r"chr\Storm\shot\StormSword", "Storm", "StormSword", "StormSword"),
        (r"chr\Storm\shot\LightningStorm", "Storm", "LightningStorm", "LightningStorm"),
        (r"chr\Sentinel\shot\SentinelForceBomb", "Sentinel",
         "SentinelForceBomb", "SentinelForceBomb"),
        (r"chr\Zero\shot\GenmuZero", "Zero", "GenmuZero", "GenmuZero"),
        (r"chr\Vergil\model\MajinVergil_Tex01", "Vergil",
         "MajinVergil_Tex01", "MajinVergil_Tex01"),
        (r"chr\IronMan\motion\IronMan_l0", BASE, "IronMan_l0", "NEW_l0"),
        (r"ui\game\ga_hp_n\n_IronMan_BM_HQ", BASE,
         "n_IronMan_BM_HQ", "n_NEW_BM_HQ"),
    ]
    for path, base, plain, under in embedded:
        leaf = lambda p: p.split("\\")[-1]
        check(f"component: {leaf(path)}", leaf(rc(path, base, "NEW")) == plain,
              leaf(rc(path, base, "NEW")))
        check(f"underscore: {leaf(path)}",
              leaf(rc(path, base, "NEW", underscores=True)) == under,
              leaf(rc(path, base, "NEW", underscores=True)))

    print("\na sound ID that collides with the chr folder")
    from mvcclone.arc import Arc, write_arc
    from mvcclone.clone import clone_sound, detect_sound_id

    def packed(*strings):
        out = b""
        for text in strings:
            out += text.encode() + b"\x00" + b"\x79\xf8\x4d\x72"
        return out

    chris = [
        ArcEntry(r"sound\se\chr\Chris\chr_vo_en\source\chr_001e",
                 hash_for_ext("sngw"), 0, 0, 0, 0, b"RIFF"),
        ArcEntry(r"sound\se\chr\Chris\chr_vo_en\chr_vo_en",
                 hash_for_ext("sbkr"), 0, 0, 0, 0,
                 packed(r"sound\se\chr\Chris\chr_vo_en\source\chr_001e")),
        ArcEntry(r"sound\event\chr\chr_en", hash_for_ext("stqr"), 0, 0, 0, 0,
                 packed(r"sound\event\chr\source\chr_038e")),
    ]
    chris_arc = Path(tempfile.mkdtemp()) / "0044_01.arc"
    write_arc(Arc(version=7, entries=chris, path_len=64, header_pad=0), chris_arc)

    check("sound ID detected as chr", detect_sound_id(read_arc(chris_arc)) == "chr")
    spec = CloneSpec(root, Path(tempfile.mkdtemp()), "0044", "Chris", "Piers",
                     new_sound_id="prs", rename_sound_contents=True)
    result = clone_sound(spec, chris_arc, log=lambda _m: None)
    check("Chris voice bank clones cleanly", not result.refused)
    if not result.refused:
        cloned = read_arc(result.dest)
        check("chr folder survives a chr sound ID",
              all(p.startswith("sound\\se\\chr\\") or p.startswith("sound\\event\\prs\\")
                  for p in (e.path for e in cloned.entries)),
              str([e.path for e in cloned.entries]))
        check("no Chris left behind",
              not any("Chris" in e.path for e in cloned.entries))

    print("\nNumColors follows the costume list")
    for costumes in (["00", "01"], ["00", "01", "02", "03", "04", "05", "06", "07"]):
        spec = CloneSpec(root, Path(tempfile.mkdtemp()), "0033", BASE, "PwrSuit",
                         costumes, sound_id="iro")
        check(f"{len(costumes)} costumes gives NumColors={len(costumes)}",
              spec.num_colors == len(costumes), f"got {spec.num_colors}")

    print("\npacked fields refuse any length change")
    packed = b"name\x00" + b"\x79\xf8\x4d\x72"      # string then a hash, no padding
    for new, want_ok in (("nick", True), ("no", False), ("longer", False)):
        r = replace(packed, "name", new)
        check(f"packed 'name' -> '{new}' ok={want_ok}", r.ok is want_ok,
              r.refused[0].reason if r.refused else "")
    padded_field = b"name" + b"\x00" * 8
    for new, want_ok in (("no", True), ("muchlongername", False)):
        r = replace(padded_field, "name", new)
        check(f"padded 'name' -> '{new}' ok={want_ok}", r.ok is want_ok)

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
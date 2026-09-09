import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mvcclone.arc import Arc, ArcEntry, hash_for_ext, read_arc, verify_roundtrip, write_arc
from mvcclone.clone import (
    CloneSpec, clone_archive, detect_base_name, find_sources, max_name_length, run,
)
from mvcclone import sound_bank as sound_bank_mod
from mvcclone.rename import replace

BASE = "IronMan"
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def make_bank(paths, magic=b"SBKR"):
    """A minimal but genuine sound bank: header, [path\\0][payload] records, trailer.

    Matches the real layout measured off Iron Man's: a fixed header, records whose
    stride tracks the string length, and no offset table anywhere.
    """
    header = magic + b"\x04\x00\x00\x00" + bytes(36)
    body = b""
    for path in paths:
        body += path.encode() + b"\x00" + bytes(80)
    return header + body + bytes(8)


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
        # the per-costume UI textures a real costume arc carries
        ArcEntry(r"ui\game\ga_hp_f\f_IronMan00_BM_HQ_NOMIP", hash_for_ext("tex"),
                 0, 0, 0, 0, b"FACE"),
        ArcEntry(r"ui\game\ga_hp_fb\fb_IronMan00_BM_HQ_NOMIP", hash_for_ext("tex"),
                 0, 0, 0, 0, b"BORDER"),
        ArcEntry(r"ui\chs\chs_b1p\chs_body\b_IronMan00_BM_HQ_NOMIP", hash_for_ext("tex"),
                 0, 0, 0, 0, b"BODY"),
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
    sound = [
        ArcEntry(r"sound\se\chr\IronMan\iro_vo_en\source\iro_001e",
                 hash_for_ext("sngw"), 0, 0, 0, 0, b"VOICE"),
        ArcEntry(r"sound\se\chr\IronMan\iro_vo_en\iro_vo_en",
                 hash_for_ext("sbkr"), 0, 0, 0, 0,
                 make_bank([r"sound\se\chr\IronMan\iro_vo_en\source\iro_001e"])),
    ]

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
    # Sound banks get rebuilt rather than patched, so any length works until the
    # path field or a non-bank field runs out. Past that the name is rejected
    # up front rather than producing a half-renamed clone.
    for name, expect_ok in (("PwrSuit", True), ("Bob", True), ("IronManJr", True),
                            ("SuperLongExtendedSuitNameHere", False)):
        out_dir = Path(tempfile.mkdtemp(prefix=f"out_{name}_"))
        shutil.rmtree(out_dir, ignore_errors=True)
        spec = CloneSpec(root, out_dir, "0033", BASE, name, ["00", "01"], sound_id="iro")
        try:
            report = run(spec, log=lambda _m: None)
        except ValueError:
            check(f"{name} ({len(name)} chars) rejected", not expect_ok)
            continue
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

        voice = out_dir / "nativePCx64/sound/se/chr/archive" / f"{name}.arc"
        check(f"{name} sound arc written", voice.is_file())
        if voice.is_file():
            # Rebuilt, not copied. The engine asks for the clone's own folder.
            voice_paths = [e.path for e in read_arc(voice).entries]
            check(f"{name} voice bank folder renamed",
                  all(f"chr\\{name}\\" in p for p in voice_paths),
                  str(voice_paths[:2]))
        check(f"{name} select screen silhouette written",
              (out_dir / "nativePCx64/ui/chs/chs_b1p/chs_body"
               / f"b_{name}99_BM_HQ_NOMIP.tex").is_file())
        check(f"{name} select screen name plate written",
              (out_dir / "nativePCx64/ui/chs/chs_b1p/chs_as_n"
               / f"n_{name}_BM_HQ_NOMIP_typeB_other.tex").is_file())
        # Per-costume UI lives inside the costume arcs, so the check is that
        # cloning the arc renamed those entries, not that loose files appeared.
        costume_arc = read_arc(out_dir / "nativePCx64/chr/archive" / f"{name}_00.arc")
        ui_entries = [e.path for e in costume_arc.entries
                      if e.path.lower().startswith("ui")]
        check(f"{name} costume arc carries UI", len(ui_entries) == 3, str(ui_entries))
        # Not "BASE is absent": IronManJr legitimately contains it.
        check(f"{name} costume UI renamed",
              all(name in p for p in ui_entries), str(ui_entries))
        check(f"{name} HP portrait entry renamed",
              any(f"f_{name}00_" in p for p in ui_entries), str(ui_entries))
        check(f"{name} ini block numbered past the existing one",
              "[Character2]" in report.ini_block)
        check(f"{name} ini uses SoundID", "SoundID=iro" in report.ini_block,
              report.ini_block.replace("\n", " | "))

    print("\nsound banks are rebuilt, so the name length is free")
    packed_bank = [
        ArcEntry(r"chr\IronMan\model\x", hash_for_ext("mod"), 0, 0, 0, 0,
                 padded(r"\IronMan\model\1p", 48)),
        ArcEntry(r"sound\se\chr\IronMan\iro_se\iro_se", hash_for_ext("sbkr"),
                 0, 0, 0, 0,
                 make_bank([r"sound\se\chr\IronMan\iro_se\source\iro_se_fx"])),
    ]
    pinned = Path(tempfile.mkdtemp()) / "g"
    (pinned / "nativePCx64/chr/archive").mkdir(parents=True)
    make_arc(packed_bank, pinned / "nativePCx64/chr/archive/0033_cmn.arc")

    for candidate, want_ok in (("PwrSuit", True), ("Extremis", True), ("Bob", True)):
        res = clone_archive(
            CloneSpec(pinned, Path(tempfile.mkdtemp()), "0033", BASE, candidate),
            "cmn", pinned / "nativePCx64/chr/archive/0033_cmn.arc", log=lambda _m: None)
        check(f"{candidate} ({len(candidate)} vs 7) ok={want_ok}",
              (not res.refused) is want_ok, f"{len(res.refused)} refusals")
        if want_ok:
            kept = read_arc(res.dest)
            check(f"{candidate} keeps the sound ID, renames the folder",
                  any(f"chr\\{candidate}\\iro_se" in e.path for e in kept.entries),
                  str([e.path for e in kept.entries]))

    print("\na custom sound ID reaches the SE bank in the character archives")
    se_entries = [
        ArcEntry(r"chr\IronMan\model\x", hash_for_ext("mod"), 0, 0, 0, 0,
                 padded(r"\IronMan\model\1p", 48)),
        ArcEntry(r"sound\se\chr\IronMan\iro_se\iro_se", hash_for_ext("sbkr"),
                 0, 0, 0, 0,
                 make_bank([r"sound\se\chr\IronMan\iro_se\source\iro_se_fx"])),
    ]
    se_game = Path(tempfile.mkdtemp()) / "g"
    (se_game / "nativePCx64/chr/archive").mkdir(parents=True)
    make_arc(se_entries, se_game / "nativePCx64/chr/archive/0033_cmn.arc")

    for sid, want in (("", "iro_se"), ("qro", "qro_se")):
        res = clone_archive(
            CloneSpec(se_game, Path(tempfile.mkdtemp()), "0033", BASE, "IronMUA",
                      base_sound_id="iro", new_sound_id=sid),
            "cmn", se_game / "nativePCx64/chr/archive/0033_cmn.arc", log=lambda _m: None)
        paths = [e.path for e in read_arc(res.dest).entries] if not res.refused else []
        check(f"SE bank uses {want} when new_sound_id={sid!r}",
              any(want in p for p in paths), str(paths))

    print("\nChris: sound ID 'chr' must not eat the chr container")
    chris_cmn = [
        ArcEntry(r"chr\Chris\model\1p\Chris", hash_for_ext("mod"), 0, 0, 0, 0,
                 r"\Chris\model\1p".encode() + b"\x00" * 40),
        ArcEntry(r"sound\se\chr\Chris\chr_se\chr_se", hash_for_ext("sbkr"),
                 0, 0, 0, 0,
                 make_bank([r"sound\se\chr\Chris\chr_se\source\cri_se_handgun"])),
        ArcEntry(r"sound\se\chr\Chris\chr_se\source\cri_se_knife",
                 hash_for_ext("sngw"), 0, 0, 0, 0, b"A"),
    ]
    chris_game = Path(tempfile.mkdtemp()) / "g"
    (chris_game / "nativePCx64/chr/archive").mkdir(parents=True)
    make_arc(chris_cmn, chris_game / "nativePCx64/chr/archive/0044_cmn.arc")

    chris_res = clone_archive(
        CloneSpec(chris_game, Path(tempfile.mkdtemp()), "0044", "Chris", "Zhris",
                  base_sound_id="chr", new_sound_id="zhr"),
        "cmn", chris_game / "nativePCx64/chr/archive/0044_cmn.arc", log=lambda _m: None)
    check("Chris cmn clones cleanly", not chris_res.refused,
          str([r.reason for r in chris_res.refused]))
    if not chris_res.refused:
        chris_paths = [e.path for e in read_arc(chris_res.dest).entries]
        check("chr container survives a chr sound ID",
              all(p.startswith(("chr\\", "sound\\se\\chr\\")) for p in chris_paths),
              str(chris_paths))
        check("SE folder became zhr_se",
              any("zhr_se" in p for p in chris_paths), str(chris_paths))
        check("cri_se_ file prefix left alone",
              any("cri_se_knife" in p for p in chris_paths), str(chris_paths))

    print("\nan unrecognised packed blob still refuses, rather than corrupting")
    not_a_bank = rb"sound\se\chr\IronMan\iro_se\source\fx" + b"\x00\x79\xf8\x4d\x72"
    check("no bank magic means no rebuild", sound_bank_mod.parse(not_a_bank) is None)
    fallback = replace(not_a_bank, "\\IronMan\\", "\\Extremis\\")
    check("fallback refuses a length change", not fallback.ok,
          str(fallback.refused))

    print("\nsound bank round trips and rebuilds at any length")
    from mvcclone import sound_bank

    real_cmn = Path("/mnt/user-data/uploads/0033_cmn.arc")
    if real_cmn.is_file():
        for entry in read_arc(real_cmn).entries:
            if entry.data[:4] == b"SBKR":
                parsed = sound_bank.parse(entry.data)
                check("real sbkr parses", parsed is not None)
                if parsed:
                    check("real sbkr round trips byte for byte",
                          parsed.rebuild() == entry.data)
                    for candidate in ("Bo", "PwrSuit", "Christopher"):
                        out = sound_bank.rename(
                            entry.data, [("\\IronMan\\", f"\\{candidate}\\")])
                        check(f"sbkr rebuilds for {candidate}",
                              out is not None and out[1] > 0
                              and f"\\{candidate}\\".encode() in out[0])
                break

    print("\nthe name limit is enforced before any work happens")
    limit, _bound = max_name_length(
        CloneSpec(root, Path(tempfile.mkdtemp()), "0033", BASE, "X"), log=lambda _m: None)
    check("a limit was worked out", limit > 0, str(limit))
    at_limit = "A" * limit
    over_limit = "A" * (limit + 1)
    ok_spec = CloneSpec(root, Path(tempfile.mkdtemp()), "0033", BASE, at_limit, ["00"])
    check(f"a {limit} character name is accepted",
          run(ok_spec, log=lambda _m: None).ok)
    try:
        run(CloneSpec(root, Path(tempfile.mkdtemp()), "0033", BASE, over_limit, ["00"]),
            log=lambda _m: None)
        check(f"a {limit + 1} character name is rejected", False, "it was accepted")
    except ValueError:
        check(f"a {limit + 1} character name is rejected", True)

    print("\nthe srqr is rebuilt too, so it does not pin the limit")
    real_cmn = Path("/mnt/user-data/uploads/0033_cmn.arc")
    if real_cmn.is_file():
        for entry in read_arc(real_cmn).entries:
            if entry.data[:4] != b"SRQR":
                continue
            noop = sound_bank_mod.srqr_rename(entry.data, [("\\IronMan\\", "\\IronMan\\")])
            check("srqr no-op is byte identical", noop is not None and noop[0] == entry.data)
            for candidate in ("Bo", "Sakura", "AVeryMuchLongerName"):
                out = sound_bank_mod.srqr_rename(
                    entry.data, [("\\IronMan\\", f"\\{candidate}\\")])
                check(f"srqr rebuilds for {candidate}",
                      out is not None and f"\\{candidate}\\".encode() in out[0])
            break

    print("\nduplicate CharacterIDs are caught")
    from mvcclone.clone import existing_character_ids

    ini_dir = root / "nativePCx64"
    ini_dir.mkdir(parents=True, exist_ok=True)
    (ini_dir / "characters.ini").write_text(
        "[Character1]\nCharacterID=Yun\nBaseCharacter=Ryu\n"
        # trailing space, exactly as real files have it
        "[Character2]\nCharacterID=Iceman \nBaseCharacter=Spencer\n")
    ids = existing_character_ids(ini_dir / "characters.ini")
    check("ids parsed", set(ids) == {"Yun", "Iceman"}, str(ids))
    check("trailing space is trimmed off the id", "Iceman" in ids)
    try:
        run(CloneSpec(root, Path(tempfile.mkdtemp()), "0033", BASE, "Yun", ["00"]),
            log=lambda _m: None)
        check("a taken CharacterID is rejected", False, "it was accepted")
    except ValueError:
        check("a taken CharacterID is rejected", True)

    print("\nassist and message tables")
    from mvcclone import csa as csa_mod, msd as msd_mod
    up = Path("/mnt/user-data/uploads")
    if (up / "AssistMsg.msd").is_file():
        for name in ("AssistMsg.msd", "EndingMsg.msd"):
            raw = (up / name).read_bytes()
            table = msd_mod.parse(raw)
            check(f"{name} parses", table is not None)
            if table:
                check(f"{name} rebuilds byte for byte", table.build() == raw)
        names = msd_mod.parse((up / "AssistMsg.msd").read_bytes())
        check("text decodes", names.messages[3] == "Shoryuken", names.messages[3])
        check("text survives a round trip",
              msd_mod.decode(msd_mod.encode("Copy Vision")) == "Copy Vision")

    if (up / "AssistDef.csa").is_file():
        raw = (up / "AssistDef.csa").read_bytes()
        defs = csa_mod.parse(raw)
        check("AssistDef.csa parses", defs is not None)
        if defs:
            check("AssistDef.csa rebuilds byte for byte", defs.build() == raw)
            check("three assists per slot", all(len(s) == 3 for s in defs.slots))
            check("Hadoken is a shot", defs.slots[1][1].type == csa_mod.TYPES["shot"])
            check("Shoryuken tilts up",
                  defs.slots[1][0].direction == csa_mod.DIRECTIONS["tiltup"])
            check("Hyakki Gojin tilts down",
                  defs.slots[3][2].direction == csa_mod.DIRECTIONS["tiltdw"])
            # Every value the shipped table uses has a name from the readme.
            used_types = {a.type for s in defs.slots for a in s if a.name1}
            used_dirs = {a.direction for s in defs.slots for a in s if a.name1}
            check("every type is named",
                  used_types <= set(csa_mod.TYPES.values()), str(used_types))
            check("every direction is named",
                  used_dirs <= set(csa_mod.DIRECTIONS.values()), str(used_dirs))
            check("a slot can be written at an index past the end",
                  (lambda d: (d.set_slot(200, [csa_mod.Assist(1, 0, 2, 4)]),
                              len(d.slots) == 201
                              and d.slots[1][0].name1 == defs.slots[1][0].name1)[1])(
                      csa_mod.parse(raw)))

    print("\nCharacters.ini is found in the game root")
    from mvcclone.clone import character_list, find_characters_ini
    # Written from scratch rather than copied, so this runs anywhere.
    sample_ini = ("[Character1]\nCharacterID=Rash\nBaseCharacter=VJoe\n"
                  "SoundID=vjo\nNumColors=8\n"
                  "[Character2]\nCharacterID=KennFist\nBaseCharacter=Ryu\n"
                  "SoundID=fis\nNumColors=7\n"
                  "[Character3]\nCharacterID=PsylockF\nBaseCharacter=FeliciaF\n")
    root_ini = Path(tempfile.mkdtemp())
    (root_ini / "Characters.ini").write_text(sample_ini)
    check("found in the root",
          find_characters_ini(root_ini).name == "Characters.ini")
    listed = character_list(find_characters_ini(root_ini))
    check("all entries listed", len(listed) == 3, str(len(listed)))
    check("first entry read", listed[0][:2] == (1, "Rash"), str(listed[0]))
    check("base character read", listed[0][2] == "VJoe", str(listed[0]))
    check("a child has no SoundID", listed[2][3] == "", str(listed[2]))
    nested = Path(tempfile.mkdtemp())
    (nested / "nativePCx64").mkdir()
    (nested / "nativePCx64" / "characters.ini").write_text(sample_ini)
    check("still found under nativePCx64",
          find_characters_ini(nested).is_file())

    print("\nassist slots line up with character IDs")
    if (up / "AssistDef.csa").is_file():
        from mvcclone.clone import ROSTER
        table = csa_mod.parse((up / "AssistDef.csa").read_bytes())
        text = msd_mod.parse((up / "AssistMsg.msd").read_bytes())
        first = lambda slot: (text.messages[slot[0].name1 - 1]
                              if 0 < slot[0].name1 <= len(text.messages) else "")
        for cid, who, move in (("0001", "Ryu", "Shoryuken"),
                               ("0026", "Spider Man", "Web Ball"),
                               ("0043", "Sentinel", "Sentinel Force")):
            check(f"slot {int(cid)} is {who}",
                  first(table.slots[int(cid)]) == move, first(table.slots[int(cid)]))
        check("the roster covers the base slots",
              max(int(c) for c, _ in ROSTER) == 51)

    print("\nBGM streams are named after the CharacterID")
    if (up / "BGM.stqr").is_file() and (up / "Characters.ini").is_file():
        from mvcclone import stqr as stqr_mod
        from mvcclone.clone import character_list as chars
        music = stqr_mod.parse((up / "BGM.stqr").read_bytes())
        leaves = {p.split("\\")[-1] for p in music.paths}
        entries = chars(up / "Characters.ini")
        playable = [c for c in entries if c[3]]
        children = [c for c in entries if not c[3]]
        check("children have no SoundID", len(children) == 14, str(len(children)))
        with_track = [c for c in playable if c[1] in leaves]
        check("most playable clones have a track",
              len(with_track) > len(playable) * 0.8,
              f"{len(with_track)} of {len(playable)}")
        check("Rash has one", "Rash" in leaves)
        check("a child does not", "PsylockF" not in leaves)

    print("\nBGM is bound by the event, not the stream position")
    if (up / "BGM.stqr").is_file() and (up / "Characters.ini").is_file():
        from mvcclone.clone import bgm_stream_owners
        music = stqr_mod.parse((up / "BGM.stqr").read_bytes())
        home2 = Path(tempfile.mkdtemp())
        shutil.copyfile(up / "Characters.ini", home2 / "Characters.ini")
        owners = bgm_stream_owners(home2, len(music.paths))
        check("stream 110 is the first clone", owners.get(110) == "Rash",
              str(owners.get(110)))
        check("children are skipped", owners.get(117) == "Cyclop", str(owners.get(117)))
        from mvcclone.clone import bgm_event_index as _ev
        check("a lookup agrees", _ev(home2, "Cyclop") == 138 + 8, str(_ev(home2, "Cyclop")))
        check("an unknown character has no event", _ev(home2, "NotHere") == -1)
        leaves = [p.split("\\")[-1] for p in music.paths]
        agree = sum(1 for i, cid in owners.items() if leaves[i] == cid)
        check("most streams are also named after their owner",
              agree > len(owners) * 0.7, f"{agree} of {len(owners)}")


        # A character past the end of the table must still be settable.
        from mvcclone.clone import bgm_stream_plan
        short = stqr_mod.parse((up / "BGM.stqr").read_bytes())
        short.streams = short.streams[:150]
        short.events = short.events[:150]
        short.paths = short.paths[:150]
        plan = bgm_stream_plan(home2)
        check("the plan covers characters with no stream",
              max(i for i, _ in plan) >= len(short.paths), str(max(i for i, _ in plan)))
        target = max(i for i, _ in plan)
        # Music is bound by the event, not the stream position, so any
        # character can be set in any order without touching the others.
        from mvcclone.clone import bgm_event_index, bgm_event_plan
        events = bgm_event_plan(home2)
        check("every character has an event",
              max(e for e, _ in events) < len(music.events),
              f"{max(e for e, _ in events)} vs {len(music.events)}")
        check("no dangling events in the shipped file",
              not any(len(music.paths) <= music.event_stream(i) != 0xFFFFFFFF
                      for i in range(len(music.events))))

        work = stqr_mod.parse((up / "BGM.stqr").read_bytes())
        started = len(work.streams)
        last = bgm_event_index(home2, events[-1][1])
        first = bgm_event_index(home2, events[0][1])
        untouched = work.event_stream(bgm_event_index(home2, events[1][1]))

        work.set_event_track(last, "sound\\bgm\\source\\Last")
        work.set_event_track(first, "sound\\bgm\\source\\First")
        check("two streams added, nothing else",
              len(work.streams) == started + 2, str(len(work.streams)))
        check("the last character got its track",
              work.paths[work.event_stream(last)].endswith("Last"))
        check("the first character got its own",
              work.paths[work.event_stream(first)].endswith("First"))
        check("a character in between was not moved",
              work.event_stream(bgm_event_index(home2, events[1][1])) == untouched)
        check("an existing path is reused, not duplicated",
              (lambda n: (work.set_event_track(first, "sound\\bgm\\source\\Last"),
                          len(work.streams) == n)[1])(len(work.streams)))
        check("it still rebuilds", stqr_mod.parse(work.build()) is not None)

    print("\nevery assist slot gets a name")
    if (up / "Characters.ini").is_file():
        from mvcclone.clone import assist_slot_names, CLONE_SLOT_BASE
        home = Path(tempfile.mkdtemp()) / "ULTIMATE MARVEL VS. CAPCOM 3"
        engine = home / "nativePCx64" / "CloneEngine"
        engine.mkdir(parents=True)
        shutil.copyfile(up / "Characters.ini", home / "Characters.ini")
        shutil.copyfile(up / "AssistDef.csa", engine / "AssistDef.csa")
        who = assist_slot_names(home, 161)
        # The install path is often blank, so the tables have to be enough.
        from_tables = assist_slot_names([engine / "AssistDef.csa", ""], 161)
        check("found from the table path alone",
              from_tables.get(60) == "Rash", str(from_tables.get(60)))
        check("no hints does not crash", find_characters_ini("", None).name
              == "Characters.ini")
        check("base roster by ID", who.get(43) == "Sentinel", str(who.get(43)))
        check("children named", who.get(52) == "ZeroSh", str(who.get(52)))
        check("last child is slot 59", who.get(59) == "DrStrangeSh", str(who.get(59)))
        check("Character1 is slot 60", who.get(CLONE_SLOT_BASE + 1) == "Rash",
              str(who.get(60)))
        check("Character2 is slot 61", who.get(61) == "KennFist", str(who.get(61)))
        check("nothing past the table", max(who) < 161)

    print("\nassists edited directly, no ini in the loop")
    if (up / "AssistDef.csa").is_file():
        defs = csa_mod.parse((up / "AssistDef.csa").read_bytes())
        names = msd_mod.parse((up / "AssistMsg.msd").read_bytes())
        before = len(defs.slots)
        defs.set_slot(89, [csa_mod.Assist(names.add("Copy Vision"), 0,
                                          csa_mod.TYPES["shot"],
                                          csa_mod.DIRECTIONS["front"])])
        check("writing a slot leaves the count alone", len(defs.slots) == before)
        check("the name landed", names.messages[-1] == "Copy Vision")
        rebuilt = csa_mod.parse(defs.build())
        check("edited table re-parses", rebuilt is not None)
        if rebuilt:
            check("the edit is there", rebuilt.slots[89][0].name1 == len(names.messages))
            check("other slots untouched",
                  rebuilt.slots[1][0].name1 == defs.slots[1][0].name1)

    print("\nstream tables round trip and take new entries")
    from mvcclone import stqr
    from mvcclone.arc import ext_for_hash

    real_voice = Path("/mnt/user-data/uploads/0033_01.arc")
    if real_voice.is_file():
        blob = next(e.data for e in read_arc(real_voice).entries
                    if ext_for_hash(e.ext_hash) == "stqr")
        table = stqr.parse(blob)
        check("real stqr parses", table is not None)
        if table:
            check("real stqr rebuilds byte for byte", table.build() == blob)
            check("streams were read", len(table.streams) > 0)

            work = Path(tempfile.mkdtemp())
            (work / "in.stqr").write_bytes(blob)
            added, total = stqr.add_streams(
                work / "in.stqr",
                ["sound\\event\\mgl\\source\\a", "sound\\event\\mgl\\source\\b"],
                work / "out.stqr")
            check("two entries added", added == 2)
            after = stqr.parse((work / "out.stqr").read_bytes())
            check("result re-parses", after is not None)
            if after:
                check("stream count grew by two",
                      len(after.streams) == len(table.streams) + 2)
                check("original paths survive",
                      after.paths[:len(table.paths)] == table.paths)
                check("new paths land at the end",
                      after.paths[-2:] == ["sound\\event\\mgl\\source\\a",
                                           "sound\\event\\mgl\\source\\b"])
                last = after.events[-1]
                import struct as _s
                check("the new event points at the new stream",
                      _s.unpack_from("<I", last, 0x5C)[0] == len(after.streams) - 1)

    check("a non-stqr is refused", stqr.parse(b"NOPE" + bytes(200)) is None)

    print("\nthe 255 counterpart of a 99 texture")
    from mvcclone.clone import costume_variants
    ui_cos = [f"{i:02d}" for i in range(4)]
    check("99 alone by default",
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", ui_cos)
          == ["b_RyA99_BM_HQ_NOMIP"])
    check("99 plus 255 when asked",
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", ui_cos, False, True)
          == ["b_RyA99_BM_HQ_NOMIP", "b_RyA255_BM_HQ_NOMIP"])
    check("255 is not padded to the width of 99",
          "b_RyA255_BM_HQ_NOMIP" in
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", ui_cos, False, True))
    check("a costume texture gets no 255",
          costume_variants("f_Ryu00_BM_HQ_NOMIP", "Ryu", "RyA", ui_cos, False, True)
          == ["f_RyA00_BM_HQ_NOMIP"])
    check("an unnumbered texture gets no 255",
          costume_variants("n_Ryu_BM_HQ_typeB", "Ryu", "RyA", ui_cos, False, True)
          == ["n_RyA_BM_HQ_typeB"])
    check("255 composes with the fan out",
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", ui_cos, True, True)
          == [f"b_RyA{s}_BM_HQ_NOMIP" for s in ui_cos]
          + ["b_RyA99_BM_HQ_NOMIP", "b_RyA255_BM_HQ_NOMIP"])

    print("\nSoundID must be exactly three characters")
    for sid, want_ok in (("mgl", True), ("sh", False), ("Shadli", False), ("abcd", False)):
        try:
            run(CloneSpec(root, Path(tempfile.mkdtemp()), "0033", BASE, "Testy",
                          ["00"], sound_id=sid), log=lambda _m: None)
            check(f"SoundID {sid!r} accepted", want_ok)
        except ValueError:
            check(f"SoundID {sid!r} rejected", not want_ok)

    print("\nUI texture leaf renaming")
    from mvcclone.clone import costume_variants, rename_ui_leaf, sound_archive_dir

    slots = [f"{i:02d}" for i in range(8)]
    check("numbered UI stays single by default",
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", slots)
          == ["b_RyA99_BM_HQ_NOMIP"])
    check("fan out is available when asked for",
          costume_variants("b_Ryu99_BM_HQ_NOMIP", "Ryu", "RyA", slots, True)
          == [f"b_RyA{s}_BM_HQ_NOMIP" for s in slots] + ["b_RyA99_BM_HQ_NOMIP"])
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

    chris = [
        ArcEntry(r"sound\se\chr\Chris\chr_vo_en\source\chr_001e",
                 hash_for_ext("sngw"), 0, 0, 0, 0, b"RIFF"),
        ArcEntry(r"sound\se\chr\Chris\chr_vo_en\chr_vo_en",
                 hash_for_ext("sbkr"), 0, 0, 0, 0,
                 make_bank([r"sound\se\chr\Chris\chr_vo_en\source\chr_001e"])),
        ArcEntry(r"sound\event\chr\chr_en", hash_for_ext("stqr"), 0, 0, 0, 0,
                 make_bank([r"sound\event\chr\source\chr_038e"], b"STQR")),
    ]
    chris_arc = Path(tempfile.mkdtemp()) / "0044_01.arc"
    write_arc(Arc(version=7, entries=chris, path_len=64, header_pad=0), chris_arc)

    check("sound ID detected as chr", detect_sound_id(read_arc(chris_arc)) == "chr")
    spec = CloneSpec(root, Path(tempfile.mkdtemp()), "0044", "Chris", "Piers",
                     new_sound_id="prs")
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
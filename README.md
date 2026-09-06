# MarvelCloneTool

Clones a UMvC3 character into a Clone Engine slot.

## Install

Grab the exe from [Releases](../../releases), or run from source:

```
pip install PyQt6
python -m mvcclone
```

Python 3.10 or newer.

## Using it

Point it at your game install, type a character ID, and hit Read name. It pulls
the codename straight out of the archive's own path table, so you don't need a
character ID list and you can't get the capitalisation wrong.

Then pick a clone name and work through three buttons.

Survey checks every archive round trips through a repack, unpacks them, and
finds every reference to the codename. It reports whether each one sits in a
padded field or a packed one, and tells you the longest name that will fit.

Stage does the port into an output folder. Nothing touches the game.

Install copies the staged files over and appends the `characters.ini` block,
backing up your original ini first.

## What it produces

| Output | Goes to |
| --- | --- |
| `Name_00.arc` through `Name_07.arc` | `nativePCx64/chr/archive/` |
| `Name_cmn.arc`, `Name_param.arc` | `nativePCx64/chr/archive/` |
| `Name.arc` | `sound/se/chr/archive/` |
| `n_Name_BM_HQ_NOMIP_typeB_other.tex` | `nativePCx64/ui/chs/chs_b1p/chs_as_n/` |
| `b_Name99_BM_HQ_NOMIP.tex` | `nativePCx64/ui/chs/chs_b1p/chs_body/` |
| a `[CharacterN]` block | `nativePCx64/characters.ini` |

## Name length

The guide says the replacement name has to match the base character's length.
That is a limitation of 010 Editor's Replace in Files, which is a straight byte
swap, not a limitation of the game.

Nearly all of these strings live in fixed-size null-padded fields. A longer name
can eat the padding, a shorter one gives padding back, and either way the field
occupies the same number of bytes so nothing downstream of it moves. That is what
`rename.py` does, and it asserts the file length never changes.

Two limits still apply. The ARC header path field holds 63 bytes for the whole
internal path, so `chr\Name\model\1p\Name` has to fit inside that. And a string
with no padding behind it pins you to the source length until someone writes a
handler that rebuilds that format's offset table.

Both come back as refusals with an offset and a reason, and the archive is not
written at all rather than shipped half renamed.

## How the rename works

Two passes, same as the guide.

The content pass rewrites strings inside the files. `cmn` and the costume arcs
search for `\Name` with a leading backslash only, so material and effect
references get caught alongside folder references. `param` searches for `\Name\`
with backslashes on both sides, so only whole folder references in the shot files
and shotlist are touched. The leading backslash is what stops class references
getting clobbered. Match case is always on.

The path pass rewrites the archive's internal path table using the bare name,
no delimiters.

## Status

The ARC reader autodetects between 64 and 128 byte path fields and 0 or 4 bytes
of header padding, and refuses to guess if neither fits. Every archive is round
tripped through a repack and compared before anything is modified.

Sound is a straight file copy by default, since `SOundID` in `characters.ini`
points at the base character's bank. The deep rename option runs the content
passes over the voice arc too, but bank IDs may be hashed from the name, in which
case that needs a real handler rather than a string replace. Untested.

## Credits

Gneiss for the Clone Engine. EternalYoshi for the porting guide and for
ThreeWorkTool, which is still the tool to reach for when you need to poke at an
archive by hand.

## License

MIT.

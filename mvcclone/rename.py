"""
Binary name replacement that does not move any bytes.

The guide's method is 010 Editor's Replace in Files with match-case on, which is
a straight byte swap and therefore pins you to an identical character count.
That restriction is not actually about the file format, it is about the tool.

Most of these strings live in fixed-size null-padded fields. If a field has
trailing nulls you can write a longer name into it and eat the padding, and a
shorter name just gets more padding. Either way the field occupies the same
number of bytes, so every offset downstream of it is untouched and nothing needs
rebuilding.

What you cannot do is grow a name into a field with no slack. That case gets
reported rather than silently corrupting the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Replacement:
    offset: int
    encoding: str
    before: str
    after: str
    padding_used: int   # negative means padding was returned to the field


@dataclass
class Refusal:
    offset: int
    encoding: str
    reason: str
    context: str


@dataclass
class ReplaceResult:
    data: bytes
    applied: list[Replacement] = field(default_factory=list)
    refused: list[Refusal] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused

    @property
    def count(self) -> int:
        return len(self.applied)


def _printable(chunk: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)


def _field_end(data: bytes, start: int, step: int) -> int:
    """Offset of the null terminator that closes the string containing `start`."""
    i = start
    unit = b"\x00" * step
    while i + step <= len(data):
        if data[i:i + step] == unit:
            return i
        i += step
    return len(data)


def _slack(data: bytes, term: int, step: int) -> int:
    """
    Usable padding after a string, in bytes.

    Counts contiguous nulls from the terminator and then discards one, because
    the terminator itself is part of the string, not spare room. Get this wrong
    and every packed null-terminated string looks like it has a byte to spare.
    """
    n = 0
    i = term
    unit = b"\x00" * step
    while i + step <= len(data) and data[i:i + step] == unit:
        n += step
        i += step
    return max(0, n - step)


def component_pattern(
    name: str,
    encoding: str = "ascii",
    match_case: bool = True,
    underscores: bool = False,
):
    r"""
    Match `\name` only where it is a whole path component.

    A raw substring search is not safe once a character's name also appears
    inside its own asset names. Storm owns StormSword and LightningStorm,
    Sentinel owns SentinelForceBomb, Zero owns ZeroBusterM and GenmuZero.
    Searching for `\Storm` with a leading backslash alone still eats the front
    of `\StormSword`; searching for `Storm` bare eats both ends.

    Requiring a separator or a string terminator behind the name fixes both,
    and it still catches a reference that ends at the name, which is what the
    guide's no-trailing-backslash advice was reaching for.
    """
    step = 1 if encoding == "ascii" else 2
    codec = "ascii" if step == 1 else "utf-16le"
    flags = 0 if match_case else re.IGNORECASE

    closers = ["\\", "/", "\x00"]
    if underscores:
        # Underscore counts as a delimiter too, which picks up leaf names like
        # IronMan_l0.lmt and n_IronMan_BM_HQ_NOMIP_typeB. It deliberately still
        # skips StormSword, GenmuZero and MajinVergil_Tex01, where the name runs
        # straight into the neighbouring word with nothing between them.
        closers.append("_")
        openers = ["\\", "/", "_"]
    else:
        openers = ["\\", "/"]

    before = b"|".join(re.escape(c.encode(codec)) for c in openers)
    after = b"|".join(re.escape(c.encode(codec)) for c in closers)
    body = re.escape(name.encode(codec))
    return re.compile(b"(?<=" + before + b")" + body + b"(?=" + after + b")", flags)


def rename_components(
    path: str, old: str, new: str, match_case: bool = True, underscores: bool = False
) -> str:
    """
    Rename whole components of an ARC internal path, never bare substrings.

    With `underscores`, a component also matches when the name is delimited by
    underscores inside it, so IronMan_l0 and n_IronMan_BM_HQ get renamed while
    StormSword and GenmuZero do not.
    """
    flags = 0 if match_case else re.IGNORECASE
    out = []
    for part in re.split(r"([\\/])", path):
        if part == old or (not match_case and part.lower() == old.lower()):
            out.append(new)
        elif underscores:
            out.append(re.sub(
                r"(?:(?<=^)|(?<=_))" + re.escape(old) + r"(?=_|$)", new, part, flags=flags))
        else:
            out.append(part)
    return "".join(out)


def replace(
    data: bytes,
    old: str,
    new: str,
    *,
    encoding: str = "ascii",
    match_case: bool = True,
    whole_component: bool = False,
    underscores: bool = False,
) -> ReplaceResult:
    """
    Swap `old` for `new` throughout `data` without changing its length.

    `old` and `new` are the raw search terms. The caller is responsible for any
    leading or trailing backslash, because which delimiters you include is the
    whole difference between the cmn pass and the param pass.
    """
    step = 1 if encoding == "ascii" else 2
    codec = "ascii" if step == 1 else "utf-16le"
    old_b = old.encode(codec)
    new_b = new.encode(codec)
    if whole_component:
        old_b = old.lstrip("\\").encode(codec)
        new_b = new.lstrip("\\").encode(codec)
    delta = len(new_b) - len(old_b)

    flags = 0 if match_case else re.IGNORECASE
    if whole_component:
        pattern = component_pattern(
            old.lstrip("\\"), encoding, match_case, underscores)
    else:
        pattern = re.compile(re.escape(old_b), flags)

    if delta == 0:
        # A lambda, not the literal bytes. These terms are full of backslashes
        # and re.sub would try to read them as escape sequences.
        out = pattern.sub(lambda _m: new_b, data)
        applied = [
            Replacement(m.start(), encoding, old, new, 0)
            for m in pattern.finditer(data)
        ]
        return ReplaceResult(out, applied, [])

    buf = bytearray(data)
    result = ReplaceResult(data)
    applied: list[Replacement] = []
    refused: list[Refusal] = []

    # Walk backwards so earlier offsets stay valid while we rewrite.
    matches = list(pattern.finditer(data))
    for m in reversed(matches):
        s, e = m.start(), m.end()
        term = _field_end(buf, e, step)
        room = _slack(buf, term, step)
        tail = bytes(buf[e:term])

        if room == 0:
            # No padding at all means this is not a fixed-size buffer, it is a
            # packed record whose stride tracks the string length. Writing a
            # shorter name here would leave stray nulls between the string and
            # whatever follows it, which is just as broken as overrunning.
            # Confirmed against a real rSoundBank: stride moves byte for byte
            # with the string.
            refused.append(Refusal(
                s, encoding,
                f"packed field with no padding, length must stay at {len(old_b)}",
                _printable(bytes(buf[max(0, s - 16):term + 16])),
            ))
            continue

        if delta > room:
            refused.append(Refusal(
                s, encoding,
                f"needs {delta} more bytes, field has {room} of padding",
                _printable(bytes(buf[max(0, s - 16):term + 16])),
            ))
            continue

        rebuilt = new_b + tail + b"\x00" * (room - delta)
        buf[s:term + room] = rebuilt
        applied.append(Replacement(s, encoding, old, new, delta))

    applied.reverse()
    result.data = bytes(buf)
    result.applied = applied
    result.refused = refused
    if len(result.data) != len(data):
        raise AssertionError(
            f"replacement changed file length {len(data)} -> {len(result.data)}, "
            "this is a bug and the output must not be used"
        )
    return result


def rename_path(path: str, old: str, new: str, match_case: bool = True) -> str:
    """Rename inside an ARC internal path. No delimiters, matching the guide's
    'Find and Replace in All File Names' step."""
    if match_case:
        return path.replace(old, new)
    return re.sub(re.escape(old), new, path, flags=re.IGNORECASE)
"""
Find every place a character's internal name appears.

This is how you answer "which files need changing on which character" without
transcribing a list out of a guide and hoping it stays current. Point it at an
unpacked archive, give it the source codename, and it reports every hit with
enough context to decide whether that hit is safely renamable.

Names are matched case-insensitively in ASCII and UTF-16LE, and each hit is
classified by how much room there is to change its length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .arc import Arc


class Room(Enum):
    """How much freedom you have to change the length of this occurrence."""

    PADDED = "padded"        # trailing nulls follow, a longer name may fit
    TIGHT = "tight"          # bytes immediately follow, length must not change
    PATH_FIELD = "path"      # ARC header path field, capped at path_len - 1
    UNKNOWN = "unknown"


@dataclass
class Hit:
    file: str                # path relative to the unpack root, or "<arc header>"
    offset: int
    encoding: str            # "ascii" or "utf16le"
    matched: str             # the bytes as they actually appear, casing preserved
    context: str             # surrounding printable text
    room: Room
    slack: int               # trailing null bytes available after the match


def _printable(chunk: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)


def _slack_after(data: bytes, end: int, step: int) -> int:
    """Count trailing null bytes after a match, in units of `step`."""
    n = 0
    i = end
    while i + step <= len(data) and data[i:i + step] == b"\x00" * step:
        n += 1
        i += step
    return n


def scan_bytes(data: bytes, name: str, label: str) -> list[Hit]:
    hits: list[Hit] = []

    for encoding, step in (("ascii", 1), ("utf16le", 2)):
        needle = name.encode("ascii") if step == 1 else name.encode("utf-16le")
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        for m in pattern.finditer(data):
            start, end = m.start(), m.end()
            slack = _slack_after(data, end, step)
            room = Room.PADDED if slack else Room.TIGHT
            hits.append(Hit(
                file=label,
                offset=start,
                encoding=encoding,
                matched=m.group().decode(encoding.replace("utf16le", "utf-16le"), "replace"),
                context=_printable(data[max(0, start - 24):end + 24]),
                room=room,
                slack=slack * step,
            ))
    return hits


def scan_tree(root: str | Path, name: str, skip_ext: set[str] | None = None) -> list[Hit]:
    """Walk an unpacked archive and report every occurrence of `name`."""
    root = Path(root)
    skip_ext = skip_ext or {".tex", ".htex"}   # textures rarely carry the name
    hits: list[Hit] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() in skip_ext:
            continue
        rel = f.relative_to(root).as_posix()
        hits.extend(scan_bytes(f.read_bytes(), name, rel))
        if name.lower() in rel.lower():
            hits.append(Hit(
                file=rel, offset=-1, encoding="filename", matched=name,
                context=rel, room=Room.UNKNOWN, slack=0,
            ))
    return hits


def scan_arc_paths(arc: Arc, name: str) -> list[Hit]:
    """Occurrences inside the ARC header's internal path fields."""
    hits = []
    cap = arc.path_len - 1
    for e in arc.entries:
        if name.lower() in e.path.lower():
            hits.append(Hit(
                file="<arc header>", offset=-1, encoding="path",
                matched=name, context=e.filename,
                room=Room.PATH_FIELD, slack=cap - len(e.path),
            ))
    return hits


def summarise(hits: list[Hit]) -> dict[str, dict]:
    """Group hits by file extension so you can see which formats you must handle."""
    out: dict[str, dict] = {}
    for h in hits:
        ext = Path(h.file).suffix.lower() or h.encoding
        bucket = out.setdefault(ext, {"count": 0, "tight": 0, "files": set()})
        bucket["count"] += 1
        bucket["files"].add(h.file)
        if h.room is Room.TIGHT:
            bucket["tight"] += 1
    for bucket in out.values():
        bucket["files"] = sorted(bucket["files"])
    return out


def max_safe_length(hits: list[Hit], source_name: str) -> int:
    """
    Longest replacement name that will not require rebuilding any offset table.

    Any TIGHT hit pins you to the original length. Otherwise you are capped by
    the smallest amount of slack found across padded hits and path fields.
    """
    if any(h.room is Room.TIGHT for h in hits):
        return len(source_name)
    slacks = [h.slack for h in hits if h.room in (Room.PADDED, Room.PATH_FIELD)]
    if not slacks:
        return 0
    return len(source_name) + min(slacks)

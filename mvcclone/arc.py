"""
MT Framework ARC container read/write.

Layout assumed (UMvC3 PC, nativePCx64/chr/archive/NNNN_CC.arc):

    magic       char[4]   "ARC\\0"
    version     u16
    file_count  u16
    <pad>       u32       present on some variants, autodetected

    per entry:
    path        char[N]   null padded, N is 64 or 128, autodetected
    ext_hash    u32       JAMCRC of the lowercase extension
    csize       u32       compressed size
    dsize_flags u32       low 29 bits = decompressed size, top 3 bits = flags
    offset      u32       absolute offset into the file

Everything past the header is zlib. Some entries store raw bytes instead,
so decompression falls back to the literal bytes on a zlib error.

The autodetection exists because I have not byte-verified this against a real
UMvC3 archive. run `verify_roundtrip` on a real file before trusting a repack.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"ARC\x00"
SIZE_MASK = 0x1FFFFFFF
FLAG_MASK = 0xE0000000


def jamcrc(data: bytes) -> int:
    """CRC32 with an inverted output. MT Framework hashes extensions this way."""
    return (~zlib.crc32(data)) & 0xFFFFFFFF


# Extensions that show up in UMvC3 character archives. Hashes are derived at
# import time rather than hardcoded, so adding a name here is the whole job.
EXT_NAMES = [
    "tex", "mod", "mrl", "lmt", "efl", "epv", "sdl", "xfs", "gmd", "chn",
    "ccl", "rtex", "obja", "stq", "srqr", "sbkr", "wpb", "mss", "msd",
    "lmcm", "cpi", "mlst", "htex", "spkg", "e2d", "sngw", "atk", "hit",
    "cmd", "eft", "mtg", "rvt", "tde", "nmr", "sbc", "arc",
]


def _build_ext_table() -> dict[int, str]:
    table: dict[int, str] = {}
    for name in EXT_NAMES:
        h = jamcrc(name.encode("ascii"))
        table[h] = name
        table[h & 0x7FFFFFFF] = name
    return table


EXT_BY_HASH = _build_ext_table()


def ext_for_hash(h: int) -> str:
    """Real extension if known, otherwise a hex placeholder that survives a repack."""
    return EXT_BY_HASH.get(h) or EXT_BY_HASH.get(h & 0x7FFFFFFF) or f"{h:08x}"


def hash_for_ext(ext: str) -> int:
    ext = ext.lstrip(".").lower()
    for h, name in EXT_BY_HASH.items():
        if name == ext:
            return h
    if len(ext) == 8:
        try:
            return int(ext, 16)
        except ValueError:
            pass
    return jamcrc(ext.encode("ascii"))


@dataclass
class ArcEntry:
    path: str            # internal path, no extension, forward or back slashes
    ext_hash: int
    csize: int
    dsize: int
    flags: int
    offset: int
    data: bytes = b""    # decompressed payload, filled by read_arc

    @property
    def ext(self) -> str:
        return ext_for_hash(self.ext_hash)

    @property
    def filename(self) -> str:
        return f"{self.path}.{self.ext}"


@dataclass
class Arc:
    version: int
    entries: list[ArcEntry] = field(default_factory=list)
    path_len: int = 64
    header_pad: int = 0      # bytes of padding after file_count
    source: Path | None = None

    @property
    def entry_size(self) -> int:
        return self.path_len + 16


def _plausible(raw: bytes, count: int, path_len: int, pad: int) -> bool:
    """Check whether a candidate layout produces sane offsets and sizes."""
    entry_size = path_len + 16
    start = 8 + pad
    if start + count * entry_size > len(raw):
        return False
    for i in range(min(count, 8)):
        off = start + i * entry_size
        blob = raw[off:off + entry_size]
        name = blob[:path_len]
        if b"\x00" not in name:
            return False
        text = name.split(b"\x00", 1)[0]
        if text and not all(32 <= b < 127 for b in text):
            return False
        _h, csize, dsize_flags, data_off = struct.unpack_from("<IIII", blob, path_len)
        if data_off < start or data_off > len(raw):
            return False
        if csize > len(raw) or (dsize_flags & SIZE_MASK) > 0x10000000:
            return False
    return True


def read_arc(path: str | Path) -> Arc:
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] != MAGIC:
        raise ValueError(f"{path.name}: not an ARC (magic was {raw[:4]!r})")

    version, count = struct.unpack_from("<HH", raw, 4)

    layout = None
    for pad in (0, 4):
        for path_len in (64, 128):
            if _plausible(raw, count, path_len, pad):
                layout = (pad, path_len)
                break
        if layout:
            break
    if layout is None:
        raise ValueError(
            f"{path.name}: could not work out the entry layout. "
            f"version={version} count={count}. Dump it in 010 and extend read_arc."
        )
    pad, path_len = layout

    arc = Arc(version=version, path_len=path_len, header_pad=pad, source=path)
    entry_size = path_len + 16
    base = 8 + pad

    for i in range(count):
        off = base + i * entry_size
        blob = raw[off:off + entry_size]
        name = blob[:path_len].split(b"\x00", 1)[0].decode("ascii", "replace")
        ext_hash, csize, dsize_flags, data_off = struct.unpack_from("<IIII", blob, path_len)
        payload = raw[data_off:data_off + csize]
        try:
            data = zlib.decompress(payload)
        except zlib.error:
            data = payload
        arc.entries.append(ArcEntry(
            path=name,
            ext_hash=ext_hash,
            csize=csize,
            dsize=dsize_flags & SIZE_MASK,
            flags=dsize_flags & FLAG_MASK,
            offset=data_off,
            data=data,
        ))
    return arc


def write_arc(arc: Arc, out_path: str | Path, compress: bool = True) -> Path:
    out_path = Path(out_path)
    entry_size = arc.entry_size
    base = 8 + arc.header_pad
    data_start = base + len(arc.entries) * entry_size

    blobs: list[bytes] = []
    cursor = data_start
    header = bytearray()
    header += MAGIC
    header += struct.pack("<HH", arc.version, len(arc.entries))
    header += b"\x00" * arc.header_pad

    for e in arc.entries:
        payload = zlib.compress(e.data, 9) if compress else e.data
        name = e.path.encode("ascii")
        if len(name) >= arc.path_len:
            raise ValueError(
                f"internal path too long for this archive: {e.path!r} "
                f"is {len(name)} bytes, the field holds {arc.path_len - 1}"
            )
        header += name.ljust(arc.path_len, b"\x00")
        header += struct.pack(
            "<IIII", e.ext_hash, len(payload), (len(e.data) & SIZE_MASK) | e.flags, cursor
        )
        blobs.append(payload)
        cursor += len(payload)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as fh:
        fh.write(header)
        for b in blobs:
            fh.write(b)
    return out_path


def unpack(arc: Arc, out_dir: str | Path) -> list[Path]:
    """Write every entry to disk, preserving internal directory structure."""
    out_dir = Path(out_dir)
    written = []
    for e in arc.entries:
        rel = Path(e.filename.replace("\\", "/"))
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(e.data)
        written.append(dest)
    return written


def verify_roundtrip(src: str | Path) -> tuple[bool, str]:
    """
    Read an archive, write it back, read that. Compare entry tables and payloads.

    Byte-identical output is not expected, zlib settings differ. What matters is
    that every path, hash and payload survives the trip. Run this on a handful of
    untouched archives before letting the tool near a real install.
    """
    src = Path(src)
    a = read_arc(src)
    tmp = src.with_suffix(".roundtrip.arc")
    try:
        write_arc(a, tmp)
        b = read_arc(tmp)
    finally:
        pass

    if len(a.entries) != len(b.entries):
        return False, f"entry count changed: {len(a.entries)} -> {len(b.entries)}"
    for x, y in zip(a.entries, b.entries):
        if x.path != y.path:
            return False, f"path changed: {x.path!r} -> {y.path!r}"
        if x.ext_hash != y.ext_hash:
            return False, f"{x.path}: ext hash changed"
        if x.data != y.data:
            return False, f"{x.path}: payload changed ({len(x.data)} -> {len(y.data)} bytes)"
    tmp.unlink(missing_ok=True)
    return True, f"{len(a.entries)} entries survived the round trip"

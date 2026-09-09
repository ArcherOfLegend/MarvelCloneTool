from __future__ import annotations
 
import struct
from dataclasses import dataclass, field
from pathlib import Path
 
MAGIC = b"STQR"
HEADER_SIZE = 0x50
STREAM_SIZE = 0x28
EVENT_SIZE = 0x98
EVENT_STREAM_FIELD = 0x5C
 
# Templates for appended entries, from Gneiss's BGM tool.
STREAM_TEMPLATE = bytes.fromhex(
    "0000000000000000" "0000000000000000"
    "0600000080BB0000" "0000000000000000"
    "CD515D2503000000"
)
EVENT_TEMPLATE = bytes.fromhex(
    "0000000001000000" "0A00000001000000"
    "0000000000000000" "0001000001000000"
    "FFFFFFFF000080C0" "0000C0C200000000"
    "0000000000000000" "FFFFFFFFFFFFFFFF"
    "FFFFFFFF00000000" "E803000000000000"
    "0000000000000000" "0000000000000000"
    "010000000000C0C2" "64000000FFFFFFFF"
    "FFFFFFFF00000000" "0000000000000000"
    "FFFFFFFF00000000" "0000000000000000"
    "0000000000000000"
)
 
 
@dataclass
class Stqr:
    header: bytes
    streams: list[bytes] = field(default_factory=list)
    events: list[bytes] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    # Real tables pad between the event block and the first string.
    string_pad: int = 0
    tail: bytes = b""
 
    def add(self, path: str) -> int:
        index = len(self.streams)
        self.streams.append(STREAM_TEMPLATE)
        event = bytearray(EVENT_TEMPLATE)
        struct.pack_into("<I", event, 0x00, len(self.events))
        struct.pack_into("<I", event, EVENT_STREAM_FIELD, index)
        self.events.append(bytes(event))
        self.paths.append(path)
        return index
 
    def set_path(self, index: int, path: str,
                 filler: str = "sound\\bgm\\source\\bgm_cr_000") -> int:
        """
        Put a path at a fixed stream index, growing the table if needed.

        The engine maps streams to characters by position, so a track has to
        land on its own index rather than at the end. Any characters in between
        get `filler` so the numbering still lines up. Returns how many of those
        placeholders were needed.
        """
        gaps = max(0, index - len(self.streams))
        while len(self.streams) <= index:
            self.add(filler)
        self.paths[index] = path
        return gaps

    def build(self) -> bytes:
        stream_start = HEADER_SIZE
        event_start = stream_start + STREAM_SIZE * len(self.streams)
        string_start = event_start + EVENT_SIZE * len(self.events)
 
        header = bytearray(self.header[:HEADER_SIZE].ljust(HEADER_SIZE, b"\x00"))
        struct.pack_into("<II", header, 0x08, len(self.streams), len(self.events))
        struct.pack_into("<qq", header, 0x20, stream_start, event_start)
 
        string_start += self.string_pad
 
        blob = bytearray()
        offsets = []
        for path in self.paths:
            offsets.append(string_start + len(blob))
            blob += path.encode("ascii") + b"\x00"
 
        streams = bytearray()
        for record, offset in zip(self.streams, offsets):
            fixed = bytearray(record[:STREAM_SIZE].ljust(STREAM_SIZE, b"\x00"))
            struct.pack_into("<q", fixed, 0x00, offset)
            streams += fixed
 
        events = bytearray()
        for record in self.events:
            events += record[:EVENT_SIZE].ljust(EVENT_SIZE, b"\x00")
 
        return bytes(header + streams + events
                     + b"\x00" * self.string_pad + blob + self.tail)
 
 
def parse(data: bytes) -> Stqr | None:
    if data[:4] != MAGIC or len(data) < HEADER_SIZE:
        return None
 
    stream_count, event_count = struct.unpack_from("<II", data, 0x08)
    stream_start, event_start = struct.unpack_from("<qq", data, 0x20)
    if stream_start != HEADER_SIZE:
        return None
    if event_start != stream_start + STREAM_SIZE * stream_count:
        return None
    if event_start + EVENT_SIZE * event_count > len(data):
        return None
 
    streams, paths = [], []
    for i in range(stream_count):
        offset = stream_start + STREAM_SIZE * i
        record = data[offset:offset + STREAM_SIZE]
        pointer = struct.unpack_from("<q", record, 0x00)[0]
        if not 0 < pointer < len(data):
            return None
        end = data.find(b"\x00", pointer)
        if end < 0:
            return None
        streams.append(record)
        paths.append(data[pointer:end].decode("ascii", "replace"))
 
    events = [
        data[event_start + EVENT_SIZE * i:event_start + EVENT_SIZE * (i + 1)]
        for i in range(event_count)
    ]
    string_block = event_start + EVENT_SIZE * event_count
    first = min((struct.unpack_from("<q", r, 0x00)[0] for r in streams),
                default=string_block)
    pad = max(0, first - string_block)
 
    last_end = max(
        (data.find(b"\x00", struct.unpack_from("<q", r, 0x00)[0]) + 1 for r in streams),
        default=string_block + pad)
    tail = data[last_end:]
 
    return Stqr(data[:HEADER_SIZE], streams, events, paths, pad, tail)
 
 
def add_streams(src: Path, entries: list[str], dest: Path) -> tuple[int, int]:
    """Returns (entries added, total streams)."""
    src, dest = Path(src), Path(dest)
    table = parse(src.read_bytes())
    if table is None:
        raise ValueError(f"{src.name} is not a stream table this tool understands")
 
    added = 0
    for entry in entries:
        entry = entry.strip()
        if entry:
            table.add(entry)
            added += 1
 
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(table.build())
    return added, len(table.streams)
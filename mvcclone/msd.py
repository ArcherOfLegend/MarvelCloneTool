from __future__ import annotations
 
import struct
from dataclasses import dataclass, field
from pathlib import Path
 
MAGIC = b"MSD\x00"
HEADER_SIZE = 0x0C
TERMINATOR = 0xFFFF
LINE_BREAK = 0xFFFE
SHIFT = 0x20
 
 
def decode(units: list[int]) -> str:
    out = []
    for unit in units:
        if unit == LINE_BREAK:
            out.append("\n")
        else:
            out.append(chr(unit + SHIFT))
    return "".join(out)
 
 
def encode(text: str) -> list[int]:
    units = []
    for ch in text:
        if ch == "\n":
            units.append(LINE_BREAK)
            continue
        units.append(ord(ch) - SHIFT)
    return units
 
 
@dataclass
class Msd:
    version: int
    messages: list[str] = field(default_factory=list)
    leading_empty: bool = True
 
    def add(self, text: str) -> int:
        self.messages.append(text)
        return len(self.messages) - 1 + (1 if self.leading_empty else 0)
 
    def build(self) -> bytes:
        out = bytearray(MAGIC)
        out += struct.pack("<II", len(self.messages) + (1 if self.leading_empty else 0),
                           self.version)
        if self.leading_empty:
            out += struct.pack("<H", TERMINATOR)
        for text in self.messages:
            units = encode(text) + [TERMINATOR]
            out += struct.pack("<I", len(units))
            out += struct.pack(f"<{len(units)}H", *units)
        return bytes(out)
 
 
def parse(data: bytes) -> Msd | None:
    if data[:4] != MAGIC or len(data) < HEADER_SIZE:
        return None
 
    count, version = struct.unpack_from("<II", data, 4)
    pos = HEADER_SIZE
    leading = data[pos:pos + 2] == struct.pack("<H", TERMINATOR)
    if leading:
        pos += 2
 
    messages = []
    while len(messages) < count - (1 if leading else 0):
        if pos + 4 > len(data):
            return None
        n = struct.unpack_from("<I", data, pos)[0]
        if n == 0 or pos + 4 + n * 2 > len(data):
            return None
        units = list(struct.unpack_from(f"<{n}H", data, pos + 4))
        messages.append(decode([u for u in units if u != TERMINATOR]))
        pos += 4 + n * 2
 
    if pos != len(data):
        return None
    return Msd(version, messages, leading)
 
 
def add_messages(src: Path, entries: list[str], dest: Path) -> tuple[int, list[int]]:
    src, dest = Path(src), Path(dest)
    table = parse(src.read_bytes())
    if table is None:
        raise ValueError(f"{src.name} is not a message table this tool reads")
 
    indices = [table.add(text) for text in entries if text.strip()]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(table.build())
    return len(indices), indices

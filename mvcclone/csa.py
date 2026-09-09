from __future__ import annotations
 
import struct
from dataclasses import dataclass, field
from pathlib import Path
 
MAGIC = b"CSA\x00"
HEADER_SIZE = 8
ASSIST_SIZE = 16
SLOT_SIZE = ASSIST_SIZE * 3
 
TYPES = {"direct": 0, "shot": 1, "extra": 2}
DIRECTIONS = {"front": 0, "upward": 1, "tiltup": 2, "tiltdw": 3, "instant": 4}
 
 
@dataclass
class Assist:
    name1: int = 0
    name2: int = 0
    type: int = 0
    direction: int = 0
 
    def pack(self) -> bytes:
        return struct.pack("<IIII", self.name1, self.name2, self.type, self.direction)
 
 
@dataclass
class Csa:
    slots: list[list[Assist]] = field(default_factory=list)
 
    def first_free_slot(self) -> int:
        for index, slot in enumerate(self.slots):
            if not any(a.name1 for a in slot):
                return index
        return len(self.slots)
 
    def set_slot(self, index: int, assists: list[Assist]):
        padded = (assists + [Assist(), Assist(), Assist()])[:3]
        while len(self.slots) <= index:
            self.slots.append([Assist(), Assist(), Assist()])
        self.slots[index] = padded
 
    def build(self) -> bytes:
        out = bytearray(MAGIC)
        out += struct.pack("<I", len(self.slots))
        for slot in self.slots:
            for assist in slot:
                out += assist.pack()
        return bytes(out)
 
 
def parse(data: bytes) -> Csa | None:
    if data[:4] != MAGIC or len(data) < HEADER_SIZE:
        return None
    count = struct.unpack_from("<I", data, 4)[0]
    if HEADER_SIZE + SLOT_SIZE * count != len(data):
        return None
 
    slots = []
    for i in range(count):
        base = HEADER_SIZE + SLOT_SIZE * i
        slots.append([
            Assist(*struct.unpack_from("<IIII", data, base + ASSIST_SIZE * j))
            for j in range(3)
        ])
    return Csa(slots)
 
 
def lookup_type(name: str) -> int:
    return TYPES.get(name.strip().lower().replace(" ", ""), 0)
 
 
def lookup_direction(name: str) -> int:
    return DIRECTIONS.get(name.strip().lower().replace(" ", ""), 0)
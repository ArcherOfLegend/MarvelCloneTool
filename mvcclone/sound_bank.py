from __future__ import annotations

import re
from dataclasses import dataclass

BANK_MAGICS = (b"SBKR", b"SRQR", b"STQR")
COUNT_OFFSET = 0x0C
PATH_RE = re.compile(rb"sound[\\/][ -~]+")


@dataclass
class Bank:
    magic: bytes
    header: bytes
    records: list[tuple[str, bytes]]   # (path, fixed payload)
    trailer: bytes
    payload_width: int

    def rebuild(self) -> bytes:
        out = bytearray(self.header)
        for path, payload in self.records:
            out += path.encode("ascii") + b"\x00" + payload
        out += self.trailer
        return bytes(out)


def is_bank(data: bytes) -> bool:
    return data[:4] in BANK_MAGICS


def parse(data: bytes) -> Bank | None:
    if not is_bank(data) or len(data) < COUNT_OFFSET + 4:
        return None

    runs = [(m.start(), m.end()) for m in PATH_RE.finditer(data)]
    if len(runs) < 2:
        return None

    widths = {
        runs[i + 1][0] - runs[i][1] - 1
        for i in range(len(runs) - 1)
    }
    if len(widths) != 1:
        return None
    width = widths.pop()
    if width < 0 or width > 4096:
        return None

    header = data[:runs[0][0]]
    records = []
    for start, end in runs:
        path = data[start:end].decode("ascii", "replace")
        payload = data[end + 1:end + 1 + width]
        if len(payload) != width:
            return None
        records.append((path, payload))

    trailer = data[runs[-1][1] + 1 + width:]
    bank = Bank(data[:4], header, records, trailer, width)

    # Only trust it if it round trips byte for byte.
    if bank.rebuild() != data:
        return None
    return bank


def rename(data: bytes, replacements: list[tuple[str, str]]) -> tuple[bytes, int] | None:
    bank = parse(data)
    if bank is None:
        return None

    changed = 0
    rebuilt = []
    for path, payload in bank.records:
        new_path = path
        for old, new in replacements:
            new_path = new_path.replace(old, new)
        if new_path != path:
            changed += 1
        rebuilt.append((new_path, payload))

    bank.records = rebuilt
    return bank.rebuild(), changed

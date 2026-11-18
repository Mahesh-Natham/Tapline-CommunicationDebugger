"""Checksum algorithms and a finder that tries them against captured frames."""
from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from typing import Callable

from .model import Frame


def _crc8(poly: int, init: int = 0, refin: bool = False) -> Callable[[bytes], int]:
    def f(data: bytes) -> int:
        crc = init
        for b in data:
            if refin:
                crc ^= b
                for _ in range(8):
                    crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
            else:
                crc ^= b
                for _ in range(8):
                    crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        return crc
    return f


def _crc16(poly: int, init: int, reflected: bool) -> Callable[[bytes], int]:
    def f(data: bytes) -> int:
        crc = init
        for b in data:
            if reflected:
                crc ^= b
                for _ in range(8):
                    crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
            else:
                crc ^= b << 8
                for _ in range(8):
                    crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return crc
    return f


ALGORITHMS: dict[str, Callable[[bytes], int]] = {
    "sum8": lambda d: sum(d) & 0xFF,
    "xor8": lambda d: reduce(lambda a, b: a ^ b, d, 0),
    "twos8": lambda d: (-sum(d)) & 0xFF,
    "crc8": _crc8(0x07),
    "crc8_maxim": _crc8(0x8C, 0, refin=True),
    "crc16_modbus": _crc16(0xA001, 0xFFFF, True),
    "crc16_ccitt_false": _crc16(0x1021, 0xFFFF, False),
    "crc16_xmodem": _crc16(0x1021, 0x0000, False),
}


def width(name: str) -> int:
    return 2 if name.startswith("crc16") else 1


@dataclass(frozen=True)
class ChecksumSpec:
    algo: str
    start: int
    end_from_end: int
    pos_from_end: int
    byteorder: str = "little"

    def to_json(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, d: dict | None) -> "ChecksumSpec | None":
        if not d:
            return None
        if d["algo"] not in ALGORITHMS:
            raise ValueError(f"Unknown checksum algorithm {d['algo']!r}")
        return cls(d["algo"], int(d["start"]), int(d["end_from_end"]), int(d["pos_from_end"]),
                   d.get("byteorder", "little"))


def _compute(spec: ChecksumSpec, data: bytes) -> bytes | None:
    end = len(data) - spec.end_from_end
    pos = len(data) - spec.pos_from_end
    w = width(spec.algo)
    if spec.start >= end or pos < 0 or pos + w > len(data):
        return None
    return ALGORITHMS[spec.algo](data[spec.start:end]).to_bytes(w, spec.byteorder)


def check(spec: ChecksumSpec, data: bytes) -> bool:
    c = _compute(spec, data)
    pos = len(data) - spec.pos_from_end
    return c is not None and data[pos:pos + len(c)] == c


def apply_checksum(spec: ChecksumSpec, data: bytes) -> bytes:
    c = _compute(spec, data)
    if c is None:
        return data
    pos = len(data) - spec.pos_from_end
    return data[:pos] + c + data[pos + len(c):]


def find_checksums(frames: list[Frame], min_ratio: float = 0.95) -> list[tuple[ChecksumSpec, float]]:
    data = [f.data for f in frames if len(f.data) >= 3 and not f.flags]
    if len(data) < 3:
        return []
    found = []
    for algo in ALGORITHMS:
        w = width(algo)
        orders = ("little", "big") if w == 2 else ("little",)
        for start in range(0, 5):
            for bo in orders:
                spec = ChecksumSpec(algo, start, w, w, bo)
                usable = [d for d in data if len(d) - w > start]
                if len(usable) < 3:
                    continue
                ok = sum(check(spec, d) for d in usable)
                ratio = ok / len(usable)
                if ratio >= min_ratio:
                    found.append((spec, round(ratio, 4)))
    found.sort(key=lambda x: (-x[1], width(x[0].algo), x[0].start))
    return found

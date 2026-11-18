"""Find a value the user saw on the display inside the captured frames."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .model import Frame, Marker

MAX_HITS = 500
_NUM = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)(.*)$")


@dataclass
class Query:
    numbers: list[float]
    text: str | None


@dataclass
class Hit:
    frame_id: int
    offset: int
    length: int
    encoding: str
    score: float

    def to_json(self) -> dict:
        return self.__dict__.copy()


def parse_query(q: str) -> Query:
    s = (q or "").strip()
    if not s:
        return Query([], None)
    if s.lower().startswith("0x"):
        try:
            return Query([int(s[2:], 16)], None)
        except ValueError:
            return Query([], s)
    m = _NUM.match(s)
    if m and not re.search(r"\d", m.group(2)):
        try:
            v = float(m.group(1).replace(",", "."))
        except ValueError:
            return Query([], s)
        if math.isfinite(v):
            return Query([int(v) if v.is_integer() else v], None)
    return Query([], s)


def _bcd(v: int) -> bytes | None:
    if v < 0 or v > 9999:
        return None
    digits = f"{v:0{2 if v < 100 else 4}d}"
    return bytes(int(digits[i:i + 2], 16) for i in range(0, len(digits), 2))


def _patterns(n: float) -> list[tuple[str, bytes, float]]:
    """(encoding name, bytes, weight) for a number."""
    out: list[tuple[str, bytes, float]] = []
    for scale, prefix in ((1, ""), (10, "x10:"), (100, "x100:")):
        v = n * scale
        if abs(v - round(v)) > 1e-6:
            continue
        v = int(round(v))
        w = 1.0 if scale == 1 else 0.8
        if 0 <= v <= 255:
            out.append((prefix + "u8", bytes([v]), w * 0.5))
        if -128 <= v < 0:
            out.append((prefix + "i8", v.to_bytes(1, "little", signed=True), w * 0.5))
        if 0 <= v <= 0xFFFF:
            out.append((prefix + "u16le", v.to_bytes(2, "little"), w))
            if v > 255:
                out.append((prefix + "u16be", v.to_bytes(2, "big"), w))
        if -32768 <= v <= 32767:
            out.append((prefix + "i16le", v.to_bytes(2, "little", signed=True), w))
            out.append((prefix + "i16be", v.to_bytes(2, "big", signed=True), w))
        if v > 0xFFFF and v <= 0xFFFFFFFF:
            out.append((prefix + "u32le", v.to_bytes(4, "little"), w))
            out.append((prefix + "u32be", v.to_bytes(4, "big"), w))
        if v < -32768 and v >= -2**31:
            out.append((prefix + "i32le", v.to_bytes(4, "little", signed=True), w))
        b = _bcd(v)
        if b is not None and v >= 10:  # single-digit BCD equals u8, skip
            out.append((prefix + "bcd", b, w * 0.9))
    # textual forms
    txt = {f"{n:g}", str(n)}
    for t in txt:
        out.append(("ascii", t.encode(), 1.0))
    return out


def _find_all(hay: bytes, needle: bytes, ci: bool = False):
    if ci:
        hay, needle = hay.lower(), needle.lower()
    i = hay.find(needle)
    while i >= 0:
        yield i
        i = hay.find(needle, i + 1)


def find_value(frames: list[Frame], q: str, markers: list[Marker] | None = None,
               window_s: float = 2.0) -> list[Hit]:
    query = parse_query(q)
    pats: list[tuple[str, bytes, float, bool]] = []
    for n in query.numbers:
        pats += [(e, b, w, False) for e, b, w in _patterns(n)]
    if query.text:
        try:
            pats.append(("ascii", query.text.encode("ascii"), 1.0, True))
        except UnicodeEncodeError:
            pass
    seen = set()
    pats = [p for p in pats if not (p[:2] in seen or seen.add(p[:2]))]
    mts = [m.t for m in (markers or [])]
    hits: list[Hit] = []
    for f in frames:
        near = any(abs(f.t_start - t) <= window_s for t in mts)
        for enc, pat, w, ci in pats:
            for off in _find_all(f.data, pat, ci):
                hits.append(Hit(f.id, off, len(pat), enc, round(w * (1.5 if near else 1.0), 3)))
    hits.sort(key=lambda h: (-h.score, h.frame_id))
    return hits[:MAX_HITS]


def type_key(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data[:4])


def near_marker(frames: list[Frame], marker: Marker, window_s: float = 2.0) -> dict:
    """Frames around a marker that are new kinds, or changed compared with the previous frame of the same kind."""
    last: dict[str, bytes] = {}
    new_types: list[str] = []
    changed: list[int] = []
    for f in sorted(frames, key=lambda f: f.t_start):
        k = (f.channel, type_key(f.data))
        inside = abs(f.t_start - marker.t) <= window_s
        if inside:
            if k not in last:
                if k[1] not in new_types:
                    new_types.append(k[1])
            elif last[k] != f.data:
                changed.append(f.id)
        last[k] = f.data
    return {"new_types": new_types, "changed": changed}

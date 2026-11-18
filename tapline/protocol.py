"""The user's growing understanding of the protocol: frame types, fields, checksum, verdicts."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .checksums import ChecksumSpec, apply_checksum, width
from .framing import LengthRule, rule_from_json

INT_TYPES = {"u8": (1, "little", False), "i8": (1, "little", True),
             "u16le": (2, "little", False), "u16be": (2, "big", False),
             "i16le": (2, "little", True), "i16be": (2, "big", True),
             "u32le": (4, "little", False), "u32be": (4, "big", False),
             "i32le": (4, "little", True), "i32be": (4, "big", True)}


@dataclass
class Field:
    name: str
    offset: int
    type: str
    scale: float = 1.0
    unit: str = ""

    def size(self) -> int:
        if self.type in INT_TYPES:
            return INT_TYPES[self.type][0]
        kind, _, n = self.type.partition(":")
        if kind in ("ascii", "bcd") and n.isdigit() and int(n) > 0:
            return int(n)
        raise ValueError(f"Unknown field type {self.type!r}")


@dataclass
class FrameType:
    name: str
    match: str
    fields: list[Field] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    channel: str = ""  # only messages on this channel (e.g. "CAN 0x100", "I²C 0x48 read")
    _pat: tuple | None = field(default=None, repr=False, compare=False)

    def pattern(self) -> list[int | None]:
        if self._pat is None or self._pat[0] != self.match:
            self._pat = (self.match, [None if t == "??" else int(t, 16) for t in self.match.split()])
        return self._pat[1]


@dataclass
class Protocol:
    framing: dict = field(default_factory=lambda: {"kind": "gap", "gap_ms": 5})
    checksum: ChecksumSpec | None = None
    frame_types: list[FrameType] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "framing": self.framing,
            "checksum": self.checksum.to_json() if self.checksum else None,
            "frame_types": [{"name": t.name, "match": t.match, "channel": t.channel,
                             "fields": [f.__dict__ for f in t.fields],
                             "verdicts": t.verdicts} for t in self.frame_types],
        }

    @classmethod
    def from_json(cls, d: dict) -> "Protocol":
        types = []
        for t in d.get("frame_types", []):
            ft = FrameType(t["name"], t.get("match", ""), [Field(**f) for f in t.get("fields", [])],
                           list(t.get("verdicts", [])), str(t.get("channel") or ""))
            ft.pattern()
            for f in ft.fields:
                f.size()
            types.append(ft)
        framing = d.get("framing") or {"kind": "gap", "gap_ms": 5}
        rule_from_json(framing)  # validate
        return cls(framing, ChecksumSpec.from_json(d.get("checksum")), types)


def load(path) -> Protocol:
    p = Path(path)
    if not p.exists():
        return Protocol()
    return Protocol.from_json(json.loads(p.read_text(encoding="utf-8")))


def save(proto: Protocol, path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(proto.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def get_type(proto: Protocol, name: str) -> FrameType:
    for t in proto.frame_types:
        if t.name == name:
            return t
    raise KeyError(f"No frame type named {name!r}")


def match_type(proto: Protocol, data: bytes, channel: str | None = None) -> FrameType | None:
    for t in proto.frame_types:
        if t.channel and channel is not None and t.channel != channel.removeprefix("sent "):
            continue
        if t.channel and channel is None:
            continue
        pat = t.pattern()
        if len(data) >= len(pat) and all(p is None or data[i] == p for i, p in enumerate(pat)):
            return t
    return None


def _read(f: Field, data: bytes):
    n = f.size()
    raw = data[f.offset:f.offset + n]
    if len(raw) < n:
        return None
    if f.type in INT_TYPES:
        _, order, signed = INT_TYPES[f.type]
        v = int.from_bytes(raw, order, signed=signed)
        out = v * f.scale
        return round(out, 6) if f.scale != 1 else v
    kind = f.type.split(":")[0]
    if kind == "ascii":
        return raw.decode("ascii", "replace").rstrip(" \x00")
    try:
        return int(raw.hex())
    except ValueError:
        return None


def decode(proto: Protocol, data: bytes, ftype: FrameType | None = None) -> dict:
    t = ftype or match_type(proto, data)
    if not t:
        return {}
    return {f.name: _read(f, data) for f in t.fields}


def _write(f: Field, buf: bytearray, value) -> None:
    n = f.size()
    if f.type in INT_TYPES:
        _, order, signed = INT_TYPES[f.type]
        raw_v = int(round(float(value) / f.scale))
        try:
            raw = raw_v.to_bytes(n, order, signed=signed)
        except OverflowError:
            raise ValueError(f"{f.name} = {value} does not fit in {f.type}")
    elif f.type.startswith("ascii"):
        raw = str(value).encode("ascii", "replace")[:n].ljust(n, b" ")
    else:
        digits = str(int(value)).rjust(n * 2, "0")
        if len(digits) > n * 2:
            raise ValueError(f"{f.name} = {value} does not fit in {f.type}")
        raw = bytes.fromhex(digits)
    buf[f.offset:f.offset + n] = raw


def compose(proto: Protocol, type_name: str, values: dict, base: bytes | None = None) -> bytes:
    t = get_type(proto, type_name)
    if base is not None:
        buf = bytearray(base)
    else:
        size = len(t.pattern())
        for f in t.fields:
            size = max(size, f.offset + f.size())
        if proto.checksum:
            size += width(proto.checksum.algo)
        buf = bytearray(size)
        for i, p in enumerate(t.pattern()):
            if p is not None:
                buf[i] = p
    for f in t.fields:
        if f.name in values and values[f.name] not in (None, ""):
            if f.offset + f.size() > len(buf):
                buf.extend(bytes(f.offset + f.size() - len(buf)))
            _write(f, buf, values[f.name])
    return finalize(proto, bytes(buf))


def finalize(proto: Protocol, data: bytes) -> bytes:
    """Fix the length byte (length framing) and the checksum, as the protocol defines them."""
    buf = bytearray(data)
    rule = rule_from_json(proto.framing)
    if isinstance(rule, LengthRule) and len(buf) >= rule.len_offset + rule.len_size:
        v = len(buf) - rule.len_offset - rule.len_size - rule.len_adjust
        if 0 <= v < 256 ** rule.len_size:
            buf[rule.len_offset:rule.len_offset + rule.len_size] = v.to_bytes(rule.len_size, "little")
    out = bytes(buf)
    if proto.checksum:
        out = apply_checksum(proto.checksum, out)
    return out


def add_verdict(proto: Protocol, type_name: str, sent_hex: str, ok: bool, note: str = "") -> None:
    get_type(proto, type_name).verdicts.append(
        {"t": time.time(), "sent": sent_hex, "ok": bool(ok), "note": note})

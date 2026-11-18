"""Core data types shared by every part of Tapline."""
from __future__ import annotations

from dataclasses import dataclass, field

CTRL_TO_DISP = "ctrl→disp"
DISP_TO_CTRL = "disp→ctrl"
SENT = "sent"
BUS = "bus"          # raw RS-485 line (both directions on one pair)
MASTER = "master→slave"
SLAVE = "slave→master"


@dataclass
class Chunk:
    """Raw bytes as they arrived from a source."""

    t: float
    channel: str
    data: bytes

    def to_json(self) -> dict:
        return {"t": self.t, "channel": self.channel, "data": self.data.hex()}

    @classmethod
    def from_json(cls, d: dict) -> "Chunk":
        return cls(float(d["t"]), d["channel"], bytes.fromhex(d["data"]))


@dataclass
class Frame:
    """One message cut out of the chunk stream by a framing rule."""

    id: int
    t_start: float
    t_end: float
    channel: str
    data: bytes
    flags: set[str] = field(default_factory=set)

    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "t_start": self.t_start,
            "t_end": self.t_end,
            "channel": self.channel,
            "hex": self.hex(),
            "flags": sorted(self.flags),
        }


@dataclass
class Marker:
    """An event noted by the user, anchored to a timestamp."""

    t: float
    label: str
    note: str = ""
    expected: str | None = None

    def to_json(self) -> dict:
        return {"t": self.t, "label": self.label, "note": self.note, "expected": self.expected}

    @classmethod
    def from_json(cls, d: dict) -> "Marker":
        return cls(float(d["t"]), d["label"], d.get("note", ""), d.get("expected"))


@dataclass
class SerialConfig:
    port: str
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1

    def label(self) -> str:
        sb = int(self.stopbits) if float(self.stopbits).is_integer() else self.stopbits
        return f"{self.baud} {self.bytesize}{self.parity}{sb}"

    def to_json(self) -> dict:
        return {"port": self.port, "baud": self.baud, "bytesize": self.bytesize,
                "parity": self.parity, "stopbits": self.stopbits}

    @classmethod
    def from_json(cls, d: dict) -> "SerialConfig":
        return cls(d.get("port", ""), int(d.get("baud", 115200)), int(d.get("bytesize", 8)),
                   str(d.get("parity", "N")).upper()[:1], float(d.get("stopbits", 1)))

    @classmethod
    def from_format(cls, port: str, baud: int, fmt: str) -> "SerialConfig":
        """fmt like '8N1' or '7E1' or '8N2'."""
        return cls(port, baud, int(fmt[0]), fmt[1].upper(), float(fmt[2:]))

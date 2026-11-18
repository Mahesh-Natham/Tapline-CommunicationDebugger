"""CAN bus: real adapters through python-can, plus a pretend car for the demo."""
from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import AsyncIterator

from ..model import Chunk
from .base import Source, SourceError

INTERFACES = [("slcan", "CANable / slcan (serial port, e.g. COM6 or /dev/ttyACM0)"),
              ("gs_usb", "candleLight / gs_usb"),
              ("pcan", "PEAK PCAN-USB (channel PCAN_USBBUS1)"),
              ("socketcan", "Linux SocketCAN (channel can0)"),
              ("kvaser", "Kvaser"), ("vector", "Vector"), ("ixxat", "IXXAT"),
              ("virtual", "Virtual (testing)")]
BITRATES = [10000, 20000, 50000, 83333, 100000, 125000, 250000, 500000, 800000, 1000000]


def available() -> bool:
    try:
        import can  # noqa: F401
        return True
    except ImportError:
        return False


def can_channel(arb_id: int, extended: bool) -> str:
    return f"CAN 0x{arb_id:08X}" if extended else f"CAN 0x{arb_id:03X}"


def channel_to_id(channel: str) -> tuple[int, bool] | None:
    m = re.search(r"CAN 0x([0-9A-F]+)$", channel or "")
    if not m:
        return None
    return int(m.group(1), 16), len(m.group(1)) > 3


def parse_can_text(s: str) -> tuple[int, bytes, bool]:
    """'123#DEADBEEF', '123#DE AD', '0x123 01 02', '18FF50E5#01' -> (id, data, extended)."""
    s = (s or "").strip()
    if "#" in s:
        ident, _, data = s.partition("#")
    else:
        parts = s.split(None, 1)
        ident, data = (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")
    ident = ident.strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{1,8}", ident):
        raise ValueError("Write CAN messages as ID#DATA, e.g. 123#DEADBEEF (ID in hex)")
    arb = int(ident, 16)
    extended = len(ident) > 3 or arb > 0x7FF
    if arb > 0x1FFFFFFF:
        raise ValueError("CAN IDs go up to 1FFFFFFF")
    clean = re.sub(r"[\s,]|0x", "", data, flags=re.I)
    if clean.upper() == "R":
        raise ValueError("Remote frames are not supported")
    if len(clean) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", clean):
        raise ValueError("CAN data must be pairs of hex digits, e.g. 123#01A2")
    payload = bytes.fromhex(clean)
    if len(payload) > 8:
        raise ValueError("Classic CAN carries at most 8 data bytes")
    return arb, payload, extended


def _friendly(e: Exception, interface: str, channel: str) -> str:
    msg = str(e) or e.__class__.__name__
    if "not found" in msg.lower() or "no such" in msg.lower() or "could not" in msg.lower():
        return f"CAN adapter not found on {channel or interface}: {msg}"
    return f"CAN ({interface} {channel}): {msg}"


class CanSource(Source):
    name = "CAN"

    def __init__(self, interface: str, channel: str, bitrate: int = 500000):
        self.interface, self.channel, self.bitrate = interface, channel, int(bitrate)
        self.error = None
        self._bus = None
        self._stop = threading.Event()
        self._thread = None

    async def start(self) -> None:
        if not available():
            raise SourceError("python-can is not installed. Run: pip install python-can")
        import can
        valid = getattr(can.interfaces, "VALID_INTERFACES", None)
        if valid is not None and self.interface not in valid:
            raise SourceError(f"Unknown CAN interface {self.interface!r}")
        self._loop = asyncio.get_running_loop()
        self._q = asyncio.Queue()
        try:
            self._bus = await asyncio.to_thread(can.Bus, interface=self.interface, channel=self.channel,
                                                bitrate=self.bitrate)
        except Exception as e:  # python-can raises many different types
            raise SourceError(_friendly(e, self.interface, self.channel)) from e
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, daemon=True, name="tapline-can")
        self._thread.start()

    def _put(self, item):
        try:
            self._loop.call_soon_threadsafe(self._q.put_nowait, item)
        except RuntimeError:
            pass

    def _reader(self):
        try:
            while not self._stop.is_set():
                m = self._bus.recv(0.1)
                if m is None or m.is_error_frame or m.is_remote_frame:
                    continue
                self._put(Chunk(time.time(), can_channel(m.arbitration_id, m.is_extended_id), bytes(m.data)))
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                self.error = _friendly(e, self.interface, self.channel)
        finally:
            self._put(None)

    async def stop(self) -> None:
        self._stop.set()
        if self._thread:
            await asyncio.to_thread(self._thread.join, 1.0)
            self._thread = None
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

    async def chunks(self) -> AsyncIterator[Chunk]:
        while True:
            c = await self._q.get()
            if c is None:
                return
            yield c

    async def write_can(self, arb_id: int, data: bytes, extended: bool) -> None:
        import can
        if not self._bus:
            raise SourceError("The CAN adapter is not open")
        try:
            await asyncio.to_thread(self._bus.send, can.Message(arbitration_id=arb_id, data=data,
                                                                is_extended_id=extended), 1.0)
        except Exception as e:  # noqa: BLE001
            raise SourceError(_friendly(e, self.interface, self.channel)) from e


class SimCanSource(Source):
    """A pretend car: 0x100 engine rpm (u16 BE), 0x101 speed km/h + gear, 0x200 status bits.
    Send 0x300 with one byte (a lamp command) and it answers on 0x301 with the same byte."""
    name = "CAN simulator"

    def __init__(self, speed: float = 1.0):
        self.speed = max(0.01, float(speed))
        self.error = None
        self._q: asyncio.Queue = asyncio.Queue()
        self._task = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        t0, n = time.time(), 0
        while True:
            rpm = 800 + (n * 37) % 2400
            spd = (n // 2) % 120
            await self._q.put(Chunk(time.time(), "CAN 0x100", rpm.to_bytes(2, "big") + b"\x00\x00"))
            await self._q.put(Chunk(time.time(), "CAN 0x101", bytes([spd, 1 + spd // 25])))
            if n % 5 == 0:
                await self._q.put(Chunk(time.time(), "CAN 0x200", bytes([0b00000101 if n % 10 else 0b00000001])))
            n += 1
            delay = t0 + n * 0.1 / self.speed - time.time()
            await asyncio.sleep(max(0.0, delay))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._q.put(None)

    async def chunks(self) -> AsyncIterator[Chunk]:
        while True:
            c = await self._q.get()
            if c is None:
                return
            yield c

    async def write_can(self, arb_id: int, data: bytes, extended: bool) -> None:
        if arb_id == 0x300 and not extended:
            await asyncio.sleep(0.01)
            await self._q.put(Chunk(time.time(), "CAN 0x301", bytes([data[0] if data else 0, 0xAA])))

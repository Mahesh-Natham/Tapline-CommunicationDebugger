"""Listen to (and optionally talk on) a real serial port such as a USB-to-TTL adapter."""
from __future__ import annotations

import asyncio
import errno
import os
import threading
import time
from typing import AsyncIterator

import serial
from serial.tools import list_ports as _lp

from ..model import CTRL_TO_DISP, Chunk, SerialConfig
from .base import Source, SourceError

_PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
           "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}
_STOP = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}


def list_ports() -> list[dict]:
    out = []
    for p in sorted(_lp.comports(), key=lambda p: p.device):
        out.append({"device": p.device, "description": p.description or p.device,
                    "vid": f"{p.vid:04X}" if p.vid is not None else None,
                    "pid": f"{p.pid:04X}" if p.pid is not None else None})
    return out


def friendly_error(exc: BaseException, port: str) -> str:
    msg = str(exc)
    low = msg.lower()
    code = getattr(exc, "errno", None)
    if "access is denied" in low or "exclusively lock" in low or "busy" in low or code == errno.EBUSY or "resource temporarily" in low:
        return f"{port} is in use by another program. Close other serial monitors (Arduino IDE, PuTTY…) and try again."
    if code == errno.EACCES or "permission denied" in low:
        return (f"Permission denied for {port}. On Linux add yourself to the dialout group "
                f"(sudo usermod -aG dialout $USER, then log out and in).")
    if code == errno.ENOENT or "no such file" in low or "cannot find" in low or "filenotfound" in low:
        return f"{port} not found — is the adapter plugged in? Pick the port again from the list."
    if "readiness to read but returned no data" in low or "input/output error" in low or code == errno.EIO:
        return f"{port} was disconnected. Captured data is kept; plug it back in and press Start."
    return f"{port}: {msg}"


def _open(cfg: SerialConfig, timeout: float = 0.02) -> serial.Serial:
    extra = {"exclusive": True} if os.name == "posix" else {}  # Windows locks ports by itself
    try:
        return serial.Serial(cfg.port, cfg.baud, bytesize=cfg.bytesize, parity=_PARITY[cfg.parity],
                             stopbits=_STOP.get(float(cfg.stopbits), serial.STOPBITS_ONE),
                             timeout=timeout, write_timeout=2, **extra)
    except (serial.SerialException, OSError, ValueError) as e:
        raise SourceError(friendly_error(e, cfg.port)) from e


class SerialSource(Source):
    name = "Serial"

    def __init__(self, cfg: SerialConfig, channel: str = CTRL_TO_DISP):
        self.cfg = cfg
        self.channel = channel
        self.error: str | None = None
        self._ser: serial.Serial | None = None
        self._q: asyncio.Queue | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wlock = threading.Lock()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._q = asyncio.Queue()
        self._ser = await asyncio.to_thread(_open, self.cfg)
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, name=f"tapline-{self.cfg.port}", daemon=True)
        self._thread.start()

    def _put(self, item):
        try:
            self._loop.call_soon_threadsafe(self._q.put_nowait, item)
        except RuntimeError:
            pass  # loop closed

    def _reader(self):
        ser = self._ser
        try:
            while not self._stop.is_set():
                data = ser.read(ser.in_waiting or 1)
                if data:
                    self._put(Chunk(time.time(), self.channel, data))
        except (serial.SerialException, OSError, TypeError, AttributeError) as e:
            if not self._stop.is_set():
                self.error = friendly_error(e, self.cfg.port)
        finally:
            self._put(None)

    async def stop(self) -> None:
        self._stop.set()
        if self._thread:
            await asyncio.to_thread(self._thread.join, 1.0)
            self._thread = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    async def chunks(self) -> AsyncIterator[Chunk]:
        while True:
            item = await self._q.get()
            if item is None:
                return
            yield item

    async def write(self, data: bytes) -> None:
        if not self._ser:
            raise SourceError("The port is not open")

        def w():
            with self._wlock:
                self._ser.write(data)
                self._ser.flush()
        try:
            await asyncio.to_thread(w)
        except (serial.SerialException, OSError) as e:
            raise SourceError(friendly_error(e, self.cfg.port)) from e


async def read_window(cfg: SerialConfig, seconds: float, min_bytes: int = 64,
                      max_s: float = 1.5) -> tuple[bytes, int]:
    """Listen for at least `seconds`, longer (up to max_s) until min_bytes arrived.

    pyserial cannot report framing/parity errors portably, so errors is always 0;
    bytes received with errors typically arrive as 0x00 and lower the score instead.
    """
    def run():
        ser = _open(cfg, timeout=0.05)
        try:
            ser.reset_input_buffer()
            buf = bytearray()
            t0 = time.time()
            while True:
                el = time.time() - t0
                if el >= max_s or (el >= seconds and len(buf) >= min_bytes):
                    break
                buf += ser.read(ser.in_waiting or 1)
            return bytes(buf)
        finally:
            ser.close()
    return await asyncio.to_thread(run), 0

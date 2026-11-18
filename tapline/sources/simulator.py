"""A pretend controller board talking to a pretend display.

Protocol (invented for Tapline): 5A A5 LEN CMD PAYLOAD... CHK
LEN = bytes after LEN, CHK = sum8 of LEN, CMD and payload.
  0x10 temperature  i16le, degrees C x10, every 0.5 s, walks 20.0 -> 30.0 and back
  0x11 status text  ASCII "READY"/"HEAT", every 5 s
  0x20 heartbeat    u8 counter, every 1 s
Inbound 0x30 backlight u8 -> reply 5A A5 03 31 <level> CHK on disp->ctrl.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Iterator

from ..framing import LengthRule, StreamFramer
from ..model import CTRL_TO_DISP, DISP_TO_CTRL, Chunk
from .base import Source


def frame(cmd: int, payload: bytes) -> bytes:
    body = bytes([len(payload) + 2, cmd]) + payload
    return b"\x5a\xa5" + body + bytes([sum(body) & 0xFF])


def _timeline() -> Iterator[tuple[float, bytes]]:
    """Endless (sim_time, frame bytes) in time order."""
    temp, step, hb = 200, 5, 0
    events = []  # (t, kind)
    t_temp, t_status, t_hb = 0.0, 0.0, 0.25
    status_i = 0
    while True:
        nxt = min(t_temp, t_status, t_hb)
        if nxt == t_status:
            yield t_status, frame(0x11, b"READY" if status_i % 2 == 0 else b"HEAT")
            status_i += 1
            t_status += 5.0
        elif nxt == t_temp:
            yield t_temp, frame(0x10, temp.to_bytes(2, "little", signed=True))
            temp += step
            if temp >= 300 or temp <= 200:
                step = -step
            t_temp += 0.5
        else:
            yield t_hb, frame(0x20, bytes([hb & 0xFF]))
            hb += 1
            t_hb += 1.0


def sim_bytes(seconds: float, channel: str = CTRL_TO_DISP) -> list[Chunk]:
    out = []
    for t, data in _timeline():
        if t >= seconds:
            break
        out.append(Chunk(round(t, 6), channel, data))
    return out


class SimulatorSource(Source):
    name = "Simulator"

    def __init__(self, speed: float = 1.0, seed: int = 1):
        self.speed = max(0.01, float(speed))
        self.seed = seed
        self._q: asyncio.Queue[Chunk | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._rx = StreamFramer(LengthRule(b"\x5a\xa5", 2))
        self.error = None

    async def start(self) -> None:
        self._t0 = time.time()
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        for t, data in _timeline():
            delay = self._t0 + t / self.speed - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            await self._q.put(Chunk(time.time(), CTRL_TO_DISP, data))

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

    async def write(self, data: bytes) -> None:
        for f in self._rx.feed(Chunk(time.time(), "rx", data)):
            d = f.data
            if f.flags or len(d) < 6 or (sum(d[2:-1]) & 0xFF) != d[-1]:
                continue
            if d[3] == 0x30:
                level = d[4]
                await asyncio.sleep(0.02)
                await self._q.put(Chunk(time.time(), DISP_TO_CTRL, frame(0x31, bytes([level]))))

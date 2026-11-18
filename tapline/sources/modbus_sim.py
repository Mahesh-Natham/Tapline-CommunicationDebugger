"""A pretend RS-485 bus: a master polling a Modbus RTU device (address 1).

Holding registers 0-9: 0 temperature x10 (°C), 1 humidity %, 2 setpoint x10,
3 status bits, 4 uptime counter, 5-9 spare. The device answers requests you
send: FC3/FC4 reads, FC6/FC16 writes (registers 0-9), others -> exception 1.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Iterator

from .. import modbus as M
from ..framing import ModbusRule, StreamFramer
from ..model import BUS, Chunk
from .base import Source

ADDR = 1


def _initial() -> list[int]:
    return [235, 45, 220, 1, 0, 0, 0, 0, 0, 0]


def _respond(regs: list[int], req: bytes) -> bytes | None:
    d = M.decode(req, prefer="request")
    if not d or d["kind"] != "request" or d["addr"] != ADDR:
        return None  # not for us: a real device stays silent
    fn = d["fn"]
    if fn in (3, 4):
        s, q = d["start"], d["quantity"]
        if s + q > len(regs):
            return M.with_crc(bytes([ADDR, fn | 0x80, 2]))
        payload = b"".join(v.to_bytes(2, "big") for v in regs[s:s + q])
        return M.with_crc(bytes([ADDR, fn, len(payload)]) + payload)
    if fn == 6:
        if d["start"] >= len(regs):
            return M.with_crc(bytes([ADDR, 0x86, 2]))
        regs[d["start"]] = d["value"]
        return req
    if fn == 16:
        s, vals = d["start"], d["registers"]
        if s + len(vals) > len(regs):
            return M.with_crc(bytes([ADDR, 0x90, 2]))
        regs[s:s + len(vals)] = vals
        return M.with_crc(req[:6])
    return M.with_crc(bytes([ADDR, fn | 0x80, 1]))


def _timeline(regs: list[int]) -> Iterator[tuple[float, bytes]]:
    t, n = 0.0, 0
    temps = [235, 236, 238, 240, 241, 240, 238, 236]
    while True:
        if n % 6 == 0:
            regs[0] = temps[(n // 6) % len(temps)]
        regs[4] = n & 0xFFFF
        req = M.build(ADDR, 3, 0, 10)
        yield t, req
        yield t + 0.015, _respond(regs, req)
        t += 0.5
        n += 1


def modbus_sim_bytes(seconds: float) -> list[Chunk]:
    out = []
    for t, data in _timeline(_initial()):
        if t >= seconds:
            break
        out.append(Chunk(round(t, 6), BUS, data))
    return out


class ModbusSimSource(Source):
    name = "Modbus RS-485 simulator"

    def __init__(self, speed: float = 1.0):
        self.speed = max(0.01, float(speed))
        self.regs = _initial()
        self._q: asyncio.Queue = asyncio.Queue()
        self._task = None
        self._rx = StreamFramer(ModbusRule())
        self._lock = asyncio.Lock()
        self.error = None

    async def start(self) -> None:
        self._t0 = time.time()
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        for t, data in _timeline(self.regs):
            delay = self._t0 + t / self.speed - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            await self._q.put(Chunk(time.time(), BUS, data))

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
        async with self._lock:
            frames = self._rx.feed(Chunk(time.time(), "rx", data)) + self._rx.flush()
            for f in frames:
                reply = _respond(self.regs, f.data)
                if reply:
                    await asyncio.sleep(0.015)
                    await self._q.put(Chunk(time.time(), BUS, reply))

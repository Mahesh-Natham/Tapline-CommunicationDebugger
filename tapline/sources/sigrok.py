"""Logic analyzers through sigrok-cli: capture first, then decode UART, I²C or SPI."""
from __future__ import annotations

import asyncio
import re
import shutil
import time
from pathlib import Path
from typing import AsyncIterator

from ..model import Chunk
from .base import Source, SourceError

_LINE = re.compile(r"^(\d+)-(\d+) (\w+)-\d+: (.*)$")
_opt_cache: dict[str, str] = {}


def available() -> str | None:
    return shutil.which("sigrok-cli")


def _need() -> str:
    exe = available()
    if not exe:
        raise SourceError("sigrok-cli is not installed. Install it from sigrok.org (or `sudo apt install sigrok-cli`) "
                          "to use a logic analyzer.")
    return exe


async def _run(args: list[str], timeout: float = 60) -> str:
    exe = _need()
    proc = await asyncio.create_subprocess_exec(exe, *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise SourceError("sigrok-cli took too long and was stopped")
    if proc.returncode != 0:
        msg = (err or out).decode(errors="replace").strip().splitlines()
        raise SourceError("sigrok-cli: " + (msg[-1] if msg else f"exit code {proc.returncode}"))
    return out.decode(errors="replace")


def parse_scan(text: str) -> list[dict]:
    devs = []
    for line in text.splitlines():
        if " - " not in line or line.startswith("The following"):
            continue
        driver, _, rest = line.partition(" - ")
        desc, _, chans = rest.partition(": ")
        devs.append({"driver": driver.strip(), "description": desc.strip(), "channels": chans.split()})
    return devs


async def scan() -> list[dict]:
    return parse_scan(await _run(["--scan"], timeout=30))


async def samplerate(path) -> int:
    out = await _run(["-i", str(path), "--show"])
    m = re.search(r"Samplerate:\s*(\d+)", out)
    if not m:
        raise SourceError("The capture file has no sample rate")
    return int(m.group(1))


async def channels(path) -> list[str]:
    out = await _run(["-i", str(path), "--show"])
    return re.findall(r"^- (\S+): logic", out, re.M)


async def _uart_parity_opt() -> str:
    if "uart" not in _opt_cache:
        show = await _run(["--protocol-decoders", "uart", "--show"])
        _opt_cache["uart"] = "parity_type" if "parity_type" in show else "parity"
    return _opt_cache["uart"]


def _lines(text: str, rate: int):
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if m:
            yield int(m.group(1)) / rate, int(m.group(2)) / rate, m.group(4).strip()


async def decode(path, spec: dict) -> list[Chunk]:
    """Decode a .sr capture into chunks (times in seconds from the capture start)."""
    proto = spec.get("protocol")
    if proto not in ("uart", "i2c", "spi"):
        raise ValueError("Protocol must be uart, i2c or spi")
    rate = await samplerate(path)
    names = await channels(path)

    def ch(key, required=True):
        v = spec.get(key)
        if not v:
            if required:
                raise ValueError(f"Choose the {key.upper()} channel")
            return None
        if v not in names:
            raise ValueError(f"Channel {v!r} is not in this capture (it has {', '.join(names)})")
        return v

    out: list[Chunk] = []
    if proto == "uart":
        opts = f":baudrate={int(spec.get('baudrate', 115200))}"
        par = {"N": "none", "E": "even", "O": "odd"}.get(str(spec.get("parity", "N"))[:1].upper(), "none")
        if par != "none":
            opts += f":{await _uart_parity_opt()}={par}"
        if spec.get("invert"):
            opts += ":invert_rx=yes"
        for key in ("rx", "tx"):
            c = ch(key, required=(key == "rx"))
            if not c:
                continue
            text = await _run(["-i", str(path), "-P", f"uart:rx={c}{opts}", "-A", "uart=rx-data",
                               "--protocol-decoder-samplenum"])
            for t0, _, v in _lines(text, rate):
                if re.fullmatch(r"[0-9A-Fa-f]{2}", v):
                    out.append(Chunk(t0, f"UART {c}", bytes([int(v, 16)])))
    elif proto == "i2c":
        text = await _run(["-i", str(path), "-P", f"i2c:scl={ch('scl')}:sda={ch('sda')}", "-A",
                           "i2c=start:repeat-start:stop:address-read:address-write:data-read:data-write",
                           "--protocol-decoder-samplenum"])
        cur = None  # [t, addr, read, bytearray]

        def flush():
            if cur and cur[1] is not None and cur[3]:
                out.append(Chunk(cur[0], f"I²C 0x{cur[1]:02X} {'read' if cur[2] else 'write'}", bytes(cur[3])))

        for t0, _, v in _lines(text, rate):
            low = v.lower()
            if low.startswith("start") or low in ("sr", "s"):
                flush()
                cur = [t0, None, False, bytearray()]
            elif low.startswith("stop") or low == "p":
                flush()
                cur = None
            elif low.startswith("address"):
                if cur is None:
                    cur = [t0, None, False, bytearray()]
                cur[1] = int(v.split(":")[-1].strip(), 16)
                cur[2] = "read" in low
            elif low.startswith("data") and cur is not None:
                cur[3].append(int(v.split(":")[-1].strip(), 16))
        flush()
    else:
        opts = f":cpol={int(spec.get('cpol', 0))}:cpha={int(spec.get('cpha', 0))}"
        pins = f"clk={ch('clk')}"
        mosi, miso, cs = ch("mosi", False), ch("miso", False), ch("cs", False)
        if not mosi and not miso:
            raise ValueError("Choose at least the MOSI or MISO channel")
        pins += (f":mosi={mosi}" if mosi else "") + (f":miso={miso}" if miso else "") + (f":cs={cs}" if cs else "")
        for name, line in (("MOSI", mosi), ("MISO", miso)):
            if not line:
                continue
            ann = f"spi={name.lower()}-transfer" if cs else f"spi={name.lower()}-data"
            text = await _run(["-i", str(path), "-P", f"spi:{pins}{opts}", "-A", ann, "--protocol-decoder-samplenum"])
            for t0, _, v in _lines(text, rate):
                try:
                    data = bytes.fromhex(v)
                except ValueError:
                    continue
                if data:
                    out.append(Chunk(t0, f"SPI {name}", data))
    out.sort(key=lambda c: c.t)
    return out


async def capture(driver: str, rate: str, seconds: float, chans: list[str], out_path) -> Path:
    if not 0 < seconds <= 60:
        raise SourceError("Capture between 0 and 60 seconds")
    args = ["-d", driver, "--config", f"samplerate={rate}", "--time", f"{int(seconds * 1000)}ms",
            "-o", str(out_path)]
    if chans:
        args[2:2] = ["--channels", ",".join(chans)]
    await _run(args, timeout=seconds + 60)
    return Path(out_path)


class SigrokSource(Source):
    """Capture (optional) then decode; delivers the decoded bytes once, then ends."""
    name = "Logic analyzer"
    offline = True

    def __init__(self, spec: dict, file=None, device: dict | None = None, on_status=None):
        self.spec, self.file, self.device = spec, file, device
        self.error = None
        self._q: asyncio.Queue = asyncio.Queue()
        self._task = None
        self._on_status = on_status or (lambda msg: None)

    async def start(self) -> None:
        _need()
        if self.file and not Path(self.file).exists():
            raise SourceError("Capture file not found")
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            t0 = time.time()
            path = self.file
            if self.device:
                self._on_status(f"Capturing {self.device['seconds']} s at {self.device['samplerate']}…")
                path = await capture(self.device["driver"], self.device["samplerate"], float(self.device["seconds"]),
                                     self.device.get("channels") or [], self.device["out"])
            self._on_status("Decoding…")
            for c in await decode(path, self.spec):
                await self._q.put(Chunk(t0 + c.t, c.channel, c.data))
            self._on_status("")
        except (SourceError, ValueError) as e:
            self.error = str(e)
        finally:
            await self._q.put(None)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            await self._q.put(None)

    async def chunks(self) -> AsyncIterator[Chunk]:
        while True:
            c = await self._q.get()
            if c is None:
                return
            yield c

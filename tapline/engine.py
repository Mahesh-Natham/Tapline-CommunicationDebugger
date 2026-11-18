"""Live capture state: sources, framing, markers, protocol, modes and the send gate."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from . import protocol as proto_mod
from .framing import GapRule, ModbusRule, NativeRule, Rule, StreamFramer, rule_from_json, rule_to_json
from .model import CTRL_TO_DISP, DISP_TO_CTRL, SENT, Chunk, Frame, Marker, SerialConfig, is_sent
from .pico import PicoBridge, PicoError
from .rs485 import RoleTracker
from .sources.modbus_sim import ModbusSimSource
from .sources import can_source
from .sources import sigrok
from . import la_sim
from .project import Project
from .sources.base import Source, SourceError
from .sources.serial_source import SerialSource
from .sources.simulator import SimulatorSource


class EngineError(Exception):
    """User-facing error (message is shown as is)."""


class SendNotAllowed(EngineError):
    pass


def parse_hex(s: str) -> bytes:
    cleaned = re.sub(r"0x", "", s or "", flags=re.I)
    cleaned = re.sub(r"[\s,;:]", "", cleaned)
    if not cleaned:
        raise ValueError("Nothing to send — type some hex bytes, e.g. 5A A5 03 30 32 65")
    if len(cleaned) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", cleaned):
        raise ValueError("That isn't valid hex. Use pairs like 5A A5 03 (spaces optional)")
    return bytes.fromhex(cleaned)


class Engine:
    def __init__(self, project: Project | None = None):
        self.project = project
        self.protocol = proto_mod.load(project.protocol_path) if project else proto_mod.Protocol()
        try:
            self.rule: Rule = rule_from_json(self.protocol.framing)
        except ValueError:
            self.rule = GapRule(5)
        self.mode = "sniff"
        self.pico: PicoBridge | None = None
        self.link = "ttl"
        self._roles = RoleTracker()
        self.sources: list[Source] = []
        self.source_label = ""
        self.source_kind = ""
        self.chunks: list[Chunk] = []
        self.frames: list[Frame] = []
        self.markers: list[Marker] = []
        self.error: str | None = None
        self.session_name: str | None = None
        self._framer = StreamFramer(self.rule)
        self._writer = None
        self._tasks: list[asyncio.Task] = []
        self._repeat: asyncio.Task | None = None
        self._subs: set[asyncio.Queue] = set()
        self._types: dict[int, object] = {}  # frame id -> FrameType | None (cache)

    # -- events -------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q) -> None:
        self._subs.discard(q)

    def _emit(self, ev: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # slow client: drop; it can re-sync from REST

    def status(self) -> dict:
        return {
            "running": self.running, "source": self.source_label, "source_kind": self.source_kind,
            "mode": self.mode, "rule": rule_to_json(self.rule), "frames": len(self.frames),
            "chunks": len(self.chunks), "markers": len(self.markers), "error": self.error,
            "project": self.project.name if self.project else None, "session": self.session_name,
            "repeating": bool(self._repeat and not self._repeat.done()),
            "link": self.link,
            "pico": self.pico.port if self.pico else None,
        }

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks if t.get_name().startswith("pump"))

    # -- capture --------------------------------------------------------------
    async def start(self, kind: str, cfg: SerialConfig | None = None, cfg2: SerialConfig | None = None,
                    speed: float = 1.0, link: str = "ttl", opts: dict | None = None) -> None:
        await self.stop()
        if link not in ("ttl", "rs232", "rs485"):
            raise EngineError("Link must be ttl, rs232 or rs485")
        auto_rule: Rule | None = None
        if kind == "simulator":
            sources = [SimulatorSource(speed=speed)]
            label = "Simulator (demo)"
        elif kind == "sim-modbus":
            sources = [ModbusSimSource(speed=speed)]
            label = "Modbus RS-485 simulator (demo)"
            link, auto_rule = "rs485", ModbusRule()
        elif kind == "sim-can":
            sources, label, auto_rule = [can_source.SimCanSource(speed=speed)], "CAN simulator (demo car)", NativeRule()
        elif kind == "can":
            o = opts or {}
            if not o.get("interface"):
                raise EngineError("Choose a CAN interface")
            sources = [can_source.CanSource(o["interface"], str(o.get("channel", "")), int(o.get("bitrate", 500000)))]
            label = f"CAN {o['interface']} {o.get('channel', '')} · {int(o.get('bitrate', 500000)) // 1000} kbit/s"
            auto_rule = NativeRule()
        elif kind in ("sigrok", "sim-la"):
            sources, label, auto_rule = self._sigrok_sources(kind, opts or {})
        elif kind == "serial":
            if not cfg or not cfg.port:
                raise EngineError("Choose a serial port first")
            sources = [SerialSource(cfg, CTRL_TO_DISP)]
            label = f"{cfg.port} · {cfg.label()}"
            if cfg2 and cfg2.port:
                if cfg2.port == cfg.port:
                    raise EngineError("The second adapter must be a different port")
                sources.append(SerialSource(cfg2, DISP_TO_CTRL))
                label += f" + {cfg2.port}"
        else:
            raise EngineError(f"Unknown source {kind!r}")
        started = []
        try:
            for s in sources:
                await s.start()
                started.append(s)
        except SourceError as e:
            for s in started:
                await s.stop()
            raise EngineError(str(e)) from e
        self.sources, self.source_label, self.source_kind = sources, label, kind
        self.link = link
        self._roles = RoleTracker()
        if auto_rule is not None and auto_rule != self.rule:
            self.rule = auto_rule
            self.protocol.framing = rule_to_json(auto_rule)
            self.save_protocol()
        self.chunks, self.frames, self.markers = [], [], []
        self.invalidate_types()
        self.error = None
        self._framer = StreamFramer(self.rule)
        if self.project:
            meta = {"source": kind, "label": label, "rule": rule_to_json(self.rule), "link": link}
            if cfg:
                meta["config"] = cfg.to_json()
            self._writer = self.project.new_session(meta)
            self.session_name = self._writer.name
        for i, s in enumerate(sources):
            task = asyncio.create_task(self._pump(s), name=f"pump{i}")
            # report the new state once the task has really finished (so "running" is accurate)
            task.add_done_callback(lambda _t: self._emit({"type": "status", "status": self.status()}))
            self._tasks.append(task)
        self._tasks.append(asyncio.create_task(self._ticker(), name="ticker"))
        for s in sources:
            if getattr(s, "warning", None):
                self._emit({"type": "error", "message": s.warning})
        self._emit({"type": "status", "status": self.status()})

    def _sigrok_sources(self, kind: str, opts: dict):
        if not sigrok.available():
            raise EngineError("sigrok-cli is not installed. Install it from sigrok.org "
                              "(Linux: sudo apt install sigrok-cli) to use a logic analyzer.")
        cap_dir = (self.project.root if self.project else Path.home() / "Tapline") / "captures"
        cap_dir.mkdir(parents=True, exist_ok=True)
        device = None
        if kind == "sim-la":
            proto = opts.get("protocol", "uart")
            if proto not in la_sim.DEMO_SPECS:
                raise EngineError("Demo protocol must be uart, i2c or spi")
            file = la_sim.demo_capture(cap_dir / "demo-board.sr")
            spec, label = la_sim.DEMO_SPECS[proto], f"Logic analyzer demo ({proto.upper()})"
        else:
            spec = opts.get("spec") or {}
            if opts.get("device"):
                d = dict(opts["device"])
                if not d.get("driver"):
                    raise EngineError("Choose a logic analyzer (press Scan)")
                d["out"] = str(cap_dir / time.strftime("capture-%Y%m%d-%H%M%S.sr"))
                device, file = d, None
                label = f"Logic analyzer {d['driver']} · {spec.get('protocol', '').upper()}"
            else:
                file = self.capture_path(opts.get("file", ""))
                label = f"Capture {file.name} · {spec.get('protocol', '').upper()}"
        proto = spec.get("protocol")
        if proto == "uart" or (proto == "spi" and not spec.get("cs")):
            rule = GapRule(1)
        else:
            rule = NativeRule()
        src = sigrok.SigrokSource(spec, file=file, device=device,
                                  on_status=lambda m: self._emit({"type": "progress", "message": m}))
        return [src], label, rule

    def capture_path(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name or ".." in name or not name.endswith(".sr"):
            raise EngineError("Choose a .sr capture file")
        p = (self.project.root if self.project else Path.home() / "Tapline") / "captures" / name
        if not p.exists():
            raise EngineError("Capture file not found")
        return p

    async def _pump(self, src: Source):
        async for c in src.chunks():
            self._ingest(c)
        if getattr(src, "offline", False):
            for f in self._framer.flush():
                self._add_frame(f)
        if src.error:
            self.error = src.error
            self._emit({"type": "error", "message": src.error})

    async def _ticker(self):
        while True:
            await asyncio.sleep(0.02)
            if any(getattr(s, "offline", False) for s in self.sources):
                continue  # recorded data: timestamps are in the past, silence means nothing
            for f in self._framer.flush_idle(time.time()):
                self._add_frame(f)

    def _ingest(self, c: Chunk):
        self.chunks.append(c)
        if self._writer:
            self._writer.write_chunk(c)
        for f in self._framer.feed(c):
            self._add_frame(f)

    def _add_frame(self, f: Frame):
        if self.link == "rs485":
            f.channel = self._roles.label(f)
        self.frames.append(f)
        self._emit({"type": "frame", "frame": f.to_json()})

    async def stop(self) -> None:
        self.stop_repeat()
        for s in self.sources:
            await s.stop()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        for f in self._framer.flush():
            self._add_frame(f)
        self._framer = StreamFramer(self.rule)
        if self._writer:
            self._writer.close()
            self._writer = None
        was = bool(self.sources)
        self.sources = []
        if was:
            self._emit({"type": "status", "status": self.status()})

    # -- framing ----------------------------------------------------------------
    def set_rule(self, rule: Rule) -> None:
        self.rule = rule
        framer = StreamFramer(rule)
        frames: list[Frame] = []
        for c in self.chunks:
            frames += framer.feed(c)
        if not self.running:
            frames += framer.flush()
        if self.link == "rs485":
            self._roles = RoleTracker()
            for f in frames:
                f.channel = self._roles.label(f)
        self._framer = framer
        self.frames = frames
        self.invalidate_types()
        self.protocol.framing = rule_to_json(rule)
        self.save_protocol()
        self._emit({"type": "reframed", "count": len(frames), "rule": rule_to_json(rule)})

    # -- markers --------------------------------------------------------------
    def add_marker(self, label: str, note: str = "", expected: str | None = None,
                   t: float | None = None) -> Marker:
        m = Marker(time.time() if t is None else float(t), (label or "").strip()[:200] or "Event", note, expected)
        self.markers.append(m)
        if self._writer:
            self._writer.write_marker(m)
        self._emit({"type": "marker", "index": len(self.markers) - 1, "marker": m.to_json()})
        return m

    # -- modes + sending ----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode not in ("sniff", "send"):
            raise EngineError("Mode must be sniff or send")
        if mode == "sniff":
            self.stop_repeat()
            for s in self.sources:
                if hasattr(s, "set_passive"):
                    asyncio.get_running_loop().create_task(s.set_passive(True))
        self.mode = mode
        self._emit({"type": "status", "status": self.status()})

    def _check_can_send(self):
        if self.mode != "send":
            raise SendNotAllowed("Switch to Send mode first — Sniff mode never transmits.")
        if not self.sources:
            raise EngineError("Start a source first")

    @property
    def is_can(self) -> bool:
        return bool(self.sources) and hasattr(self.sources[0], "write_can")

    async def _write(self, data, can_id: tuple[int, bool] | None = None):
        """data: bytes for serial sources; for CAN, payload bytes plus can_id=(id, extended)."""
        self._check_can_send()
        src = self.sources[0]
        channel = SENT
        try:
            if self.is_can:
                if can_id is None:
                    raise EngineError("CAN messages need an ID — write them as ID#DATA, e.g. 300#01")
                await src.write_can(can_id[0], data, can_id[1])
                channel = f"{SENT} {can_source.can_channel(*can_id)}"
            else:
                await src.write(data)
        except SourceError as e:
            raise EngineError(str(e)) from e
        except NotImplementedError as e:
            raise EngineError(str(e)) from e
        self._ingest(Chunk(time.time(), channel, data))
        # a sent message is complete as soon as it is written
        for f in self._framer.flush_channel(channel):
            self._add_frame(f)

    def _parse_out(self, text: str):
        if self.is_can:
            arb, data, ext = can_source.parse_can_text(text)
            return data, (arb, ext)
        return parse_hex(text), None

    async def send(self, hex_str: str, repeat_ms: int | None = None, count: int = 1,
                   wait_ms: int = 0, return_replies: bool = False):
        self._check_can_send()
        data, can_id = self._parse_out(hex_str)
        if wait_ms and not repeat_ms:
            self.stop_repeat()
            before = len(self.frames)
            await self._write(data, can_id)
            deadline = time.time() + min(5000, int(wait_ms)) / 1000
            while time.time() < deadline:
                await asyncio.sleep(0.02)
            replies = [f for f in self.frames[before:] if not is_sent(f.channel)]
            return replies if return_replies else data
        self._check_can_send()
        self.stop_repeat()
        if repeat_ms:
            interval = max(10, int(repeat_ms)) / 1000

            async def loop():
                n = 0
                while count <= 1 or n < count:
                    await self._write(data, can_id)
                    n += 1
                    await asyncio.sleep(interval)
            self._repeat = asyncio.create_task(self._guard(loop()))
            self._emit({"type": "status", "status": self.status()})
        else:
            for _ in range(max(1, int(count))):
                await self._write(data, can_id)
        return data

    async def replay(self, frame_ids: list[int], keep_timing: bool = True) -> int:
        self._check_can_send()
        by_id = {f.id: f for f in self.frames}
        frames = [by_id[i] for i in frame_ids if i in by_id]
        if not frames:
            raise EngineError("Pick at least one frame to replay")
        self.stop_repeat()

        async def run():
            prev = None
            for f in frames:
                if keep_timing and prev is not None:
                    await asyncio.sleep(min(2.0, max(0.0, f.t_start - prev)))
                await self._write(f.data, can_source.channel_to_id(f.channel) if self.is_can else None)
                prev = f.t_start
            self._emit({"type": "status", "status": self.status()})
        self._repeat = asyncio.create_task(self._guard(run()))
        return len(frames)

    async def _guard(self, coro):
        """Run a background send loop; if it fails, tell the user instead of dying silently."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - any failure must reach the UI
            self.error = str(e)
            self._emit({"type": "error", "message": f"Sending stopped: {e}"})
        finally:
            if self._repeat is asyncio.current_task():
                self._repeat = None
            self._emit({"type": "status", "status": self.status()})

    def stop_repeat(self) -> None:
        if self._repeat and not self._repeat.done():
            self._repeat.cancel()
        self._repeat = None

    # -- protocol + sessions -----------------------------------------------------------
    def type_of(self, f: Frame):
        """The protocol frame type of a frame (cached; cleared when framing or protocol change)."""
        try:
            return self._types[f.id]
        except KeyError:
            t = proto_mod.match_type(self.protocol, f.data, f.channel)
            self._types[f.id] = t
            return t

    def invalidate_types(self) -> None:
        self._types = {}

    def save_protocol(self) -> None:
        self.invalidate_types()
        if self.project:
            proto_mod.save(self.protocol, self.project.protocol_path)

    def load_session(self, name: str) -> None:
        if not self.project:
            raise EngineError("No project open")
        chunks, markers, meta = self.project.load_session(name)
        self.chunks, self.markers = chunks, markers
        self.link = meta.get("link", "ttl")
        self.session_name = name
        self.source_label = f"Saved session {name}"
        self.set_rule(self.rule)
        self._emit({"type": "status", "status": self.status()})

    # -- Raspberry Pi Pico bridge (I²C / SPI sending) ---------------------------------------
    async def pico_connect(self, port: str) -> str:
        await self.pico_disconnect()
        bridge = PicoBridge(port)
        try:
            await asyncio.to_thread(bridge.open)
        except PicoError as e:
            raise EngineError(str(e)) from e
        self.pico = bridge
        self._emit({"type": "status", "status": self.status()})
        return bridge.version

    async def pico_disconnect(self) -> None:
        if getattr(self, "pico", None):
            await asyncio.to_thread(self.pico.close)
        self.pico = None

    async def pico_send(self, bus: str, addr: int | None, hex_str: str, read_len: int = 0) -> dict:
        if self.mode != "send":
            raise SendNotAllowed("Switch to Send mode first — Sniff mode never transmits.")
        if not getattr(self, "pico", None):
            raise EngineError("Connect the Pico bridge first")
        data = parse_hex(hex_str) if (hex_str or "").strip() else b""
        read_len = int(read_len or 0)
        try:
            if bus == "i2c":
                if addr is None or not 0x03 <= int(addr) <= 0x77:
                    raise EngineError("I²C address must be 03–77 (hex)")
                if not data and not read_len:
                    raise ValueError("Give bytes to write, a number of bytes to read, or both")
                if data:
                    self._ingest_frame(f"{SENT} I²C 0x{addr:02X} write", data)
                if read_len:
                    got = await asyncio.to_thread(self.pico.i2c_read, addr, read_len, data)
                elif data:
                    got = b""
                    await asyncio.to_thread(self.pico.i2c_write, addr, data)
                if read_len:
                    self._ingest_frame(f"I²C 0x{addr:02X} read", got)
            elif bus == "spi":
                if not data:
                    raise ValueError("Give the bytes to clock out on MOSI")
                self._ingest_frame(f"{SENT} SPI MOSI", data)
                got = await asyncio.to_thread(self.pico.spi_xfer, data)
                self._ingest_frame("SPI MISO", got)
            else:
                raise EngineError("Bus must be i2c or spi")
        except PicoError as e:
            raise EngineError(str(e)) from e
        return {"read": got.hex(" ").upper() if got else ""}

    def _ingest_frame(self, channel: str, data: bytes) -> None:
        """Record a complete message (used for Pico traffic, which is not part of the live source)."""
        c = Chunk(time.time(), channel, data)
        self.chunks.append(c)
        if self._writer:
            self._writer.write_chunk(c)
        f = Frame(max((x.id for x in self.frames[-1:]), default=-1) + 1, c.t, c.t, channel, data)
        self._framer._next_id = max(self._framer._next_id, f.id + 1)
        self._add_frame(f)

    async def open_project(self, project: Project) -> None:
        await self.stop()
        self.project = project
        self.protocol = proto_mod.load(project.protocol_path)
        try:
            self.rule = rule_from_json(self.protocol.framing)
        except ValueError:
            self.rule = GapRule(5)
        self.chunks, self.frames, self.markers = [], [], []
        self.invalidate_types()
        self.session_name = None
        self.source_label = ""
        self.error = None
        self._framer = StreamFramer(self.rule)
        self._emit({"type": "reframed", "count": 0, "rule": rule_to_json(self.rule)})
        self._emit({"type": "status", "status": self.status()})

    def frame(self, fid: int) -> Frame:
        for f in self.frames:
            if f.id == fid:
                return f
        raise EngineError(f"Frame {fid} not found")

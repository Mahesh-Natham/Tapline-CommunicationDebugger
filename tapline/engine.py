"""Live capture state: sources, framing, markers, protocol, modes and the send gate."""
from __future__ import annotations

import asyncio
import re
import time

from . import protocol as proto_mod
from .framing import GapRule, Rule, StreamFramer, rule_from_json, rule_to_json
from .model import CTRL_TO_DISP, DISP_TO_CTRL, SENT, Chunk, Frame, Marker, SerialConfig
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
        }

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks if t.get_name().startswith("pump"))

    # -- capture --------------------------------------------------------------
    async def start(self, kind: str, cfg: SerialConfig | None = None, cfg2: SerialConfig | None = None,
                    speed: float = 1.0) -> None:
        await self.stop()
        if kind == "simulator":
            sources = [SimulatorSource(speed=speed)]
            label = "Simulator (demo)"
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
        self.chunks, self.frames, self.markers = [], [], []
        self.invalidate_types()
        self.error = None
        self._framer = StreamFramer(self.rule)
        if self.project:
            meta = {"source": kind, "label": label, "rule": rule_to_json(self.rule)}
            if cfg:
                meta["config"] = cfg.to_json()
            self._writer = self.project.new_session(meta)
            self.session_name = self._writer.name
        for i, s in enumerate(sources):
            self._tasks.append(asyncio.create_task(self._pump(s), name=f"pump{i}"))
        self._tasks.append(asyncio.create_task(self._ticker(), name="ticker"))
        self._emit({"type": "status", "status": self.status()})

    async def _pump(self, src: Source):
        async for c in src.chunks():
            self._ingest(c)
        if src.error:
            self.error = src.error
            self._emit({"type": "error", "message": src.error})
        self._emit({"type": "status", "status": self.status()})

    async def _ticker(self):
        while True:
            await asyncio.sleep(0.02)
            for f in self._framer.flush_idle(time.time()):
                self._add_frame(f)

    def _ingest(self, c: Chunk):
        self.chunks.append(c)
        if self._writer:
            self._writer.write_chunk(c)
        for f in self._framer.feed(c):
            self._add_frame(f)

    def _add_frame(self, f: Frame):
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
        self.mode = mode
        self._emit({"type": "status", "status": self.status()})

    def _check_can_send(self):
        if self.mode != "send":
            raise SendNotAllowed("Switch to Send mode first — Sniff mode never transmits.")
        if not self.sources:
            raise EngineError("Start a source first")

    async def _write(self, data: bytes):
        self._check_can_send()
        try:
            await self.sources[0].write(data)
        except SourceError as e:
            raise EngineError(str(e)) from e
        except NotImplementedError as e:
            raise EngineError(str(e)) from e
        self._ingest(Chunk(time.time(), SENT, data))
        # a sent message is complete as soon as it is written
        for f in self._framer.flush_channel(SENT):
            self._add_frame(f)

    async def send(self, hex_str: str, repeat_ms: int | None = None, count: int = 1) -> bytes:
        data = parse_hex(hex_str)
        self._check_can_send()
        self.stop_repeat()
        if repeat_ms:
            interval = max(10, int(repeat_ms)) / 1000

            async def loop():
                n = 0
                while count <= 1 or n < count:
                    await self._write(data)
                    n += 1
                    await asyncio.sleep(interval)
            self._repeat = asyncio.create_task(self._guard(loop()))
            self._emit({"type": "status", "status": self.status()})
        else:
            for _ in range(max(1, int(count))):
                await self._write(data)
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
                await self._write(f.data)
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
            t = proto_mod.match_type(self.protocol, f.data)
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
        self.session_name = name
        self.source_label = f"Saved session {name}"
        self.set_rule(self.rule)
        self._emit({"type": "status", "status": self.status()})

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

"""HTTP + WebSocket API, and the static browser UI."""
from __future__ import annotations

import asyncio
import csv
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import autodetect, protocol as P
from .checksums import ChecksumSpec, check, find_checksums
from .diff import diff_frames, variability
from .engine import Engine, EngineError, SendNotAllowed, parse_hex
from .framing import guess_framing, rule_from_json, rule_to_json
from .model import SerialConfig
from .project import Project
from .search import find_value, near_marker
from .sources.serial_source import list_ports, read_window
from .sources.simulator import sim_bytes
from .uartsim import reinterpret

STATIC = Path(__file__).parent / "static"
MAX_PAGE = 2000


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class NotFound(Exception):
    pass


def _hostname(value: str) -> str:
    """'localhost:8765' -> 'localhost', '[::1]:8765' -> '[::1]'."""
    v = (value or "").strip().lower()
    if v.startswith("["):
        return v[: v.find("]") + 1] if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def is_local_request(headers) -> str | None:
    """None if the request comes from Tapline's own page on this computer, else the reason.

    Blocks other websites (Origin check) and DNS-rebinding tricks (Host check), so no
    web page can switch Tapline into Send mode or transmit.
    """
    if _hostname(headers.get("host", "")) not in LOCAL_HOSTS:
        return "Tapline only accepts requests addressed to localhost"
    origin = headers.get("origin")
    if origin and origin != "null":
        host = origin.split("://", 1)[-1]
        if not origin.startswith("http://") or _hostname(host) not in LOCAL_HOSTS:
            return "Requests from other websites are blocked"
    elif origin == "null":
        return "Requests from other websites are blocked"
    return None


def create_app(project: str = "Default", base: Path | None = None) -> FastAPI:
    engine = Engine(Project.open_or_create(project, base=base))

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await engine.stop()

    app = FastAPI(title="Tapline", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.engine = engine
    app.state.base = base

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        reason = is_local_request(request.headers)
        if reason:
            return JSONResponse({"error": reason}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse({"error": "Send JSON (Content-Type: application/json)"}, status_code=415)
        return await call_next(request)

    # -- errors -> friendly JSON ---------------------------------------------------
    @app.exception_handler(SendNotAllowed)
    async def _send_na(_: Request, e: Exception):
        return JSONResponse({"error": str(e)}, status_code=403)

    @app.exception_handler(EngineError)
    async def _eng(_: Request, e: Exception):
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _val(_: Request, e: Exception):
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.exception_handler(NotFound)
    async def _nf(_: Request, e: Exception):
        return JSONResponse({"error": str(e)}, status_code=404)

    @app.exception_handler(KeyError)
    async def _ke(_: Request, e: Exception):
        return JSONResponse({"error": str(e).strip("'\"")}, status_code=404)

    def frame_json(f):
        d = f.to_json()
        t = P.match_type(engine.protocol, f.data)
        d["type"] = t.name if t else None
        return d

    async def body(req: Request) -> dict:
        try:
            d = await req.json()
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    def status():
        return engine.status()

    # -- setup -------------------------------------------------------------------
    @app.get("/api/ports")
    async def ports():
        return {"ports": await asyncio.to_thread(list_ports)}

    @app.get("/api/options")
    async def options():
        return {"bauds": autodetect.BAUDS, "formats": autodetect.FORMATS, "bytesize": [5, 6, 7, 8],
                "parity": ["N", "E", "O", "M", "S"], "stopbits": [1, 1.5, 2],
                "framing": ["gap", "delimiter", "length", "fixed"]}

    @app.get("/api/status")
    async def get_status():
        return status()

    @app.post("/api/start")
    async def start(req: Request):
        d = await body(req)
        src = d.get("source", "simulator")
        cfg = SerialConfig.from_json(d["config"]) if d.get("config") else None
        cfg2 = SerialConfig.from_json(d["config2"]) if d.get("config2") else None
        await engine.start(src, cfg, cfg2, speed=float(d.get("speed", 1.0)))
        return status()

    @app.post("/api/stop")
    async def stop():
        await engine.stop()
        return status()

    @app.post("/api/detect")
    async def detect(req: Request):
        d = await body(req)
        port = d.get("port") or ""
        if not port:
            raise EngineError("Choose a port to detect on")
        if port == "simulator":
            stream = b"".join(c.data for c in sim_bytes(4.0))
            true = SerialConfig("simulator", 115200)

            async def read(cfg, seconds):
                await asyncio.sleep(0.03)
                got, _ = reinterpret(stream, true, cfg, errors_as_zero=True)
                return got, 0
        else:
            if engine.running and any(getattr(s, "cfg", None) and s.cfg.port == port for s in engine.sources):
                raise EngineError("Stop the capture on this port before detecting")

            async def read(cfg, seconds):
                return await read_window(cfg, seconds)

        def progress(done, total, label):
            engine._emit({"type": "detect_progress", "done": done, "total": total, "label": label})
        cands = await autodetect.detect(read, port, progress=progress)
        if not cands or cands[0].score < 0.05:
            return {"candidates": [c.to_json() for c in cands],
                    "message": "No clear signal. Check wiring (adapter RX ← controller TX, GND ↔ GND) and that the board is sending."}
        return {"candidates": [c.to_json() for c in cands]}

    # -- frames ------------------------------------------------------------------
    @app.get("/api/frames")
    async def frames(after: int | None = None, before: int | None = None, limit: int = 500):
        limit = max(1, min(MAX_PAGE, limit))
        fr = engine.frames
        if after is not None:
            sel = [f for f in fr if f.id > after][:limit]
        elif before is not None:
            sel = [f for f in fr if f.id < before][-limit:]
        else:
            sel = fr[-limit:]
        return {"items": [frame_json(f) for f in sel], "total": len(fr)}

    @app.api_route("/api/framing", methods=["POST", "PUT"])
    async def set_framing(req: Request):
        d = await body(req)
        engine.set_rule(rule_from_json(d.get("rule") or {}))
        return status()

    @app.post("/api/framing/guess")
    async def guess():
        chunks = [c for c in engine.chunks if c.channel != "sent"][-20000:]
        return {"candidates": [{"rule": rule_to_json(r), "score": s} for r, s in guess_framing(chunks)]}

    # -- markers + analysis --------------------------------------------------------
    @app.post("/api/markers")
    async def add_marker(req: Request):
        d = await body(req)
        m = engine.add_marker(d.get("label", ""), d.get("note", ""), d.get("expected"), d.get("t"))
        return {"marker": m.to_json(), "index": len(engine.markers) - 1}

    @app.get("/api/markers")
    async def markers():
        return {"markers": [m.to_json() for m in engine.markers]}

    @app.get("/api/markers/{i}/nearby")
    async def nearby(i: int, window: float = 2.0):
        if not 0 <= i < len(engine.markers):
            raise NotFound("No such marker")
        return near_marker(engine.frames, engine.markers[i], window)

    @app.get("/api/search")
    async def search(q: str = "", window: float = 2.0):
        hits = find_value(engine.frames, q, engine.markers, window)
        by_id = {f.id: f for f in engine.frames}
        return {"hits": [{**h.to_json(), "t": by_id[h.frame_id].t_start, "hex": by_id[h.frame_id].hex()}
                         for h in hits]}

    @app.post("/api/diff")
    async def diff(req: Request):
        d = await body(req)
        a, b = engine.frame(int(d["a"])), engine.frame(int(d["b"]))
        return {"diff": diff_frames(a.data, b.data), "a": a.hex(), "b": b.hex()}

    def _frames_for(type_name: str | None, prefix: str | None):
        if type_name:
            t = P.get_type(engine.protocol, type_name)
            return [f for f in engine.frames if P.match_type(engine.protocol, f.data) is t]
        if prefix:
            pre = bytes.fromhex(prefix.replace(" ", ""))
            return [f for f in engine.frames if f.data.startswith(pre)]
        return [f for f in engine.frames if f.channel != "sent"]

    @app.get("/api/variability")
    async def var(type: str | None = None, prefix: str | None = None):
        fr = _frames_for(type, prefix)[-5000:]
        return {"offsets": variability(fr), "count": len(fr)}

    @app.post("/api/checksums/find")
    async def checks(req: Request):
        d = await body(req)
        fr = _frames_for(d.get("type"), d.get("prefix"))[-3000:]
        return {"matches": [{"spec": s.to_json(), "ratio": r} for s, r in find_checksums(fr)[:8]]}

    @app.get("/api/series")
    async def series(type: str, field: str):
        t = P.get_type(engine.protocol, type)
        if not any(f.name == field for f in t.fields):
            raise NotFound(f"No field {field!r} in {type!r}")
        pts = []
        for f in engine.frames:
            if P.match_type(engine.protocol, f.data) is t:
                v = P.decode(engine.protocol, f.data, t).get(field)
                if isinstance(v, (int, float)):
                    pts.append([f.t_start, v])
        return {"points": pts[-2000:]}

    # -- protocol --------------------------------------------------------------------
    @app.get("/api/protocol")
    async def get_protocol():
        return engine.protocol.to_json()

    @app.put("/api/protocol")
    async def put_protocol(req: Request):
        d = await body(req)
        engine.protocol = P.Protocol.from_json(d)
        engine.save_protocol()
        return engine.protocol.to_json()

    @app.post("/api/protocol/field")
    async def add_field(req: Request):
        d = await body(req)
        name = (d.get("type") or "").strip()
        if not name:
            raise ValueError("Give the message type a name")
        fd = d.get("field") or {}
        f = P.Field(str(fd["name"]).strip() or "field", int(fd["offset"]), str(fd["type"]),
                    float(fd.get("scale") or 1), str(fd.get("unit", "")))
        f.size()
        try:
            t = P.get_type(engine.protocol, name)
        except KeyError:
            match = d.get("match") or ""
            P.FrameType(name, match).pattern()
            t = P.FrameType(name, match)
            engine.protocol.frame_types.append(t)
        t.fields = [x for x in t.fields if x.name != f.name] + [f]
        engine.save_protocol()
        return engine.protocol.to_json()

    @app.post("/api/protocol/field/delete")
    async def del_field(req: Request):
        d = await body(req)
        t = P.get_type(engine.protocol, d.get("type", ""))
        t.fields = [x for x in t.fields if x.name != d.get("field")]
        if d.get("drop_type_if_empty") and not t.fields:
            engine.protocol.frame_types.remove(t)
        engine.save_protocol()
        return engine.protocol.to_json()

    @app.post("/api/protocol/checksum")
    async def set_checksum(req: Request):
        d = await body(req)
        engine.protocol.checksum = ChecksumSpec.from_json(d.get("spec"))
        engine.save_protocol()
        return engine.protocol.to_json()

    @app.post("/api/protocol/decode")
    async def decode(req: Request):
        d = await body(req)
        f = engine.frame(int(d["frame_id"]))
        t = P.match_type(engine.protocol, f.data)
        cs = None
        if engine.protocol.checksum:
            cs = check(engine.protocol.checksum, f.data)
        return {"type": t.name if t else None, "values": P.decode(engine.protocol, f.data, t) if t else {},
                "fields": [x.__dict__ for x in t.fields] if t else [], "checksum_ok": cs}

    @app.post("/api/compose")
    async def compose(req: Request):
        d = await body(req)
        base = engine.frame(int(d["base_frame_id"])).data if d.get("base_frame_id") is not None else None
        out = P.compose(engine.protocol, d["type"], d.get("values") or {}, base)
        return {"hex": out.hex(" ").upper()}

    @app.post("/api/finalize")
    async def finalize(req: Request):
        d = await body(req)
        return {"hex": P.finalize(engine.protocol, parse_hex(d.get("hex", ""))).hex(" ").upper()}

    @app.post("/api/verdict")
    async def verdict(req: Request):
        d = await body(req)
        data = parse_hex(d.get("hex", ""))
        t = P.get_type(engine.protocol, d["type"]) if d.get("type") else P.match_type(engine.protocol, data)
        if t is None:
            prefix = data[:4].hex(" ").upper()
            t = P.FrameType(f"Message {prefix}", prefix)
            engine.protocol.frame_types.append(t)
        P.add_verdict(engine.protocol, t.name, data.hex(" ").upper(), bool(d.get("ok")), d.get("note", ""))
        engine.save_protocol()
        return {"type": t.name, "verdicts": t.verdicts}

    # -- sending ---------------------------------------------------------------------
    @app.post("/api/mode")
    async def mode(req: Request):
        engine.set_mode((await body(req)).get("mode", ""))
        return status()

    @app.post("/api/send")
    async def send(req: Request):
        d = await body(req)
        data = await engine.send(d.get("hex", ""), d.get("repeat_ms"), int(d.get("count", 1) or 1))
        return {"sent": data.hex(" ").upper(), "status": status()}

    @app.post("/api/replay")
    async def replay(req: Request):
        d = await body(req)
        n = await engine.replay([int(i) for i in d.get("frame_ids", [])], bool(d.get("keep_timing", True)))
        return {"count": n}

    @app.post("/api/send/stop")
    async def send_stop():
        engine.stop_repeat()
        return status()

    # -- projects + sessions -------------------------------------------------------------
    @app.get("/api/projects")
    async def projects():
        return {"projects": Project.list_projects(app.state.base),
                "current": engine.project.name if engine.project else None}

    @app.post("/api/projects/open")
    async def open_project(req: Request):
        d = await body(req)
        await engine.open_project(Project.open_or_create(d.get("name", ""), base=app.state.base))
        return {"status": status(), "protocol": engine.protocol.to_json()}

    @app.get("/api/sessions")
    async def sessions():
        return {"sessions": engine.project.list_sessions()[::-1]}

    @app.post("/api/sessions/load")
    async def load_session(req: Request):
        await engine.stop()
        try:
            engine.load_session((await body(req)).get("name", ""))
        except FileNotFoundError:
            raise NotFound("Session not found")
        return status()

    # -- export ------------------------------------------------------------------------
    @app.get("/api/export/frames.csv")
    async def export_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "time", "channel", "type", "hex", "flags", "decoded"])
        for f in engine.frames:
            t = P.match_type(engine.protocol, f.data)
            dec = P.decode(engine.protocol, f.data, t) if t else {}
            w.writerow([f.id, f"{f.t_start:.6f}", f.channel, t.name if t else "", f.hex(),
                        " ".join(sorted(f.flags)), json.dumps(dec, ensure_ascii=False) if dec else ""])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="tapline-frames.csv"'})

    @app.get("/api/export/protocol.json")
    async def export_proto():
        return Response(json.dumps(engine.protocol.to_json(), indent=2, ensure_ascii=False),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="protocol.json"'})

    # -- live events -------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        if is_local_request(sock.headers):
            await sock.close(code=1008)
            return
        await sock.accept()
        q = engine.subscribe()
        try:
            await sock.send_json({"type": "status", "status": status()})
            while True:
                ev = await q.get()
                batch, others = [], []
                (batch if ev["type"] == "frame" else others).append(ev)
                await asyncio.sleep(0.1)
                while not q.empty():
                    e = q.get_nowait()
                    (batch if e["type"] == "frame" else others).append(e)
                for e in others:
                    if e["type"] == "reframed":
                        batch = []  # client reloads everything
                    await sock.send_json(e)
                if batch:
                    items = []
                    for e in batch:
                        f = e["frame"]
                        t = P.match_type(engine.protocol, bytes.fromhex(f["hex"].replace(" ", "")))
                        items.append({**f, "type": t.name if t else None})
                    await sock.send_json({"type": "frames", "items": items})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            engine.unsubscribe(q)

    if STATIC.exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app

import asyncio

import pytest

from tapline.engine import Engine, SendNotAllowed, EngineError
from tapline.framing import GapRule, LengthRule
from tapline.model import DISP_TO_CTRL, SENT
from tapline.project import Project


@pytest.fixture
def engine(tmp_path):
    e = Engine(Project.open_or_create("t", base=tmp_path))
    e.rule = LengthRule(b"\x5a\xa5", 2)
    yield e


async def wait_for(cond, timeout=4):
    t = 0
    while not cond():
        await asyncio.sleep(0.02)
        t += 0.02
        if t > timeout:
            raise AssertionError("timeout")


async def test_engine_receives_frames(engine):
    q = engine.subscribe()
    await engine.start("simulator", speed=20)
    await wait_for(lambda: len(engine.frames) >= 5)
    await engine.stop()
    assert all(not f.flags for f in engine.frames[:5])
    kinds = set()
    while not q.empty():
        kinds.add(q.get_nowait()["type"])
    assert {"frame", "status"} <= kinds


async def test_send_requires_send_mode(engine):
    await engine.start("simulator", speed=20)
    with pytest.raises(SendNotAllowed):
        await engine.send("5A A5 03 30 32 65")
    await engine.stop()


async def test_send_rejects_bad_hex(engine):
    await engine.start("simulator", speed=20)
    engine.set_mode("send")
    for bad in ["zz", "5A A", "", "   "]:
        with pytest.raises(ValueError):
            await engine.send(bad)
    await engine.stop()


async def test_send_without_source(engine):
    engine.set_mode("send")
    with pytest.raises(EngineError):
        await engine.send("01")


async def test_send_to_simulator_gets_reply(engine):
    await engine.start("simulator", speed=1)
    engine.set_mode("send")
    await engine.send("0x5A,0xA5,03,30,32,65")
    await wait_for(lambda: any(f.channel == DISP_TO_CTRL for f in engine.frames))
    assert any(f.channel == SENT and f.data == bytes.fromhex("5AA5033032 65".replace(" ", "")) for f in engine.frames)
    await engine.stop()


async def test_repeat_and_stop(engine):
    await engine.start("simulator", speed=1)
    engine.set_mode("send")
    await engine.send("5A A5 03 30 32 65", repeat_ms=30)
    await asyncio.sleep(0.25)
    engine.stop_repeat()
    n = sum(c.channel == SENT for c in engine.chunks)
    await asyncio.sleep(0.1)
    assert n >= 3 and sum(c.channel == SENT for c in engine.chunks) == n
    engine.set_mode("sniff")  # leaving send mode also stops any repeat
    await engine.stop()


async def test_set_rule_reframes(engine):
    q = engine.subscribe()
    await engine.start("simulator", speed=40)
    await wait_for(lambda: len(engine.frames) >= 6)
    await engine.stop()
    engine.set_rule(GapRule(1))
    assert len(engine.frames) > 0
    assert any(m["type"] == "reframed" for m in iter_q(q))
    assert engine.protocol.framing["kind"] == "gap"


def iter_q(q):
    while not q.empty():
        yield q.get_nowait()


async def test_session_written_to_project(engine):
    await engine.start("simulator", speed=40)
    await wait_for(lambda: len(engine.chunks) >= 3)
    engine.add_marker("shows 25")
    await engine.stop()
    name = engine.project.list_sessions()[-1]
    chunks, markers, meta = engine.project.load_session(name)
    assert len(chunks) >= 3 and markers[0].label == "shows 25" and meta["source"] == "simulator"
    e2 = Engine(engine.project)
    e2.load_session(name)
    assert e2.markers[0].label == "shows 25" and e2.frames


async def test_gap_rule_flushes_idle_frame(engine):
    engine.rule = GapRule(5)
    await engine.start("simulator", speed=1)
    await wait_for(lambda: len(engine.frames) >= 1, timeout=2)  # first frame emitted after idle, not next chunk
    await engine.stop()


class BrokenWriteSource:
    """A source whose port disappears when we try to send."""
    name = "broken"
    error = None

    async def start(self):
        import asyncio as _a
        self._q = _a.Queue()

    async def stop(self):
        await self._q.put(None)

    async def chunks(self):
        while True:
            c = await self._q.get()
            if c is None:
                return
            yield c

    async def write(self, data):
        from tapline.sources.base import SourceError
        raise SourceError("COM5 was disconnected.")


async def test_repeat_failure_is_reported(engine, monkeypatch):
    import tapline.engine as E
    monkeypatch.setattr(E, "SimulatorSource", lambda speed=1.0: BrokenWriteSource())
    q = engine.subscribe()
    await engine.start("simulator")
    engine.set_mode("send")
    await engine.send("01 02", repeat_ms=20)
    await asyncio.sleep(0.15)
    assert engine.status()["repeating"] is False
    events = list(iter_q(q))
    assert any(e["type"] == "error" and "disconnected" in e["message"] for e in events)
    await engine.stop()



async def test_rs485_modbus_demo_labels_roles(engine):
    from tapline.model import MASTER, SLAVE
    await engine.start("sim-modbus", speed=10)
    await wait_for(lambda: len(engine.frames) >= 4)
    await engine.stop()
    assert engine.link == "rs485"
    assert engine.status()["rule"]["kind"] == "modbus"
    assert [f.channel for f in engine.frames[:4]] == [MASTER, SLAVE, MASTER, SLAVE]


async def test_send_and_wait_returns_reply(engine):
    from tapline import modbus as M
    await engine.start("sim-modbus", speed=0.2)
    engine.set_mode("send")
    replies = await engine.send(M.build(1, 6, 2, 260).hex(), wait_ms=400, return_replies=True)
    assert any(M.decode(f.data, prefer="response") and M.decode(f.data, prefer="response")["fn"] == 6 for f in replies)
    await engine.stop()


async def test_serial_link_type_recorded(engine):
    await engine.start("simulator", speed=20, link="rs232")
    assert engine.status()["link"] == "rs232"
    await engine.stop()

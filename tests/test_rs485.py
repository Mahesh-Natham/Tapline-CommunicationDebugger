import asyncio

from tapline import modbus as M
from tapline.framing import ModbusRule, frame_chunks
from tapline.model import Chunk, Frame, MASTER, SLAVE, BUS
from tapline.rs485 import RoleTracker
from tapline.sources.modbus_sim import ModbusSimSource, modbus_sim_bytes


def F(i, t, data):
    return Frame(i, t, t, BUS, data)


def test_tracker_uses_modbus_shape():
    req = M.build(1, 3, 0, 2)
    resp = M.with_crc(bytes([1, 3, 4, 0, 1, 0, 2]))
    tr = RoleTracker()
    assert [tr.label(F(i, t, d)) for i, (t, d) in enumerate([(0, req), (0.02, resp), (0.5, req), (0.52, resp)])] == [MASTER, SLAVE, MASTER, SLAVE]


def test_tracker_alternates_for_unknown_protocols():
    tr = RoleTracker(reply_window_s=0.2)
    labels = [tr.label(F(i, t, b"\x10\x20")) for i, t in enumerate([0, 0.05, 1.0, 1.05, 2.0, 3.0])]
    assert labels == [MASTER, SLAVE, MASTER, SLAVE, MASTER, MASTER]


def test_modbus_sim_bytes_valid():
    chunks = modbus_sim_bytes(2.0)
    frames = frame_chunks(chunks, ModbusRule())
    assert len(frames) >= 6 and all(not f.flags for f in frames)
    tr = RoleTracker()
    roles = [tr.label(f) for f in frames]
    assert roles[:4] == [MASTER, SLAVE, MASTER, SLAVE]
    regs = M.decode(frames[1].data, prefer="response")["registers"]
    assert len(regs) == 10 and regs[0] == 235


async def test_modbus_sim_answers_writes_and_reads():
    src = ModbusSimSource(speed=1)
    await src.start()
    await src.write(M.build(1, 6, 2, 250))
    await src.write(M.build(1, 3, 2, 1))
    await src.write(M.build(9, 3, 0, 1))          # nobody at address 9: silence
    await src.write(M.build(1, 3, 500, 1))        # out of range: exception 2
    got = []

    async def collect():
        async for c in src.chunks():
            d = M.decode(c.data, prefer="response")
            if d and d["kind"] in ("response", "exception") and len(d.get("registers") or []) != 10:
                got.append(d)
            if len(got) >= 3:
                return
    await asyncio.wait_for(collect(), 5)
    await src.stop()
    assert got[0]["fn"] == 6 and got[0]["value"] == 250
    assert got[1]["registers"] == [250]
    assert got[2]["kind"] == "exception" and got[2]["code"] == 2

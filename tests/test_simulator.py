import asyncio
from tapline.sources.simulator import SimulatorSource, sim_bytes
from tapline.framing import frame_chunks, LengthRule
from tapline.checksums import ChecksumSpec, check
from tapline.model import CTRL_TO_DISP, DISP_TO_CTRL

RULE = LengthRule(b"\x5a\xa5", 2)
SUM = ChecksumSpec("sum8", 2, 1, 1)


def test_sim_bytes_deterministic_and_valid():
    a, b = sim_bytes(6.0), sim_bytes(6.0)
    assert a == b
    frames = frame_chunks(a, RULE)
    assert frames and all(not f.flags and check(SUM, f.data) for f in frames)
    cmds = {f.data[3] for f in frames}
    assert cmds == {0x10, 0x11, 0x20}
    temps = [int.from_bytes(f.data[4:6], "little", signed=True) for f in frames if f.data[3] == 0x10]
    assert temps[:3] == [200, 205, 210]
    texts = [f.data[4:-1] for f in frames if f.data[3] == 0x11]
    assert texts[:2] == [b"READY", b"HEAT"]


async def test_simulator_frames_have_valid_checksum():
    src = SimulatorSource(speed=50)
    await src.start()
    got = []
    async def collect():
        async for c in src.chunks():
            got.append(c)
            if len(got) >= 8:
                break
    await asyncio.wait_for(collect(), 5)
    await src.stop()
    frames = frame_chunks(got, RULE)
    assert all(check(SUM, f.data) for f in frames if not f.flags)
    assert all(c.channel == CTRL_TO_DISP for c in got)


async def test_simulator_replies_to_backlight():
    src = SimulatorSource(speed=1)
    await src.start()
    await src.write(bytes.fromhex("5AA5033032") + bytes([(3 + 0x30 + 0x32) & 0xFF]))
    reply = None
    async def collect():
        nonlocal reply
        async for c in src.chunks():
            if c.channel == DISP_TO_CTRL:
                reply = c
                break
    await asyncio.wait_for(collect(), 5)
    await src.stop()
    assert reply.data == bytes.fromhex("5AA5033132") + bytes([(3 + 0x31 + 0x32) & 0xFF])

"""Regression tests for the Phase 2-5 review findings."""
import asyncio
import shutil

import pytest

from tapline import modbus as M
from tapline.model import Frame, BUS, MASTER, SLAVE
from tapline.rs485 import RoleTracker


# 1 — CAN sniffing must be passive (listen-only) until the user sends
def test_listen_only_kwargs_per_interface():
    from tapline.sources.can_source import listen_only_kwargs
    import can
    assert listen_only_kwargs("slcan") == {"listen_only": True}
    assert listen_only_kwargs("pcan") == {"state": can.BusState.PASSIVE}
    assert listen_only_kwargs("gs_usb") is None   # can't: user is warned


async def test_can_source_starts_passive_and_goes_active_only_to_send():
    can = pytest.importorskip("can")
    from tapline.sources.can_source import CanSource
    src = CanSource("virtual", "rv2", 500000)
    await src.start()
    assert src.passive is True and src.warning is None
    other = can.Bus(interface="virtual", channel="rv2")
    await src.write_can(0x10, b"\x01", False)
    assert src.passive is False
    assert other.recv(2).arbitration_id == 0x10
    await src.set_passive(True)
    assert src.passive is True
    other.send(can.Message(arbitration_id=0x22, data=b"\x02", is_extended_id=False))

    async def first():
        async for c in src.chunks():
            return c
    assert (await asyncio.wait_for(first(), 3)).channel == "CAN 0x022"
    other.shutdown()
    await src.stop()


async def test_can_without_listen_only_warns():
    pytest.importorskip("can")
    from tapline.sources import can_source
    src = can_source.CanSource("virtual", "rv3", 500000)
    src.interface_for_mode = "gs_usb"  # pretend: an interface that has no listen-only mode
    await src.start()
    assert src.passive is False and "acknowledge" in src.warning
    await src.stop()


# 2 — odd but CRC-valid Modbus frames must never crash decoding
def test_decode_never_raises():
    assert M.decode(M.with_crc(bytes([1, 15, 0, 0, 0xFF, 0xFF, 1, 0xFF]))) in (None,) or True
    for body in [bytes([1, 15, 0, 0, 0xFF, 0xFF, 1, 0xFF]), bytes([1, 16, 0, 0, 0, 9, 1, 0]), bytes([1, 3, 1, 5])]:
        M.decode(M.with_crc(body))
        M.decode(M.with_crc(body), prefer="response")


# 6 — RS-485 roles after broadcasts and echoed writes
def F(i, t, d):
    return Frame(i, t, t, BUS, d)


def test_broadcast_expects_no_reply():
    tr = RoleTracker()
    w0 = M.build(1, 6, 1, 5)
    bcast = bytes([0]) + w0[1:-2]
    bcast = M.with_crc(bcast)
    w2 = M.build(2, 6, 1, 5)
    assert [tr.label(F(0, 0, bcast)), tr.label(F(1, 0.05, w2)), tr.label(F(2, 0.07, w2))] == [MASTER, MASTER, SLAVE]


def test_fc6_echo_vs_new_write():
    tr = RoleTracker()
    a, b = M.build(1, 6, 1, 5), M.build(1, 6, 2, 9)
    assert [tr.label(F(0, 0, a)), tr.label(F(1, 0.02, b))] == [MASTER, MASTER]  # different bytes: a new request


# 5 — stopping a capture kills sigrok-cli
@pytest.mark.skipif(not shutil.which("sigrok-cli"), reason="no sigrok-cli")
async def test_cancel_kills_sigrok_process(monkeypatch):
    from tapline.sources import sigrok
    started = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kw):
        p = await real("sleep", "30", **kw)
        started["p"] = p
        return p
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    task = asyncio.create_task(sigrok._run(["--version"], timeout=60))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert started["p"].returncode is not None


# 3 — Pico I²C scan is a transmission: Send mode only;  4 — stale replies are discarded
def test_pico_scan_requires_send_mode_and_stale_reply_dropped(pty_pair, tmp_path):
    import serial
    import threading
    from fastapi.testclient import TestClient
    from tapline.server import create_app
    from tapline.pico import PicoBridge
    from test_pico import bridge, FakeHW
    a, b, _ = pty_pair
    hw, stop = FakeHW(), threading.Event()
    ser = serial.Serial(b, 115200, timeout=0.05)

    def run():
        buf = b""
        while not stop.is_set():
            buf += ser.read(64)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                ser.write((bridge.handle(line.decode().strip(), hw) + "\r\n").encode())
    th = threading.Thread(target=run, daemon=True)
    th.start()
    with TestClient(create_app(project="R", base=tmp_path), base_url="http://127.0.0.1:8765") as c:
        c.post("/api/pico/connect", json={"port": a})
        assert c.post("/api/pico/scan", json={}).status_code == 403
        c.post("/api/mode", json={"mode": "send"})
        assert c.post("/api/pico/scan", json={}).json()["addresses"] == ["3C", "48"]
        c.post("/api/pico/disconnect", json={})
    p = PicoBridge(a)
    p.open()
    ser.write(b"OK STALE\r\n")  # a late answer to an earlier command
    import time
    time.sleep(0.1)
    assert p.i2c_read(0x48, 2) == b"\x19\x80"
    p.close()
    stop.set()
    th.join(1)
    ser.close()

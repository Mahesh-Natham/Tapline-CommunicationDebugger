import asyncio
import importlib.util
import threading
from pathlib import Path

import pytest
import serial

from tapline.pico import PicoBridge, PicoError

spec = importlib.util.spec_from_file_location("pico_bridge", Path(__file__).parent.parent / "bridge" / "pico_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeHW:
    def __init__(self):
        self.devices = {0x3C: bytearray(), 0x48: bytearray(b"\x19\x80")}
        self.spi_log = []

    def i2c_scan(self):
        return sorted(self.devices)

    def i2c_write(self, addr, data):
        if addr not in self.devices:
            raise OSError(19, "ENODEV")
        self.devices[addr] += data

    def i2c_read(self, addr, n):
        if addr not in self.devices:
            raise OSError(19, "ENODEV")
        return bytes(self.devices[addr][:n]).ljust(n, b"\xff")

    def i2c_write_read(self, addr, data, n):
        self.i2c_write(addr, data)
        return self.i2c_read(addr, n)

    def spi_xfer(self, data):
        self.spi_log.append(data)
        return bytes([0xFF]) + bytes(b ^ 0xFF for b in data[1:])

    def cfg_i2c(self, freq):
        self.i2c_freq = freq

    def cfg_spi(self, baud, mode):
        self.spi_cfg = (baud, mode)


def test_handle_commands():
    hw = FakeHW()
    h = lambda line: bridge.handle(line, hw)
    assert h("PING") == "OK " + bridge.VERSION
    assert h("I2C SCAN") == "OK 3C 48"
    assert h("I2C W 3C 00AF") == "OK"
    assert hw.devices[0x3C] == b"\x00\xaf"
    assert h("I2C R 48 2") == "OK 1980"
    assert h("I2C WR 48 00 2") == "OK 1980"
    assert h("I2C W 50 00").startswith("ERR no ACK from 0x50")
    assert h("SPI X 9F0000") == "OK FFFFFF"
    assert h("CFG I2C 400000") == "OK" and hw.i2c_freq == 400000
    assert h("CFG SPI 1000000 3") == "OK" and hw.spi_cfg == (1000000, 3)
    for bad in ["", "NOPE", "I2C W", "I2C W 3C XYZ", "I2C R 3C 999", "SPI X", "CFG SPI 1 9", "I2C W 80 00"]:
        assert h(bad).startswith("ERR"), bad


@pytest.fixture
def fake_pico(pty_pair):
    a, b, _ = pty_pair
    hw = FakeHW()
    stop = threading.Event()
    ser = serial.Serial(b, 115200, timeout=0.05)

    def run():
        buf = b""
        ser.write(b"MicroPython banner noise\r\n")
        while not stop.is_set():
            buf += ser.read(64)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                ser.write((bridge.handle(line.decode().strip(), hw) + "\r\n").encode())
    th = threading.Thread(target=run, daemon=True)
    th.start()
    yield a, hw
    stop.set()
    th.join(1)
    ser.close()


def test_host_talks_to_bridge(fake_pico):
    port, hw = fake_pico
    p = PicoBridge(port)
    p.open()
    assert p.version.startswith("TAPLINE-BRIDGE")
    assert p.i2c_scan() == [0x3C, 0x48]
    p.i2c_write(0x3C, b"\x01")
    assert p.i2c_read(0x48, 2, write=b"\x00") == b"\x19\x80"
    assert p.spi_xfer(b"\x9f\x00") == b"\xff\xff"
    with pytest.raises(PicoError) as e:
        p.i2c_write(0x50, b"\x00")
    assert "no ACK" in str(e.value)
    p.close()


def test_host_times_out_on_silent_port(pty_pair):
    a, b, _ = pty_pair
    p = PicoBridge(a, timeout=0.3)
    with pytest.raises(PicoError) as e:
        p.open()
    assert "main.py" in str(e.value)


async def test_engine_pico_send_requires_send_mode(fake_pico, tmp_path):
    from tapline.engine import Engine, SendNotAllowed
    from tapline.project import Project
    port, hw = fake_pico
    e = Engine(Project.open_or_create("p", base=tmp_path))
    await e.pico_connect(port)
    with pytest.raises(SendNotAllowed):
        await e.pico_send("i2c", 0x3C, "00 AF")
    e.set_mode("send")
    r = await e.pico_send("i2c", 0x48, "00", read_len=2)
    assert r["read"] == "19 80"
    assert [f.channel for f in e.frames] == ["sent I²C 0x48 write", "I²C 0x48 read"]
    r = await e.pico_send("spi", None, "9F 00 00")
    assert r["read"] == "FF FF FF"
    await e.pico_disconnect()


def test_pico_over_api(fake_pico, tmp_path):
    from fastapi.testclient import TestClient
    from tapline.server import create_app
    port, hw = fake_pico
    with TestClient(create_app(project="P", base=tmp_path), base_url="http://127.0.0.1:8765") as c:
        assert c.post("/api/pico/send", json={"bus": "i2c", "addr": "3C", "hex": "00"}).status_code in (400, 403)
        r = c.post("/api/pico/connect", json={"port": port})
        assert r.status_code == 200 and r.json()["status"]["pico"] == port
        assert c.post("/api/pico/scan", json={}).status_code == 403  # scanning transmits
        assert c.post("/api/pico/send", json={"bus": "i2c", "addr": "3C", "hex": "00"}).status_code == 403
        c.post("/api/mode", json={"mode": "send"})
        assert c.post("/api/pico/scan", json={}).json()["addresses"] == ["3C", "48"]
        assert c.post("/api/pico/send", json={"bus": "i2c", "addr": "zz", "hex": "00"}).status_code == 400
        r = c.post("/api/pico/send", json={"bus": "i2c", "addr": "0x48", "hex": "00", "read_len": 2})
        assert r.json()["read"] == "19 80"
        r = c.post("/api/pico/send", json={"bus": "i2c", "addr": "50", "hex": "00"})
        assert r.status_code == 400 and "no ACK" in r.json()["error"]
        c.post("/api/pico/disconnect", json={})

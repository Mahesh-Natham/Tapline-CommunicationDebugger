"""Fixes from the first real-hardware test (CH343 adapter: HTerm saw data, Tapline did not)."""
import serial

from tapline.model import SerialConfig
from tapline.sources import serial_source


def test_port_opens_with_dtr_and_rts_off(monkeypatch):
    events = []

    class FakeSerial:
        def __init__(self, port, baud, **kw):
            events.append(("init", port))
            self._dtr = self._rts = True  # pyserial's default is ON

        @property
        def dtr(self): return self._dtr

        @dtr.setter
        def dtr(self, v): self._dtr = v; events.append(("dtr", v))

        @property
        def rts(self): return self._rts

        @rts.setter
        def rts(self, v): self._rts = v; events.append(("rts", v))

        def open(self):
            events.append(("open", self._dtr, self._rts))

    monkeypatch.setattr(serial_source.serial, "Serial", FakeSerial)
    serial_source._open(SerialConfig("COM3", 9600))
    assert events[0] == ("init", None)            # not opened by the constructor
    assert events[-1] == ("open", False, False)   # both lines already off when the port opens


def test_status_counts_raw_bytes(tmp_path):
    from fastapi.testclient import TestClient
    from tapline.server import create_app
    import time
    with TestClient(create_app(project="B", base=tmp_path), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/status").json()["bytes"] == 0
        c.post("/api/start", json={"source": "simulator", "speed": 50})
        t0 = time.time()
        while c.get("/api/status").json()["bytes"] == 0 and time.time() - t0 < 5:
            time.sleep(0.05)
        assert c.get("/api/status").json()["bytes"] > 0
        c.post("/api/stop", json={})

import time

import pytest
from fastapi.testclient import TestClient

from tapline.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(project="Bench", base=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.put("/api/framing", json={"rule": {"kind": "length", "header": "5A A5", "len_offset": 2}})
        yield c
        c.post("/api/stop", json={})


def wait_frames(c, n, timeout=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get("/api/frames?limit=2000").json()
        if r["total"] >= n:
            return r
        time.sleep(0.05)
    raise AssertionError("no frames")


def start_sim(c, speed=40):
    r = c.post("/api/start", json={"source": "simulator", "speed": speed})
    assert r.status_code == 200, r.text


def test_start_simulator_and_get_frames(client):
    start_sim(client)
    r = wait_frames(client, 5)
    item = r["items"][0]
    assert {"id", "t_start", "channel", "hex", "flags", "type"} <= set(item)
    assert client.get("/api/status").json()["running"] is True


def test_frames_endpoint_paginates(client):
    start_sim(client, speed=200)
    wait_frames(client, 30)
    client.post("/api/stop", json={})
    r = client.get("/api/frames?limit=99999").json()
    assert len(r["items"]) <= 2000
    r = client.get("/api/frames?limit=5").json()
    assert len(r["items"]) == 5 and r["items"][-1]["id"] == r["total"] - 1
    first = r["items"][0]["id"]
    older = client.get(f"/api/frames?before={first}&limit=3").json()["items"]
    assert [f["id"] for f in older] == [first - 3, first - 2, first - 1]
    newer = client.get("/api/frames?after=2&limit=2").json()["items"]
    assert [f["id"] for f in newer] == [3, 4]


def test_start_bad_port_returns_friendly_error(client):
    r = client.post("/api/start", json={"source": "serial", "config": {"port": "/dev/nope-x", "baud": 9600}})
    assert r.status_code == 400
    assert "not found" in r.json()["error"]


def test_send_forbidden_in_sniff_mode(client):
    start_sim(client)
    r = client.post("/api/send", json={"hex": "5A A5 03 30 32 65"})
    assert r.status_code == 403
    client.post("/api/mode", json={"mode": "send"})
    assert client.post("/api/send", json={"hex": "zz"}).status_code == 400
    assert client.post("/api/send", json={"hex": "5A A5 03 30 32 65"}).status_code == 200


def test_ws_streams_frames(client):
    start_sim(client, speed=40)
    with client.websocket_connect("/ws", headers={"Host": "127.0.0.1:8765"}) as ws:
        first = ws.receive_json()
        assert first["type"] == "status"
        for _ in range(50):
            m = ws.receive_json()
            if m["type"] == "frames":
                assert m["items"] and "hex" in m["items"][0]
                break
        else:
            raise AssertionError("no frames batch")


def test_search_endpoint(client):
    start_sim(client, speed=100)
    wait_frames(client, 10)
    client.post("/api/stop", json={})
    r = client.get("/api/search", params={"q": "20.5"}).json()
    assert any(h["encoding"] == "x10:i16le" for h in r["hits"])
    assert client.get("/api/search", params={"q": ""}).json()["hits"] == []


def test_markers_and_nearby(client):
    start_sim(client, speed=20)
    wait_frames(client, 3)
    r = client.post("/api/markers", json={"label": "shows 20.0"})
    assert r.status_code == 200
    assert "changed" in client.get("/api/markers/0/nearby").json()
    assert client.get("/api/markers/99/nearby").status_code == 404


def test_protocol_field_decode_series_compose_verdict(client):
    start_sim(client, speed=100)
    wait_frames(client, 12)
    client.post("/api/stop", json={})
    r = client.post("/api/protocol/field", json={"type": "temperature", "match": "5A A5 ?? 10",
                                               "field": {"name": "temp", "offset": 4, "type": "i16le", "scale": 0.1, "unit": "°C"}})
    assert r.status_code == 200
    found = client.post("/api/checksums/find", json={}).json()["matches"]
    assert found and found[0]["spec"]["algo"] == "sum8"
    client.post("/api/protocol/checksum", json={"spec": found[0]["spec"]})
    frames = client.get("/api/frames?limit=2000").json()["items"]
    tf = next(f for f in frames if f["type"] == "temperature")
    assert client.post("/api/protocol/decode", json={"frame_id": tf["id"]}).json()["values"]["temp"] == 20.0
    series = client.get("/api/series", params={"type": "temperature", "field": "temp"}).json()["points"]
    assert len(series) >= 3 and series[0][1] == 20.0
    hx = client.post("/api/compose", json={"type": "temperature", "values": {"temp": 30}, "base_frame_id": tf["id"]}).json()["hex"]
    assert hx == "5A A5 04 10 2C 01 41"
    assert client.post("/api/finalize", json={"hex": "5A A5 00 10 2C 01 00"}).json()["hex"] == "5A A5 04 10 2C 01 41"
    client.post("/api/verdict", json={"hex": hx, "ok": True, "note": "shows 30"})
    p = client.get("/api/protocol").json()
    assert p["frame_types"][0]["verdicts"][0]["ok"] is True
    v = client.get("/api/variability", params={"type": "temperature"}).json()
    assert v["count"] >= 3 and v["offsets"][0]["kind"] == "constant"


def test_series_endpoint(client):
    assert client.get("/api/series", params={"type": "nope", "field": "x"}).status_code == 404


def test_guess_framing_and_diff(client):
    start_sim(client, speed=100)
    wait_frames(client, 20)
    client.post("/api/stop", json={})
    g = client.post("/api/framing/guess", json={}).json()["candidates"]
    assert g[0]["rule"]["kind"] == "length"
    r = client.post("/api/diff", json={"a": 0, "b": 1}).json()
    assert len(r["diff"]) > 0


def test_export_csv(client):
    start_sim(client, speed=100)
    wait_frames(client, 5)
    client.post("/api/stop", json={})
    r = client.get("/api/export/frames.csv")
    assert r.status_code == 200 and r.text.splitlines()[0].startswith("id,time,channel")
    assert client.get("/api/export/protocol.json").status_code == 200


def test_sessions_and_projects(client):
    start_sim(client, speed=100)
    wait_frames(client, 5)
    client.post("/api/stop", json={})
    names = client.get("/api/sessions").json()["sessions"]
    assert names
    assert client.post("/api/sessions/load", json={"name": names[0]}).status_code == 200
    assert client.post("/api/projects/open", json={"name": "../evil"}).status_code == 400
    assert client.post("/api/projects/open", json={"name": "Other"}).json()["status"]["project"] == "Other"
    assert "Other" in client.get("/api/projects").json()["projects"]


def test_detect_on_simulator(client):
    r = client.post("/api/detect", json={"port": "simulator"})
    assert r.status_code == 200
    c = r.json()["candidates"]
    assert c[0]["label"] == "115200 8N1"


def test_options_and_ports(client):
    o = client.get("/api/options").json()
    assert 115200 in o["bauds"] and "8N1" in o["formats"] and o["parity"][0] == "N"
    assert isinstance(client.get("/api/ports").json()["ports"], list)


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Tapline" in r.text


def test_modbus_demo_summaries_build_and_wait(client):
    r = client.post("/api/start", json={"source": "sim-modbus", "speed": 10})
    assert r.status_code == 200 and r.json()["link"] == "rs485"
    items = wait_frames(client, 4)["items"]
    assert items[0]["modbus"]["summary"].startswith("Read holding registers")
    assert items[1]["channel"] == "slave→master" and items[1]["modbus"]["registers"][0] == 235
    b = client.post("/api/modbus/build", json={"addr": 1, "fn": 6, "start": 2, "qty": 230}).json()
    assert b["hex"].replace(" ", "") == "01060002 00E6".replace(" ", "") + b["hex"].replace(" ", "")[-4:]
    assert client.post("/api/modbus/build", json={"addr": 0, "fn": 3, "start": 0, "qty": 1}).status_code == 400
    client.post("/api/mode", json={"mode": "send"})
    r = client.post("/api/send", json={"hex": b["hex"], "wait_ms": 300}).json()
    assert r["replies"] and r["replies"][0]["modbus"]["fn"] == 6

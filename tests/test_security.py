"""Only this computer's own Tapline page may drive the API (no cross-site requests)."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tapline.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(project="Sec", base=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c
        c.post("/api/stop", json={})


def test_text_plain_post_is_rejected(client):
    r = client.post("/api/start", content='{"source":"simulator"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    assert client.get("/api/status").json()["running"] is False


def test_foreign_origin_is_rejected(client):
    for path, body in [("/api/start", {"source": "simulator"}), ("/api/mode", {"mode": "send"}),
                       ("/api/send", {"hex": "01"})]:
        r = client.post(path, json=body, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, path
    assert client.get("/api/status").json()["mode"] == "sniff"


def test_foreign_host_is_rejected_dns_rebinding(client):
    r = client.get("/api/status", headers={"Host": "evil.example:8765"})
    assert r.status_code == 403


def test_own_origin_is_allowed(client):
    r = client.post("/api/mode", json={"mode": "sniff"}, headers={"Origin": "http://localhost:8765"})
    assert r.status_code == 200
    r = client.post("/api/mode", json={"mode": "sniff"}, headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


def test_ws_foreign_origin_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"Origin": "https://evil.example", "Host": "127.0.0.1:8765"}) as ws:
            ws.receive_json()


def test_ws_own_origin_ok(client):
    with client.websocket_connect("/ws", headers={"Origin": "http://localhost:8765", "Host": "127.0.0.1:8765"}) as ws:
        assert ws.receive_json()["type"] == "status"

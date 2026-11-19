from tests.test_server import client, start_sim, wait_frames  # noqa: F401  (fixture reuse)


def test_ai_export_and_raw_log(client):
    assert client.get("/api/export/log.jsonl").status_code == 404  # nothing recorded yet
    start_sim(client, speed=200)
    wait_frames(client, 20)
    client.post("/api/markers", json={"label": "display shows 25", "note": "pressed UP"})
    wait_frames(client, 30)
    client.post("/api/stop", json={})

    r = client.get("/api/export/ai.md")
    assert r.status_code == 200
    assert "for-ai.md" in r.headers["content-disposition"]
    md = r.text
    for part in ("# Tapline capture report", "For the AI reading this", "## Kinds of message",
                 "display shows 25", "pressed UP", "## Message log", "5A A5"):
        assert part in md, part
    assert "map   " in md  # byte map line

    log = client.get("/api/export/log.jsonl")
    assert log.status_code == 200
    lines = log.text.splitlines()
    assert '"type": "meta"' in lines[0] and any('"type": "chunk"' in l for l in lines)
    assert client.get("/api/status").json()["session"].endswith(".jsonl")

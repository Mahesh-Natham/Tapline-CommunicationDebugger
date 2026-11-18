"""Long captures must not freeze the app when the user clicks around."""
import time

import pytest
from fastapi.testclient import TestClient

from tapline import protocol as P
from tapline.model import Frame, CTRL_TO_DISP
from tapline.server import create_app


@pytest.fixture
def big(tmp_path):
    app = create_app(project="Big", base=tmp_path)
    eng = app.state.engine
    types = [P.FrameType(f"type{i:02d}", f"5A A5 ?? {0x40 + i:02X}", [P.Field("v", 4, "i16le", 0.1)]) for i in range(30)]
    eng.protocol.frame_types = types
    eng.frames = [Frame(i, i * 0.01, i * 0.01, CTRL_TO_DISP,
                        bytes([0x5A, 0xA5, 4, 0x40 + i % 30, i & 0xFF, 0, 0])) for i in range(128_000)]
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def timed(fn):
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0


def test_series_is_fast_on_long_capture(big):
    big.get("/api/series", params={"type": "type29", "field": "v"})  # warm-up allowed
    r, dt = timed(lambda: big.get("/api/series", params={"type": "type07", "field": "v"}))
    assert r.status_code == 200 and len(r.json()["points"]) == 200
    assert dt < 0.15, f"series took {dt:.2f}s"


def test_type_filters_are_fast_on_long_capture(big):
    big.get("/api/variability", params={"type": "type01"})
    r, dt = timed(lambda: big.get("/api/variability", params={"type": "type05"}))
    assert r.json()["count"] == 4267  # 128000 frames / 30 types
    assert dt < 0.3, f"variability took {dt:.2f}s"
    r, dt = timed(lambda: big.get("/api/frames", params={"limit": 2000}))
    assert dt < 0.3, f"frames took {dt:.2f}s"

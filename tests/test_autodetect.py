import pytest
from tapline.autodetect import detect, score, BAUDS, FORMATS
from tapline.uartsim import reinterpret
from tapline.sources.simulator import sim_bytes
from tapline.model import SerialConfig

STREAM = b"".join(c.data for c in sim_bytes(4.0))


def fake_reader(true: SerialConfig, hide_errors: bool):
    data = STREAM if true.bytesize == 8 else bytes(b & 0x7F for b in STREAM)

    async def read(cfg: SerialConfig, seconds: float):
        got, err = reinterpret(data, true, cfg, errors_as_zero=hide_errors)
        return got, (0 if hide_errors else err)
    return read


@pytest.mark.parametrize("baud,fmt,hide", [
    (115200, "8N1", True), (9600, "8N1", True), (57600, "8N1", True),
    (115200, "8N1", False), (19200, "8E1", False), (9600, "8O1", False),
])
async def test_detect_finds_true_settings(baud, fmt, hide):
    true = SerialConfig.from_format("p", baud, fmt)
    seen = []
    cands = await detect(fake_reader(true, hide), "p", window_s=0.01, progress=lambda *a: seen.append(a))
    assert len(cands) == 3
    top = cands[0]
    assert (top.config.baud, top.config.label().split()[1]) == (baud, fmt)
    assert top.score > cands[1].score
    assert top.preview_hex
    assert seen and seen[-1][0] == seen[-1][1]


def test_score_empty_is_zero():
    assert score(b"") == 0
    assert score(bytes(100)) < 0.2
    assert score(STREAM) > 0.6


def test_lists():
    assert 115200 in BAUDS and len(BAUDS) == 15
    assert FORMATS[0] == "8N1"

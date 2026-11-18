from tapline.diff import diff_frames, variability
from tapline.model import Frame, CTRL_TO_DISP


def test_diff_frames():
    assert diff_frames(b"\x01\x02\x03", b"\x01\x09") == ["same", "changed", "removed"]
    assert diff_frames(b"\x01", b"\x01\x02") == ["same", "added"]


def test_variability_detects_counter_and_constant():
    frames = []
    for i in range(50):
        rare = 1 if i == 25 else 0
        frames.append(Frame(i, i, i, CTRL_TO_DISP, bytes([0x5a, i & 0xFF, (i * 37) % 251, rare])))
    v = variability(frames)
    kinds = [x["kind"] for x in v]
    assert kinds == ["constant", "counter", "varies", "rare"]
    assert v[0]["distinct"] == 1
    assert variability([]) == []

import pytest
from tapline.project import Project
from tapline.model import Chunk, Marker, CTRL_TO_DISP


def test_session_roundtrip(tmp_path):
    pr = Project.open_or_create("bench", base=tmp_path)
    assert pr.root == tmp_path / "bench"
    w = pr.new_session({"source": "simulator"})
    w.write_chunk(Chunk(1.0, CTRL_TO_DISP, b"\x01\x02"))
    w.write_marker(Marker(1.5, "shows 25"))
    w.close()
    names = pr.list_sessions()
    assert names == [w.name]
    chunks, markers, meta = pr.load_session(w.name)
    assert chunks == [Chunk(1.0, CTRL_TO_DISP, b"\x01\x02")]
    assert markers == [Marker(1.5, "shows 25")]
    assert meta["source"] == "simulator"


def test_session_survives_truncated_last_line(tmp_path):
    pr = Project.open_or_create("bench", base=tmp_path)
    w = pr.new_session({})
    w.write_chunk(Chunk(1.0, CTRL_TO_DISP, b"\x01"))
    w.close()
    with open(pr.root / "sessions" / w.name, "a") as f:
        f.write('{"type": "chunk", "t": 2.0, "chan')
    chunks, _, _ = pr.load_session(w.name)
    assert len(chunks) == 1


def test_bad_project_names(tmp_path):
    for bad in ["", "../x", "a/b", "con\0"]:
        with pytest.raises(ValueError):
            Project.open_or_create(bad, base=tmp_path)
    with pytest.raises(ValueError):
        Project.open_or_create("ok", base=tmp_path).load_session("../../etc/passwd")

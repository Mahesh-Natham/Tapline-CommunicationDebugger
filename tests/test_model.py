from tapline.model import Chunk, Frame, Marker, SerialConfig, CTRL_TO_DISP


def test_chunk_roundtrip_json():
    c = Chunk(1.5, CTRL_TO_DISP, b"\x5a\xa5\x00")
    d = c.to_json()
    assert d["data"] == "5aa500"
    assert Chunk.from_json(d) == c


def test_frame_hex():
    f = Frame(id=1, t_start=0, t_end=0, channel=CTRL_TO_DISP, data=b"\x5a\xa5")
    assert f.hex() == "5A A5"


def test_marker_roundtrip():
    m = Marker(2.0, "shows 25", note="n", expected="25")
    assert Marker.from_json(m.to_json()) == m


def test_serialconfig_label():
    assert SerialConfig("COM1", 9600, 8, "E", 1).label() == "9600 8E1"
    assert SerialConfig("COM1").label() == "115200 8N1"
    assert SerialConfig("COM1", stopbits=1.5).label() == "115200 8N1.5"

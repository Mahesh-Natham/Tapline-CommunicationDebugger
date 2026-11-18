from tapline.search import parse_query, find_value, near_marker
from tapline.model import Frame, Marker, CTRL_TO_DISP


def F(i, data, t=None):
    return Frame(i, t if t is not None else i, t if t is not None else i, CTRL_TO_DISP, data)


def test_parse_query_shapes():
    assert parse_query("25°C").numbers == [25]
    assert parse_query("25 C").numbers == [25]
    assert parse_query("0x19").numbers == [25]
    assert parse_query("-3").numbers == [-3]
    assert parse_query("25.5").numbers == [25.5]
    q = parse_query("hello")
    assert q.numbers == [] and q.text == "hello"
    assert parse_query("").numbers == [] and parse_query("").text is None
    assert parse_query("12%").numbers == [12]
    for weird in ["--", ".", "0x", "1e999", "   ", "∞", "25,5"]:
        parse_query(weird)  # must not raise


def test_find_value_i16le_x10():
    hits = find_value([F(0, b"\x5a\xa5\x04\x10\xFA\x00\x0e")], "25")
    assert any(h.encoding == "x10:i16le" and h.offset == 4 for h in hits)


def test_find_value_u16be_and_bcd():
    hits = find_value([F(0, b"\x00\x01\x2c"), F(1, b"\x03\x00")], "300")
    assert any(h.encoding == "u16be" and h.frame_id == 0 and h.offset == 1 for h in hits)
    hits = find_value([F(0, b"\x10\x25\x00")], "25")
    assert any(h.encoding == "bcd" and h.offset == 1 for h in hits)


def test_find_value_negative():
    hits = find_value([F(0, b"\x01\xFD\xFF")], "-3")
    assert any(h.encoding == "i16le" and h.offset == 1 for h in hits)


def test_find_value_marker_boost():
    frames = [F(0, b"\x00\x19\x00\x00", t=0.0), F(1, b"\x00\x19\x00\x00", t=10.0)]
    hits = find_value(frames, "25", markers=[Marker(10.2, "shows 25")])
    best = [h for h in hits if h.encoding == "u8"]
    assert best[0].frame_id == 1


def test_find_ascii():
    hits = find_value([F(0, b"\x01READY\x00")], "ready")
    assert any(h.encoding == "ascii" and h.offset == 1 and h.length == 5 for h in hits)
    hits = find_value([F(0, b"\x0125.5\x00")], "25.5")
    assert any(h.encoding == "ascii" for h in hits)


def test_find_value_hit_cap():
    frames = [F(i, bytes([25] * 20)) for i in range(100)]
    assert len(find_value(frames, "25")) <= 500


def test_near_marker_reports_changed_frames():
    frames = [F(0, b"\x5a\xa5\x04\x10\x01", t=0.0), F(1, b"\x5a\xa5\x04\x10\x01", t=1.0),
              F(2, b"\x5a\xa5\x04\x10\x02", t=5.0), F(3, b"\x5a\xa5\x04\x30\x01", t=5.1)]
    r = near_marker(frames, Marker(5.0, "x"), window_s=1.0)
    assert r["changed"] == [2]
    assert r["new_types"] == ["5A A5 04 30"]

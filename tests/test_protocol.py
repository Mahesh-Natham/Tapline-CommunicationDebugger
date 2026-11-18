from tapline import protocol as P
from tapline.checksums import ChecksumSpec
from tapline.framing import LengthRule, rule_to_json
from helpers import sim_frame


def proto():
    return P.Protocol(
        framing=rule_to_json(LengthRule(b"\x5a\xa5", 2)),
        checksum=ChecksumSpec("sum8", 2, 1, 1),
        frame_types=[
            P.FrameType("temperature", "5A A5 ?? 10", [P.Field("temp", 4, "i16le", 0.1, "°C")]),
            P.FrameType("status", "5A A5 ?? 11", [P.Field("text", 4, "ascii:5")]),
        ],
    )


def test_match_type():
    pr = proto()
    assert P.match_type(pr, sim_frame(0x10, b"\xFA\x00")).name == "temperature"
    assert P.match_type(pr, sim_frame(0x20, b"\x01")) is None


def test_decode_temperature():
    assert P.decode(proto(), sim_frame(0x10, b"\xFA\x00")) == {"temp": 25.0}
    assert P.decode(proto(), sim_frame(0x10, b"\xFE\xFF")) == {"temp": -0.2}
    assert P.decode(proto(), sim_frame(0x11, b"READY")) == {"text": "READY"}
    assert P.decode(proto(), b"\x5a\xa5\x02\x10") == {"temp": None}  # too short, no crash


def test_compose_temperature_fixes_checksum():
    pr = proto()
    out = P.compose(pr, "temperature", {"temp": 30.0}, base=sim_frame(0x10, b"\xFA\x00"))
    assert out == sim_frame(0x10, (300).to_bytes(2, "little"))


def test_compose_from_template_fixes_length():
    pr = proto()
    assert P.compose(pr, "temperature", {"temp": 25.0}) == sim_frame(0x10, b"\xFA\x00")
    assert P.compose(pr, "status", {"text": "HEAT"}) == sim_frame(0x11, b"HEAT ")


def test_protocol_save_load_roundtrip(tmp_path):
    pr = proto()
    P.add_verdict(pr, "temperature", "5A A5 04 10 2C 01 41", True, "shows 30.0")
    path = tmp_path / "protocol.json"
    P.save(pr, path)
    again = P.load(path)
    assert again == pr
    assert again.frame_types[0].verdicts[0]["ok"] is True


def test_load_missing_returns_empty(tmp_path):
    pr = P.load(tmp_path / "nope.json")
    assert pr.frame_types == [] and pr.checksum is None

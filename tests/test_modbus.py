import pytest
from tapline import modbus as M
from tapline.checksums import ALGORITHMS


def crc(b: bytes) -> bytes:
    return b + ALGORITHMS["crc16_modbus"](b).to_bytes(2, "little")


def test_build_known_vector():
    assert M.build(1, 3, 0, 10) == bytes.fromhex("01030000000AC5CD")


def test_build_all_functions_roundtrip():
    for fn, args in [(1, (0, 8)), (2, (4, 3)), (3, (0, 10)), (4, (1, 2)), (5, (3, 1)), (6, (2, 300)),
                     (15, (0, 3, [1, 0, 1])), (16, (5, 2, [10, 65535]))]:
        data = M.build(7, fn, *args)
        d = M.decode(data, prefer="request")
        assert d and d["addr"] == 7 and d["fn"] == fn and d["kind"] == "request"


def test_decode_fc3_request_and_response():
    req = M.decode(bytes.fromhex("01030000000AC5CD"))
    assert req["kind"] == "request" and req["start"] == 0 and req["quantity"] == 10
    assert "Read holding registers" in req["summary"] and "device 1" in req["summary"]
    resp = M.decode(crc(bytes([1, 3, 4, 0x00, 0xFA, 0x01, 0x2C])))
    assert resp["kind"] == "response" and resp["registers"] == [250, 300]


def test_ambiguous_eight_bytes_uses_hint():
    # FC1 reply with 3 data bytes is also 8 bytes long, like an FC1 request
    data = crc(bytes([1, 1, 3, 1, 2, 3]))
    assert M.decode(data, prefer="response")["kind"] == "response"
    assert M.decode(data, prefer="request")["kind"] == "request"


def test_exception_and_writes():
    e = M.decode(crc(bytes([1, 0x83, 2])))
    assert e["kind"] == "exception" and "Illegal data address" in e["summary"]
    w = M.decode(M.build(1, 6, 2, 300))
    assert w["value"] == 300 and "Write single register" in w["summary"]
    c = M.decode(M.build(1, 5, 3, 1))
    assert c["value"] is True


def test_bad_crc_or_junk_is_none():
    assert M.decode(bytes.fromhex("01030000000A0000")) is None
    for junk in [b"", b"\x01", b"\x01\x03", bytes(3)]:
        assert M.decode(junk) is None


def test_build_rejects_bad_values():
    for args in [(0, 3, 0, 1), (248, 3, 0, 1), (1, 3, 0, 0), (1, 3, 0, 126), (1, 6, 0, 70000), (1, 99, 0, 1), (1, 16, 0, 2, [1])]:
        with pytest.raises(ValueError):
            M.build(*args)

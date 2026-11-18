from tapline.checksums import ALGORITHMS, ChecksumSpec, apply_checksum, check, find_checksums, width
from tapline.model import Frame, CTRL_TO_DISP
from helpers import sim_frame


def frames():
    out = []
    for i in range(20):
        out.append(Frame(i, i, i, CTRL_TO_DISP, sim_frame(0x10, (200 + i * 7).to_bytes(2, "little"))))
        out.append(Frame(i, i, i, CTRL_TO_DISP, sim_frame(0x11, b"READY"[: 1 + i % 5])))
    return out


def test_sum8_found_on_sim_frames():
    found = find_checksums(frames())
    assert (ChecksumSpec("sum8", 2, 1, 1), 1.0) in found
    assert found[0][1] == 1.0


def test_nothing_found_on_random():
    import random
    rnd = random.Random(3)
    fr = [Frame(i, i, i, CTRL_TO_DISP, bytes(rnd.randrange(256) for _ in range(8))) for i in range(60)]
    assert find_checksums(fr) == []


def test_apply_checksum_roundtrip():
    spec = ChecksumSpec("sum8", 2, 1, 1)
    good = sim_frame(0x20, b"\x05")
    broken = good[:-1] + b"\x00"
    assert not check(spec, broken)
    assert apply_checksum(spec, broken) == good
    assert check(spec, good)


def test_crc16_modbus_known_vector():
    data = bytes.fromhex("01030000000A")
    assert ALGORITHMS["crc16_modbus"](data) == 0xCDC5
    spec = ChecksumSpec("crc16_modbus", 0, 2, 2, "little")
    assert apply_checksum(spec, data + b"\x00\x00")[-2:] == bytes.fromhex("C5CD")
    assert width("crc16_modbus") == 2 and width("sum8") == 1


def test_crc16_ccitt_false_vector():
    assert ALGORITHMS["crc16_ccitt_false"](b"123456789") == 0x29B1
    assert ALGORITHMS["crc16_xmodem"](b"123456789") == 0x31C3
    assert ALGORITHMS["crc8"](b"123456789") == 0xF4
    assert ALGORITHMS["crc8_maxim"](b"123456789") == 0xA1

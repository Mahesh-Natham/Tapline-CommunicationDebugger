from tapline.framing import (GapRule, DelimiterRule, LengthRule, FixedRule, frame_chunks,
                             StreamFramer, guess_framing, rule_from_json, rule_to_json, MAX_FRAME)
from tapline.model import Chunk, CTRL_TO_DISP, DISP_TO_CTRL
from helpers import sim_frame, sim_stream, gapped_chunks


def test_gap_rule_splits_on_silence():
    chunks = [Chunk(0.000, CTRL_TO_DISP, b"\x01\x02"), Chunk(0.001, CTRL_TO_DISP, b"\x03"),
              Chunk(0.100, CTRL_TO_DISP, b"\x04\x05")]
    frames = frame_chunks(chunks, GapRule(5))
    assert [f.data for f in frames] == [b"\x01\x02\x03", b"\x04\x05"]
    assert frames[0].t_start == 0.0 and frames[0].t_end == 0.001
    assert [f.id for f in frames] == [0, 1]


def test_channels_framed_independently():
    chunks = [Chunk(0.0, CTRL_TO_DISP, b"\x01"), Chunk(0.001, DISP_TO_CTRL, b"\x09"),
              Chunk(0.002, CTRL_TO_DISP, b"\x02")]
    frames = frame_chunks(chunks, GapRule(5))
    assert {(f.channel, f.data) for f in frames} == {(CTRL_TO_DISP, b"\x01\x02"), (DISP_TO_CTRL, b"\x09")}


def test_delimiter_rule_start_only():
    data = b"\xAA\x01\x02\xAA\x03\xAA\x04"
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, data)], DelimiterRule(start=b"\xAA"))
    assert [f.data for f in frames] == [b"\xAA\x01\x02", b"\xAA\x03", b"\xAA\x04"]


def test_delimiter_rule_end_only_and_leading_junk():
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, b"\x01\xFF\xFF\xFF\x02\xFF\xFF\xFF")],
                          DelimiterRule(end=b"\xFF\xFF\xFF"))
    assert [f.data for f in frames] == [b"\x01\xFF\xFF\xFF", b"\x02\xFF\xFF\xFF"]
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, b"\x09\x09\xAA\x01")], DelimiterRule(start=b"\xAA"))
    assert frames[0].data == b"\x09\x09" and "partial" in frames[0].flags


def test_length_rule_sim_protocol():
    data = sim_stream(10)
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, data)], LengthRule(b"\x5a\xa5", 2, 1, 0))
    assert all(not f.flags for f in frames)
    assert b"".join(f.data for f in frames) == data
    assert frames[0].data == sim_frame(0x10, (200).to_bytes(2, "little"))


def test_length_rule_resyncs_after_junk():
    good = sim_frame(0x20, b"\x01")
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, b"\x00\x13" + good + good)], LengthRule(b"\x5a\xa5", 2))
    assert frames[0].data == b"\x00\x13" and "partial" in frames[0].flags
    assert [f.data for f in frames[1:]] == [good, good]


def test_fixed_rule():
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, bytes(range(7)))], FixedRule(3))
    assert [len(f.data) for f in frames] == [3, 3, 1]
    assert "partial" in frames[-1].flags


def test_stream_framer_matches_batch():
    data = sim_stream(5)
    chunks = [Chunk(i * 0.001, CTRL_TO_DISP, data[i:i + 3]) for i in range(0, len(data), 3)]
    rule = LengthRule(b"\x5a\xa5", 2)
    sf = StreamFramer(rule)
    live = []
    for c in chunks:
        live += sf.feed(c)
    live += sf.flush()
    batch = frame_chunks(chunks, rule)
    assert [f.data for f in live] == [f.data for f in batch]


def test_gap_framer_caps_oversize():
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, bytes(10000))], GapRule(5))
    assert len(frames) == 3
    assert all(len(f.data) <= MAX_FRAME for f in frames)
    assert sum(len(f.data) for f in frames) == 10000
    assert all("oversize" in f.flags for f in frames[:2])


def test_rule_json_roundtrip():
    for r in [GapRule(7), DelimiterRule(b"\x5a", b"\xff"), LengthRule(b"\x5a\xa5", 2, 1, 1), FixedRule(4)]:
        assert rule_from_json(rule_to_json(r)) == r


def test_guess_framing_finds_length_rule():
    data = sim_stream(30)
    ranked = guess_framing([Chunk(0, CTRL_TO_DISP, data)])
    rule, score = ranked[0]
    assert isinstance(rule, LengthRule)
    assert rule.header == b"\x5a\xa5" and rule.len_offset == 2 and rule.len_adjust == 0
    assert score > 0.9


def test_guess_framing_with_gaps_and_no_data():
    assert guess_framing([]) == []
    frames = [sim_frame(0x20, bytes([i])) for i in range(10)]
    ranked = guess_framing(gapped_chunks(frames))
    assert ranked[0][1] > 0.9


from tapline.framing import ModbusRule, NativeRule


def _crc(b):
    from tapline.checksums import ALGORITHMS
    return b + ALGORITHMS["crc16_modbus"](b).to_bytes(2, "little")


def test_modbus_rule_splits_merged_request_and_reply():
    req = bytes.fromhex("01030000000AC5CD")
    resp = _crc(bytes([1, 3, 20]) + bytes(range(20)))
    frames = frame_chunks([Chunk(0, CTRL_TO_DISP, req + resp + req)], ModbusRule())
    assert [f.data for f in frames] == [req, resp, req]
    assert all(not f.flags for f in frames)


def test_modbus_rule_resyncs_after_junk_and_split_chunks():
    req = bytes.fromhex("01030000000AC5CD")
    data = b"\xff\x00\x13" + req + _crc(bytes([1, 6, 0, 2, 1, 44]))
    sf = StreamFramer(ModbusRule())
    out = []
    for i in range(len(data)):
        out += sf.feed(Chunk(i * 0.0001, CTRL_TO_DISP, data[i:i + 1]))
    out += sf.flush()
    good = [f.data for f in out if not f.flags]
    assert good == [req, _crc(bytes([1, 6, 0, 2, 1, 44]))]


def test_native_rule_one_chunk_one_frame():
    frames = frame_chunks([Chunk(0, "I²C 0x3C write", b"\x00\xaf"), Chunk(0.0001, "I²C 0x3C write", b"\x01")], NativeRule())
    assert [f.data for f in frames] == [b"\x00\xaf", b"\x01"]
    assert rule_from_json(rule_to_json(ModbusRule())) == ModbusRule()
    assert rule_from_json(rule_to_json(NativeRule())) == NativeRule()

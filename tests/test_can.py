import asyncio

import pytest

from tapline.sources.can_source import parse_can_text, can_channel, channel_to_id, SimCanSource, CanSource
from tapline import protocol as P
from tapline.model import Frame


def test_parse_can_text_shapes():
    assert parse_can_text("123#DEADBEEF") == (0x123, bytes.fromhex("DEADBEEF"), False)
    assert parse_can_text("123#de ad") == (0x123, b"\xde\xad", False)
    assert parse_can_text("0x123 01 02") == (0x123, b"\x01\x02", False)
    assert parse_can_text("18FF50E5#01") == (0x18FF50E5, b"\x01", True)
    assert parse_can_text("7FF#") == (0x7FF, b"", False)
    for bad in ["", "#01", "XYZ#01", "123#0", "123#010203040506070809", "20000000#01", "123#R"]:
        with pytest.raises(ValueError):
            parse_can_text(bad)


def test_channel_names_roundtrip():
    assert can_channel(0x123, False) == "CAN 0x123"
    assert can_channel(0x18FF50E5, True) == "CAN 0x18FF50E5"
    assert channel_to_id("CAN 0x18FF50E5") == (0x18FF50E5, True)
    assert channel_to_id("sent CAN 0x123") == (0x123, False)
    assert channel_to_id("UART TX") is None


def test_frame_type_filtered_by_channel():
    proto = P.Protocol(frame_types=[P.FrameType("rpm", "", [P.Field("rpm", 0, "u16be")], channel="CAN 0x100")])
    assert P.match_type(proto, b"\x0b\xb8", "CAN 0x100").name == "rpm"
    assert P.match_type(proto, b"\x0b\xb8", "CAN 0x101") is None
    again = P.Protocol.from_json(proto.to_json())
    assert again.frame_types[0].channel == "CAN 0x100"


async def test_sim_can_emits_and_replies():
    src = SimCanSource(speed=10)
    await src.start()
    await src.write_can(0x300, b"\x01", False)
    seen = set()

    async def collect():
        async for c in src.chunks():
            seen.add(c.channel)
            if {"CAN 0x100", "CAN 0x101", "CAN 0x301"} <= seen:
                return
    await asyncio.wait_for(collect(), 5)
    await src.stop()


async def test_real_can_source_on_virtual_bus():
    can = pytest.importorskip("can")
    src = CanSource("virtual", "tapline-test", 500000)
    await src.start()
    other = can.Bus(interface="virtual", channel="tapline-test")
    other.send(can.Message(arbitration_id=0x123, data=b"\x01\x02", is_extended_id=False))

    async def first():
        async for c in src.chunks():
            return c
    c = await asyncio.wait_for(first(), 3)
    assert c.channel == "CAN 0x123" and c.data == b"\x01\x02"
    await src.write_can(0x456, b"\xaa", False)
    m = other.recv(2)
    assert m.arbitration_id == 0x456 and bytes(m.data) == b"\xaa"
    other.shutdown()
    await src.stop()


async def test_can_source_bad_interface_is_friendly():
    pytest.importorskip("can")
    from tapline.sources.base import SourceError
    with pytest.raises(SourceError):
        await CanSource("nope-interface", "x", 500000).start()

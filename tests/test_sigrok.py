import shutil

import pytest

from tapline import la_sim
from tapline.sources import sigrok as SG

need = pytest.mark.skipif(not shutil.which("sigrok-cli"), reason="sigrok-cli not installed")


def test_parse_scan_output():
    text = ("The following devices were found:\n"
            "demo - Demo device with 13 channels: D0 D1 D2 D3 D4 D5 D6 D7 A0 A1 A2 A3 A4\n"
            "fx2lafw:conn=1.5 - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7\n")
    devs = SG.parse_scan(text)
    assert devs[1] == {"driver": "fx2lafw:conn=1.5", "description": "Saleae Logic with 8 channels",
                       "channels": ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]}


@need
async def test_decode_uart(tmp_path):
    w = la_sim.Wave(1_000_000, 0.01)
    w.uart(0, 0.001, b"\x5a\xa5\x03\x20\x01\x24", 115200)
    p = tmp_path / "u.sr"
    w.write_sr(p, ["TX"])
    chunks = await SG.decode(p, {"protocol": "uart", "rx": "TX", "baudrate": 115200})
    assert b"".join(c.data for c in chunks) == b"\x5a\xa5\x03\x20\x01\x24"
    assert chunks[0].channel == "UART TX" and 0.0009 < chunks[0].t < 0.0012


@need
async def test_decode_i2c_write_and_register_read(tmp_path):
    w = la_sim.Wave(1_000_000, 0.02, n=2)
    w.i2c(0, 1, 0.001, 0x3C, b"\x00\xaf")
    w.i2c_read_reg(0, 1, 0.006, 0x48, 0x00, b"\x19\x80")  # repeated start
    p = tmp_path / "i.sr"
    w.write_sr(p, ["SCL", "SDA"])
    chunks = await SG.decode(p, {"protocol": "i2c", "scl": "SCL", "sda": "SDA"})
    assert [(c.channel, c.data) for c in chunks] == [
        ("I²C 0x3C write", b"\x00\xaf"), ("I²C 0x48 write", b"\x00"), ("I²C 0x48 read", b"\x19\x80")]


@need
async def test_decode_spi_with_and_without_cs(tmp_path):
    w = la_sim.Wave(1_000_000, 0.01, n=4)
    w.spi(0, 1, 2, 3, 0.001, b"\x9f\x00\x00", b"\xff\xef\x40")
    p = tmp_path / "s.sr"
    w.write_sr(p, ["CS", "CLK", "MOSI", "MISO"])
    chunks = await SG.decode(p, {"protocol": "spi", "cs": "CS", "clk": "CLK", "mosi": "MOSI", "miso": "MISO"})
    assert ("SPI MOSI", b"\x9f\x00\x00") in [(c.channel, c.data) for c in chunks]
    assert ("SPI MISO", b"\xff\xef\x40") in [(c.channel, c.data) for c in chunks]
    nocs = await SG.decode(p, {"protocol": "spi", "clk": "CLK", "mosi": "MOSI"})
    assert b"".join(c.data for c in nocs if c.channel == "SPI MOSI") == b"\x9f\x00\x00"


@need
async def test_decode_bad_spec_is_friendly(tmp_path):
    w = la_sim.Wave(1_000_000, 0.001, n=1)
    p = tmp_path / "x.sr"
    w.write_sr(p, ["D0"])
    with pytest.raises(ValueError):
        await SG.decode(p, {"protocol": "uart", "rx": "NOPE"})
    with pytest.raises(ValueError):
        await SG.decode(p, {"protocol": "can"})


@need
async def test_demo_capture_has_all_three(tmp_path):
    p = tmp_path / "demo.sr"
    la_sim.demo_capture(p)
    for spec, chan in [(la_sim.DEMO_SPECS["uart"], "UART TX"), (la_sim.DEMO_SPECS["i2c"], "I²C 0x3C write"),
                       (la_sim.DEMO_SPECS["spi"], "SPI MOSI")]:
        chunks = await SG.decode(p, spec)
        assert any(c.channel == chan for c in chunks), chan

"""Generate logic-analyzer captures (sigrok .sr files) with UART, I²C and SPI traffic.

Used by the logic-analyzer demo and by tests, so the real sigrok decoders can be
exercised without hardware.
"""
from __future__ import annotations

import zipfile
from pathlib import Path


class Wave:
    def __init__(self, rate: int, seconds: float, n: int = 1):
        self.rate = int(rate)
        self.n_samples = int(rate * seconds)
        self.ch = [bytearray(b"\x01" * self.n_samples) for _ in range(n)]
        self._clk_low: set[int] = set()

    def _i(self, t: float) -> int:
        return max(0, min(self.n_samples, int(round(t * self.rate))))

    def set(self, ch: int, t0: float, t1: float, v: int) -> None:
        a, b = self._i(t0), self._i(t1)
        if b > a:
            self.ch[ch][a:b] = (b"\x01" if v else b"\x00") * (b - a)

    def hold(self, ch: int, t: float, v: int) -> None:
        """Set the level from t until the end of the capture."""
        self.set(ch, t, self.n_samples / self.rate, v)

    # -- UART ------------------------------------------------------------------------
    def uart(self, ch: int, t: float, data: bytes, baud: int, parity: str = "N", stop: float = 1) -> float:
        bit = 1 / baud
        for b in data:
            bits = [0] + [(b >> i) & 1 for i in range(8)]
            if parity in ("E", "O"):
                ones = bin(b).count("1") & 1
                bits.append(ones if parity == "E" else 1 - ones)
            for v in bits:
                self.set(ch, t, t + bit, v)
                t += bit
            self.set(ch, t, t + bit * stop, 1)
            t += bit * stop
        return t

    # -- I²C -------------------------------------------------------------------------
    def _i2c_byte(self, scl, sda, t, byte, q, ack_level=0):
        for k in range(9):
            v = (byte >> (7 - k)) & 1 if k < 8 else ack_level
            self.hold(sda, t, v)
            t += q
            self.hold(scl, t, 1)
            t += 2 * q
            self.hold(scl, t, 0)
            t += q
        return t

    def _i2c_start(self, scl, sda, t, q):
        self.hold(sda, t, 1)
        self.hold(scl, t + q, 1)
        self.hold(sda, t + 2 * q, 0)
        self.hold(scl, t + 3 * q, 0)
        return t + 4 * q

    def _i2c_stop(self, scl, sda, t, q):
        self.hold(sda, t, 0)
        self.hold(scl, t + q, 1)
        self.hold(sda, t + 2 * q, 1)
        return t + 3 * q

    def i2c(self, scl: int, sda: int, t: float, addr: int, data: bytes, read: bool = False,
            freq: float = 100e3) -> float:
        q = 1 / freq / 4
        t = self._i2c_start(scl, sda, t, q)
        t = self._i2c_byte(scl, sda, t, (addr << 1) | int(read), q)
        for i, b in enumerate(data):
            last = i == len(data) - 1
            t = self._i2c_byte(scl, sda, t, b, q, ack_level=1 if (read and last) else 0)
        return self._i2c_stop(scl, sda, t, q)

    def i2c_read_reg(self, scl: int, sda: int, t: float, addr: int, reg: int, data: bytes,
                     freq: float = 100e3) -> float:
        q = 1 / freq / 4
        t = self._i2c_start(scl, sda, t, q)
        t = self._i2c_byte(scl, sda, t, addr << 1, q)
        t = self._i2c_byte(scl, sda, t, reg, q)
        t = self._i2c_start(scl, sda, t, q)  # repeated start
        t = self._i2c_byte(scl, sda, t, (addr << 1) | 1, q)
        for i, b in enumerate(data):
            t = self._i2c_byte(scl, sda, t, b, q, ack_level=1 if i == len(data) - 1 else 0)
        return self._i2c_stop(scl, sda, t, q)

    # -- SPI (mode 0, MSB first) -------------------------------------------------------
    def spi(self, cs: int, clk: int, mosi: int, miso: int | None, t: float, out: bytes,
            inp: bytes | None = None, freq: float = 100e3) -> float:
        if clk not in self._clk_low:
            self.hold(clk, 0, 0)
            self._clk_low.add(clk)
        half = 1 / freq / 2
        self.hold(cs, t, 0)
        t += half
        for i, b in enumerate(out):
            r = inp[i] if inp and i < len(inp) else 0
            for k in range(8):
                self.hold(mosi, t, (b >> (7 - k)) & 1)
                if miso is not None:
                    self.hold(miso, t, (r >> (7 - k)) & 1)
                t += half
                self.hold(clk, t, 1)
                t += half
                self.hold(clk, t, 0)
        t += half
        self.hold(cs, t, 1)
        return t + half

    # -- output -----------------------------------------------------------------------
    def packed(self) -> bytes:
        total = 0
        for c, arr in enumerate(self.ch):
            table = bytes((1 << c) if i == 1 else 0 for i in range(256))
            total |= int.from_bytes(bytes(arr).translate(table), "big")
        return total.to_bytes(self.n_samples, "big")

    def write_sr(self, path, names: list[str]) -> None:
        if len(names) != len(self.ch) or len(names) > 8:
            raise ValueError("One name per channel, at most 8 channels")
        r = self.rate
        rate = f"{r // 1_000_000} MHz" if r % 1_000_000 == 0 else f"{r // 1000} kHz" if r % 1000 == 0 else f"{r} Hz"
        probes = "\n".join(f"probe{i + 1}={n}" for i, n in enumerate(names))
        meta = (f"[global]\nsigrok version=0.5.2\n\n[device 1]\ncapturefile=logic-1\n"
                f"total probes={len(names)}\nsamplerate={rate}\ntotal analog=0\n{probes}\nunitsize=1\n")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("version", "2")
            z.writestr("metadata", meta)
            z.writestr("logic-1-1", self.packed())


DEMO_NAMES = ["TX", "SCL", "SDA", "CS", "CLK", "MOSI", "MISO"]
DEMO_SPECS = {
    "uart": {"protocol": "uart", "rx": "TX", "baudrate": 115200},
    "i2c": {"protocol": "i2c", "scl": "SCL", "sda": "SDA"},
    "spi": {"protocol": "spi", "cs": "CS", "clk": "CLK", "mosi": "MOSI", "miso": "MISO"},
}


def demo_capture(path) -> Path:
    """A board with a UART display, an I²C OLED (0x3C), an I²C temperature sensor (0x48) and an SPI flash."""
    from .sources.simulator import frame
    w = Wave(1_000_000, 0.12, n=7)
    t = 0.002
    for temp in (200, 205, 210):
        t = w.uart(0, t, frame(0x10, temp.to_bytes(2, "little")), 115200) + 0.004
    w.uart(0, t, frame(0x20, b"\x07"), 115200)
    w.i2c(1, 2, 0.010, 0x3C, b"\x00\xae\xd5\x80\xaf")          # OLED init commands
    w.i2c(1, 2, 0.020, 0x3C, b"\x40" + bytes(range(8)))       # OLED pixel data
    w.i2c_read_reg(1, 2, 0.035, 0x48, 0x00, b"\x19\x80")      # temperature 25.5 °C
    w.i2c_read_reg(1, 2, 0.050, 0x48, 0x00, b"\x1a\x00")      # 26.0 °C
    w.spi(3, 4, 5, 6, 0.070, b"\x9f\x00\x00\x00", b"\xff\xef\x40\x18")     # JEDEC ID
    w.spi(3, 4, 5, 6, 0.085, b"\x03\x00\x10\x00\x00\x00", b"\xff\xff\xff\xff\x48\x69")  # read data
    w.write_sr(path, DEMO_NAMES)
    return Path(path)

"""Tapline bridge for Raspberry Pi Pico (MicroPython).

Copy this file to the Pico as  main.py  (Thonny: File > Save as > Raspberry Pi Pico).
Tapline then talks to it over USB and can send I²C and SPI messages.

Wiring (Pico pin names):
  I²C0:  SDA = GP4 (pin 6),  SCL = GP5 (pin 7)      — the target needs pull-ups (most modules have them)
  SPI0:  SCK = GP18 (pin 24), MOSI = GP19 (pin 25), MISO = GP16 (pin 21), CS = GP17 (pin 22)
  GND:   any GND pin to the target's GND.   Pico I/O is 3.3 V only — never connect 5 V signals.

Commands (one per line, hex without spaces; answers start with OK or ERR):
  PING                     -> OK TAPLINE-BRIDGE 1
  I2C SCAN                 -> OK 3C 48
  I2C W <addr> <hex>       -> OK
  I2C R <addr> <n>         -> OK <hex>
  I2C WR <addr> <hex> <n>  -> OK <hex>      (write, repeated start, read)
  SPI X <hex>              -> OK <hex>      (CS low, full-duplex transfer, CS high)
  CFG I2C <freq>           -> OK
  CFG SPI <baud> <mode0-3> -> OK
"""

VERSION = "TAPLINE-BRIDGE 1"


def _hex(s):
    s = s.strip()
    if not s or len(s) % 2:
        raise ValueError("hex bytes expected")
    return bytes(int(s[i:i + 2], 16) for i in range(0, len(s), 2))


def _addr(s):
    a = int(s, 16)
    if not 0x03 <= a <= 0x77:
        raise ValueError("I2C address must be 03-77")
    return a


def _n(s):
    n = int(s)
    if not 1 <= n <= 256:
        raise ValueError("read 1-256 bytes")
    return n


def _out(b):
    return "".join("%02X" % x for x in b)


def handle(line, hw):
    """Run one command line against hardware `hw`; returns the answer line."""
    p = line.strip().split()
    try:
        if not p:
            return "ERR empty command"
        cmd = p[0].upper()
        if cmd == "PING":
            return "OK " + VERSION
        sub = p[1].upper() if len(p) > 1 else ""
        if cmd == "I2C":
            if sub == "SCAN":
                return ("OK " + " ".join("%02X" % a for a in hw.i2c_scan())).strip()
            addr = _addr(p[2])
            try:
                if sub == "W" and len(p) == 4:
                    hw.i2c_write(addr, _hex(p[3]))
                    return "OK"
                if sub == "R" and len(p) == 4:
                    return "OK " + _out(hw.i2c_read(addr, _n(p[3])))
                if sub == "WR" and len(p) == 5:
                    return "OK " + _out(hw.i2c_write_read(addr, _hex(p[3]), _n(p[4])))
            except OSError:
                return "ERR no ACK from 0x%02X (check address, wiring and pull-ups)" % addr
            return "ERR usage: I2C W|R|WR <addr> ..."
        if cmd == "SPI" and sub == "X" and len(p) == 3:
            return "OK " + _out(hw.spi_xfer(_hex(p[2])))
        if cmd == "CFG" and sub == "I2C" and len(p) == 3:
            hw.cfg_i2c(int(p[2]))
            return "OK"
        if cmd == "CFG" and sub == "SPI" and len(p) == 4:
            mode = int(p[3])
            if mode not in (0, 1, 2, 3):
                raise ValueError("SPI mode must be 0-3")
            hw.cfg_spi(int(p[2]), mode)
            return "OK"
        return "ERR unknown command"
    except (ValueError, IndexError) as e:
        return "ERR " + (str(e) or "bad arguments")


class PicoHW:  # pragma: no cover - runs on the Pico only
    def __init__(self):
        from machine import I2C, SPI, Pin
        self.Pin, self.I2C, self.SPI = Pin, I2C, SPI
        self.cs = Pin(17, Pin.OUT, value=1)
        self.cfg_i2c(100000)
        self.cfg_spi(1000000, 0)

    def cfg_i2c(self, freq):
        self.i2c = self.I2C(0, sda=self.Pin(4), scl=self.Pin(5), freq=freq)

    def cfg_spi(self, baud, mode):
        self.spi = self.SPI(0, baudrate=baud, polarity=mode >> 1, phase=mode & 1,
                            sck=self.Pin(18), mosi=self.Pin(19), miso=self.Pin(16))

    def i2c_scan(self):
        return self.i2c.scan()

    def i2c_write(self, addr, data):
        self.i2c.writeto(addr, data)

    def i2c_read(self, addr, n):
        return self.i2c.readfrom(addr, n)

    def i2c_write_read(self, addr, data, n):
        self.i2c.writeto(addr, data, False)
        return self.i2c.readfrom(addr, n)

    def spi_xfer(self, data):
        buf = bytearray(len(data))
        self.cs.value(0)
        try:
            self.spi.write_readinto(data, buf)
        finally:
            self.cs.value(1)
        return bytes(buf)


def main():  # pragma: no cover - runs on the Pico only
    import sys
    hw = PicoHW()
    while True:
        line = sys.stdin.readline()
        if line:
            print(handle(line, hw))


if __name__ == "__main__":  # pragma: no cover
    main()

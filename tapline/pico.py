"""Host side of the Raspberry Pi Pico bridge (bridge/pico_bridge.py)."""
from __future__ import annotations

import threading
import time

import serial


class PicoError(Exception):
    """Message is written for the user."""


class PicoBridge:
    def __init__(self, port: str, timeout: float = 1.5):
        self.port, self.timeout = port, timeout
        self.ser: serial.Serial | None = None
        self.version = ""
        self._lock = threading.Lock()

    def open(self) -> None:
        try:
            self.ser = serial.Serial(self.port, 115200, timeout=0.05, write_timeout=1)
        except (serial.SerialException, OSError) as e:
            raise PicoError(f"Could not open {self.port}: {e}") from e
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        try:
            self.version = self.command("PING")
        except PicoError:
            self.close()
            raise PicoError(f"No Tapline bridge answered on {self.port}. Copy bridge/pico_bridge.py to the Pico "
                            f"as main.py (e.g. with Thonny), then unplug and replug it.")

    def close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def command(self, line: str) -> str:
        if not self.ser:
            raise PicoError("The Pico bridge is not connected")
        with self._lock:
            try:
                self.ser.write((line + "\n").encode())
                deadline = time.time() + self.timeout
                buf = b""
                while time.time() < deadline:
                    buf += self.ser.read(256)
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        text = raw.decode(errors="replace").strip()
                        if text == "OK" or text.startswith("OK "):
                            return text[3:]
                        if text.startswith("ERR"):
                            raise PicoError("Pico: " + text[4:])
                        # anything else (MicroPython banner, echo) is skipped
            except (serial.SerialException, OSError) as e:
                raise PicoError(f"Pico connection lost: {e}") from e
        raise PicoError("The Pico did not answer in time")

    def i2c_scan(self) -> list[int]:
        return [int(x, 16) for x in self.command("I2C SCAN").split()]

    def i2c_write(self, addr: int, data: bytes) -> None:
        self.command(f"I2C W {addr:02X} {data.hex().upper()}")

    def i2c_read(self, addr: int, n: int, write: bytes = b"") -> bytes:
        if write:
            return bytes.fromhex(self.command(f"I2C WR {addr:02X} {write.hex().upper()} {n}"))
        return bytes.fromhex(self.command(f"I2C R {addr:02X} {n}"))

    def spi_xfer(self, data: bytes) -> bytes:
        return bytes.fromhex(self.command(f"SPI X {data.hex().upper()}"))

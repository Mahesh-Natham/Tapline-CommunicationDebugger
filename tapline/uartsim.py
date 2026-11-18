"""Bit-level model of a UART line.

Used to test auto-detection: it reproduces what an adapter set to the *wrong*
settings would receive from a line driven with the *true* settings.
Time is measured in bit periods of the true baud rate.
"""
from __future__ import annotations

from bisect import bisect_right

from .model import SerialConfig


def _parity_bit(byte: int, bits: int, parity: str) -> int | None:
    if parity == "N":
        return None
    ones = bin(byte & ((1 << bits) - 1)).count("1")
    if parity == "E":
        return ones & 1
    if parity == "O":
        return 1 - (ones & 1)
    if parity == "M":
        return 1
    if parity == "S":
        return 0
    raise ValueError(parity)


def encode(data: bytes, bytesize: int = 8, parity: str = "N", stopbits: float = 1,
           idle_bits: float = 0) -> tuple[list[float], list[int], float]:
    """Return (segment start times, levels, end time) for the line signal."""
    starts: list[float] = [0.0]
    levels: list[int] = [1]
    t = 10.0  # leading idle

    def put(level: int, dur: float):
        nonlocal t
        if levels[-1] != level:
            starts.append(t)
            levels.append(level)
        t += dur

    for b in data:
        put(0, 1)
        for i in range(bytesize):
            put((b >> i) & 1, 1)
        p = _parity_bit(b, bytesize, parity)
        if p is not None:
            put(p, 1)
        put(1, stopbits + idle_bits)
    put(1, 10)
    return starts, levels, t


def reinterpret(data: bytes, true: SerialConfig, read: SerialConfig,
                errors_as_zero: bool = False) -> tuple[bytes, int]:
    """Bytes (and error count) a receiver configured as `read` sees on a line driven as `true`."""
    starts, levels, end = encode(data, true.bytesize, true.parity, true.stopbits)
    period = true.baud / read.baud  # read bit length in true-bit units

    def level(t: float) -> int:
        return levels[bisect_right(starts, t) - 1]

    falling = [starts[i] for i in range(1, len(starts)) if levels[i] == 0 and levels[i - 1] == 1]
    out = bytearray()
    errors = 0
    has_par = read.parity != "N"
    frame_len = 1 + read.bytesize + (1 if has_par else 0)
    t = 0.0
    idx = 0
    while True:
        idx = bisect_right(falling, t, lo=idx)
        if idx >= len(falling):
            break
        te = falling[idx]
        if te + (frame_len + 0.5) * period > end:
            break
        if level(te + 0.5 * period) != 0:
            t = te
            continue
        byte = 0
        for i in range(read.bytesize):
            byte |= level(te + (1.5 + i) * period) << i
        bad = False
        if has_par:
            pb = level(te + (1.5 + read.bytesize) * period)
            if pb != _parity_bit(byte, read.bytesize, read.parity):
                bad = True
        stop_t = te + (frame_len + 0.5) * period
        if level(stop_t) != 1:
            bad = True
        if bad:
            errors += 1
        out.append(0 if (bad and errors_as_zero) else byte)
        t = stop_t  # after a framing error the receiver waits for the next falling edge
    return bytes(out), errors

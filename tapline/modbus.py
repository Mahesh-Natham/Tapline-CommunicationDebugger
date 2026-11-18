"""Modbus RTU: decode frames into plain words and build requests."""
from __future__ import annotations

from .checksums import ALGORITHMS

_crc = ALGORITHMS["crc16_modbus"]

NAMES = {1: "Read coils", 2: "Read discrete inputs", 3: "Read holding registers", 4: "Read input registers",
         5: "Write single coil", 6: "Write single register", 15: "Write multiple coils",
         16: "Write multiple registers"}
EXCEPTIONS = {1: "Illegal function", 2: "Illegal data address", 3: "Illegal data value",
              4: "Server device failure", 5: "Acknowledge", 6: "Server busy",
              8: "Memory parity error", 10: "Gateway path unavailable", 11: "Gateway target failed to respond"}


def crc_ok(data: bytes) -> bool:
    return len(data) >= 4 and _crc(data[:-2]) == int.from_bytes(data[-2:], "little")


def with_crc(body: bytes) -> bytes:
    return body + _crc(body).to_bytes(2, "little")


def frame_lengths(buf: bytes) -> list[int] | None:
    """Possible total lengths of the frame starting at buf[0]; None if the function is unknown.
    An empty list means more bytes are needed to tell."""
    if len(buf) < 2:
        return []
    fn = buf[1]
    if fn & 0x80:
        return [5]
    if fn in (1, 2, 3, 4):
        return [8] + ([5 + buf[2]] if len(buf) >= 3 else [])
    if fn in (5, 6):
        return [8]
    if fn in (15, 16):
        return [8] + ([9 + buf[6]] if len(buf) >= 7 else [])
    return None


def _u16(b: bytes, i: int) -> int:
    return int.from_bytes(b[i:i + 2], "big")


def _as_request(d: bytes, out: dict) -> dict | None:
    fn = out["fn"]
    if fn in (1, 2, 3, 4) and len(d) == 8:
        out.update(start=_u16(d, 2), quantity=_u16(d, 4))
        obj = {1: "coils", 2: "inputs", 3: "registers", 4: "registers"}[fn]
        out["summary"] = (f"{NAMES[fn]}: {out['quantity']} {obj} from {out['start']} "
                          f"(device {out['addr']})")
    elif fn == 5 and len(d) == 8:
        out.update(start=_u16(d, 2), value=_u16(d, 4) == 0xFF00)
        out["summary"] = f"Write single coil {out['start']} = {'ON' if out['value'] else 'OFF'} (device {out['addr']})"
    elif fn == 6 and len(d) == 8:
        out.update(start=_u16(d, 2), value=_u16(d, 4))
        out["summary"] = f"Write single register {out['start']} = {out['value']} (device {out['addr']})"
    elif fn in (15, 16) and len(d) >= 9 and len(d) == 9 + d[6]:
        out.update(start=_u16(d, 2), quantity=_u16(d, 4))
        payload = d[7:-2]
        if fn == 16:
            out["registers"] = [_u16(payload, i) for i in range(0, len(payload) - 1, 2)]
            vals = ", ".join(map(str, out["registers"][:8]))
        else:
            out["coils"] = [(payload[i // 8] >> (i % 8)) & 1 for i in range(min(out["quantity"], len(payload) * 8))]
            vals = "".join(map(str, out["coils"][:16]))
        out["summary"] = f"{NAMES[fn]}: {out['quantity']} from {out['start']} = [{vals}] (device {out['addr']})"
    else:
        return None
    out["kind"] = "request"
    return out


def _as_response(d: bytes, out: dict) -> dict | None:
    fn = out["fn"]
    if fn in (1, 2, 3, 4) and len(d) == 5 + d[2]:
        payload = d[3:-2]
        if fn in (3, 4):
            if len(payload) % 2:
                return None
            out["registers"] = [_u16(payload, i) for i in range(0, len(payload), 2)]
            vals = ", ".join(map(str, out["registers"][:10])) + (" …" if len(out["registers"]) > 10 else "")
            out["summary"] = f"Reply: {len(out['registers'])} registers = [{vals}] (device {out['addr']})"
        else:
            out["bits"] = [(b >> i) & 1 for b in payload for i in range(8)]
            out["summary"] = f"Reply: {''.join(map(str, out['bits'][:16]))} (device {out['addr']})"
    elif fn in (5, 6, 15, 16) and len(d) == 8:
        out.update(start=_u16(d, 2), value=_u16(d, 4))
        out["summary"] = f"Reply: {NAMES[fn].lower()} OK at {out['start']} (device {out['addr']})"
    else:
        return None
    out["kind"] = "response"
    return out


def decode(data: bytes, prefer: str | None = None) -> dict | None:
    """Plain-words view of a Modbus RTU frame, or None if it isn't one (bad CRC, unknown shape)."""
    try:
        return _decode(data, prefer)
    except (IndexError, ValueError, KeyError):
        return None  # CRC-valid but malformed: show it as plain bytes


def _decode(data: bytes, prefer: str | None) -> dict | None:
    if len(data) < 5 or not crc_ok(data):
        return None
    addr, fn = data[0], data[1]
    if addr > 247:
        return None
    base = {"addr": addr, "fn": fn & 0x7F, "name": NAMES.get(fn & 0x7F, f"Function {fn & 0x7F}")}
    if fn & 0x80:
        if len(data) != 5:
            return None
        code = data[2]
        return {**base, "kind": "exception", "code": code,
                "summary": f"Error from device {addr}: {EXCEPTIONS.get(code, f'code {code}')} ({base['name']})"}
    if fn not in NAMES:
        return None
    order = (_as_response, _as_request) if prefer == "response" else (_as_request, _as_response)
    for f in order:
        r = f(data, dict(base))
        if r:
            return r
    return None


def build(addr: int, fn: int, start: int, qty_or_value: int, values: list[int] | None = None) -> bytes:
    """Build a Modbus RTU request (CRC included)."""
    if not 1 <= int(addr) <= 247:
        raise ValueError("Device address must be 1–247")
    if not 0 <= int(start) <= 0xFFFF:
        raise ValueError("Start address must be 0–65535")
    addr, fn, start, q = int(addr), int(fn), int(start), int(qty_or_value)
    head = bytes([addr, fn]) + start.to_bytes(2, "big")
    if fn in (1, 2):
        if not 1 <= q <= 2000:
            raise ValueError("Read 1–2000 coils/inputs")
        return with_crc(head + q.to_bytes(2, "big"))
    if fn in (3, 4):
        if not 1 <= q <= 125:
            raise ValueError("Read 1–125 registers")
        return with_crc(head + q.to_bytes(2, "big"))
    if fn == 5:
        return with_crc(head + (b"\xff\x00" if q else b"\x00\x00"))
    if fn == 6:
        if not 0 <= q <= 0xFFFF:
            raise ValueError("Register value must be 0–65535")
        return with_crc(head + q.to_bytes(2, "big"))
    if fn == 15:
        vals = list(values or [])
        if len(vals) != q or not 1 <= q <= 1968:
            raise ValueError("Give one 0/1 value per coil")
        payload = bytearray((q + 7) // 8)
        for i, v in enumerate(vals):
            if v:
                payload[i // 8] |= 1 << (i % 8)
        return with_crc(head + q.to_bytes(2, "big") + bytes([len(payload)]) + bytes(payload))
    if fn == 16:
        vals = [int(v) for v in (values or [])]
        if len(vals) != q or not 1 <= q <= 123:
            raise ValueError("Give one value per register (1–123 registers)")
        if any(not 0 <= v <= 0xFFFF for v in vals):
            raise ValueError("Register values must be 0–65535")
        payload = b"".join(v.to_bytes(2, "big") for v in vals)
        return with_crc(head + q.to_bytes(2, "big") + bytes([len(payload)]) + payload)
    raise ValueError(f"Function {fn} is not supported by the builder (use 1–6, 15 or 16)")

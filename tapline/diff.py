"""Compare frames byte by byte."""
from __future__ import annotations

from .model import Frame


def diff_frames(a: bytes, b: bytes) -> list[str]:
    out = []
    for i in range(max(len(a), len(b))):
        if i >= len(a):
            out.append("added")
        elif i >= len(b):
            out.append("removed")
        else:
            out.append("same" if a[i] == b[i] else "changed")
    return out


def variability(frames: list[Frame]) -> list[dict]:
    """Per byte offset: constant / rare / varies / counter."""
    if not frames:
        return []
    width = max(len(f.data) for f in frames)
    out = []
    for off in range(width):
        vals = [f.data[off] for f in frames if len(f.data) > off]
        steps = list(zip(vals, vals[1:]))
        distinct = len(set(vals))
        if distinct == 1:
            kind = "constant"
        elif steps and sum((b - a) % 256 == 1 for a, b in steps) / len(steps) >= 0.8:
            kind = "counter"
        elif steps and sum(a != b for a, b in steps) / len(steps) < 0.1:
            kind = "rare"
        else:
            kind = "varies"
        out.append({"offset": off, "kind": kind, "distinct": distinct, "present": len(vals)})
    return out

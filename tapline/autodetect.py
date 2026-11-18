"""Guess baud rate and line format by listening (never transmits)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Awaitable, Callable

from .model import SerialConfig

BAUDS = [1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 115200, 230400,
         250000, 460800, 500000, 921600, 1000000]
FORMATS = ["8N1", "8E1", "8O1", "7E1", "8N2"]
_GARBAGE = {0x00, 0xFF, 0x80, 0xC0, 0xE0, 0xF0, 0xF8, 0xFC, 0xFE}


@dataclass
class Candidate:
    config: SerialConfig
    score: float
    preview_hex: str
    errors: int = 0

    def to_json(self) -> dict:
        return {"config": self.config.to_json(), "label": self.config.label(),
                "score": self.score, "preview": self.preview_hex}


def _structure(data: bytes, n: int = 2) -> float:
    if len(data) < n * 3:
        return 0.0
    grams = Counter(data[i:i + n] for i in range(len(data) - n + 1))
    covered = sum(c for g, c in grams.items() if c >= 3)
    return min(1.0, covered / (len(data) - n + 1))


def score(data: bytes, errors: int = 0) -> float:
    """0..1: how much this looks like real protocol traffic."""
    if not data:
        return 0.0
    err = min(1.0, 3 * errors / len(data))
    garbage = sum(b in _GARBAGE for b in data) / len(data)
    # long runs of a single repeated byte are noise, not protocol
    run_share = Counter(data).most_common(1)[0][1] / len(data)
    structure = _structure(data)
    excess = max(0.0, garbage - 0.15) / 0.85  # real protocols contain some 00/FF bytes
    s = (1 - err) * (1 - excess) ** 2 * (0.4 + 0.6 * min(1.0, structure * 2)) * (1 - max(0.0, run_share - 0.3))
    return round(max(0.0, min(1.0, s)), 4)


ReadFn = Callable[[SerialConfig, float], Awaitable[tuple[bytes, int]]]


async def detect(read_fn: ReadFn, port: str, window_s: float = 0.4,
                 progress: Callable | None = None) -> list[Candidate]:
    plan = [SerialConfig.from_format(port, b, "8N1") for b in BAUDS]
    total = len(plan) + 2 * (len(FORMATS) - 1)
    done = 0
    results: list[tuple[Candidate, bytes]] = []
    baud_rank: dict[int, float] = {}

    async def run(cfg: SerialConfig):
        nonlocal done
        data, errors = await read_fn(cfg, window_s)
        done += 1
        if progress:
            progress(done, total, cfg.label())
        # identical reception under a more exotic format adds nothing: keep the simpler one
        if cfg.label().endswith("8N1"):
            # rank bauds on the bytes alone: a wrong format still gives the right data bits
            baud_rank[cfg.baud] = score(data, 0)
        for c, d in results:
            if c.config.baud == cfg.baud and d == data and c.errors == errors:
                return
        results.append((Candidate(cfg, score(data, errors), data[:24].hex(" ").upper(), errors), data))

    for cfg in plan:
        await run(cfg)
    top_bauds = sorted(baud_rank, key=lambda b: -baud_rank[b])[:2]
    for b in top_bauds:
        for fmt in FORMATS[1:]:
            await run(SerialConfig.from_format(port, b, fmt))
    if progress and done < total:
        progress(total, total, "")
    ranked = sorted((c for c, _ in results), key=lambda c: -c.score)
    return ranked[:3]

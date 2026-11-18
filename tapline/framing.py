"""Cut a stream of raw chunks into frames (messages)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .model import Chunk, Frame

MAX_FRAME = 4096


@dataclass(frozen=True)
class GapRule:
    gap_ms: float = 5


@dataclass(frozen=True)
class DelimiterRule:
    start: bytes = b""
    end: bytes = b""


@dataclass(frozen=True)
class LengthRule:
    header: bytes
    len_offset: int
    len_size: int = 1
    len_adjust: int = 0

    def total(self, value: int) -> int:
        return self.len_offset + self.len_size + value + self.len_adjust


@dataclass(frozen=True)
class FixedRule:
    length: int


Rule = GapRule | DelimiterRule | LengthRule | FixedRule


def rule_to_json(rule: Rule) -> dict:
    if isinstance(rule, GapRule):
        return {"kind": "gap", "gap_ms": rule.gap_ms}
    if isinstance(rule, DelimiterRule):
        return {"kind": "delimiter", "start": rule.start.hex(" "), "end": rule.end.hex(" ")}
    if isinstance(rule, LengthRule):
        return {"kind": "length", "header": rule.header.hex(" "), "len_offset": rule.len_offset,
                "len_size": rule.len_size, "len_adjust": rule.len_adjust}
    if isinstance(rule, FixedRule):
        return {"kind": "fixed", "length": rule.length}
    raise TypeError(rule)


def _hex(s) -> bytes:
    return bytes.fromhex(str(s or "").replace("0x", "").replace(",", " "))


def rule_from_json(d: dict) -> Rule:
    kind = d.get("kind")
    if kind == "gap":
        return GapRule(max(0.1, float(d.get("gap_ms", 5))))
    if kind == "delimiter":
        r = DelimiterRule(_hex(d.get("start")), _hex(d.get("end")))
        if not r.start and not r.end:
            raise ValueError("A delimiter rule needs start bytes, end bytes or both")
        return r
    if kind == "length":
        r = LengthRule(_hex(d.get("header")), int(d.get("len_offset", 0)), int(d.get("len_size", 1)),
                       int(d.get("len_adjust", 0)))
        if r.len_size not in (1, 2) or r.len_offset < 0:
            raise ValueError("Length field must be 1 or 2 bytes at a non-negative offset")
        return r
    if kind == "fixed":
        n = int(d.get("length", 0))
        if n < 1:
            raise ValueError("Fixed length must be at least 1")
        return FixedRule(n)
    raise ValueError(f"Unknown framing kind: {kind!r}")


class _Buf:
    __slots__ = ("data", "ts")

    def __init__(self):
        self.data = bytearray()
        self.ts: list[float] = []

    def take(self, n: int) -> tuple[bytes, float, float]:
        d = bytes(self.data[:n])
        t0, t1 = self.ts[0], self.ts[n - 1]
        del self.data[:n]
        del self.ts[:n]
        return d, t0, t1


class StreamFramer:
    """Incremental framer. Feed chunks in arrival order; frames come out as soon as they are complete."""

    def __init__(self, rule: Rule):
        self.rule = rule
        self._bufs: dict[str, _Buf] = {}
        self._last_t: dict[str, float] = {}
        self._next_id = 0

    def _emit(self, out: list, channel: str, buf: _Buf, n: int, flags=()):
        if n <= 0:
            return
        d, t0, t1 = buf.take(n)
        out.append(Frame(self._next_id, t0, t1, channel, d, set(flags)))
        self._next_id += 1

    def feed(self, chunk: Chunk) -> list[Frame]:
        out: list[Frame] = []
        if not chunk.data:
            return out
        ch = chunk.channel
        buf = self._bufs.setdefault(ch, _Buf())
        if isinstance(self.rule, GapRule) and buf.data:
            if (chunk.t - self._last_t[ch]) * 1000 > self.rule.gap_ms:
                self._emit(out, ch, buf, len(buf.data))
        self._last_t[ch] = chunk.t
        buf.data += chunk.data
        buf.ts += [chunk.t] * len(chunk.data)
        self._process(out, ch, buf, final=False)
        return out

    def flush(self) -> list[Frame]:
        out: list[Frame] = []
        for ch, buf in self._bufs.items():
            self._process(out, ch, buf, final=True)
            if buf.data:
                flags = () if isinstance(self.rule, GapRule) or (
                    isinstance(self.rule, DelimiterRule) and not self.rule.end) else ("partial",)
                if isinstance(self.rule, DelimiterRule) and self.rule.start and not buf.data.startswith(self.rule.start):
                    flags = ("partial",)
                self._emit(out, ch, buf, len(buf.data), flags)
        return out

    # -- per-rule processing -------------------------------------------------
    def _process(self, out, ch, buf: _Buf, final: bool):
        r = self.rule
        if isinstance(r, GapRule):
            while len(buf.data) > MAX_FRAME:
                self._emit(out, ch, buf, MAX_FRAME, ("oversize",))
        elif isinstance(r, FixedRule):
            while len(buf.data) >= r.length:
                self._emit(out, ch, buf, r.length)
        elif isinstance(r, DelimiterRule):
            self._process_delim(out, ch, buf, r)
        elif isinstance(r, LengthRule):
            self._process_length(out, ch, buf, r)

    def _process_delim(self, out, ch, buf: _Buf, r: DelimiterRule):
        while buf.data:
            data = buf.data
            if r.start:
                s = data.find(r.start)
                if s < 0:
                    keep = len(r.start) - 1
                    if len(data) - keep > MAX_FRAME or (not r.end and len(data) > MAX_FRAME):
                        self._emit(out, ch, buf, len(data) - keep, ("partial", "oversize"))
                    return
                if s > 0:
                    self._emit(out, ch, buf, s, ("partial",))
                    continue
                if r.end:
                    e = data.find(r.end, len(r.start))
                    nxt = data.find(r.start, len(r.start))
                    if e >= 0 and (nxt < 0 or e < nxt):
                        self._emit(out, ch, buf, e + len(r.end))
                        continue
                    if nxt >= 0:
                        self._emit(out, ch, buf, nxt, ("partial",))
                        continue
                else:
                    nxt = data.find(r.start, len(r.start))
                    if nxt >= 0:
                        self._emit(out, ch, buf, nxt)
                        continue
                if len(data) > MAX_FRAME:
                    self._emit(out, ch, buf, MAX_FRAME, ("oversize",))
                    continue
                return
            else:
                e = data.find(r.end)
                if e >= 0:
                    self._emit(out, ch, buf, e + len(r.end))
                    continue
                if len(data) > MAX_FRAME:
                    self._emit(out, ch, buf, MAX_FRAME, ("oversize",))
                    continue
                return

    def _process_length(self, out, ch, buf: _Buf, r: LengthRule):
        hdr = r.header
        while buf.data:
            data = buf.data
            if hdr:
                s = data.find(hdr)
                if s < 0:
                    keep = len(hdr) - 1
                    if len(data) > keep:
                        self._emit(out, ch, buf, len(data) - keep, ("partial",))
                    return
                if s > 0:
                    self._emit(out, ch, buf, s, ("partial",))
                    continue
            need = r.len_offset + r.len_size
            if len(data) < need:
                return
            value = int.from_bytes(data[r.len_offset:need], "little")
            total = r.total(value)
            if total < max(need, len(hdr), 1) or total > MAX_FRAME:
                # implausible length: drop one byte and resync
                self._emit(out, ch, buf, max(1, len(hdr)), ("partial",))
                continue
            if len(data) < total:
                return
            self._emit(out, ch, buf, total)


def frame_chunks(chunks: list[Chunk], rule: Rule) -> list[Frame]:
    sf = StreamFramer(rule)
    frames: list[Frame] = []
    for c in sorted(chunks, key=lambda c: c.t):
        frames += sf.feed(c)
    frames += sf.flush()
    frames.sort(key=lambda f: (f.t_start, f.id))
    for i, f in enumerate(frames):
        f.id = i
    return frames


# -- guessing ------------------------------------------------------------------

def _clean_ratio(frames: list[Frame]) -> float:
    total = sum(len(f.data) for f in frames)
    if not total:
        return 0.0
    good = sum(len(f.data) for f in frames if not f.flags)
    return good / total


def _prefix_consistency(frames: list[Frame]) -> float:
    if not frames:
        return 0.0
    c = Counter(f.data[:2] for f in frames)
    return c.most_common(1)[0][1] / len(frames)


def guess_framing(chunks: list[Chunk]) -> list[tuple[Rule, float]]:
    """Try plausible framing rules and rank them by how cleanly they cut the data (score 0-1)."""
    if not chunks or not any(c.data for c in chunks):
        return []
    results: dict[Rule, float] = {}

    gap_frames = None
    for g in (2, 5, 10, 20):
        r = GapRule(g)
        fr = frame_chunks(chunks, r)
        if gap_frames is None or g == 5:
            gap_frames = fr
        if len(fr) >= 3:
            results[r] = round(_clean_ratio(fr) * _prefix_consistency(fr) * 0.9, 4)

    # header candidates: common prefix of gap frames + most frequent bigrams
    headers: list[bytes] = []
    if gap_frames and len(gap_frames) >= 3:
        p, n = Counter(f.data[:2] for f in gap_frames if len(f.data) >= 2).most_common(1)[0] if any(
            len(f.data) >= 2 for f in gap_frames) else (b"", 0)
        if p and n / len(gap_frames) >= 0.6:
            headers.append(p)
    allbytes = b"".join(c.data for c in sorted(chunks, key=lambda c: c.t))
    bigrams = Counter(allbytes[i:i + 2] for i in range(len(allbytes) - 1))
    for bg, n in bigrams.most_common(3):
        if n >= 3 and bg not in headers and bg not in (b"\x00\x00", b"\xff\xff"):
            headers.append(bg)

    for h in headers:
        fr = frame_chunks(chunks, DelimiterRule(start=h))
        if len(fr) >= 3:
            results[DelimiterRule(start=h)] = round(_clean_ratio(fr) * 0.95, 4)
        for off in range(len(h), len(h) + 3):
            for size in (1, 2):
                for adj in range(-2, 3):
                    r = LengthRule(h, off, size, adj)
                    fr = frame_chunks(chunks, r)
                    clean = [f for f in fr if not f.flags]
                    if len(clean) >= 3:
                        results[r] = round(_clean_ratio(fr), 4)

    ranked = sorted(results.items(), key=lambda kv: (-kv[1], _rule_rank(kv[0])))
    return ranked[:5]


def _rule_rank(r: Rule) -> tuple:
    # tie-break: length rules first, then simpler (1-byte length, no adjust)
    if isinstance(r, LengthRule):
        return (0, r.len_size, abs(r.len_adjust), r.len_offset)
    if isinstance(r, DelimiterRule):
        return (1,)
    return (2,)

"""A single Markdown report of a capture that you can hand to an AI assistant (Claude, ChatGPT…).

It holds what the AI needs to help reverse-engineer the link: how it was captured, what the
user already worked out, the events they marked, a summary of every kind of message with a
byte-by-byte analysis, and the message log itself.
"""
from __future__ import annotations

import time
from collections import OrderedDict

from . import protocol as P
from .diff import variability
from .framing import rule_to_json
from .model import Frame, is_sent

MAX_LOG_LINES = 3000
MARKER_CONTEXT = 4

PROMPT = """\
> **For the AI reading this:** this is a capture of a digital link (UART / RS-485 / CAN / I²C / SPI)
> between a controller board and a display or other device, recorded with Tapline. Help the user
> reverse-engineer the protocol: identify message types, the meaning of each byte (headers, length,
> commands, values, counters, checksums), and connect changes to the user's marked events.
> Say which conclusions are certain and which are guesses, and suggest test messages to send
> to confirm them. Bytes are hex. Times are seconds from the start of the capture."""


def _group_key(engine, f: Frame) -> str:
    t = engine.type_of(f)
    if t:
        return f"{f.channel} · {t.name}"
    head = " ".join(f"{b:02X}" for b in f.data[:2])
    return f"{f.channel} · starts {head} · {len(f.data)} bytes"


def _cell(s: str) -> str:
    return s.replace("|", "\\|")


def build_report(engine) -> str:
    frames: list[Frame] = engine.frames
    proto = engine.protocol
    t0 = frames[0].t_start if frames else (engine.markers[0].t if engine.markers else 0.0)
    out: list[str] = []
    w = out.append

    w(f"# Tapline capture report")
    w("")
    w(PROMPT)
    w("")
    w("## Capture")
    w("")
    span = (frames[-1].t_end - t0) if frames else 0.0
    chans = OrderedDict()
    for f in frames:
        chans[f.channel] = chans.get(f.channel, 0) + 1
    rows = [
        ("Exported", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("Project", engine.project.name if engine.project else "—"),
        ("Log file", engine.session_name or "—"),
        ("Source", engine.source_label or "—"),
        ("Link", engine.link or "—"),
        ("Framing rule", str(rule_to_json(engine.rule))),
        ("Messages", str(len(frames))),
        ("Duration", f"{span:.2f} s"),
        ("Channels", ", ".join(f"{c} ({n})" for c, n in chans.items()) or "—"),
    ]
    w("| | |\n|---|---|")
    for k, v in rows:
        w(f"| {k} | {_cell(v)} |")
    w("")

    # -- what the user already knows -------------------------------------------------------
    w("## What the user has worked out so far")
    w("")
    if proto.checksum:
        w(f"- Checksum: `{proto.checksum.to_json()}`")
    if not proto.frame_types:
        w("- Nothing named yet.")
    for t in proto.frame_types:
        scope = f" on `{t.channel}`" if t.channel else ""
        w(f"- **{t.name}**{scope}: matches `{t.match}`")
        for fl in t.fields:
            unit = f" {fl.unit}" if fl.unit else ""
            scale = f", ×{fl.scale:g}" if fl.scale != 1 else ""
            w(f"  - field `{fl.name}` at byte {fl.offset}, {fl.type}{scale}{unit}")
        for v in t.verdicts:
            ok = "✓ display did what was expected" if v.get("ok") else "✗ did not work"
            note = f" — {v['note']}" if v.get("note") else ""
            w(f"  - sent `{v.get('sent', '')}`: {ok}{note}")
    w("")

    # -- events -------------------------------------------------------------------------------
    w("## Events the user marked")
    w("")
    if not engine.markers:
        w("None. (Press Space in Tapline when something happens on the display.)")
    for m in engine.markers:
        note = f" — {m.note}" if m.note else ""
        exp = f" (expected: {m.expected})" if m.expected else ""
        w(f"### +{m.t - t0:.3f} s · {m.label}{note}{exp}")
        w("")
        before = [f for f in frames if f.t_start <= m.t][-MARKER_CONTEXT:]
        after = [f for f in frames if f.t_start > m.t][:MARKER_CONTEXT]
        w("```")
        for f in before:
            w(f"  before  +{f.t_start - t0:9.3f}  {f.channel:<14} {f.hex()}")
        w(f"  ------  +{m.t - t0:9.3f}  ★ {m.label}")
        for f in after:
            w(f"  after   +{f.t_start - t0:9.3f}  {f.channel:<14} {f.hex()}")
        w("```")
        w("")

    # -- message kinds -------------------------------------------------------------------------
    w("## Kinds of message")
    w("")
    w("Messages grouped by channel and type (or first two bytes and length). The byte map shows "
      "each position as `C`onstant, `V`aries, `R`arely changes or `+` counter.")
    w("")
    groups: "OrderedDict[str, list[Frame]]" = OrderedDict()
    for f in frames:
        groups.setdefault(_group_key(engine, f), []).append(f)
    for key, fs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        times = [f.t_start for f in fs]
        period = (times[-1] - times[0]) / (len(times) - 1) if len(times) > 1 else 0
        w(f"### {key} — {len(fs)}×" + (f", about every {period * 1000:.0f} ms" if period else ""))
        w("")
        var = variability(fs)
        code = {"constant": "C", "varies": "V", "rare": "R", "counter": "+"}
        w("```")
        w("byte  " + " ".join(f"{v['offset']:>2}" for v in var))
        w("map   " + " ".join(f"{code[v['kind']]:>2}" for v in var))
        distinct = OrderedDict()
        for f in fs:
            distinct.setdefault(f.data, 0)
            distinct[f.data] += 1
        examples = list(distinct.items())[:8]
        for data, n in examples:
            w(f"ex    {' '.join(f'{b:02X}' for b in data)}   ({n}×)")
        if len(distinct) > len(examples):
            w(f"…     {len(distinct) - len(examples)} more different versions")
        w("```")
        w("")

    # -- full log ------------------------------------------------------------------------------
    w("## Message log")
    w("")
    shown = frames[:MAX_LOG_LINES]
    if len(frames) > len(shown):
        w(f"First {len(shown)} of {len(frames)} messages. The complete log is in the Tapline "
          f"log file `{engine.session_name}` and the CSV export.")
        w("")
    marks = sorted(engine.markers, key=lambda m: m.t)
    mi = 0
    w("```")
    w("  time(s)  channel          hex" + "  |  decoded")
    for f in shown:
        while mi < len(marks) and marks[mi].t <= f.t_start:
            w(f"  ★ +{marks[mi].t - t0:.3f}  {marks[mi].label}" + (f" — {marks[mi].note}" if marks[mi].note else ""))
            mi += 1
        t = engine.type_of(f)
        dec = ""
        if t:
            d = P.decode(proto, f.data, t)
            dec = f"  |  {t.name} " + ", ".join(f"{k}={v}" for k, v in d.items()) if d else f"  |  {t.name}"
        sent = "  (sent by Tapline)" if is_sent(f.channel) else ""
        w(f"{f.t_start - t0:9.3f}  {f.channel:<16} {f.hex()}{dec}{sent}")
    while mi < len(marks):
        w(f"  ★ +{marks[mi].t - t0:.3f}  {marks[mi].label}")
        mi += 1
    w("```")
    w("")
    return "\n".join(out)

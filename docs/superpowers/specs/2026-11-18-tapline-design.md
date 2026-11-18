# Tapline — Design

Date: 2026-11-18 · Status: approved by user 2026-11-18

## 1. Intent

The user has a controller board driving a display and wants to understand the data flowing between them. The tool must:

1. **Capture** the traffic from the controller to the display (and back, when the link has two wires).
2. **Help decode it** by combining the raw bytes with hints from the user ("right now the screen shows 25 °C", "I just pressed the UP button").
3. **Verify the guesses by sending.** Once the user believes they understand a message, they compose it (or replay or edit a captured one), send it to the display themselves, and see whether the display reacts as predicted.

The user's own link is **UART** (confirmed by the user), and they own **USB-to-TTL adapters**. A cheap **logic analyzer** (FX2-based, sigrok-compatible) can be bought easily.

## 2. Goals, non-goals, anti-goals

**Goals**
- Support, in phases: UART → RS-232/RS-485 → logic-analyzer buses (I²C, SPI, UART via LA) → CAN → transmit on I²C/SPI via a bridge.
- Analysis features shared by all buses: framing, hex/ASCII view, event markers, value search, frame diff, field templates, checksum detection, export.
- Send/replay of frames, with a safety gate.
- Runs locally on Windows, macOS and Linux (the user's OS is unknown).

**Non-goals**
- High-speed video links (MIPI-DSI, LVDS, parallel RGB, HDMI).
- Cloud features, accounts, multi-user.
- Replacing PulseView as a waveform viewer. Raw waveforms stay in PulseView; this tool works at the byte and frame level.

**Anti-goals** (a failure even if it technically works)
- Heavy, cluttered or dark "hacker" UI. It must feel **light, friendly and lightweight**: light colours, a little interactive, quick to start.
- Accidentally driving the bus while sniffing. Sending must never happen unless the user has deliberately switched into Send mode.
- A setup that needs compilers, Node or a build step on the user's machine.

## 3. Constraints

- Python 3.10+ on the user's machine. One install command, one run command. The app opens in the default browser.
- No frontend build step. Plain HTML/CSS/JS served by the Python app. A small vendored library is allowed if needed; no CDN, so it works offline at the bench.
- The developer (Claude) builds in a cloud container **without access to the user's hardware**. Every phase therefore needs a **simulator source** and a test suite that runs without hardware. The user does the real-hardware checks.
- Logic analyzers are accessed only through `sigrok-cli` (an external program the user installs). The app must work without it and only disable LA features when it's missing.

## 4. Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| **A. Python engine + browser UI (local web server)** | One process. Python reaches all hardware (pyserial, sigrok-cli, python-can). Browser gives rich, light UI cheaply. Needs Python installed. | **Picked** by the user on Claude's recommendation |
| B. Pure browser app (Web Serial API) | Zero install, great for UART. Cannot reach sigrok or most CAN adapters, so needs a second program from Phase 3; Chrome/Edge only. | Rejected |
| C. Python desktop app (Qt) | Single native app. Slower to build rich analysis views; heavier install; looks dated unless heavily styled. | Rejected |

## 5. Architecture

```
 Browser UI (HTML/CSS/JS)
        │  WebSocket (live frames, status)  +  REST (config, actions)
 ┌──────┴───────────────────────────────────────────────┐
 │ FastAPI app (uvicorn, localhost:8765)                 │
 │                                                      │
 │  Sources ──► Framer ──► Session store ──► Analysis    │
 │  (serial, simulator, sigrok, can)         (pure Py)   │
 │                                                      │
 │  Senders (serial, can, pico-bridge)  ◄── Send gate    │
 └──────────────────────────────────────────────────────┘
```

### 5.1 Core data model
- **Chunk:** raw bytes from a source with a host timestamp and a `channel` (e.g. `ctrl→disp`, `disp→ctrl`, or an I²C address).
- **Frame:** a message cut out of the chunk stream by the active framing rule: `{id, t_start, t_end, channel, bytes, flags (checksum_ok, error…), markers[]}`.
- **Marker:** a user event `{t, label, note, expected_value?}`.
- **Protocol definition** (JSON, user-editable, saved per project): framing rule + named frame types (match pattern) + fields (`offset, length, type: u8/u16le/u16be/i16…/ascii/bcd/bits, scale, unit`) + checksum spec.
- **Session file:** JSON Lines (header, then raw chunks and markers). Frames are *derived*, never stored as truth, so a session can be reopened offline and re-framed with a different rule.
- **Project:** a folder the user picks (default `~/Tapline/<name>/`) holding `protocol.json` and `sessions/*.jsonl`. One project = one device pair under study, with many sessions. Markers are anchored to **timestamps**, not frame IDs, so they survive re-framing. Send verdicts are stored on **frame types** in `protocol.json`, along with the exact bytes sent.

### 5.2 Sources (plugin interface: `start()`, `stop()`, async iterator of Chunks)
- `SerialSource`: pyserial; port, baud, data bits, parity, stop bits — each a **dropdown** (baud also accepts a custom value). The user may set them manually at any time.
- **Auto-detect serial settings** (Phase 1, user-requested): a "Detect" button listens on the chosen port, cycling through common bauds (1200…921600, plus 250000/500000/1000000) and formats (8N1, 8E1, 8O1, 7E1, 8N2) for a short window each. Each candidate is scored on framing/parity errors reported by the driver where available, byte-distribution plausibility (no flood of 0x00/0xFF/garbage), and repeating-structure score (the same frame patterns recurring). The top 3 candidates are shown with a confidence and a preview of the bytes, and one click applies a candidate to the dropdowns. Detection is listen-only. The simulator can emit at a chosen baud through a virtual serial pair so the detector is testable. Listing ports includes USB VID/PID names. Two adapters can run at once: one on controller TX, one on display TX, labelled as two channels.
- `SimulatorSource`: a fake "display controller" that emits a realistic made-up protocol (header, length, command, values, checksum), changes values on a timeline, and responds to sent frames. Used for demo mode, development and tests.
- `SigrokSource` (Phase 3): runs `sigrok-cli` capture-first (`.sr` file, then decode, following the installed sigrok skill's guidance). Also imports existing `.sr` files. Decoders: uart, i2c, spi, with output mapped to Chunks/Frames.
- `CanSource` (Phase 4): python-can (slcan, gs_usb/candleLight, PCAN, socketcan). CAN frames map 1:1 to Frames, with the ID as a field.

### 5.3 Framing rules (selectable, live re-framing of the session)
- Idle gap (default: new frame after N ms of silence, with auto-suggested N)
- Delimiter (start bytes and/or end bytes, e.g. `5A A5` … or … `FF FF FF`)
- Header + length byte (offset, size, which bytes it counts)
- Fixed length
- Bus-native (I²C transaction, SPI CS-low window, CAN frame)
- **"Guess framing"** button: tries the rules above on the capture and ranks them by consistency.

### 5.4 Analysis (pure Python module, fully unit-tested)
- **Value search:** the user enters a number or text, and the app searches all frames for u8, u16/u32 in both byte orders, BCD, ASCII digits, scaled ×10/×100 and signed versions. Hits are ranked, with a preference for those near a marker.
- **Marker correlation:** shows which frames appeared or changed within ±X ms of each marker.
- **Diff:** byte-level diff between two selected frames, or a "variability map" across all frames of one type (constant / rarely changing / always changing / counter-like).
- **Checksum detection:** tries sum8, XOR8, two's-complement sum, CRC-8 variants, CRC-16 (Modbus, CCITT…) over candidate ranges, and reports the ones that match ≥ 95 % of frames.
- **Field decode:** applies the protocol definition to every frame and shows named values. Plots a numeric field over time (small line chart).
- **Export:** CSV of frames or decoded fields; protocol JSON.

### 5.5 Sending (the "test my guess" loop)
- **Send mode** is a separate, clearly coloured mode. Entering it shows a one-time checklist: disconnect the controller TX from the display, connect adapter TX → display RX, common GND, voltage matches.
- **Composer:** start from a captured frame (right-click → "Edit & send"), or from a frame type in the protocol definition with fields filled in by value (e.g. temperature = 30.0). Checksum and length are recomputed automatically.
- Send once, repeat every N ms, or replay a sequence of captured frames with original timing.
- After sending, the user clicks **"Display did what I expected" / "Didn't"**. The verdict is stored with the frame type in the protocol definition, so the confirmed and unconfirmed parts are visible.
- Phase 5: I²C/SPI sending through a **Raspberry Pi Pico bridge** (MicroPython script shipped with the app; serial command protocol to the Pico).

## 6. UI

Light theme: off-white background, soft blue accent, mint for "confirmed", amber for markers, soft red only for errors. System font, generous spacing, rounded corners. One screen, three areas:

```
┌──────────────────────────────────────────────────────────────────┐
│ ● Tapline   [Source: COM5 · 115200 8N1 ▾]  ▶ Start  ■ Stop    │
│                 Mode: (Sniff) Send          Framing: Gap 5ms ▾    │
├──────────────────────────────────────┬───────────────────────────┤
│ Frames (live)          🔍 filter      │ Inspector                 │
│ 12:01:03.120 ctrl→disp 5A A5 06 82 …  │ Frame #213  · 9 bytes     │
│ 12:01:03.220 ctrl→disp 5A A5 06 82 …  │ 5A A5 06 82 00 10 00 FA   │
│ ── ★ "shows 25.0°C" ──                │  ▲ hover: 0xFA=250 'ú'    │
│ 12:01:03.320 ctrl→disp 5A A5 06 82 …  │ Fields: temp = 25.0 °C ✓  │
│                                      │ [Diff with…] [Edit & send]│
├──────────────────────────────────────┴───────────────────────────┤
│ Tools:  [★ Mark event (Space)] [Find value] [Guess framing]        │
│         [Checksum finder] [Variability map] [Protocol] [Export]    │
└──────────────────────────────────────────────────────────────────┘
```

Interaction touches: Space adds a marker with a quick label box. Hovering a byte shows dec/char/binary. Click a byte column to highlight the same offset in every frame. Drag across bytes → "Make this a field…". Byte colours in the variability map. Toasts for actions. First launch opens **demo mode** (simulator) with a 4-step guided tour, so the user can learn the tool before wiring anything.

## 7. Phases (each ends with a usable app)

1. **UART sniff + analyse + send.** Core, SerialSource (with settings dropdowns + auto-detect), SimulatorSource, framing, all analysis features, protocol editor, Send mode over serial, sessions, export, demo + tour.
2. **RS-232 / RS-485.** Same serial path. Adapter guidance in UI, RS-485 half-duplex notes (direction from frame content and timing), optional Modbus RTU auto-decode.
3. **Logic analyzer via sigrok-cli.** Device scan, capture-first capture, `.sr` import; I²C, SPI and UART decoders; channel-mapping UI; bus-native framing.
4. **CAN.** python-can sources, CAN-ID filters, sending CAN frames.
5. **I²C/SPI transmit** via a Pico bridge (MicroPython script + sender).

Phase 1 gets the detailed plan first. Later phases are planned when reached.

## 8. Testing

- pytest unit tests for framers, value search, checksum finder, field decode, diff and composer (checksum recompute).
- Integration tests: SimulatorSource → framer → WebSocket → client, all with no hardware. Serial path tested with a virtual serial pair (`socat` pty pair on Linux) so pyserial is exercised for real.
- Playwright smoke test of the UI in demo mode (start, mark, find value, send).
- User-side checklist per phase for real hardware.

## 9. Distribution

`pip install -r requirements.txt` then `python -m tapline` (or `run.bat` / `run.sh`). Opens `http://localhost:8765`. Delivered as a git repository. README with wiring diagrams for sniffing and sending.

## 10. Calls made by Claude (user can override)

- Name "Tapline"; port 8765; FastAPI + uvicorn + pyserial; no frontend framework.
- JSON Lines sessions, JSON protocol definitions.
- Simulator protocol is invented (not copied from any vendor).
- Host timestamps for serial (ms resolution, adequate for framing); precise timing comes from the logic analyzer in Phase 3.

## 11. Left to the builder

File/module layout, exact library versions, chart drawing approach (hand-rolled SVG/canvas preferred to stay light), CRC variant list, tour wording, colour hex values within the stated palette.

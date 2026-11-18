# Tapline Phases 2–5 Implementation Plan

> Executed inline (superpowers:executing-plans) on branch `phase1-uart` continuation; final whole-branch review at the end.

**Goal:** Extend Tapline beyond TTL UART: RS-232/RS-485 + Modbus RTU (P2), logic analyzers via sigrok-cli for UART/I²C/SPI (P3), CAN via python-can (P4), and I²C/SPI transmit through a Raspberry Pi Pico bridge (P5). Each phase gets a simulator so everything is testable without hardware.

**Spec:** `docs/superpowers/specs/2026-11-18-tapline-design.md` (§5.2, §7) + Phase 2 design approved in chat 2026-11-18.

## Global constraints (unchanged from Phase 1)
Local-only server, JSON-only API, no build step, light UI, Sniff never transmits, every send passes the server-side Send-mode gate (incl. Pico and CAN), friendly errors, optional deps (python-can, sigrok-cli) missing → feature disabled with a clear message, not a crash.

## Review focus
1. Optional dependency missing (no sigrok-cli / no python-can) → clear message, rest of app works.
2. RS-485 single-wire traffic: request/response labelled correctly even when adapter latency merges a request and its reply into one read (Modbus CRC framing must split them).
3. sigrok output variations (no CS line for SPI, I²C repeated start, UART framing errors) → no crash, sensible frames.
4. CAN send text in different shapes (`123#DEADBEEF`, `123 DE AD`, extended IDs, >8 bytes) → parsed or rejected clearly.
5. Pico bridge not answering / wrong firmware → timeout with a friendly message, never hangs the server.

## Tasks
1. **Framing kinds** `modbus` (CRC-validated Modbus RTU splitting) and `native` (one chunk = one frame); JSON round-trip. Tests: merged request+reply split; junk resync; native.
2. **`modbus.py`**: `decode(data, prefer=None) -> dict|None` (addr, fn, name, kind request/response/exception, summary, registers/coils), `build(addr, fn, start, qty_or_value, values=None) -> bytes`. Tests on FC1-6, 15, 16, exceptions, bad CRC.
3. **`rs485.py` RoleTracker** labelling frames `master→slave` / `slave→master` (Modbus-aware, else alternation within reply window). Engine applies it when `link == "rs485"`.
4. **Modbus RS-485 simulator** (`sim-modbus`): master polls slave 1 (FC3 regs 0-9) every 0.5 s, answers user FC3/FC6/FC16 requests; writes change registers.
5. **Engine/server P2**: `start(..., link=)`, frame JSON gets `modbus` summary, `send(..., wait_ms)` returns replies, `/api/modbus/build`.
6. **`la_sim.py`**: write sigrok `.sr` files from generated UART/I²C/SPI waveforms; `demo_capture(path)`.
7. **`sources/sigrok.py`**: `available()`, `scan()`, `capture()`, `decode(path, spec) -> list[Chunk]` for uart/i2c/spi (CS optional), `SigrokSource` (capture-then-decode or import). Tests run real sigrok-cli on generated `.sr` files (skip if absent).
8. **Frame types filtered by channel** (needed for I²C addresses and CAN IDs): `FrameType.channel` optional; `match_type(proto, data, channel)`.
9. **`sources/can_source.py`**: `CanSource` (python-can, listen-only when supported), `parse_can_text`, `SimCanSource` (`sim-can`: 0x100 rpm, 0x101 speed, 0x200 status; replies 0x301 to 0x300). Tests with python-can `virtual` bus.
10. **Pico bridge**: `bridge/pico_bridge.py` (MicroPython; pure `handle(line, hw)`), `tapline/pico.py` host `PicoBridge` with timeouts; engine `pico_send` behind Send gate. Tests: handle() with fake hardware; host over pty pair talking to handle().
11. **Server endpoints** for sigrok (scan, import as base64 JSON, capture), CAN options, Pico (ports, connect, send), modbus build; status gains `link`, `capabilities`.
12. **UI**: grouped Source picker (Demos / Serial / Logic analyzer / CAN), per-kind settings, Link type + wiring guides, Modbus builder, CAN send format, Pico target in Composer, "wait for reply".
13. **Tests + README** (wiring for RS-232/485, logic analyzer, CAN, Pico pinout), full suite, UI smoke for each demo, final review.

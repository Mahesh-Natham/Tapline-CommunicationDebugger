# Tapline Phase 1 (UART) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local, light-themed browser app that sniffs a UART link, helps the user decode it with markers, value search, diff, variability map and checksum finder, auto-detects serial settings, and sends or replays frames to test guesses.

**Architecture:** A Python package `tapline`. It has pure analysis modules (framing, search, diff, checksums, protocol, autodetect) with no I/O, sources (serial, simulator) producing `Chunk`s, a FastAPI server with REST + one WebSocket, and a no-build static frontend (`index.html`, `app.css`, `app.js`).

**Tech Stack:** Python ≥3.10, FastAPI, uvicorn, pyserial, pytest + pytest-asyncio, httpx (TestClient). socat for virtual serial pairs in tests. Playwright (pre-installed) for the UI smoke test.

**Spec:** `docs/superpowers/specs/2026-11-18-tapline-design.md`

## Global Constraints

- Python 3.10+; install = `pip install -r requirements.txt`; run = `python -m tapline` → opens `http://localhost:8765`. Bind to `127.0.0.1` only.
- No frontend build step, no CDN; everything in `tapline/static/` works offline.
- Light theme: off-white background, soft blue accent, mint = confirmed, amber = markers, soft red = errors only. System font, rounded corners, generous spacing.
- Sending is impossible unless the session is in Send mode (server enforces, not just the UI).
- Auto-detect is listen-only.
- Every feature must be testable without hardware (simulator / socat pty pair).
- Sessions = JSON Lines of raw chunks + markers; frames are derived. Project = folder with `protocol.json` + `sessions/*.jsonl`, default `~/Tapline/<name>/`.
- Markers anchor to timestamps; send verdicts are stored on frame types in `protocol.json` with the exact bytes sent.
- The simulator protocol is invented (below), not copied from a vendor.

## Review Focus

1. **Port unplugged / busy / permission denied** → the source stops cleanly, the UI shows a friendly message (e.g. "COM5 is in use by another program"), and the captured data is kept. Test: Task 6 `test_serial_source_port_disappears`, Task 8 `test_start_bad_port_returns_friendly_error`.
2. **Long captures (hours, 100k+ frames)** → the UI stays responsive: the frame list renders at most the last 2000 rows plus "load older", and the server never sends the full list per update. Test: Task 8 `test_frames_endpoint_paginates`.
3. **Value search input in many shapes** (`25`, `25.5`, `-3`, `0x19`, `25°C`, `hello`) → parsed sensibly, never a 500. Test: Task 3 `test_parse_query_shapes`.
4. **Degenerate framing** (no gaps → one giant frame; delimiter never seen) → frames are capped at 4096 bytes and flagged `oversize`, not lost or hung. Test: Task 2 `test_gap_framer_caps_oversize`.
5. **Send outside Send mode** or with malformed hex → rejected with a clear 4xx, nothing written to the port. Test: Task 7 `test_send_requires_send_mode`, `test_send_rejects_bad_hex`.

---

## Simulator protocol (fixed copy, used by Tasks 5, 9 and the tests)

Frame: `5A A5 LEN CMD PAYLOAD… CHK`. `LEN` = number of bytes after LEN (CMD + payload + CHK). `CHK` = sum8 of LEN, CMD and payload.
- `0x10` temperature: payload i16 little-endian, °C ×10. Every 500 ms; value walks 20.0 → 30.0 and back in 0.5 steps.
- `0x11` status text: ASCII, `"READY"` / `"HEAT"` alternating every 5 s.
- `0x20` heartbeat: u8 counter, every 1 s.
- Inbound (sent by the user): `0x30` backlight, payload u8 0–100 → the simulator replies on channel `disp→ctrl` with `5A A5 03 31 <level> CHK`.

## File structure

```
tapline/
  pyproject.toml  requirements.txt  run.sh  run.bat  README.md  .gitignore
  tapline/__init__.py   __main__.py      # launch uvicorn + open browser
  tapline/model.py                        # Chunk, Frame, Marker, SerialConfig
  tapline/framing.py                      # framing rules + guess_framing
  tapline/search.py                       # value search
  tapline/diff.py                         # diff + variability map
  tapline/checksums.py                    # algorithms + finder
  tapline/protocol.py                     # protocol definition, decode, compose
  tapline/project.py                      # project folders + session JSONL
  tapline/autodetect.py                   # serial settings scoring/detect
  tapline/uartsim.py                      # bit-level UART encode/decode (tests + sim)
  tapline/sources/base.py  serial_source.py  simulator.py
  tapline/engine.py                       # live session state, mode, send gate
  tapline/server.py                       # FastAPI REST + WS
  tapline/static/index.html  app.css  app.js
  tests/…  (one test file per module)
```

---

### Task 1: Scaffold + data model

**Files:** Create `pyproject.toml`, `requirements.txt`, `.gitignore`, `tapline/__init__.py`, `tapline/model.py`, `tests/test_model.py`. `git init`.

**Interfaces — Produces:**
- `@dataclass Chunk(t: float, channel: str, data: bytes)`; `to_json()->dict` (data as hex string) / `Chunk.from_json(d)`.
- `@dataclass Frame(id: int, t_start: float, t_end: float, channel: str, data: bytes, flags: set[str] = field(default_factory=set))`; `hex()->str` space-separated upper-case.
- `@dataclass Marker(t: float, label: str, note: str = "", expected: str | None = None)` with to/from_json.
- `@dataclass SerialConfig(port: str, baud: int = 115200, bytesize: int = 8, parity: str = "N", stopbits: float = 1)`; `label()->"115200 8N1"`.
- Channel constants `CTRL_TO_DISP = "ctrl→disp"`, `DISP_TO_CTRL = "disp→ctrl"`.

- [ ] Step 1: Tests `test_chunk_roundtrip_json`, `test_frame_hex` (`Frame(...data=b"\x5a\xa5").hex() == "5A A5"`), `test_serialconfig_label` (`"9600 8E1"`).
- [ ] Step 2: Run `pytest tests/test_model.py -v` → FAIL (import error).
- [ ] Step 3: Implement.
- [ ] Step 4: Run → PASS.
- [ ] Step 5: Commit `feat: scaffold and data model`.

### Task 2: Framing

**Files:** `tapline/framing.py`, `tests/test_framing.py`

**Interfaces — Produces:**
- Rule dataclasses: `GapRule(gap_ms: float = 5)`, `DelimiterRule(start: bytes = b"", end: bytes = b"")`, `LengthRule(header: bytes, len_offset: int, len_size: int = 1, len_adjust: int = 0)` (total frame length = `len_offset + len_size + value + len_adjust`), `FixedRule(length: int)`.
- `rule_from_json(d: dict)` / `rule_to_json(rule)->dict` with `"kind"` ∈ `gap|delimiter|length|fixed`.
- `frame_chunks(chunks: list[Chunk], rule) -> list[Frame]`: frames per channel independently, IDs assigned in time order, `MAX_FRAME = 4096` → split and flag `"oversize"`. Leftover bytes that don't complete a frame are emitted with flag `"partial"`.
- `class StreamFramer(rule)` with `feed(chunk) -> list[Frame]` and `flush() -> list[Frame]` for live use (same results as `frame_chunks`; Gap rule closes a frame when the next chunk's `t` is > gap after the last byte, or on `flush`).
- `guess_framing(chunks) -> list[tuple[rule, float]]`: top candidates sorted by score 0–1. Score = fraction of frames that are not partial/oversize × consistency (for length rules: fraction whose length field matches). Candidate generation: gap at 2/5/10/20 ms; delimiter = most frequent 2-byte prefix that starts ≥ 60 % of gap-framed frames; length rule tried at offsets 1–4 after that prefix with adjust −2..+2.

- [ ] Step 1: Tests: `test_gap_rule_splits_on_silence`; `test_delimiter_rule_start_only`; `test_length_rule_sim_protocol` (simulator bytes concatenated in one chunk → correct frames with `LengthRule(b"\x5a\xa5", 2, 1, 0)`); `test_stream_framer_matches_batch`; `test_gap_framer_caps_oversize` (10 000 bytes, no gaps → 3 frames of ≤4096, flagged `oversize`); `test_guess_framing_finds_length_rule` (sim stream → top candidate is LengthRule header `5A A5` offset 2, score > 0.9).
- [ ] Step 2: Run → FAIL.  Step 3: Implement.  Step 4: Run → PASS.  Step 5: Commit `feat: framing rules and guesser`.

### Task 3: Value search, diff, variability

**Files:** `tapline/search.py`, `tapline/diff.py`, `tests/test_search.py`, `tests/test_diff.py`

**Interfaces — Produces:**
- `parse_query(q: str) -> Query(numbers: list[float], text: str | None)`. It strips units/°/%, accepts hex `0x..`, decimals and negatives. Non-numeric text → text search only.
- `@dataclass Hit(frame_id: int, offset: int, length: int, encoding: str, score: float)`; encodings: `u8, i8, u16le, u16be, i16le, i16be, u32le, u32be, bcd, ascii, x10:<enc>, x100:<enc>`.
- `find_value(frames, q: str, markers: list[Marker] = [], window_s: float = 2.0) -> list[Hit]`, sorted by score. Base score 1.0. ×1.5 if the frame is within `window_s` of a marker. ×0.5 for u8 matches (too common). Max 500 hits.
- `near_marker(frames, marker, window_s=2.0) -> dict(new_types: list[str], changed: list[int])`: frames in the window whose 2-byte+cmd prefix (first 4 bytes) was unseen before, or whose bytes differ from the previous frame with the same prefix.
- `diff_frames(a: bytes, b: bytes) -> list[str]` per offset: `"same" | "changed" | "added" | "removed"`.
- `variability(frames: list[Frame]) -> list[dict]` per offset: `{offset, kind: "constant"|"rare"|"varies"|"counter", distinct: int}`. Counter = increments by 1 mod 256 in ≥ 80 % of steps. Rare = changes in < 10 % of steps.

- [ ] Step 1: Tests: `test_parse_query_shapes` (`"25°C"`→[25], `"0x19"`→[25], `"-3"`, `"25.5"`, `"hello"`→text, `""`→empty, no exceptions); `test_find_value_i16le_x10` (frame with `FA 00` → hit encoding `x10:i16le` for "25"); `test_find_value_marker_boost`; `test_find_ascii`; `test_near_marker_reports_changed_frames`; `test_diff_frames`; `test_variability_detects_counter_and_constant`.
- [ ] Steps 2–5 as usual. Commit `feat: value search, diff, variability`.

### Task 4: Checksums + protocol definition

**Files:** `tapline/checksums.py`, `tapline/protocol.py`, `tests/test_checksums.py`, `tests/test_protocol.py`

**Interfaces — Produces:**
- `ALGORITHMS: dict[str, Callable[[bytes], int]]`: `sum8, xor8, twos8 (−sum8 & 0xFF), crc8 (poly 0x07), crc8_maxim, crc16_modbus, crc16_ccitt_false, crc16_xmodem`; `width(name)->1|2`.
- `@dataclass ChecksumSpec(algo: str, start: int, end_from_end: int, pos_from_end: int, byteorder: str = "little")`. The range is `data[start : len-end_from_end]` and the checksum sits at `len-pos_from_end`.
- `apply_checksum(spec, data: bytes) -> bytes` (returns data with checksum rewritten); `check(spec, data) -> bool`.
- `find_checksums(frames, min_ratio=0.95) -> list[tuple[ChecksumSpec, float]]`. Try every algo × start 0..4 × checksum at the end (pos 1 for 8-bit, pos 2 for 16-bit, both byte orders), on frames ≥ 3 bytes.
- `protocol.py`: dataclasses `Field(name, offset, type, scale=1.0, unit="")` (types: `u8,i8,u16le,u16be,i16le,i16be,u32le,u32be,ascii:<n>,bcd:<n>`), `FrameType(name, match: str (hex prefix, e.g. "5A A5 ?? 10"; ?? = wildcard), fields: list[Field], verdicts: list[dict])`, `Protocol(framing: dict, checksum: ChecksumSpec|None, frame_types: list[FrameType])`. Functions: `load(path)->Protocol`, `save(proto, path)`, `match_type(proto, data)->FrameType|None`, `decode(proto, data)->dict[name, value]`, `compose(proto, type_name, values: dict, base: bytes | None) -> bytes` (writes field values into base, or into a zero-filled template sized to the furthest field, then reapplies the checksum and fixes the length byte if the framing is a LengthRule), `add_verdict(proto, type_name, sent_hex, ok: bool, note="")`.

- [ ] Step 1: Tests: `test_sum8_found_on_sim_frames` (finder over simulator frames returns sum8 start=2 with ratio 1.0 among results); `test_apply_checksum_roundtrip`; `test_crc16_modbus_known_vector` (`01 03 00 00 00 0A` → `C5 CD` little-endian); `test_decode_temperature` (frame `5A A5 04 10 FA 00 CHK` with field temp i16le scale 0.1 → 25.0); `test_compose_temperature_fixes_checksum`; `test_protocol_save_load_roundtrip` incl. verdicts.
- [ ] Steps 2–5. Commit `feat: checksum finder and protocol definitions`.

### Task 5: UART bit model + simulator + auto-detect

**Files:** `tapline/uartsim.py`, `tapline/sources/base.py`, `tapline/sources/simulator.py`, `tapline/autodetect.py`, tests for each.

**Interfaces — Produces:**
- `uartsim.encode(data: bytes, bytesize=8, parity="N", stopbits=1) -> list[int]` (bits incl. idle-high gaps, LSB first); `uartsim.reinterpret(data, true: SerialConfig, read: SerialConfig) -> tuple[bytes, int]`. It resamples the bitstream at `read.baud` (sample mid-bit) and returns the received bytes and the count of framing/parity errors. It models what a mis-configured adapter receives.
- `sources/base.py`: `class Source(Protocol)`: `async def start()`, `async def stop()`, `def chunks() -> AsyncIterator[Chunk]`, optional `async def write(data: bytes)`.
- `sources/simulator.py`: `class SimulatorSource(Source)` with `speed: float = 1.0` and `seed: int = 1`, emitting the simulator protocol above on `CTRL_TO_DISP`. `write()` parses `0x30` and emits the reply on `DISP_TO_CTRL`. Also `sim_bytes(seconds: float) -> list[Chunk]`, a deterministic offline generator used by tests.
- `autodetect.py`: `BAUDS = [1200,2400,4800,9600,14400,19200,38400,57600,115200,230400,250000,460800,500000,921600,1000000]`; `FORMATS = ["8N1","8E1","8O1","7E1","8N2"]`. `score(data: bytes, errors: int = 0) -> float` (0–1). It combines: error penalty; garbage-byte ratio (0x00, 0xFF, 0x80, 0xF0, 0xF8, 0xFC, 0xFE share); repeated-structure score (share of bytes covered by 2–4-byte n-grams occurring ≥ 3 times); and an empty-data → 0 rule. `async def detect(read_fn: Callable[[SerialConfig, float], Awaitable[tuple[bytes,int]]], port: str, window_s=0.4, progress=None) -> list[Candidate(config, score, preview_hex)]`, returning the top 3. `read_fn` is injected (a real serial read in production, `reinterpret` in tests). Try all 15 bauds with 8N1 first, then the 5 formats at the top 2 bauds.

- [ ] Step 1: Tests: `test_reinterpret_same_config_is_identity`; `test_reinterpret_wrong_baud_garbles`; `test_simulator_frames_have_valid_checksum`; `test_simulator_replies_to_backlight`; `test_detect_finds_true_settings[115200-8N1, 9600-8N1, 19200-8E1]` (fake read_fn = `reinterpret(sim bytes, true, cfg)` → top candidate equals the truth); `test_score_empty_is_zero`.
- [ ] Steps 2–5. Commit `feat: simulator, UART model, auto-detect`.

### Task 6: Serial source + project/session storage

**Files:** `tapline/sources/serial_source.py`, `tapline/project.py`, `tests/test_serial_source.py`, `tests/test_project.py`, `tests/conftest.py` (fixture `pty_pair` using `socat -d -d pty,raw,echo=0 pty,raw,echo=0`; skip if socat missing).

**Interfaces — Produces:**
- `class SerialSource(Source)` with `__init__(cfg: SerialConfig, channel=CTRL_TO_DISP)`. It reads in a thread (`serial.Serial(timeout=0.01)`) and pushes `Chunk(time.time(), …)` onto an asyncio queue. On `SerialException` it puts a final error event and stops; `self.error: str | None` holds a friendly message.
- `list_ports() -> list[dict(device, description, vid, pid)]`.
- `friendly_error(exc) -> str` mapping: permission denied → "Permission denied — on Linux add yourself to the dialout group"; busy/access denied → "<port> is in use by another program"; not found → "<port> not found — is the adapter plugged in?"
- `async def read_window(cfg, seconds) -> tuple[bytes, int]` (for autodetect; errors = 0, since pyserial doesn't report them portably).
- `project.py`: `class Project(root: Path)` with `open_or_create(name, base=~/Tapline)`, `protocol_path`, `new_session() -> SessionWriter`, `list_sessions()`, `load_session(name) -> (chunks, markers, meta)`. `SessionWriter.write_chunk / write_marker / close` appends JSONL lines `{"type":"chunk"|"marker"|"meta", ...}` and flushes every write.

- [ ] Step 1: Tests: `test_serial_source_reads_bytes` (write to one pty end → chunk arrives); `test_serial_source_write`; `test_serial_source_port_disappears` (kill socat → `error` set, iterator ends, no exception leaks); `test_friendly_error_messages`; `test_session_roundtrip`; `test_session_survives_truncated_last_line`.
- [ ] Steps 2–5. Commit `feat: serial source and project storage`.

### Task 7: Engine (live state, modes, send gate)

**Files:** `tapline/engine.py`, `tests/test_engine.py`

**Interfaces — Consumes:** everything above. **Produces:**
- `class Engine` with `source`, `mode: "sniff"|"send"`, `rule`, `frames: list[Frame]`, `chunks`, `markers`, `project`, `subscribers` (asyncio queues of event dicts).
- `async start(source_kind: "serial"|"simulator", cfg: SerialConfig|None, cfg2: SerialConfig|None = None)` — `cfg2` opens a second adapter on channel `DISP_TO_CTRL` (spec: two adapters at once); sends go to the first source, `async stop()`.
- `set_rule(rule)` re-frames all chunks and broadcasts `{"type":"reframed","count":n}`.
- `add_marker(label, note="", expected=None, t=None)`.
- `set_mode(mode)`.
- `async send(hex_str: str, repeat_ms: int | None = None, count: int = 1)`: raises `SendNotAllowed` unless mode == "send"; `ValueError` on bad hex. Sent bytes are also recorded as a chunk on channel `"sent"`.
- `async replay(frame_ids: list[int], keep_timing=True)`; `stop_repeat()`.
- Event types broadcast: `frame`, `marker`, `status`, `error`, `reframed`, `detect_progress`.

- [ ] Step 1: Tests (simulator source, speed 20): `test_engine_receives_frames`; `test_send_requires_send_mode`; `test_send_rejects_bad_hex`; `test_send_to_simulator_gets_reply`; `test_set_rule_reframes`; `test_session_written_to_project`.
- [ ] Steps 2–5. Commit `feat: engine with send gate`.

### Task 8: HTTP/WS server + launcher

**Files:** `tapline/server.py`, `tapline/__main__.py`, `run.sh`, `run.bat`, `tests/test_server.py`

**Interfaces — Produces** (JSON; errors are `{"error": "<friendly>"}` with 4xx):
- `GET /api/ports`; `GET /api/options` (bauds, formats, framing kinds).
- `POST /api/start {source, config}`; `POST /api/stop`; `GET /api/status`.
- `POST /api/detect {port}` → runs autodetect, progress over WS, returns candidates.
- `GET /api/frames?after=<id>&limit=<n≤2000>&before=<id>`.
- `POST /api/framing {rule}`; `POST /api/framing/guess`.
- `POST /api/markers`; `GET /api/search?q=`; `POST /api/diff {a,b}`; `GET /api/variability?type=<name>|prefix=<hex>`; `POST /api/checksums/find`.
- `GET/PUT /api/protocol`; `POST /api/protocol/decode {frame_id}`; `POST /api/compose {type, values, base_frame_id?}`; `GET /api/series?type=<name>&field=<name>` → `[[t, value], …]` for the sparkline; `GET /api/markers/{i}/nearby`; `POST /api/verdict`.
- `POST /api/mode {mode}`; `POST /api/send {hex, repeat_ms?, count?}`; `POST /api/replay`; `POST /api/send/stop`.
- `GET /api/projects`, `POST /api/projects/open {name}`, `GET /api/sessions`, `POST /api/sessions/load {name}`; `GET /api/export/frames.csv`, `GET /api/export/protocol.json`.
- `WS /ws`: pushes Engine events. Frames are batched every 100 ms as `{"type":"frames","items":[...]}`.
- `__main__`: `--port 8765`, `--no-browser`, `--project NAME`; runs uvicorn on 127.0.0.1 and opens the browser after startup.

- [ ] Step 1: Tests via `TestClient`: `test_start_simulator_and_get_frames`; `test_frames_endpoint_paginates` (limit capped at 2000); `test_start_bad_port_returns_friendly_error`; `test_send_forbidden_in_sniff_mode` (403); `test_ws_streams_frames`; `test_search_endpoint`; `test_export_csv`.
- [ ] Steps 2–5. Commit `feat: REST/WS server and launcher`.

### Task 9: Frontend

**Files:** `tapline/static/index.html`, `app.css`, `app.js`

Layout per spec §6: top bar (logo "Tapline", source dropdown [Simulator, ports…], baud / data bits / parity / stop-bits dropdowns + custom baud, **Detect** button, Start/Stop, Sniff/Send toggle, framing dropdown + params + "Guess"), left frames list, right inspector, bottom tools bar. Send mode turns the top bar border amber and shows the one-time wiring checklist modal (controller TX disconnected; adapter TX → display RX; common GND; voltage matches).

Interactions: Space = marker with a quick label popover; hovering a byte → tooltip dec/char/bin; clicking a byte column highlights that offset in all visible frames; drag-select bytes in the inspector → "Make a field…" form (name, type, scale, unit) → saved to the protocol; right-click/⋯ on a frame → "Edit & send" opens the composer (hex editable, field inputs if the type is known, auto checksum toggle, Send / Repeat every N ms / Stop) followed by "Display did what I expected ✓ / Didn't ✗"; Find value panel lists hits and clicking one jumps to and highlights it; Variability map colours byte columns (constant grey, rare blue, varies amber, counter violet); Checksum finder shows matches and "Use this"; a sparkline for a selected numeric field; toasts; a 4-step tour on first visit (stored in localStorage, wrapped in try/catch); demo mode = the simulator is preselected on first launch. The list renders ≤ 2000 rows with "Load older".

- [ ] Step 1: Implement the three files.
- [ ] Step 2: Write `tests/test_ui_smoke.py` (Playwright, server on a free port, simulator): start → frames appear; press Space → marker row appears; Find value "25" → ≥ 1 hit; switch to Send → checklist modal; send `5A A5 03 30 32 65` → reply frame on `disp→ctrl`; Detect button present; no console errors.
- [ ] Step 3: Run `pytest tests/test_ui_smoke.py -v` → PASS. Take screenshots (desktop + 390 px wide) and check that the layout looks right in both.
- [ ] Step 4: Commit `feat: light browser UI`.

### Task 10: README + full verification

**Files:** `README.md`

- [ ] Step 1: README: what it is; install/run on Windows/macOS/Linux; wiring diagrams in text for sniffing (adapter GND↔GND, adapter RX ← controller TX; optional second adapter on display TX) and sending (controller TX disconnected; adapter TX → display RX); a voltage warning (3.3 V vs 5 V jumper; never connect RS-232 ±12 V to TTL); a workflow walkthrough (Detect → Guess framing → mark events → Find value → name fields → Edit & send → verdict); troubleshooting (dialout group, COM port busy, garbage = wrong baud).
- [ ] Step 2: Run `pytest -q` → all pass. Run `python -m tapline --no-browser` and curl `/api/status` → 200.
- [ ] Step 3: Commit `docs: README`.

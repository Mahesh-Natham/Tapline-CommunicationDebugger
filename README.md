# Tapline

**Tap the line, read what's on it.** Tapline listens to the serial (UART) link between a controller board and a display, helps you work out what the bytes mean, and lets you send your own messages to check your guesses.

- 🔌 Works with a cheap **USB-to-TTL adapter** (CP2102, CH340, FT232…)
- ✨ **Auto-detects** baud rate and format, or pick them from dropdowns
- ★ **Mark events** (press <kbd>Space</kbd>) when the display changes, then **find that value** in every encoding
- 🧩 **Name bytes as fields** (temperature, mode, text…) and watch them decode live
- 🔍 **Byte map** shows which bytes are constant, changing or counters; a **checksum finder** tries sum, XOR and CRCs
- 📤 **Send mode**: edit a captured message, Tapline fixes the length and checksum, send it, and record whether the display did what you expected
- 🧪 **Demo mode** with a built-in simulator, so you can learn it before wiring anything

Runs locally in your browser on Windows, macOS and Linux. Nothing leaves your computer.

---

## Install & run

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick "Add Python to PATH").

**Windows:** double-click `run.bat`

**macOS / Linux:**

```bash
./run.sh
```

Or by hand:

```bash
pip install -r requirements.txt
```

```bash
python -m tapline
```

Your browser opens at `http://localhost:8765`. Options: `--project NAME` (separate notes per device), `--port 9000`, `--no-browser`.

Your work is saved in `~/Tapline/<project>/`: `protocol.json` holds what you've worked out, and `sessions/*.jsonl` holds every capture.

---

## Wiring

> ⚠️ **Check the voltage first.** Most boards use **3.3 V** or **5 V** logic. Set your adapter's jumper to match. **Never** connect an RS-232 port (±12 V, usually a DB9 connector) straight to a TTL adapter.

### To listen (Sniff mode)

```
 Controller board              Display
   TX ─────────────┬──────────► RX
   RX ◄────────────┼─────────── TX        (optional)
   GND ────────────┼──────┬──── GND
                   │      │
                   ▼      │
            USB-TTL RX    │
            USB-TTL GND ──┘
            USB-TTL TX   ✗ leave unconnected
```

The adapter only **listens**: connect its **RX** to the controller's TX wire and **GND to GND**. Leave the adapter's TX unconnected.

To hear the display's replies too, add a **second adapter** whose RX goes to the display's TX, then click **+ 2nd adapter** in Tapline.

### To send (Send mode)

```
 Controller board              Display
   TX ──── ✗ disconnect ────── RX
                          ┌──► RX
   USB-TTL TX ────────────┘
   USB-TTL GND ─────────────── GND
```

Unplug the controller's TX from the display so the two don't talk over each other, then connect the **adapter TX → display RX** and **GND ↔ GND**. Tapline shows this checklist the first time you switch to Send.

---

## A typical session

1. **Choose your adapter** in *Source*. Don't know the speed? Press **✨ Detect**. Tapline listens at each common setting (it never transmits) and suggests the best 3.
2. **Start.** Messages appear on the left. If they look cut in odd places, press **Guess** next to *Framing* and pick the best suggestion.
3. **Make something happen** on the display and press <kbd>Space</kbd>. Type what you saw ("shows 25.0 °C", "pressed UP").
4. **Find value → 25**. Tapline searches every message for 25 as a byte, 16/32-bit number, BCD, text, ×10 and ×100, and ranks matches near your marker first.
5. **Click a match**, then **Name these bytes…** to save it as a field (e.g. `temperature`, i16 LE, scale 0.1, °C). Every matching message now shows the value, with a small chart.
6. **Byte map** and **Checksum** help with the rest: find the counter, the constant header, the checksum.
7. **Test it:** switch to **Send**, select a message, choose **Edit & send**, change the value, then **Send**. Mark **✓ / ✗** depending on what the display did. Your confirmed findings are listed in **Protocol**.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| *Permission denied* on Linux | `sudo usermod -aG dialout $USER`, then log out and back in |
| *Port is in use* | Close Arduino IDE serial monitor, PuTTY, other Tapline windows |
| Port not in the list | Unplug/replug the adapter, press ↻. Windows may need the CH340/CP210x driver |
| Only `00` / `FF` / random bytes | Wrong baud rate. Try **✨ Detect**. Check the adapter's RX goes to the controller's **TX** |
| Nothing at all | Check GND is connected, and that the board is actually sending (some only talk after a button press) |
| Display ignores what you send | Check the checksum (Checksum tab), that the controller TX is disconnected, and the voltage |

---

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
python -m pytest
```

The tests need no hardware: they use the simulator and virtual serial ports (`socat`, Linux/macOS). The UI test uses Playwright with Chromium.

**Roadmap:** RS-232/RS-485 → logic analyzer (I²C, SPI via sigrok) → CAN → I²C/SPI transmit via a Raspberry Pi Pico bridge.

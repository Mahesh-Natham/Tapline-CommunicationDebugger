# Tapline

**Tap the line, read what's on it.** Tapline listens to the link between a controller board and a display (or any two devices), helps you work out what the bytes mean, and lets you send your own messages to check your guesses.

| Link | Hardware you need | Listen | Send |
|---|---|---|---|
| **UART (TTL)** | USB-to-TTL adapter (CP2102, CH340, FT232…) | ✅ | ✅ |
| **RS-232** | USB-to-RS232 adapter | ✅ | ✅ |
| **RS-485 / Modbus RTU** | USB-to-RS485 adapter | ✅ master/slave labelled, Modbus decoded | ✅ request builder, waits for reply |
| **I²C, SPI, UART with exact timing** | Logic analyzer + [sigrok-cli](https://sigrok.org/wiki/Downloads) | ✅ | via Pico bridge |
| **CAN** | CANable / candleLight / PCAN / SocketCAN adapter | ✅ | ✅ `ID#DATA` |
| **I²C / SPI sending** | Raspberry Pi Pico running `bridge/pico_bridge.py` | — | ✅ |

Every kind has a **demo** in the Source list, so you can try it with no hardware.

- 🔌 **🔌 Wiring** button shows how to connect the current kind of link
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

### RS-232 and RS-485

Choose your adapter's port, set **Link** to RS-232 or RS-485, and press **🔌 Wiring** for the diagram. RS-485 uses one shared pair (A/B), so one adapter hears both sides; Tapline labels each message *master → slave* or *slave → master*. If nothing decodes, swap A and B. Modbus RTU traffic is recognised automatically and shown in plain words, e.g. "Read holding registers: 10 registers from 0 (device 1)". In Send mode the **Modbus request builder** makes requests with the right CRC, and **Wait … ms and show the reply** shows the device's answer.

### Logic analyzer (I²C, SPI, UART)

1. Install **sigrok-cli** once: Windows/macOS from [sigrok.org/wiki/Downloads](https://sigrok.org/wiki/Downloads), Linux `sudo apt install sigrok-cli`. Cheap 24 MHz/8-channel analyzers use the `fx2lafw` driver (on Windows install the WinUSB driver with Zadig).
2. Source → **Capture from a logic analyzer**, press **Scan**, choose sample rate (≥ 4× the bus speed) and seconds.
3. Pick the protocol and which channel goes to which signal, then **Start**. Tapline captures, decodes with sigrok's decoders, and shows each I²C transaction (e.g. *I²C 0x48 read*), SPI transfer or UART message.
4. You can also **Open a capture file (.sr)** saved by PulseView.

The analyzer only listens. To send on I²C or SPI, use the Pico bridge.

### CAN

`python-can` is installed by `run.bat` / `run.sh`. Source → **CAN adapter**, choose the interface (slcan for CANable, gs_usb for candleLight, pcan, socketcan…), the channel (e.g. `COM6`, `/dev/ttyACM0`, `can0`, `PCAN_USBBUS1`) and the bitrate (cars: usually 500 kbit/s). Each ID is its own line (*CAN 0x100*); when you name bytes as a field you can tie it to that ID. Send messages as `ID#DATA`, e.g. `300#01` or `18FF50E5#01A2`.

### Raspberry Pi Pico bridge (send I²C / SPI)

1. Put MicroPython on the Pico (hold BOOTSEL while plugging in, copy the `.uf2` from micropython.org).
2. Open `bridge/pico_bridge.py` in **Thonny** and save it to the Pico as **`main.py`**. Unplug and replug.
3. In **Composer** choose **Send through → Pico bridge — I²C / SPI**, pick the Pico's port, **Connect**.

```
I²C0: SDA = GP4 (pin 6)    SCL = GP5 (pin 7)
SPI0: SCK = GP18 (pin 24)  MOSI = GP19 (pin 25)  MISO = GP16 (pin 21)  CS = GP17 (pin 22)
GND:  any GND pin → target GND            ⚠️ Pico pins are 3.3 V only
```

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
| RS-485: only garbage | Swap A and B; check the baud rate (Detect works on RS-485 too) |
| Logic analyzer not found | Install sigrok-cli; on Windows install the WinUSB driver with Zadig; try another USB port |
| CAN: no messages | Wrong bitrate is the usual cause; check CAN_H/CAN_L and that the bus is terminated (120 Ω at each end) |
| Pico: "No Tapline bridge answered" | Save `bridge/pico_bridge.py` on the Pico as `main.py`, then replug it; close Thonny (it holds the port) |

---

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
python -m pytest
```

The tests need no hardware: they use the simulator and virtual serial ports (`socat`, Linux/macOS). The UI test uses Playwright with Chromium.

The tests need no hardware: simulators for every link, virtual serial ports (`socat`), python-can's virtual bus, generated logic-analyzer captures decoded by the real `sigrok-cli` (skipped if it's not installed), and the Pico bridge's command handler run against fake hardware.

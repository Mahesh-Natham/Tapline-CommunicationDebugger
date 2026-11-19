"""Serial port check, independent of the Tapline app.

    python -m tapline.diagnose            (asks for the port and baud rate)
    python -m tapline.diagnose COM3 9600

Lists the ports, opens the chosen one exactly as Tapline does, listens, and shows what
arrived. Everything printed is also saved to tapline-diagnose.txt to send for help.
"""
from __future__ import annotations

import platform
import sys
import time
import traceback

LISTEN_S = 4.0


class Tee:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")

    def __call__(self, *a):
        line = " ".join(str(x) for x in a)
        print(line)
        self.f.write(line + "\n")
        self.f.flush()


def preview(data: bytes) -> str:
    hx = " ".join(f"{b:02X}" for b in data[:48])
    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:48])
    return f"    hex  : {hx}\n    ascii: {asc}"


def listen(out, port, baud, fmt, dtr_rts_off):
    import serial
    from .model import SerialConfig
    from .sources.serial_source import _open, friendly_error
    cfg = SerialConfig.from_format(port, baud, fmt)
    label = f"{cfg.label()}, DTR/RTS {'off' if dtr_rts_off else 'on'}"
    try:
        if dtr_rts_off:
            ser = _open(cfg, timeout=0.05)  # exactly how Tapline opens it
        else:
            ser = serial.Serial(port, baud, bytesize=cfg.bytesize, parity=cfg.parity,
                                stopbits=cfg.stopbits, timeout=0.05)
    except Exception as e:  # noqa: BLE001
        raw = e.__cause__ or e
        out(f"  [{label}] could NOT open: {friendly_error(raw, port)}")
        out(f"      raw error: {type(raw).__name__}: {raw}")
        return None
    buf = bytearray()
    t0 = time.time()
    try:
        while time.time() - t0 < LISTEN_S:
            buf += ser.read(ser.in_waiting or 1)
    except Exception as e:  # noqa: BLE001
        out(f"  [{label}] error while reading: {type(e).__name__}: {e}")
    finally:
        ser.close()
    out(f"  [{label}] received {len(buf)} bytes in {LISTEN_S:.0f} s")
    if buf:
        out(preview(bytes(buf)))
    return len(buf)


def main(argv: list[str]) -> int:
    out = Tee("tapline-diagnose.txt")
    out("Tapline serial check —", time.strftime("%Y-%m-%d %H:%M:%S"))
    out(f"Python {sys.version.split()[0]} on {platform.platform()}")
    try:
        import serial
        from serial.tools import list_ports
        out(f"pyserial {serial.__version__}")
    except ImportError:
        out("pyserial is NOT installed. Run:  py -m pip install -r requirements.txt")
        return 1

    ports = sorted(list_ports.comports(), key=lambda p: p.device)
    out("\nPorts found:")
    for p in ports:
        vid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "-"
        out(f"  {p.device:<8} {p.description}  [{vid}]  {p.manufacturer or ''}")
    if not ports:
        out("  none — is the adapter plugged in? Check Device Manager → Ports (COM & LPT).")
        return 1

    port = argv[0] if argv else input("\nPort to test (e.g. COM3): ").strip()
    baud_s = argv[1] if len(argv) > 1 else input("Baud rate used in HTerm (e.g. 9600): ").strip()
    fmt = (argv[2] if len(argv) > 2 else "8N1").upper()
    try:
        baud = int(baud_s)
    except ValueError:
        out(f"'{baud_s}' is not a number"); return 1
    out(f"\nListening on {port} at {baud} {fmt} for {LISTEN_S:.0f} s at a time. "
        f"Close HTerm and Tapline first.\n")

    a = listen(out, port, baud, fmt, dtr_rts_off=True)
    b = listen(out, port, baud, fmt, dtr_rts_off=False)

    out("\nResult:")
    if a is None and b is None:
        out("  The port could not be opened. Another program (HTerm, Tapline, Arduino IDE…) probably has it open.")
    elif not a and not b:
        out("  The port opens but nothing arrives. Check the wiring (adapter RX → controller TX, GND ↔ GND)"
            " and that the controller is sending right now.")
    elif a:
        out("  Data arrives the way Tapline opens the port. If Tapline still shows nothing, send this file to Claude.")
    else:
        out("  Data arrives only with DTR/RTS on — send this file to Claude.")
    out("\nSaved to tapline-diagnose.txt")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    if sys.stdin.isatty() and len(sys.argv) <= 1:
        input("\nPress Enter to close.")
    sys.exit(code)

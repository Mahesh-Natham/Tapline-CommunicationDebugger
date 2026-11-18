from tapline.model import Chunk, CTRL_TO_DISP


def sim_frame(cmd: int, payload: bytes) -> bytes:
    body = bytes([len(payload) + 2, cmd]) + payload
    return b"\x5a\xa5" + body + bytes([sum(body) & 0xFF])


def sim_stream(n: int = 30) -> bytes:
    out = b""
    for i in range(n):
        t = 200 + 5 * i
        out += sim_frame(0x10, t.to_bytes(2, "little", signed=True))
        if i % 3 == 0:
            out += sim_frame(0x11, b"READY" if i % 2 else b"HEAT")
        out += sim_frame(0x20, bytes([i & 0xFF]))
    return out


def gapped_chunks(frames: list[bytes], gap: float = 0.05, channel=CTRL_TO_DISP) -> list[Chunk]:
    return [Chunk(i * gap, channel, f) for i, f in enumerate(frames)]

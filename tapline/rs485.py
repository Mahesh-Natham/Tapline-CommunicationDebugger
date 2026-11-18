"""RS-485 is one shared pair of wires: work out who is talking for each message."""
from __future__ import annotations

from . import modbus
from .model import MASTER, SENT, SLAVE, Frame, is_sent


class RoleTracker:
    """Labels frames master→slave (request) or slave→master (reply).

    Modbus frames are recognised by their shape; anything else alternates:
    a frame soon after a request is its reply, otherwise a new request.
    """

    def __init__(self, reply_window_s: float = 0.25):
        self.window = reply_window_s
        self.last_role: str | None = None
        self.last_t = -1e9
        self.last_data = b""
        self.no_reply = False  # broadcast (address 0): devices never answer

    def _expect_reply(self, t: float) -> bool:
        return self.last_role == MASTER and not self.no_reply and t - self.last_t <= self.window

    def label(self, f: Frame) -> str:
        if is_sent(f.channel):  # we sent it, acting as the master
            self.last_role, self.last_t, self.last_data = MASTER, f.t_end, f.data
            self.no_reply = bool(f.data) and f.data[0] == 0
            return SENT
        expect = self._expect_reply(f.t_start)
        mb = modbus.decode(f.data, prefer="response" if expect else "request")
        if mb and expect and len(f.data) == 8 and f.data[1] in (5, 6):
            # FC5/FC6 replies echo the request byte for byte: same bytes = reply, different = new request
            role = SLAVE if f.data == self.last_data else MASTER
        elif mb:
            role = MASTER if mb["kind"] == "request" else SLAVE
        else:
            role = SLAVE if expect else MASTER
        self.no_reply = role == MASTER and bool(mb) and f.data[0] == 0
        self.last_role, self.last_t, self.last_data = role, f.t_end, f.data
        return role

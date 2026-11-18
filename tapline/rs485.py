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

    def _expect_reply(self, t: float) -> bool:
        return self.last_role == MASTER and t - self.last_t <= self.window

    def label(self, f: Frame) -> str:
        if is_sent(f.channel):  # we sent it, acting as the master
            self.last_role, self.last_t = MASTER, f.t_end
            return SENT
        expect = self._expect_reply(f.t_start)
        mb = modbus.decode(f.data, prefer="response" if expect else "request")
        if mb:
            role = MASTER if mb["kind"] == "request" else SLAVE
        else:
            role = SLAVE if expect else MASTER
        self.last_role, self.last_t = role, f.t_end
        return role

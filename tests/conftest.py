import re
import shutil
import subprocess
import time

import pytest


@pytest.fixture
def pty_pair():
    """Two linked virtual serial ports (like a null-modem cable)."""
    if not shutil.which("socat"):
        pytest.skip("socat not installed")
    p = subprocess.Popen(["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
                         stderr=subprocess.PIPE, text=True)
    ports = []
    deadline = time.time() + 5
    while len(ports) < 2 and time.time() < deadline:
        line = p.stderr.readline()
        m = re.search(r"PTY is (\S+)", line)
        if m:
            ports.append(m.group(1))
    if len(ports) < 2:
        p.kill()
        pytest.skip("socat did not create ptys")
    yield ports[0], ports[1], p
    p.kill()
    p.wait()

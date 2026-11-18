import asyncio
import errno

import pytest
import serial

from tapline.model import SerialConfig
from tapline.sources.base import SourceError
from tapline.sources.serial_source import SerialSource, friendly_error, list_ports, read_window


async def first_chunk(src, timeout=3):
    async def go():
        async for c in src.chunks():
            return c
    return await asyncio.wait_for(go(), timeout)


async def test_serial_source_reads_bytes(pty_pair):
    a, b, _ = pty_pair
    src = SerialSource(SerialConfig(a, 115200))
    await src.start()
    other = serial.Serial(b, 115200)
    other.write(b"\x5a\xa5\x03\x20\x01\x24")
    c = await first_chunk(src)
    assert c.data.startswith(b"\x5a")
    other.close()
    await src.stop()


async def test_serial_source_write(pty_pair):
    a, b, _ = pty_pair
    src = SerialSource(SerialConfig(a, 115200))
    await src.start()
    other = serial.Serial(b, 115200, timeout=2)
    await src.write(b"hello")
    assert other.read(5) == b"hello"
    other.close()
    await src.stop()


async def test_serial_source_port_disappears(pty_pair):
    a, b, proc = pty_pair
    src = SerialSource(SerialConfig(a, 115200))
    await src.start()
    proc.kill()
    proc.wait()

    async def drain():
        async for _ in src.chunks():
            pass
    await asyncio.wait_for(drain(), 5)  # iterator ends, no exception leaks
    assert src.error
    await src.stop()


async def test_start_bad_port_raises_friendly():
    src = SerialSource(SerialConfig("/dev/does-not-exist-123", 9600))
    with pytest.raises(SourceError) as e:
        await src.start()
    assert "not found" in str(e.value)


def test_friendly_error_messages():
    assert "dialout" in friendly_error(serial.SerialException(errno.EACCES, "Permission denied: '/dev/ttyUSB0'"), "/dev/ttyUSB0")
    assert "in use" in friendly_error(serial.SerialException("could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)"), "COM5")
    assert "not found" in friendly_error(serial.SerialException(errno.ENOENT, "could not open port /dev/x: No such file"), "/dev/x")
    assert "COM9" in friendly_error(serial.SerialException("FileNotFoundError(2, 'The system cannot find the file specified.')"), "COM9")


def test_list_ports_shape():
    for p in list_ports():
        assert {"device", "description", "vid", "pid"} <= set(p)


async def test_read_window_collects(pty_pair):
    a, b, _ = pty_pair
    other = serial.Serial(b, 115200)

    async def feed():
        for _ in range(10):
            other.write(b"\x5a\xa5\x03\x20\x01\x24" * 3)
            await asyncio.sleep(0.05)
    task = asyncio.create_task(feed())
    data, errors = await read_window(SerialConfig(a, 115200), 0.2, min_bytes=40, max_s=1.5)
    await task
    other.close()
    assert len(data) >= 40 and errors == 0

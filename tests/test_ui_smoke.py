"""Drive the real UI in a browser against the simulator (no hardware)."""
import socket
import threading
import time

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from tapline.server import create_app  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(project="UI", base=tmp_path_factory.mktemp("proj"))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)


@pytest.fixture
def page(server):
    with playwright.sync_playwright() as p:
        import glob
        exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") or [None])[0]
        try:
            b = p.chromium.launch()
        except Exception:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.request.method} {r.url}") if r.status >= 400 else None)
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(server)
        yield pg, errors
        b.close()


def test_ui_flow(page, server):
    pg, errors = page
    # tour appears on first visit; skip it
    pg.wait_for_selector("#tour:not(.hidden)")
    pg.click("#tourSkip")
    assert pg.is_visible("#detectBtn")
    # framing: header + length
    pg.select_option("#frameKind", "length")
    pg.fill('#frameParams [data-k="header"]', "5A A5")
    pg.fill('#frameParams [data-k="len_offset"]', "2")
    pg.click("#applyFraming")
    pg.select_option("#source", "simulator")
    pg.click("#startBtn")
    pg.wait_for_selector(".frow", timeout=5000)
    pg.wait_for_function("document.querySelectorAll('.frow').length >= 4", timeout=8000)
    # marker with space
    pg.click("#frames")
    pg.keyboard.press("Space")
    pg.fill("#markerLabel", "shows 20.5")
    pg.keyboard.press("Enter")
    pg.wait_for_selector(".mrow")
    # find value
    pg.click('.tabs [data-tab="find"]')
    pg.fill("#findQ", "20.5")
    pg.click("#findForm button")
    pg.wait_for_selector("#findResults .result")
    pg.click("#findResults .result")
    pg.wait_for_selector("#cells .cell.hit")
    # name the field from the selection
    pg.click("#mkField")
    pg.fill("#ffName", "temp")
    pg.fill("#ffScale", "0.1")
    pg.fill("#ffType", "temperature")
    pg.click("#fieldForm button.primary")
    pg.wait_for_selector(".tname")
    # detect on the simulator
    pg.click("#startBtn")  # stop
    try:
        pg.wait_for_selector("#startBtn:not(.running)", timeout=4000)
    except Exception:
        pg.screenshot(path="/tmp/claude-0/-home-user/3dac1ff0-0b55-5287-b2c2-78ca9dc40088/scratchpad/fail.png")
        print("ERRORS", errors, pg.inner_text("#toasts"), pg.get_attribute("#startBtn", "class"))
        raise
    pg.click("#detectBtn")
    pg.wait_for_selector("#detOut .cand", timeout=15000)
    assert "115200 8N1" in pg.inner_text("#detOut .cand.best")
    pg.click("#detOut .cand.best button")
    # send mode: checklist then send
    pg.click("#startBtn")
    pg.wait_for_selector(".frow")
    pg.click('#modeSeg [data-mode="send"]')
    pg.wait_for_selector("#ckOk")
    for cb in pg.query_selector_all("#modalCard input[type=checkbox]"):
        cb.check()
    pg.click("#ckOk")
    pg.wait_for_selector("body.send-mode")
    pg.click('.tabs [data-tab="checksum"]')
    pg.click("#findCs")
    pg.wait_for_selector("#csOut .cand.best")
    assert "sum8" in pg.inner_text("#csOut .cand.best")
    pg.click("#csOut .cand.best button")
    pg.click('.tabs [data-tab="compose"]')
    pg.fill("#composeHex", "5A A5 00 30 32 00")  # wrong length + checksum: auto-fix repairs both
    pg.click("#sendBtn")
    pg.wait_for_selector(".chan.d2c", timeout=5000)
    pg.wait_for_selector("#verdictBox:not(.hidden)")
    pg.click('#verdictBox [data-ok="1"]')
    pg.wait_for_selector(".chan.sent")
    pg.screenshot(path=str(__import__("pathlib").Path(__file__).parent.parent / ".superpowers" / "ui-desktop.png"))
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.screenshot(path=str(__import__("pathlib").Path(__file__).parent.parent / ".superpowers" / "ui-phone.png"), full_page=True)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    pg.click("#startBtn")
    assert not errors, errors

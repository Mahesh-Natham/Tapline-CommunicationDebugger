/* Tapline browser UI — plain JS, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const MAX_ROWS = 2000;
const KEEP = 20000;

const S = {
  options: null, ports: [], status: {}, protocol: { frame_types: [] },
  frames: [], byId: new Map(), markers: [], selected: null, hlOffset: null,
  selRange: null, hitCells: null, lastSent: null, follow: true, oldestLoaded: null,
  windowEnd: null, // null = show the newest messages; else id of the last row shown
};

/* ---------------- helpers ---------------- */
function store(key, val) {
  try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (e) { return null; }
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  $("#toasts").append(t);
  while ($("#toasts").children.length > 3) $("#toasts").firstElementChild.remove();
  setTimeout(() => t.remove(), kind === "err" ? 6500 : 3200);
}
async function api(path, body, method) {
  const opts = { method: method || (body === undefined ? "GET" : "POST"), headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch("api/" + path, opts);
  let data = {};
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
async function safe(fn) { try { return await fn(); } catch (e) { toast(e.message, "err"); } }
function bytesOf(hex) { return hex ? hex.split(" ").map(h => parseInt(h, 16)) : []; }
function fmtTime(t) {
  const d = new Date(t * 1000);
  const p = (n, l = 2) => String(n).padStart(l, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}
function isSent(c) { return c === "sent" || c.startsWith("sent "); }
function chanClass(c) {
  if (isSent(c)) return "sent";
  if (/^(disp|slave)|read$|MISO$/.test(c)) return "d2c";
  if (/^(ctrl|master)|write$|MOSI$/.test(c)) return "c2d";
  return "oth";
}
function chanLabel(c) {
  if (c === "sent") return "you sent";
  if (c.startsWith("sent ")) return "you → " + c.slice(5);
  return c.replace("ctrl→disp", "ctrl → disp").replace("disp→ctrl", "disp → ctrl").replace("master→slave", "master → slave").replace("slave→master", "slave → master");
}
function byteInfo(b, idx, bytes) {
  const ch = b >= 32 && b < 127 ? `'${String.fromCharCode(b)}'` : "·";
  const s8 = b > 127 ? b - 256 : b;
  let s = `#${idx}  0x${b.toString(16).toUpperCase().padStart(2, "0")}\ndec ${b}   signed ${s8}\nchar ${ch}\nbits ${b.toString(2).padStart(8, "0")}`;
  if (bytes && idx + 1 < bytes.length) {
    const le = bytes[idx] | (bytes[idx + 1] << 8), be = (bytes[idx] << 8) | bytes[idx + 1];
    s += `\nwith next: ${le} LE · ${be} BE`;
  }
  return s;
}

/* ---------------- settings bar ---------------- */
function fillSelect(sel, items, value) {
  sel.innerHTML = items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  if (value !== undefined) sel.value = String(value);
}
const DEMOS = [["simulator", "UART display (demo)"], ["sim-modbus", "RS-485 Modbus device (demo)"],
  ["sim-la:uart", "Logic analyzer · UART (demo)"], ["sim-la:i2c", "Logic analyzer · I²C (demo)"],
  ["sim-la:spi", "Logic analyzer · SPI (demo)"], ["sim-can", "CAN bus · car (demo)"]];
function optgroup(label, items) {
  return `<optgroup label="${esc(label)}">${items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</optgroup>`;
}
async function loadPorts() {
  const r = await api("ports");
  S.ports = r.ports;
  const prev = $("#source").value || store("tl.source") || "simulator";
  const ports = r.ports.map(p => [p.device, portLabel(p)]);
  $("#source").innerHTML = optgroup("Try without hardware", DEMOS) +
    optgroup("Serial ports (USB-TTL, RS-232, RS-485)", ports.length ? ports : [["", "No serial ports found — plug in an adapter, press ↻"]]) +
    optgroup("Logic analyzer", [["la-device", "Capture from a logic analyzer (sigrok)"], ["la-file", "Open a capture file (.sr)"]]) +
    optgroup("CAN bus", [["can", "CAN adapter"]]);
  r.ports.forEach(p => { const o = $$("#source option").find(x => x.value === p.device); if (o) o.title = `${p.device} — ${p.description}`; });
  const values = $$("#source option").map(o => o.value);
  $("#source").value = values.includes(prev) && prev ? prev : "simulator";
  const portItems = r.ports.map(p => [p.device, p.device]);
  fillSelect($("#source2"), [["", "— none —"]].concat(portItems));
  fillSelect($("#picoPort"), portItems.length ? portItems : [["", "No serial ports"]], store("tl.picoPort") || undefined);
  onSourceChange();
}
const CHIPS = [[/ch34\d|wch/i, "CH340"], [/cp21\d\d|silicon lab/i, "CP210x"], [/ft\d{3}|ftdi/i, "FTDI"],
  [/pl2303|prolific/i, "PL2303"], [/pico|rp2040|micropython|board in fs/i, "Pico"], [/canable|candle|gs_usb/i, "CAN"],
  [/arduino/i, "Arduino"], [/bluetooth/i, "Bluetooth"]];
function portLabel(p) {
  const VIDS = { "1A86": "CH340", "10C4": "CP210x", "0403": "FTDI", "067B": "PL2303", "2E8A": "Pico" };
  const desc = `${p.description || ""} ${p.manufacturer || ""}`;
  const chip = CHIPS.find(([re]) => re.test(desc));
  let tag = VIDS[p.vid] || (chip ? chip[1] : (p.description || "").replace(/\(?(COM\d+|\/dev\/\S+)\)?/gi, "").replace(/usb[- ]?(to[- ])?(serial|uart)|device|port/gi, "").trim());
  if (tag.length > 14) tag = tag.slice(0, 13) + "…";
  const dev = p.device.replace(/^\/dev\//, "");
  return tag && tag !== dev ? `${dev} · ${tag}` : dev;
}
function sourceKind(v) {
  if (v === "la-device") return "ladev";
  if (v === "la-file") return "lafile";
  if (v === "can") return "can";
  if (v === "simulator" || v.startsWith("sim")) return "demo";
  return "serial";
}
function onSourceChange() {
  const v = $("#source").value;
  const kind = sourceKind(v);
  document.body.className = document.body.className.replace(/\b(kind|src)-\S+/g, "").trim();
  document.body.classList.add("kind-" + kind, "src-" + (v === "simulator" ? "simulator" : "x"));
  store("tl.source", v);
  if (kind === "ladev" || kind === "lafile") { renderLaPins(); if (kind === "lafile") loadCaptures(); }
  if (kind === "can") loadCan();
  $("#srcHint").innerHTML = sourceHint(v, kind);
}
function sourceHint(v, kind) {
  if (kind === "demo") return "<b>Demo</b>: no settings needed, just press <b>▶ Start</b>. For your adapter, open <b>Source</b> and pick its <b>COM port</b>; Baud, Parity and Stop then appear here.";
  if (kind === "serial" && !v) return "<b>No adapter found.</b> Plug in your USB-TTL, wait 3 s, press <b>↻</b>. Windows: check Device Manager → Ports (COM &amp; LPT); you may need the CH340 or CP210x driver.";
  if (kind === "serial") return "Adapter selected: set <b>Baud / Data / Parity / Stop</b> above, or press <b>✨ Detect</b> while the board is running. Then <b>▶ Start</b>.";
  if (kind === "ladev") return "Press <b>Scan</b> to find your logic analyzer, choose the protocol and pins, then <b>▶ Start</b>.";
  if (kind === "lafile") return "Pick a saved <b>.sr</b> capture (or Import one), choose the protocol, then <b>▶ Start</b>.";
  if (kind === "can") return "Choose interface, channel and bitrate (cars: usually 500k), then <b>▶ Start</b>.";
  return "";
}
function guide(tab = "demo") {
  const demo = `<ol>
    <li><b>Source</b> → <b>UART display (demo)</b> → <b>▶ Start</b>. A fake controller starts talking to a fake display.</li>
    <li>Messages appear on the left. Click one to see its bytes.</li>
    <li>The demo sends a temperature around <b>25</b>. Press <kbd>Space</kbd> to mark the moment and type “shows 25”.</li>
    <li>Open <b>Find value</b>, type <code>25</code>, <b>Find</b>. Click the top match → <b>Name these bytes…</b> → call it <code>temperature</code>.</li>
    <li>Open <b>Byte map</b> (constant / changing bytes) and <b>Checksum</b> (works out how the last byte is computed).</li>
    <li>Switch top-right to <b>Send</b>. Pick a message → <b>Edit &amp; send</b>, change a byte, <b>Send</b>. Tapline fixes the checksum for you.</li>
    <li>Try the other demos in <b>Source</b>: Modbus RS-485, logic analyzer (UART / I²C / SPI), CAN car. Press <b>■ Stop</b> before switching.</li></ol>`;
  const hw = `<ol>
    <li><b>Wire it (listen only):</b> adapter <b>GND → board GND</b>, adapter <b>RX → controller TX</b> (the wire going to the display). Leave adapter TX unconnected. Set the adapter's jumper to the board voltage (3.3 V or 5 V). <b>🔌 Wiring</b> shows the picture.</li>
    <li>Plug the adapter into USB and press <b>↻</b> next to Source.</li>
    <li><b>Source</b> → under <b>Serial ports</b> pick your adapter (e.g. <code>COM5 — USB-SERIAL CH340</code>). Now <b>Link, Baud, Data, Parity, Stop</b> appear.</li>
    <li>Power the board. Press <b>✨ Detect</b>: it only listens and suggests the best settings; click one. Or choose Baud / Parity yourself.</li>
    <li><b>▶ Start</b> (stay in <b>Sniff</b> mode). Messages should repeat in a pattern. Random bytes / 00 / FF → wrong baud. Nothing → check GND and that RX is on the controller's TX.</li>
    <li>If messages are cut in odd places, press <b>Guess</b> next to <b>Framing</b>.</li>
    <li>Change something on the display, press <kbd>Space</kbd>, then use <b>Find value</b> as in the demo.</li>
    <li><b>To send:</b> switch to <b>Send</b> and follow the checklist (disconnect controller TX from the display, adapter TX → display RX). Edit a captured message, <b>Send</b>, then mark ✓ / ✗ by what the display did.</li></ol>
    <p>Stuck? Send Claude a screenshot of the whole window.</p>`;
  openModal(`<div class="guide"><h2>How to use Tapline</h2>
    <div class="tabs2"><button class="ghost ${tab === "demo" ? "on" : ""}" data-g="demo">1 · Try the demo</button><button class="ghost ${tab === "hw" ? "on" : ""}" data-g="hw">2 · Test with real hardware</button></div>
    <div class="gpane">${tab === "demo" ? demo : hw}</div>
    <div class="row-inline" style="justify-content:flex-end;margin-top:10px"><button class="primary" id="guideClose">Got it</button></div></div>`);
  $$("#modalCard [data-g]").forEach(b => b.onclick = () => guide(b.dataset.g));
  $("#guideClose").onclick = () => $("#modal").classList.add("hidden");
}
function setupSettings() {
  const o = S.options;
  fillSelect($("#baud"), o.bauds.map(b => [b, b]).concat([["custom", "Custom…"]]), store("tl.baud") || 115200);
  if (!o.bauds.includes(+$("#baud").value)) $("#baud").value = "custom";
  fillSelect($("#bytesize"), o.bytesize.map(b => [b, b]), store("tl.bytesize") || 8);
  fillSelect($("#parity"), [["N", "None"], ["E", "Even"], ["O", "Odd"], ["M", "Mark"], ["S", "Space"]], store("tl.parity") || "N");
  fillSelect($("#stopbits"), o.stopbits.map(b => [b, b]), store("tl.stopbits") || 1);
  $("#baudCustom").value = store("tl.baudCustom") || "";
  $("#baudCustom").classList.toggle("hidden", $("#baud").value !== "custom");
  $("#baud").onchange = () => { $("#baudCustom").classList.toggle("hidden", $("#baud").value !== "custom"); saveSettings(); };
  ["#bytesize", "#parity", "#stopbits", "#baudCustom"].forEach(s => $(s).onchange = saveSettings);
  $("#source").onchange = onSourceChange;
  $("#refreshPorts").onclick = () => safe(async () => { await loadPorts(); toast("Port list refreshed"); });
  $("#secondBtn").onclick = () => $("#secondWrap").classList.toggle("hidden");
}
function saveSettings() {
  ["baud", "bytesize", "parity", "stopbits", "baudCustom"].forEach(k => store("tl." + k, $("#" + k).value));
}
function currentConfig(port) {
  const baud = $("#baud").value === "custom" ? parseInt($("#baudCustom").value, 10) : parseInt($("#baud").value, 10);
  if (!baud || baud < 50) throw new Error("Enter a valid custom baud rate");
  return { port, baud, bytesize: +$("#bytesize").value, parity: $("#parity").value, stopbits: +$("#stopbits").value };
}
function applyConfig(cfg) {
  if (S.options.bauds.includes(cfg.baud)) { $("#baud").value = cfg.baud; $("#baudCustom").classList.add("hidden"); }
  else { $("#baud").value = "custom"; $("#baudCustom").value = cfg.baud; $("#baudCustom").classList.remove("hidden"); }
  $("#bytesize").value = cfg.bytesize; $("#parity").value = cfg.parity; $("#stopbits").value = cfg.stopbits;
  saveSettings();
}

async function toggleStart() {
  if (S.status.running) { await safe(async () => applyStatus(await api("stop", {}))); return; }
  const src = $("#source").value;
  await safe(async () => {
    const body = startBody(src);
    if (sourceKind(src) === "serial" && $("#source2").value && !$("#secondWrap").classList.contains("hidden")) body.config2 = currentConfig($("#source2").value);
    const st = await api("start", body);  // throws on failure: keep what's on screen
    S.frames = []; S.byId.clear(); S.markers = []; S.selected = null; S.windowEnd = null; S.oldestLoaded = null;
    renderAll(); clearInspect();
    applyStatus(st);
    toast(sourceKind(src) === "demo" ? "Demo running — watch the messages arrive" : sourceKind(src).startsWith("la") ? "Logic analyzer: capturing and decoding…" : `Listening — ${st.source}`, "ok");
  });
}

/* ---------------- status ---------------- */
function applyStatus(st) {
  S.status = st;
  $("#modbusBox").classList.toggle("hidden", st.link !== "rs485");
  const can = st.source_kind === "can" || st.source_kind === "sim-can";
  $("#composeLabel").textContent = can ? "CAN message (ID#DATA, e.g. 300#01 or 18FF50E5#01A2)" : "Message (hex)";
  $("#composeHex").placeholder = can ? "300#01" : "5A A5 03 30 32 65";
  $("#autoFix").closest("label").style.visibility = can ? "hidden" : "";
  $("#picoState").textContent = st.pico ? `connected on ${st.pico}` : "not connected";
  const b = $("#startBtn");
  b.classList.toggle("running", !!st.running);
  b.textContent = st.running ? "■ Stop" : "▶ Start";
  document.body.classList.toggle("send-mode", st.mode === "send");
  $$("#modeSeg button").forEach(x => x.classList.toggle("on", x.dataset.mode === st.mode));
  const dot = `<span class="dot ${st.running ? "live" : ""}"></span>`;
  const what = st.running ? esc(st.source) : (st.source ? `Stopped · ${esc(st.source)}` : "Not capturing");
  $("#statusText").innerHTML = `${dot}${what} · ${st.frames} messages${st.markers ? ` · ${st.markers} markers` : ""}${st.repeating ? " · <b>repeating send</b>" : ""}${st.project ? ` · project <b>${esc(st.project)}</b>` : ""}`;
  if (st.rule && !document.activeElement?.closest?.("#subbar")) showRule(st.rule);
}

/* ---------------- framing ---------------- */
const FRAME_PARAMS = {
  gap: [["gap_ms", "after", "ms of silence", "number"]],
  delimiter: [["start", "start", "hex", "text"], ["end", "end", "hex", "text"]],
  length: [["header", "header", "hex", "text"], ["len_offset", "length at byte", "", "number"],
           ["len_size", "size", "", "select12"], ["len_adjust", "adjust", "", "number"]],
  fixed: [["length", "every", "bytes", "number"]],
  modbus: [],
  native: [],
};
function showRule(rule) {
  $("#frameKind").value = rule.kind;
  renderFrameParams(rule);
}
function renderFrameParams(rule) {
  const kind = $("#frameKind").value;
  $("#frameParams").innerHTML = FRAME_PARAMS[kind].map(([k, pre, post, type]) => {
    const v = rule && rule.kind === kind ? rule[k] ?? "" : "";
    const input = type === "select12"
      ? `<select data-k="${k}"><option value="1"${v == 2 ? "" : " selected"}>1 byte</option><option value="2"${v == 2 ? " selected" : ""}>2 bytes LE</option></select>`
      : `<input data-k="${k}" ${type === "number" ? 'type="number"' : 'class="mono"'} value="${esc(v)}" style="width:${type === "number" ? 70 : 110}px">`;
    return `<label>${esc(pre)} ${input} ${esc(post)}</label>`;
  }).join("");
}
function readRule() {
  const rule = { kind: $("#frameKind").value };
  $$("#frameParams [data-k]").forEach(el => { rule[el.dataset.k] = el.type === "number" || el.tagName === "SELECT" ? Number(el.value || 0) : el.value; });
  return rule;
}
async function applyFraming(rule) {
  await safe(async () => { await api("framing", { rule: rule || readRule() }); toast("Framing applied — messages re-cut", "ok"); });
}
async function guessFraming() {
  await safe(async () => {
    const r = await api("framing/guess", {});
    if (!r.candidates.length) { toast("Not enough data yet — capture a few seconds first"); return; }
    openModal(`<h3>How the data is probably split</h3><p class="muted">Best guesses from the captured bytes. Pick one; you can change it any time.</p>
      ${r.candidates.map((c, i) => `<div class="cand ${i ? "" : "best"}"><div class="lbl">${esc(ruleText(c.rule))}</div>
      <button class="${i ? "ghost" : "primary"} tiny" data-i="${i}">Use</button><div class="bar"><div style="width:${Math.round(c.score * 100)}%"></div></div>
      <div class="pv">${Math.round(c.score * 100)}% of bytes fall into clean messages</div></div>`).join("")}
      <div class="actions"><button class="ghost" data-close>Close</button></div>`);
    $$("#modalCard [data-i]").forEach(b => b.onclick = async () => { closeModal(); await applyFraming(r.candidates[+b.dataset.i].rule); });
  });
}
function ruleText(r) {
  if (r.kind === "gap") return `New message after ${r.gap_ms} ms of silence`;
  if (r.kind === "delimiter") return `Messages ${r.start ? "start with " + r.start.toUpperCase() : ""}${r.start && r.end ? " and " : ""}${r.end ? "end with " + r.end.toUpperCase() : ""}`;
  if (r.kind === "modbus") return "Modbus RTU messages (cut where the CRC checks out)";
  if (r.kind === "native") return "As delivered by the bus (I²C transaction, SPI transfer, CAN frame)";
  if (r.kind === "length") return `Start with ${r.header.toUpperCase()}, length in byte ${r.len_offset}${r.len_size === 2 ? " (2 bytes)" : ""}${r.len_adjust ? `, adjust ${r.len_adjust > 0 ? "+" : ""}${r.len_adjust}` : ""}`;
  return `Every ${r.length} bytes`;
}

/* ---------------- messages list ---------------- */
function frameRow(f, fresh) {
  const bytes = f.hex ? f.hex.split(" ") : [];
  const shown = bytes.slice(0, 48).map((h, i) => `<span class="b${S.hlOffset === i ? " hl" : ""}" data-off="${i}">${h}</span>`).join(" ");
  const more = bytes.length > 48 ? ` <span class="muted">… +${bytes.length - 48}</span>` : "";
  const flags = (f.flags || []).map(x => `<span class="flag">${esc(x)}</span>`).join("");
  return `<div class="frow${S.selected === f.id ? " sel" : ""}${fresh ? " new" : ""}" data-id="${f.id}">
    <span class="t">${fmtTime(f.t_start)}</span><span class="chan ${chanClass(f.channel)}">${esc(chanLabel(f.channel))}</span>
    <span class="body">${f.type ? `<span class="tname">${esc(f.type)}</span>` : ""}${f.modbus ? `<span class="mb" title="${esc(f.modbus.summary)}">${esc(f.modbus.summary)}</span>` : ""}${flags}<span class="hex">${shown}${more}</span></span></div>`;
}
function markerRow(m, i) {
  return `<div class="mrow" data-marker="${i}"><span>★</span><span class="t">${fmtTime(m.t)}</span><span>${esc(m.label)}</span></div>`;
}
function passesFilter(f) {
  const q = $("#filter").value.trim().toLowerCase();
  if (!q) return true;
  return f.hex.toLowerCase().includes(q.replace(/0x/g, "")) || (f.type || "").toLowerCase().includes(q) || f.channel.toLowerCase().includes(q) || chanLabel(f.channel).includes(q);
}
function viewRange(list) {
  let end = list.length;
  if (S.windowEnd !== null) { const i = list.findIndex(f => f.id > S.windowEnd); if (i >= 0) end = i; }
  return [Math.max(0, end - MAX_ROWS), end];
}
function renderAll() {
  const all = S.frames.filter(passesFilter);
  const [start, end] = viewRange(all);
  const list = all.slice(start, end);
  const markers = S.markers.map((m, i) => ({ m, i })).sort((a, b) => a.m.t - b.m.t);
  let html = "", mi = 0;
  for (const f of list) {
    while (mi < markers.length && markers[mi].m.t <= f.t_start) { html += markerRow(markers[mi].m, markers[mi].i); mi++; }
    html += frameRow(f);
  }
  const firstT = list.length ? list[0].t_start : -Infinity;
  for (; mi < markers.length; mi++) if (markers[mi].m.t >= firstT) html += markerRow(markers[mi].m, markers[mi].i);
  $("#rows").innerHTML = html;
  $("#empty").classList.toggle("hidden", S.frames.length > 0 || S.markers.length > 0);
  $("#loadOlder").classList.toggle("hidden", !(start > 0 || S.oldestLoaded > 0));
  if (S.follow) scrollBottom();
}
function appendFrames(items) {
  const rows = $("#rows");
  let html = "";
  for (const f of items) {
    if (S.byId.has(f.id)) continue;
    S.frames.push(f); S.byId.set(f.id, f);
    if (passesFilter(f)) html += frameRow(f, true);
  }
  if (S.frames.length > KEEP) {
    const drop = S.frames.splice(0, S.frames.length - KEEP);
    drop.forEach(f => S.byId.delete(f.id));
    S.oldestLoaded = S.frames[0].id;
  }
  if (S.windowEnd !== null) return;  // browsing older messages: keep the view still
  if (html) rows.insertAdjacentHTML("beforeend", html);
  while (rows.children.length > MAX_ROWS) rows.firstElementChild.remove();
  if (rows.querySelector(".frow")?.dataset.id > 0) $("#loadOlder").classList.remove("hidden");
  $("#empty").classList.toggle("hidden", S.frames.length > 0);
  if (S.follow) scrollBottom();
}
function scrollBottom() { const el = $("#frames"); el.scrollTop = el.scrollHeight; }
async function reloadFrames() {
  const r = await api(`frames?limit=${MAX_ROWS}`);
  S.frames = r.items; S.byId = new Map(r.items.map(f => [f.id, f])); S.windowEnd = null;
  S.oldestLoaded = r.items.length ? r.items[0].id : null;
  const m = await api("markers");
  S.markers = m.markers;
  renderAll(); renderMarkers();
  if (S.selected !== null && S.byId.has(S.selected)) inspect(S.selected); else clearInspect();
}
async function loadOlder() {
  await safe(async () => {
    let [start, end] = viewRange(S.frames);
    if (start < 1000 && S.oldestLoaded > 0) {  // fetch more history from the server first
      const r = await api(`frames?before=${S.frames[0].id}&limit=1000`);
      S.frames = r.items.concat(S.frames);
      r.items.forEach(f => S.byId.set(f.id, f));
      S.oldestLoaded = r.items.length ? r.items[0].id : 0;
      [start, end] = viewRange(S.frames);
    }
    const newEnd = Math.max(Math.min(MAX_ROWS, S.frames.length), end - 1000);
    S.windowEnd = S.frames[newEnd - 1].id;
    S.follow = false; $("#follow").checked = false;
    renderAll();
    $("#frames").scrollTop = 0;
  });
}
function selectFrame(id, scroll) {
  S.selected = id;
  $$(".frow.sel").forEach(r => r.classList.remove("sel"));
  const row = $(`.frow[data-id="${id}"]`);
  if (row) { row.classList.add("sel"); if (scroll) row.scrollIntoView({ block: "center", behavior: "smooth" }); }
  inspect(id);
  switchTab("inspect");
}
function highlightOffset(off) {
  S.hlOffset = S.hlOffset === off ? null : off;
  $$(".b.hl").forEach(b => b.classList.remove("hl"));
  if (S.hlOffset !== null) $$(`.b[data-off="${S.hlOffset}"]`).forEach(b => b.classList.add("hl"));
  $("#hint").textContent = S.hlOffset === null ? "Tip: hover any byte · click a byte to highlight that position in every message" : `Highlighting byte #${S.hlOffset} in every message — click it again to clear`;
}

/* ---------------- inspector ---------------- */
function clearInspect() { $("#inspect").classList.add("hidden"); $("#inspectEmpty").classList.remove("hidden"); }
async function inspect(id, opts = {}) {
  const f = S.byId.get(id);
  if (!f) return;
  $("#inspectEmpty").classList.add("hidden"); $("#inspect").classList.remove("hidden");
  const bytes = bytesOf(f.hex);
  $("#inspTitle").textContent = `Message #${f.id}`;
  $("#inspMeta").textContent = `· ${fmtTime(f.t_start)} · ${chanLabel(f.channel)} · ${bytes.length} bytes`;
  S.selRange = null; S.hitCells = opts.hit || null;
  $("#fieldForm").classList.add("hidden"); $("#selInfo").classList.add("hidden"); $("#diffBox").classList.add("hidden");
  let dec = { values: {}, fields: [], type: null, checksum_ok: null };
  try { dec = await api("protocol/decode", { frame_id: id }); } catch (e) { /* frame may be gone after reframe */ }
  if (S.selected !== id && !opts.force) return;
  const fieldCells = new Set();
  dec.fields.forEach(fl => { const n = fieldSize(fl.type); for (let i = 0; i < n; i++) fieldCells.add(fl.offset + i); });
  $("#cells").innerHTML = bytes.map((b, i) => `<div class="cell${fieldCells.has(i) ? " fld" : ""}${S.hitCells && i >= S.hitCells[0] && i < S.hitCells[0] + S.hitCells[1] ? " hit" : ""}" data-i="${i}"><span class="i">${i}</span><span class="v">${b.toString(16).toUpperCase().padStart(2, "0")}</span></div>`).join("");
  let badges = "";
  if (dec.type) badges += `<span class="badge info">${esc(dec.type)}</span>`;
  if (dec.checksum_ok === true) badges += `<span class="badge ok">checksum ✓</span>`;
  if (dec.checksum_ok === false) badges += `<span class="badge bad">checksum ✗</span>`;
  (f.flags || []).forEach(x => badges += `<span class="badge bad">${esc(x)}</span>`);
  $("#inspBadges").innerHTML = badges;
  renderDecoded(dec);
  if (f.modbus) {
    const m = f.modbus, regs = m.registers || m.coils || m.bits;
    $("#decoded").insertAdjacentHTML("afterbegin", `<div class="card"><div class="card-title">Modbus ${esc(m.kind)}</div><div>${esc(m.summary)}</div>
      ${regs ? `<table class="kv">${regs.slice(0, 40).map((v, i) => `<tr><td>${m.start !== undefined ? "address " + (m.start + i) : "#" + i}</td><td class="v">${esc(v)}</td></tr>`).join("")}</table>` : ""}</div>`);
  }
}
function fieldSize(t) {
  const m = { u8: 1, i8: 1, u16le: 2, u16be: 2, i16le: 2, i16be: 2, u32le: 4, u32be: 4, i32le: 4, i32be: 4 };
  return m[t] || parseInt(t.split(":")[1] || "1", 10);
}
async function renderDecoded(dec) {
  if (!dec.fields.length) { $("#decoded").innerHTML = `<p class="muted small" style="margin-top:12px">No named fields for this message yet. Drag across bytes above to name some.</p>`; return; }
  $("#decoded").innerHTML = `<table class="kv">${dec.fields.map(fl => {
    const v = dec.values[fl.name];
    return `<tr><td>${esc(fl.name)} <span class="muted small">${esc(fl.type)}${fl.scale !== 1 ? ` ×${fl.scale}` : ""} @${fl.offset}</span></td>
      <td class="v">${v === null || v === undefined ? "—" : esc(v)} ${esc(fl.unit || "")}</td><td><svg class="spark" data-field="${esc(fl.name)}"></svg></td></tr>`;
  }).join("")}</table>`;
  for (const fl of dec.fields) {
    try {
      const r = await api(`series?type=${encodeURIComponent(dec.type)}&field=${encodeURIComponent(fl.name)}`);
      drawSpark($(`.spark[data-field="${CSS.escape(fl.name)}"]`), r.points.slice(-200));
    } catch (e) { /* ignore */ }
  }
}
function drawSpark(svg, pts) {
  if (!svg || pts.length < 2) return;
  const W = 120, H = 28, ys = pts.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys);
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
  const pl = pts.map(([t, y]) => `${((t - t0) / (t1 - t0 || 1) * (W - 4) + 2).toFixed(1)},${(H - 3 - ((y - lo) / (hi - lo || 1)) * (H - 6)).toFixed(1)}`).join(" ");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `<title>${lo} … ${hi}</title><polyline points="${pl}"/>`;
}

let dragStart = null;
function cellsDown(e) {
  const c = e.target.closest(".cell"); if (!c) return;
  const i = +c.dataset.i;
  if (e.shiftKey && S.selRange) dragStart = S.selRange[0]; else dragStart = i;
  setSel(Math.min(dragStart, i), Math.max(dragStart, i));
}
function cellsOver(e) {
  if (dragStart === null || !(e.buttons & 1)) return;
  const c = e.target.closest(".cell"); if (!c) return;
  const i = +c.dataset.i;
  setSel(Math.min(dragStart, i), Math.max(dragStart, i));
}
function cellsUp() { if (dragStart !== null) { dragStart = null; showSelInfo(); } }
function setSel(a, b) {
  S.selRange = [a, b];
  $$("#cells .cell").forEach(c => { const i = +c.dataset.i; c.classList.toggle("sel", i >= a && i <= b); });
}
function selBytes() {
  const f = S.byId.get(S.selected); if (!f || !S.selRange) return [];
  return bytesOf(f.hex).slice(S.selRange[0], S.selRange[1] + 1);
}
function interpret(bs) {
  const out = [];
  const n = bs.length;
  if (n === 1) out.push(["u8", bs[0]], ["i8", bs[0] > 127 ? bs[0] - 256 : bs[0]]);
  if (n === 2) {
    const le = bs[0] | bs[1] << 8, be = bs[0] << 8 | bs[1];
    out.push(["u16 LE", le], ["u16 BE", be], ["i16 LE", le > 32767 ? le - 65536 : le], ["÷10 LE", (le > 32767 ? le - 65536 : le) / 10]);
  }
  if (n === 4) {
    const le = (bs[0] | bs[1] << 8 | bs[2] << 16 | bs[3] << 24) >>> 0;
    const be = (bs[0] << 24 | bs[1] << 16 | bs[2] << 8 | bs[3]) >>> 0;
    out.push(["u32 LE", le], ["u32 BE", be]);
  }
  if (bs.every(b => b >= 32 && b < 127)) out.push(["text", `"${String.fromCharCode(...bs)}"`]);
  if (bs.every(b => (b >> 4) < 10 && (b & 15) < 10)) out.push(["BCD", bs.map(b => b.toString(16).padStart(2, "0")).join("")]);
  return out;
}
function showSelInfo() {
  const bs = selBytes(); if (!bs.length) return;
  const [a, b] = S.selRange;
  $("#selInfo").innerHTML = `<span>Bytes ${a}${b > a ? "–" + b : ""}:</span>${interpret(bs).map(([k, v]) => `<span>${esc(k)} <b>${esc(v)}</b></span>`).join("")}
    <button class="primary soft tiny" id="mkField">Name these bytes…</button>`;
  $("#selInfo").classList.remove("hidden");
  $("#mkField").onclick = openFieldForm;
}
function openFieldForm() {
  const f = S.byId.get(S.selected); const bs = bytesOf(f.hex); const [a, b] = S.selRange; const n = b - a + 1;
  const kinds = [["u8", "number, 1 byte"], ["i8", "signed, 1 byte"], ["u16le", "number, 2 bytes LE"], ["u16be", "number, 2 bytes BE"],
    ["i16le", "signed, 2 bytes LE"], ["i16be", "signed, 2 bytes BE"], ["u32le", "number, 4 bytes LE"], ["u32be", "number, 4 bytes BE"],
    ["i32le", "signed, 4 bytes LE"], [`ascii:${n}`, `text, ${n} bytes`], [`bcd:${n}`, `BCD digits, ${n} bytes`]];
  fillSelect($("#ffKind"), kinds.filter(([k]) => fieldSize(k) === n || k.includes(":")), n === 1 ? "u8" : n === 2 ? "i16le" : n === 4 ? "u32le" : `ascii:${n}`);
  if (!$("#ffKind").value) $("#ffKind").selectedIndex = 0;
  const t = S.protocol.frame_types.find(t => t.name === f.type);
  const rule = S.status.rule || {};
  const match = bs.slice(0, Math.min(4, a || 4)).map((x, i) => rule.kind === "length" && i === rule.len_offset ? "??" : x.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  $("#ffType").value = t ? t.name : `Message ${(bs[3] ?? bs[0] ?? 0).toString(16).toUpperCase().padStart(2, "0")}`;
  $("#ffMatch").value = t ? t.match : match;
  $("#ffName").value = ""; $("#ffScale").value = 1; $("#ffUnit").value = "";
  const scoped = !/^(ctrl|disp|master|slave|sent$|bus$)/.test(f.channel);
  $("#ffChanWrap").classList.toggle("hidden", !scoped);
  $("#ffChan").checked = scoped && !t; $("#ffChanName").textContent = f.channel.replace(/^sent /, "");
  if (scoped && !t) { $("#ffMatch").value = ""; $("#ffType").value = f.channel.replace(/^sent /, "").replace(/^CAN /, "ID "); }
  $("#fieldForm").classList.remove("hidden");
  updateFieldPreview();
  $("#ffName").focus();
}
function updateFieldPreview() {
  const bs = selBytes(); const k = $("#ffKind").value; const sc = parseFloat($("#ffScale").value) || 1;
  const ints = { u8: [1, 0, 0], i8: [1, 0, 1], u16le: [2, 0, 0], u16be: [2, 1, 0], i16le: [2, 0, 1], i16be: [2, 1, 1], u32le: [4, 0, 0], u32be: [4, 1, 0], i32le: [4, 0, 1] };
  let v;
  if (ints[k]) {
    const [n, be, sg] = ints[k]; const arr = be ? bs.slice(0, n) : bs.slice(0, n).reverse();
    v = arr.reduce((a, b) => a * 256 + b, 0);
    if (sg && v >= 2 ** (8 * n - 1)) v -= 2 ** (8 * n);
    v = +(v * sc).toFixed(6);
  } else if (k.startsWith("ascii")) v = `"${String.fromCharCode(...bs)}"`;
  else v = bs.map(b => b.toString(16).padStart(2, "0")).join("");
  $("#ffPreview").textContent = `In this message it reads as: ${v} ${$("#ffUnit").value}`;
}
async function saveField(e) {
  e.preventDefault();
  const [a] = S.selRange;
  await safe(async () => {
    const f0 = S.byId.get(S.selected);
    S.protocol = await api("protocol/field", { type: $("#ffType").value.trim(), match: $("#ffMatch").value.trim().toUpperCase(),
      channel: $("#ffChan").checked && f0 ? f0.channel : "",
      field: { name: $("#ffName").value.trim(), offset: a, type: $("#ffKind").value, scale: parseFloat($("#ffScale").value) || 1, unit: $("#ffUnit").value.trim() } });
    toast(`Saved “${$("#ffName").value.trim()}” — every matching message now shows it`, "ok");
    $("#fieldForm").classList.add("hidden");
    await reloadFrames();
    renderProtocol();
  });
}
async function diffPrev() {
  const f = S.byId.get(S.selected); if (!f) return;
  const key = f.type ? (x => x.type === f.type) : (x => x.hex.slice(0, 11) === f.hex.slice(0, 11));
  const idx = S.frames.indexOf(f);
  const prev = [...S.frames.slice(0, idx)].reverse().find(x => x.channel === f.channel && key(x));
  if (!prev) { toast("No earlier message of the same kind to compare with"); return; }
  await safe(async () => {
    const r = await api("diff", { a: prev.id, b: f.id });
    const A = bytesOf(r.a), B = bytesOf(r.b);
    const row = (bs, label) => `<div class="muted small" style="margin:8px 0 4px">${label}</div><div class="cells">${r.diff.map((d, i) => bs[i] === undefined ? `<div class="cell" style="opacity:.3"><span class="i">${i}</span><span class="v">··</span></div>` :
      `<div class="cell${d !== "same" ? " chg" : ""}"><span class="i">${i}</span><span class="v">${bs[i].toString(16).toUpperCase().padStart(2, "0")}</span></div>`).join("")}</div>`;
    const changed = r.diff.filter(d => d !== "same").length;
    $("#diffBox").innerHTML = `<div class="card"><div class="card-title">${changed ? `${changed} byte${changed > 1 ? "s" : ""} changed` : "Identical"} since message #${prev.id}</div>${row(A, `#${prev.id} · ${fmtTime(prev.t_start)}`)}${row(B, `#${f.id} · ${fmtTime(f.t_start)}`)}</div>`;
    $("#diffBox").classList.remove("hidden");
  });
}

/* ---------------- markers ---------------- */
function openMarker() {
  $("#markerPop").classList.remove("hidden");
  $("#markerPop").dataset.t = Date.now() / 1000;
  $("#markerLabel").value = "";
  $("#markerLabel").focus();
}
async function addMarker(e) {
  e.preventDefault();
  const label = $("#markerLabel").value.trim() || "Something happened";
  const t = parseFloat($("#markerPop").dataset.t);
  $("#markerPop").classList.add("hidden");
  await safe(() => api("markers", { label, t }));
}
function renderMarkers() {
  $("#markerList").innerHTML = S.markers.length ? S.markers.map((m, i) => `<div class="mitem"><span>★ <span class="mono small">${fmtTime(m.t)}</span> ${esc(m.label)}</span>
    <span class="row-inline"><button class="ghost tiny" data-near="${i}">What changed?</button>${/\d/.test(m.label) ? `<button class="ghost tiny" data-findm="${i}">Find number</button>` : ""}</span></div>`).join("")
    : `<p class="muted small">No markers yet.</p>`;
}
async function whatChanged(i) {
  await safe(async () => {
    const r = await api(`markers/${i}/nearby`);
    const m = S.markers[i];
    openModal(`<h3>Around “${esc(m.label)}”</h3><p class="muted">Within 2 seconds of the marker.</p>
      <h4>New kinds of message</h4>${r.new_types.length ? r.new_types.map(t => `<div class="mono">${esc(t)} …</div>`).join("") : `<p class="muted small">None</p>`}
      <h4>Messages that changed</h4>${r.changed.length ? r.changed.slice(0, 50).map(id => { const f = S.byId.get(id); return f ? `<div class="result" data-go="${id}"><span class="mono small">#${id} ${esc(f.hex.slice(0, 60))}</span><span class="score">${fmtTime(f.t_start)}</span></div>` : ""; }).join("") : `<p class="muted small">None — the display may have changed by itself, or the data is in a message that repeats unchanged.</p>`}
      <div class="actions"><button class="ghost" data-close>Close</button></div>`);
    $$("#modalCard [data-go]").forEach(el => el.onclick = () => { closeModal(); selectFrame(+el.dataset.go, true); });
  });
}

/* ---------------- find value ---------------- */
async function findValue(e) {
  if (e) e.preventDefault();
  const q = $("#findQ").value.trim();
  if (!q) { $("#findResults").innerHTML = ""; return; }
  await safe(async () => {
    const r = await api(`search?q=${encodeURIComponent(q)}`);
    if (!r.hits.length) { $("#findResults").innerHTML = `<p class="muted">Not found in ${S.frames.length} messages. Try a nearby value, or capture while the display shows it.</p>`; return; }
    $("#findResults").innerHTML = `<p class="muted small">${r.hits.length}${r.hits.length >= 500 ? "+" : ""} matches — best first (matches near a ★ marker rank higher)</p>` +
      r.hits.slice(0, 200).map(h => `<div class="result" data-go="${h.frame_id}" data-off="${h.offset}" data-len="${h.length}">
        <span>#${h.frame_id} · byte ${h.offset}${h.length > 1 ? "–" + (h.offset + h.length - 1) : ""} <span class="enc">${esc(h.encoding)}</span></span>
        <span class="score">${fmtTime(h.t)}${h.score > 1 ? " · near marker" : ""}</span></div>`).join("");
  });
}
async function goHit(el) {
  const id = +el.dataset.go;
  if (!S.byId.has(id)) { toast("That message is not loaded — use Load older"); return; }
  S.selected = id;
  $$(".frow.sel").forEach(r => r.classList.remove("sel"));
  const row = $(`.frow[data-id="${id}"]`);
  if (row) { row.classList.add("sel"); row.scrollIntoView({ block: "center", behavior: "smooth" }); S.follow = false; $("#follow").checked = false; }
  await inspect(id, { hit: [+el.dataset.off, +el.dataset.len], force: true });
  switchTab("inspect");
  setSel(+el.dataset.off, +el.dataset.off + +el.dataset.len - 1); showSelInfo();
}

/* ---------------- byte map ---------------- */
function renderMapTypes() {
  fillSelect($("#mapType"), [["", "All messages"]].concat(S.protocol.frame_types.map(t => [t.name, t.name])), $("#mapType").value);
}
async function showMap(e) {
  if (e) e.preventDefault();
  const type = $("#mapType").value; const prefix = $("#mapPrefix").value.trim();
  await safe(async () => {
    let path = "variability";
    if (type) path += `?type=${encodeURIComponent(type)}`;
    else if (prefix) path += `?prefix=${encodeURIComponent(prefix.split(/\s+/).filter(x => x !== "??").join(" ").split("??")[0])}`;
    const r = await api(path);
    if (!r.count) { $("#mapOut").innerHTML = `<p class="muted">No messages of that kind yet.</p>`; return; }
    $("#mapOut").innerHTML = `<p class="muted small">${r.count} messages compared</p><div class="mapgrid">${r.offsets.map(o =>
      `<div class="mc ${o.kind}" title="byte ${o.offset}: ${o.kind}, ${o.distinct} different value${o.distinct > 1 ? "s" : ""}">${o.offset}<small>${o.distinct}×</small></div>`).join("")}</div>
      <p class="muted small" style="margin-top:10px">Numbers under each byte = how many different values it took. Bytes that <em>change</em> are where the data lives; <em>counters</em> are usually sequence numbers.</p>`;
  });
}

/* ---------------- checksum ---------------- */
async function findChecksum() {
  await safe(async () => {
    const r = await api("checksums/find", {});
    if (!r.matches.length) { $("#csOut").innerHTML = `<p class="muted">No common checksum fits ≥95% of messages. It may use a custom formula, cover a different range, or there may be no checksum. Fix the framing first if messages look cut wrongly.</p>`; return; }
    $("#csOut").innerHTML = r.matches.map((m, i) => `<div class="cand ${i ? "" : "best"}"><div class="lbl">${esc(m.spec.algo)}</div><button class="${i ? "ghost" : "primary"} tiny" data-cs="${i}">Use this</button>
      <div class="bar"><div style="width:${Math.round(m.ratio * 100)}%"></div></div><div class="pv">${csText(m.spec)} · fits ${Math.round(m.ratio * 100)}% of messages</div></div>`).join("");
    $$("#csOut [data-cs]").forEach(b => b.onclick = () => safe(async () => {
      S.protocol = await api("protocol/checksum", { spec: r.matches[+b.dataset.cs].spec });
      toast("Checksum saved — the composer will now fix it for you", "ok"); renderProtocol(); if (S.selected !== null) inspect(S.selected);
    }));
  });
}
function csText(s) { return `bytes ${s.start}…(end−${s.end_from_end + 1}), stored in the last ${s.pos_from_end === 1 ? "byte" : s.pos_from_end + " bytes"}${s.pos_from_end > 1 ? ` (${s.byteorder}-endian)` : ""}`; }

/* ---------------- protocol tab ---------------- */
async function renderProtocol() {
  const p = S.protocol;
  $("#csCurrent").textContent = p.checksum ? `Current: ${p.checksum.algo}` : "";
  $("#protoOut").innerHTML = `<div class="card"><div class="card-title">What you've worked out</div>
    <div class="small"><b>Framing:</b> ${esc(ruleText(p.framing))}</div>
    <div class="small"><b>Checksum:</b> ${p.checksum ? `${esc(p.checksum.algo)} — ${esc(csText(p.checksum))} <button class="x" id="csClear" title="Remove checksum">×</button>` : `<span class="muted">not set</span>`}</div>
    ${p.frame_types.length ? p.frame_types.map((t, ti) => {
      const y = t.verdicts.filter(v => v.ok).length, n = t.verdicts.length - y;
      return `<div class="ftype"><div class="h"><strong>${esc(t.name)}</strong><span class="vcount">${t.verdicts.length ? `<span class="y">✓ ${y}</span> <span class="n">✗ ${n}</span>` : `<span class="muted">not tested</span>`}</span></div>
      <div class="m">${esc(t.match)} …</div><ul>${t.fields.map(f => `<li><span>${esc(f.name)} <span class="muted small">${esc(f.type)} @${f.offset}${f.scale !== 1 ? ` ×${f.scale}` : ""} ${esc(f.unit)}</span></span>
      <button class="x" data-del="${ti}" data-f="${esc(f.name)}" title="Delete field">×</button></li>`).join("")}</ul></div>`;
    }).join("") : `<p class="muted small" style="margin-top:8px">No message types yet. Select bytes in the inspector and name them.</p>`}</div>`;
  $("#csClear")?.addEventListener("click", () => safe(async () => { S.protocol = await api("protocol/checksum", { spec: null }); renderProtocol(); }));
  $$("#protoOut [data-del]").forEach(b => b.onclick = () => safe(async () => {
    S.protocol = await api("protocol/field/delete", { type: S.protocol.frame_types[+b.dataset.del].name, field: b.dataset.f, drop_type_if_empty: true });
    renderProtocol(); await reloadFrames();
  }));
  renderMapTypes();
  const pr = await api("projects");
  fillSelect($("#projectSel"), pr.projects.map(x => [x, x]), pr.current);
  const ss = await api("sessions");
  fillSelect($("#sessionSel"), [["", "Saved captures…"]].concat(ss.sessions.map(s => [s, s.replace(".jsonl", "").replace(/(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)/, "$1-$2-$3 $4:$5:$6")])));
}
async function openProject() {
  const name = $("#projectNew").value.trim() || $("#projectSel").value;
  await safe(async () => {
    const r = await api("projects/open", { name });
    S.protocol = r.protocol; $("#projectNew").value = "";
    toast(`Project “${name}” opened`, "ok");
    await reloadFrames(); renderProtocol();
  });
}
async function loadSession() {
  const name = $("#sessionSel").value; if (!name) return;
  await safe(async () => { await api("sessions/load", { name }); toast("Capture opened — you can analyse it offline", "ok"); await reloadFrames(); });
}

/* ---------------- composer + send ---------------- */
async function setMode(mode) {
  if (mode === "send" && !store("tl.sendChecked")) { sendChecklist(); return; }
  await safe(async () => {
    applyStatus(await api("mode", { mode }));
    if (mode === "send") { switchTab("compose"); toast("Send mode — Tapline can now transmit", ""); }
  });
}
function sendChecklist() {
  openModal(`<h3>Before sending</h3><p class="muted">Sending puts your adapter in place of the controller. Check the wiring first:</p>
    <label class="check"><input type="checkbox"> <span>The controller's TX wire is <b>disconnected</b> from the display (two senders on one wire can fight).</span></label>
    <label class="check"><input type="checkbox"> <span>Adapter <b>TX → display RX</b>.</span></label>
    <label class="check"><input type="checkbox"> <span>Adapter <b>GND ↔ GND</b> of the display/board.</span></label>
    <label class="check"><input type="checkbox"> <span>Voltage matches — adapter jumper on <b>3.3 V or 5 V</b> like the board. Never connect RS-232 (±12 V) to TTL.</span></label>
    <div class="actions"><button class="ghost" data-close>Cancel</button><button class="primary" id="ckOk" disabled>I'm ready — switch to Send</button></div>`);
  const boxes = $$("#modalCard input[type=checkbox]");
  boxes.forEach(b => b.onchange = () => $("#ckOk").disabled = !boxes.every(x => x.checked));
  $("#ckOk").onclick = () => { store("tl.sendChecked", "1"); closeModal(); setMode("send"); };
}
async function editAndSend() {
  const f = S.byId.get(S.selected); if (!f) return;
  const canId = (f.channel.match(/CAN 0x([0-9A-F]+)$/) || [])[1];
  $("#composeHex").value = canId ? `${canId}#${f.hex.replace(/ /g, "")}` : f.hex;
  S.composeBase = f.id;
  await renderComposeFields(f);
  switchTab("compose");
  if (S.status.mode !== "send") toast("Switch to Send mode to transmit this message");
}
async function renderComposeFields(f) {
  const t = S.protocol.frame_types.find(t => t.name === f.type);
  if (!t || !t.fields.length) { $("#composeFields").innerHTML = ""; return; }
  const dec = await api("protocol/decode", { frame_id: f.id });
  $("#composeFields").innerHTML = `<div class="card"><div class="card-title">${esc(t.name)} — change values</div><div class="grid2">${t.fields.map(fl =>
    `<label class="field"><span>${esc(fl.name)} ${esc(fl.unit || "")}</span><input data-fld="${esc(fl.name)}" value="${esc(dec.values[fl.name] ?? "")}"></label>`).join("")}</div>
    <div class="actions"><button class="ghost tiny" id="buildMsg">Update message</button></div></div>`;
  $("#buildMsg").onclick = () => safe(async () => {
    const values = {}; $$("#composeFields [data-fld]").forEach(i => values[i.dataset.fld] = i.value);
    const r = await api("compose", { type: t.name, values, base_frame_id: f.id });
    $("#composeHex").value = r.hex; toast("Message updated — length and checksum fixed", "ok");
  });
}
async function prepareHex() {
  let hex = $("#composeHex").value;
  if (hex.includes("#")) return hex;  // CAN text: nothing to fix
  if ($("#autoFix").checked && (S.protocol.checksum || S.protocol.framing.kind === "length")) {
    hex = (await api("finalize", { hex })).hex;
    $("#composeHex").value = hex;
  }
  return hex;
}
async function doSend(repeat) {
  const target = $("#target").value;
  if (target !== "source") { if (repeat) toast("Repeat is for the current source; sending once"); return picoSend(target === "pico-i2c" ? "i2c" : "spi"); }
  await safe(async () => {
    const hex = await prepareHex();
    const body = { hex };
    if (repeat) body.repeat_ms = parseInt($("#repeatMs").value, 10) || 500;
    else if ($("#waitReply").checked) body.wait_ms = parseInt($("#waitMs").value, 10) || 300;
    const r = await api("send", body);
    showReplies(r.replies);
    S.lastSent = r.sent;
    applyStatus(r.status);
    toast(repeat ? `Repeating every ${body.repeat_ms} ms` : `Sent ${r.sent}`, "ok");
    $("#verdictBox").classList.remove("hidden"); $("#verdictNote").value = "";
  });
}
async function verdict(ok) {
  if (!S.lastSent) return;
  await safe(async () => {
    const r = await api("verdict", { hex: S.lastSent, ok, note: $("#verdictNote").value });
    S.protocol = await api("protocol");
    $("#verdictBox").classList.add("hidden");
    toast(ok ? `Nice — noted as confirmed for “${r.type}”` : `Noted. Try changing one byte at a time.`, ok ? "ok" : "");
    renderProtocol();
  });
}
async function replaySelected() {
  if (S.selected === null) { toast("Select a message to start replaying from"); return; }
  const idx = S.frames.findIndex(f => f.id === S.selected);
  const ch = S.frames[idx].channel;
  const ids = S.frames.slice(idx).filter(f => f.channel === ch).slice(0, 200).map(f => f.id);
  await safe(async () => { const r = await api("replay", { frame_ids: ids, keep_timing: true }); toast(`Replaying ${r.count} messages with original timing`, "ok"); });
}

/* ---------------- detect ---------------- */
async function detect() {
  const port = $("#source").value;
  openModal(`<h3>Detecting serial settings…</h3><p class="muted">Listening on <b>${esc(port === "simulator" ? "the simulator" : port)}</b> at different speeds. Tapline never transmits while detecting. Make sure the board is on and sending.</p>
    <div class="progress"><div id="detBar"></div></div><div class="muted small" id="detLabel">Starting…</div><div id="detOut"></div>
    <div class="actions"><button class="ghost" data-close>Close</button></div>`);
  try {
    const r = await api("detect", { port });
    $("#detBar").style.width = "100%";
    $("#detLabel").textContent = r.message || "Done — best match first";
    $("#detOut").innerHTML = r.candidates.map((c, i) => `<div class="cand ${i ? "" : "best"}"><div class="lbl">${esc(c.label)}</div>
      <button class="${i ? "ghost" : "primary"} tiny" data-c="${i}">Use these settings</button>
      <div class="bar"><div style="width:${Math.round(c.score * 100)}%"></div></div><div class="pv">${Math.round(c.score * 100)}% confidence · ${esc(c.preview) || "no bytes"}</div></div>`).join("");
    $$("#detOut [data-c]").forEach(b => b.onclick = () => { applyConfig(r.candidates[+b.dataset.c].config); closeModal(); toast(`Settings set to ${r.candidates[+b.dataset.c].label}`, "ok"); });
  } catch (e) { $("#detLabel").textContent = e.message; }
}

/* ---------------- tabs, modal, tooltip ---------------- */
function switchTab(name) {
  $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$(".tab-body").forEach(b => b.classList.toggle("hidden", b.id !== "tab-" + name));
  if (name === "protocol" || name === "map") safe(renderProtocol);
  if (name === "find") renderMarkers();
}
function openModal(html) { $("#modalCard").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
function tipShow(e) {
  const el = e.target.closest(".b, .cell");
  if (!el) { $("#tooltip").classList.add("hidden"); return; }
  let bytes, idx;
  if (el.classList.contains("b")) { const row = el.closest(".frow"); const f = S.byId.get(+row.dataset.id); bytes = bytesOf(f.hex); idx = +el.dataset.off; }
  else { const f = S.byId.get(S.selected); bytes = bytesOf(f.hex); idx = +el.dataset.i; }
  const t = $("#tooltip");
  t.textContent = byteInfo(bytes[idx], idx, bytes);
  t.classList.remove("hidden");
  const r = el.getBoundingClientRect();
  const x = Math.min(window.innerWidth - t.offsetWidth - 8, Math.max(8, r.left + r.width / 2 - t.offsetWidth / 2));
  const y = r.top - t.offsetHeight - 8 < 8 ? r.bottom + 8 : r.top - t.offsetHeight - 8;
  t.style.left = x + "px"; t.style.top = y + "px";
}

/* ---------------- tour ---------------- */
const TOUR = [
  ["source", "1 · Choose what to listen to", "Start with <b>Simulator (demo)</b> — a pretend board talking to a display. Later pick your USB-TTL adapter here, and use <b>✨ Detect</b> if you don't know the baud rate."],
  ["start", "2 · Press Start", "Messages appear on the left as they arrive. Hover a byte to see it as a number, letter and bits."],
  ["mark", "3 · Mark what you see", "When the display changes, press <kbd>Space</kbd> and type what it shows (e.g. “25.0 °C”). Then <b>Find value</b> searches every encoding for it."],
  ["mode", "4 · Test your guess", "Switch to <b>Send</b>, edit a message in the <b>Composer</b>, send it to the display, and record whether it did what you expected."],
];
function tour(step = 0) {
  $$(".tour-focus").forEach(e => e.classList.remove("tour-focus"));
  if (step >= TOUR.length) { $("#tour").classList.add("hidden"); store("tl.tourDone", "1"); return; }
  const [key, title, text] = TOUR[step];
  const target = $(`[data-tour="${key}"]`);
  $("#tour").classList.remove("hidden");
  target.classList.add("tour-focus");
  const r = target.getBoundingClientRect();
  const card = $("#tourCard");
  card.innerHTML = `<h4>${title}</h4><p>${text}</p><div class="actions"><span class="steps">${step + 1} / ${TOUR.length}</span>
    <button class="ghost tiny" id="tourSkip">Skip</button><button class="primary tiny" id="tourNext">${step === TOUR.length - 1 ? "Done" : "Next"}</button></div>`;
  const w = card.offsetWidth;
  card.style.left = Math.max(12, Math.min(window.innerWidth - w - 12, r.left)) + "px";
  card.style.top = (r.bottom + 12 + card.offsetHeight > window.innerHeight ? Math.max(12, r.top - card.offsetHeight - 12) : r.bottom + 12) + "px";
  $("#tourNext").onclick = () => tour(step + 1);
  $("#tourSkip").onclick = () => tour(TOUR.length);
}

/* ---------------- live connection ---------------- */
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "status") applyStatus(m.status);
    else if (m.type === "frames") { appendFrames(m.items); applyStatus({ ...S.status, frames: S.frames.length ? Math.max(S.status.frames, S.frames[S.frames.length - 1].id + 1) : 0 }); }
    else if (m.type === "marker") {
      S.markers.push(m.marker);
      $("#rows").insertAdjacentHTML("beforeend", markerRow(m.marker, S.markers.length - 1));
      if (S.follow) scrollBottom();
      renderMarkers(); toast(`★ Marked: ${m.marker.label}`, "");
    }
    else if (m.type === "reframed") reloadFrames();
    else if (m.type === "error") toast(m.message, "err");
    else if (m.type === "progress" && m.message) toast(m.message);
    else if (m.type === "detect_progress" && $("#detBar")) {
      $("#detBar").style.width = Math.round(m.done / m.total * 100) + "%";
      $("#detLabel").textContent = m.label ? `Trying ${m.label}… (${m.done}/${m.total})` : "";
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

/* ---------------- wire up ---------------- */
function bind() {
  $("#startBtn").onclick = toggleStart;
  $("#detectBtn").onclick = detect;
  $$("#modeSeg button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
  $("#frameKind").onchange = () => renderFrameParams(S.status.rule);
  $("#applyFraming").onclick = () => applyFraming();
  $("#guessFraming").onclick = guessFraming;
  $("#filter").oninput = renderAll;
  $("#follow").onchange = e => {
    S.follow = e.target.checked;
    if (S.follow) { if (S.windowEnd !== null) { S.windowEnd = null; renderAll(); } scrollBottom(); }
  };
  $("#frames").addEventListener("scroll", () => {
    const el = $("#frames"); const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (!atBottom && S.follow && el.dataset.user) { S.follow = false; $("#follow").checked = false; }
    if (atBottom && !S.follow && S.windowEnd === null) { S.follow = true; $("#follow").checked = true; }
  });
  $("#frames").addEventListener("wheel", () => $("#frames").dataset.user = "1", { passive: true });
  $("#frames").addEventListener("touchmove", () => $("#frames").dataset.user = "1", { passive: true });
  $("#loadOlder").onclick = loadOlder;
  $("#rows").addEventListener("click", e => {
    const b = e.target.closest(".b"); const row = e.target.closest(".frow");
    if (b) highlightOffset(+b.dataset.off);
    if (row) selectFrame(+row.dataset.id);
  });
  document.addEventListener("mouseover", tipShow);
  document.addEventListener("scroll", () => $("#tooltip").classList.add("hidden"), true);
  $("#cells").addEventListener("mousedown", cellsDown);
  $("#cells").addEventListener("mouseover", cellsOver);
  document.addEventListener("mouseup", cellsUp);
  $("#fieldForm").onsubmit = saveField;
  $("#ffCancel").onclick = () => $("#fieldForm").classList.add("hidden");
  ["#ffKind", "#ffScale", "#ffUnit"].forEach(s => $(s).addEventListener("input", updateFieldPreview));
  $("#diffPrev").onclick = diffPrev;
  $("#copyHex").onclick = () => { const f = S.byId.get(S.selected); if (f) navigator.clipboard?.writeText(f.hex).then(() => toast("Copied")).catch(() => toast(f.hex)); };
  $("#editSend").onclick = editAndSend;
  $$(".tabs button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
  $("#markBtn").onclick = openMarker;
  $("#markerForm").onsubmit = addMarker;
  $("#markerCancel").onclick = () => $("#markerPop").classList.add("hidden");
  $("#findForm").onsubmit = findValue;
  $("#findResults").addEventListener("click", e => { const r = e.target.closest("[data-go]"); if (r) goHit(r); });
  $("#markerList").addEventListener("click", e => {
    const n = e.target.closest("[data-near]"); if (n) whatChanged(+n.dataset.near);
    const fm = e.target.closest("[data-findm]");
    if (fm) { const m = S.markers[+fm.dataset.findm]; $("#findQ").value = (m.label.match(/-?\d+(?:[.,]\d+)?/) || [""])[0]; findValue(); }
  });
  $("#mapForm").onsubmit = showMap;
  $("#findCs").onclick = findChecksum;
  $("#projectOpen").onclick = openProject;
  $("#sessionLoad").onclick = loadSession;
  $("#tourAgain").onclick = () => tour(0);
  $("#sendBtn").onclick = () => doSend(false);
  $("#repeatBtn").onclick = () => doSend(true);
  $("#stopSend").onclick = () => safe(async () => { applyStatus(await api("send/stop", {})); toast("Stopped sending"); });
  $("#replaySel").onclick = replaySelected;
  $("#wiringBtn").onclick = () => wiringGuide();
  $("#guideBtn").onclick = () => guide();
  $("#hintGuide").onclick = () => guide(sourceKind($("#source").value) === "serial" ? "hw" : "demo");
  $("#laScan").onclick = laScan;
  $("#laProto").onchange = renderLaPins;
  $("#laFile").onchange = renderLaPins;
  $("#laDevice").onchange = renderLaPins;
  $("#laImport").onchange = laImport;
  $("#target").onchange = onTargetChange;
  $("#picoConnect").onclick = picoConnect;
  $("#picoScan").onclick = picoScan;
  $("#mbFn").onchange = onMbFn;
  $("#mbBuild").onclick = mbBuild;
  $("#picoGuide").onclick = () => wiringGuide("pico");
  $$("#verdictBox [data-ok]").forEach(b => b.onclick = () => verdict(b.dataset.ok === "1"));
  $("#modal").addEventListener("click", e => { if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal(); });
  document.addEventListener("keydown", e => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if (e.key === "Escape") { closeModal(); $("#markerPop").classList.add("hidden"); $("#fieldForm").classList.add("hidden"); }
    if (typing) return;
    if (e.code === "Space") { e.preventDefault(); openMarker(); }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && S.selected !== null) {
      const vis = $$(".frow"); const i = vis.findIndex(r => +r.dataset.id === S.selected);
      const nx = vis[i + (e.key === "ArrowDown" ? 1 : -1)];
      if (nx) { e.preventDefault(); selectFrame(+nx.dataset.id, true); }
    }
  });
}


/* ---------------- start body per source ---------------- */
function startBody(src) {
  if (src === "simulator" || src === "sim-modbus" || src === "sim-can") return { source: src };
  if (src.startsWith("sim-la:")) return { source: "sim-la", opts: { protocol: src.split(":")[1] } };
  if (src === "la-device" || src === "la-file") {
    const spec = laSpec();
    if (src === "la-file") {
      if (!$("#laFile").value) throw new Error("Import or choose a .sr capture file first");
      return { source: "sigrok", opts: { file: $("#laFile").value, spec } };
    }
    if (!$("#laDevice").value) throw new Error("Press Scan and choose your logic analyzer");
    const dev = S.laDevices.find(d => d.driver === $("#laDevice").value);
    return { source: "sigrok", opts: { spec, device: { driver: dev.driver, samplerate: $("#laRate").value,
      seconds: parseFloat($("#laSeconds").value) || 2, channels: [...new Set(Object.values(laPinValues()).filter(Boolean))] } } };
  }
  if (src === "can") return { source: "can", opts: { interface: $("#canIf").value, channel: $("#canCh").value.trim(), bitrate: +$("#canRate").value } };
  if (!src) throw new Error("No serial port selected — plug in your adapter and press ↻");
  return { source: "serial", config: currentConfig(src), link: $("#link").value };
}

/* ---------------- logic analyzer ---------------- */
S.laDevices = []; S.laChannels = ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"];
const LA_PINS = { uart: [["rx", "RX (TX of board)", true], ["tx", "2nd line", false]], i2c: [["scl", "SCL", true], ["sda", "SDA", true]],
  spi: [["clk", "CLK", true], ["mosi", "MOSI", false], ["miso", "MISO", false], ["cs", "CS", false]] };
function laPinValues() { const o = {}; $$("#laPins [data-pin]").forEach(e => o[e.dataset.pin] = e.value); return o; }
function laSpec() {
  const proto = $("#laProto").value, spec = { protocol: proto, ...laPinValues() };
  for (const k of Object.keys(spec)) if (spec[k] === "") delete spec[k];
  if (proto === "uart") spec.baudrate = parseInt($("#laBaud")?.value, 10) || 115200;
  if (proto === "spi") { const m = +($("#laMode")?.value || 0); spec.cpol = m >> 1; spec.cpha = m & 1; }
  return spec;
}
async function renderLaPins() {
  const kind = sourceKind($("#source").value);
  if (kind === "lafile" && $("#laFile").value) {
    try { S.laChannels = (await api("sigrok/channels", { file: $("#laFile").value })).channels; } catch (e) { /* ignore */ }
  } else if (kind === "ladev") {
    const d = S.laDevices.find(d => d.driver === $("#laDevice").value);
    if (d && d.channels.length) S.laChannels = d.channels;
  }
  const proto = $("#laProto").value, prev = laPinValues();
  const opts = (req) => (req ? [] : [["", "—"]]).concat(S.laChannels.map(c => [c, c]));
  $("#laPins").innerHTML = LA_PINS[proto].map(([k, label, req], i) => `<label>${esc(label)}<select data-pin="${k}">${opts(req).map(([v, l]) =>
    `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label>`).join("") +
    (proto === "uart" ? `<label>Baud<input id="laBaud" type="number" value="${esc(store("tl.laBaud") || 115200)}"></label>` : "") +
    (proto === "spi" ? `<label>Mode<select id="laMode"><option>0</option><option>1</option><option>2</option><option>3</option></select></label>` : "");
  // sensible defaults: guess by channel name, else D0, D1, ...
  LA_PINS[proto].forEach(([k, , req], i) => {
    const sel = $(`#laPins [data-pin="${k}"]`);
    const byName = S.laChannels.find(c => c.toLowerCase() === k || (k === "rx" && c.toLowerCase() === "tx"));
    const dflt = proto === "uart" && k === "tx" ? "" : S.laChannels[i] || "";  // SPI: CLK D0, MOSI D1, MISO D2, CS D3
    sel.value = prev[k] && S.laChannels.includes(prev[k]) ? prev[k] : byName || dflt;
  });
  $("#laBaud")?.addEventListener("change", e => store("tl.laBaud", e.target.value));
}
async function laScan() {
  await safe(async () => {
    toast("Looking for logic analyzers…");
    const r = await api("sigrok/scan", {});
    S.laDevices = r.devices;
    fillSelect($("#laDevice"), r.devices.length ? r.devices.map(d => [d.driver, `${d.description} (${d.driver})`]) : [["", "None found — plugged in? driver installed?"]]);
    renderLaPins();
  });
}
async function loadCaptures() {
  try {
    const r = await api("sigrok");
    if (!r.available) toast("sigrok-cli is not installed — see 🔌 Wiring for how to install it", "err");
    const keep = $("#laFile").value;
    fillSelect($("#laFile"), r.captures.length ? r.captures.map(c => [c, c]) : [["", "No captures yet — Import…"]]);
    if (keep && r.captures.includes(keep)) $("#laFile").value = keep;
    renderLaPins();
  } catch (e) { /* ignore */ }
}
async function laImport(e) {
  const file = e.target.files[0]; if (!file) return;
  await safe(async () => {
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    const r = await api("sigrok/import", { name: file.name, data_b64: btoa(bin) });
    await loadCaptures(); $("#laFile").value = r.file; renderLaPins();
    toast(`Imported ${r.file}`, "ok");
  });
  e.target.value = "";
}

/* ---------------- CAN ---------------- */
async function loadCan() {
  if (S.canLoaded) return;
  try {
    const r = await api("can");
    fillSelect($("#canIf"), r.interfaces, store("tl.canIf") || "slcan");
    fillSelect($("#canRate"), r.bitrates.map(b => [b, b >= 1000 ? `${b / 1000} kbit/s` : b]), store("tl.canRate") || 500000);
    $("#canCh").value = store("tl.canCh") || "";
    $("#canIf").onchange = () => store("tl.canIf", $("#canIf").value);
    $("#canRate").onchange = () => store("tl.canRate", $("#canRate").value);
    $("#canCh").onchange = () => store("tl.canCh", $("#canCh").value);
    if (!r.available) toast("python-can is not installed: run  pip install python-can", "err");
    S.canLoaded = true;
  } catch (e) { /* ignore */ }
}

/* ---------------- composer: targets, Pico, Modbus, replies ---------------- */
function onTargetChange() {
  const t = $("#target").value;
  document.body.classList.toggle("target-source", t === "source");
  $("#picoBox").classList.toggle("hidden", t === "source");
  $("#picoI2cOpts").classList.toggle("hidden", t !== "pico-i2c");
  $("#composeLabel").textContent = t === "pico-i2c" ? "Bytes to write (hex, may be empty for a pure read)" : t === "pico-spi" ? "Bytes to clock out on MOSI (hex)" : $("#composeLabel").textContent;
}
async function picoConnect() {
  await safe(async () => {
    store("tl.picoPort", $("#picoPort").value);
    const r = await api("pico/connect", { port: $("#picoPort").value });
    applyStatus(r.status); toast(`Pico bridge ready (${r.version})`, "ok");
  });
}
async function picoScan() {
  await safe(async () => {
    const r = await api("pico/scan", {});
    toast(r.addresses.length ? `I²C devices answering: ${r.addresses.map(a => "0x" + a).join(", ")}` : "No I²C device answered — check wiring and pull-ups");
    if (r.addresses.length && !$("#picoAddr").value) $("#picoAddr").value = r.addresses[0];
  });
}
async function picoSend(bus) {
  await safe(async () => {
    const r = await api("pico/send", { bus, addr: $("#picoAddr").value, hex: $("#composeHex").value, read_len: +$("#picoRead").value || 0 });
    S.lastSent = $("#composeHex").value.trim() ? $("#composeHex").value : null;
    toast(r.read ? `Sent · read back ${r.read}` : "Sent", "ok");
    if (S.lastSent) { $("#verdictBox").classList.remove("hidden"); $("#verdictNote").value = ""; }
  });
}
function onMbFn() {
  const fn = +$("#mbFn").value;
  $("#mbQtyLabel").textContent = fn === 5 ? "Value (1 = ON, 0 = OFF)" : fn === 6 ? "Value" : "How many";
  $("#mbValsWrap").classList.toggle("hidden", !(fn === 15 || fn === 16));
}
async function mbBuild() {
  await safe(async () => {
    const fn = +$("#mbFn").value;
    const values = (fn === 15 || fn === 16) ? $("#mbVals").value : undefined;
    const qty = (fn === 15 || fn === 16) ? $("#mbVals").value.split(/[\s,]+/).filter(Boolean).length : +$("#mbQty").value;
    const r = await api("modbus/build", { addr: +$("#mbAddr").value, fn, start: +$("#mbStart").value, qty, values });
    $("#composeHex").value = r.hex; $("#mbSummary").textContent = r.summary;
    $("#autoFix").checked = false;  // the CRC is already right
    $("#waitReply").checked = true;
  });
}
function showReplies(replies) {
  const box = $("#replyBox");
  if (!replies) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  box.innerHTML = `<div class="card replies"><div class="card-title">${replies.length ? (replies.length > 1 ? `${replies.length} messages arrived — the reply is usually first` : "Reply received") : "No reply"}</div>
    ${replies.length ? replies.slice(0, 20).map(f => `<div class="result"><span><span class="chan ${chanClass(f.channel)}">${esc(chanLabel(f.channel))}</span>
      <span class="mono small">${esc(f.hex)}</span></span><span class="score">${f.modbus ? esc(f.modbus.summary) : ""}</span></div>`).join("")
      : `<p class="muted small">Nothing arrived in time. Check wiring (TX → RX, GND), the device address, and the baud rate.</p>`}</div>`;
}

/* ---------------- wiring guides ---------------- */
const GUIDES = {
  ttl: ["TTL UART (USB-to-TTL adapter)", `<div class="warn">Set the adapter's jumper to the board's voltage (3.3 V or 5 V).</div>
<b>Listen</b> (Sniff mode):<pre>Controller TX ──┬──────► Display RX
                └──────► Adapter RX
Controller GND ────────► Adapter GND
Adapter TX: not connected</pre>
<b>Send</b> (Send mode): unplug Controller TX from the display, then<pre>Adapter TX ───► Display RX
Adapter GND ──► Display GND</pre>`],
  rs232: ["RS-232 (DB9, ±12 V)", `<div class="warn">RS-232 uses ±12 V. Use a USB-to-RS232 adapter — never a TTL adapter.</div>
<b>Listen</b>: the adapter's <b>RX (DB9 pin 2)</b> and <b>GND (pin 5)</b> to the line you want to hear (e.g. the controller's TX, DB9 pin 3). A "Y" or breakout cable makes this easy.
<pre>DB9: 2 = RX   3 = TX   5 = GND</pre>
<b>Send</b>: replace the controller: adapter <b>TX (pin 3)</b> → display RX, GND ↔ GND.`],
  rs485: ["RS-485 (A/B twisted pair)", `<div class="warn">Use a USB-to-RS485 adapter. RS-485 is one shared pair, so one adapter hears both sides — Tapline labels master → slave and slave → master.</div>
<b>Listen</b>:<pre>Bus A (D+) ───► Adapter A (D+)
Bus B (D−) ───► Adapter B (D−)
Bus GND    ───► Adapter GND (recommended)</pre>
Nothing decoded? Swap A and B — vendors label them differently. Don't add a 120 Ω terminator unless your adapter is at the end of the bus.
<b>Send</b>: with the same wiring Tapline becomes another master. Make sure the real master is quiet (unplugged or stopped), or messages will collide.`],
  la: ["Logic analyzer (sigrok / PulseView compatible)", `<b>Install sigrok-cli</b> once: Windows/macOS — download from <b>sigrok.org/wiki/Downloads</b>; Linux — <code>sudo apt install sigrok-cli</code>. Cheap "24 MHz 8-channel" analyzers use the <b>fx2lafw</b> driver (Windows needs the Zadig WinUSB driver).
<pre>Analyzer GND ──► board GND   (always!)
UART: D0 ──► the TX line you want to read
I²C:  D0 ──► SCL   D1 ──► SDA
SPI:  D0 ──► CLK   D1 ──► MOSI   D2 ──► MISO   D3 ──► CS</pre>
Sample at least 4× the bus speed (UART 115200 → 1 MHz; I²C 400 kHz → 4 MHz; SPI 2 MHz → 8–12 MHz). The analyzer only listens. To send on I²C/SPI, use the Pico bridge.`],
  can: ["CAN bus adapter", `<b>Install</b>: <code>pip install python-can</code> (done by run.bat / run.sh). Pick your adapter type in <b>Interface</b>:
<pre>CANable (slcan): channel = COM6 or /dev/ttyACM0
candleLight / CANable (gs_usb firmware): channel = 0
PEAK PCAN-USB: channel = PCAN_USBBUS1
Linux SocketCAN: channel = can0</pre>
<pre>Adapter CAN_H ──► bus CAN_H
Adapter CAN_L ──► bus CAN_L
Adapter GND   ──► bus GND</pre>
Bitrate must match the bus (cars: usually 500 kbit/s). Add a 120 Ω terminator only if your adapter is at an end of the bus. Note: a CAN adapter acknowledges messages like any node — fine for sniffing; for strictly passive listening use an adapter with listen-only mode.`],
  pico: ["Raspberry Pi Pico bridge (send I²C / SPI)", `1. Install MicroPython on the Pico (hold BOOTSEL, plug in, copy the .uf2 from micropython.org).
2. Open <b>bridge/pico_bridge.py</b> (in the Tapline folder) in <b>Thonny</b> and save it to the Pico as <b>main.py</b>. Unplug and replug.
3. In Composer choose <b>Pico bridge</b>, pick its port and press Connect.
<pre>I²C0: SDA = GP4 (pin 6)   SCL = GP5 (pin 7)
SPI0: SCK = GP18 (pin 24) MOSI = GP19 (pin 25)
      MISO = GP16 (pin 21) CS = GP17 (pin 22)
GND:  pin 3 (or any GND) ──► target GND</pre>
<div class="warn">Pico pins are 3.3 V only. Disconnect the original controller from the bus while the Pico drives it.</div>`],
};
function wiringGuide(key) {
  const src = $("#source").value, kind = sourceKind(src);
  key = key || (kind === "can" || src === "sim-can" ? "can" : kind.startsWith("la") || src.startsWith("sim-la") ? "la" :
    src === "sim-modbus" ? "rs485" : kind === "serial" ? $("#link").value : "ttl");
  const [title, html] = GUIDES[key];
  openModal(`<div class="guide"><h3>${title}</h3>${html}</div>
    <div class="actions wrap">${Object.keys(GUIDES).map(k => `<button class="ghost tiny" data-g="${k}">${GUIDES[k][0].split(" (")[0]}</button>`).join("")}<button class="primary" data-close>Got it</button></div>`);
  $$("#modalCard [data-g]").forEach(b => b.onclick = () => wiringGuide(b.dataset.g));
}

async function init() {
  bind();
  onTargetChange(); onMbFn();
  try {
    S.options = await api("options");
    setupSettings();
    await loadPorts();
    S.protocol = await api("protocol");
    applyStatus(await api("status"));
    showRule(S.status.rule);
    await reloadFrames();
    renderProtocol();
  } catch (e) { toast("Can't reach Tapline — is it still running? " + e.message, "err"); }
  connect();
  if (!store("tl.tourDone")) setTimeout(() => tour(0), 500);
}
init();

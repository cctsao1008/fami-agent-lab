"""Local-only browser observer for authoritative NES frames and SMB1 telemetry."""

from __future__ import annotations

from array import array
from collections import deque
import binascii
import json
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fami_pixel.adapters.mesen import copy_nes_raw_frame

_NES_RGB = (
    (84, 84, 84), (0, 30, 116), (8, 16, 144), (48, 0, 136),
    (68, 0, 100), (92, 0, 48), (84, 4, 0), (60, 24, 0),
    (32, 42, 0), (8, 58, 0), (0, 64, 0), (0, 60, 0),
    (0, 50, 60), (0, 0, 0), (0, 0, 0), (0, 0, 0),
    (152, 150, 152), (8, 76, 196), (48, 50, 236), (92, 30, 228),
    (136, 20, 176), (160, 20, 100), (152, 34, 32), (120, 60, 0),
    (84, 90, 0), (40, 114, 0), (8, 124, 0), (0, 118, 40),
    (0, 102, 120), (0, 0, 0), (0, 0, 0), (0, 0, 0),
    (236, 238, 236), (76, 154, 236), (120, 124, 236), (176, 98, 236),
    (228, 84, 236), (236, 88, 180), (236, 106, 100), (212, 136, 32),
    (160, 170, 0), (116, 196, 0), (76, 208, 32), (56, 204, 108),
    (56, 180, 204), (60, 60, 60), (0, 0, 0), (0, 0, 0),
    (236, 238, 236), (168, 204, 236), (188, 188, 236), (212, 178, 236),
    (236, 174, 236), (236, 174, 212), (236, 180, 176), (228, 196, 144),
    (204, 210, 120), (180, 222, 120), (168, 226, 144), (152, 226, 180),
    (160, 214, 228), (160, 162, 160), (0, 0, 0), (0, 0, 0),
)


def _packed_frame_to_bmp(width: int, height: int, packed_pixels: bytes) -> bytes:
    row_stride = (width * 3 + 3) & ~3
    image_size = row_stride * height
    offset = 14 + 40
    file_size = offset + image_size

    header = bytearray()
    header += b"BM"
    header += struct.pack("<IHHI", file_size, 0, 0, offset)
    header += struct.pack(
        "<IIIHHIIIIII",
        40, width, height, 1, 24, 0, image_size, 2835, 2835, 0, 0,
    )

    words = memoryview(packed_pixels).cast("H")
    pixels = bytearray(image_size)
    out = 0
    for y in range(height - 1, -1, -1):
        row = y * width
        for x in range(width):
            raw = words[row + x]
            r, g, b = _NES_RGB[raw & 0x3F]
            pixels[out] = b
            pixels[out + 1] = g
            pixels[out + 2] = r
            out += 3
        out += row_stride - width * 3
    return bytes(header + pixels)


def raw_frame_to_bmp(frame) -> bytes:
    packed = array("H", frame.pixels).tobytes()
    return _packed_frame_to_bmp(frame.width, frame.height, packed)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)


def raw_frame_to_png(frame) -> bytes:
    """Encode a caller-owned NES raw frame as dependency-free RGB PNG."""
    width = int(frame.width)
    height = int(frame.height)
    if width <= 0 or height <= 0:
        raise ValueError("frame geometry must be positive")
    if len(frame.pixels) != width * height:
        raise ValueError("frame pixel count does not match geometry")

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # PNG filter type: None
        row = y * width
        for x in range(width):
            r, g, b = _NES_RGB[int(frame.pixels[row + x]) & 0x3F]
            scanlines.extend((r, g, b))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        (
            signature,
            _png_chunk(b"IHDR", ihdr),
            _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6)),
            _png_chunk(b"IEND", b""),
        )
    )


def format_radar_strip(
    enemy_dx: int | None,
    gap_dx: int | None,
    obstacle_dx: int | None,
    *,
    lookahead_px: int = 192,
    width: int = 30,
) -> str:
    """Render a compact forward-scene strip for human telemetry."""
    if lookahead_px <= 0 or width <= 0:
        raise ValueError("lookahead_px and width must be > 0")
    cells = ["-"] * width
    occupied: set[int] = set()

    def place(distance, marker):
        if distance is None:
            return
        dx = int(distance)
        if dx < 0:
            return
        index = min(width - 1, max(0, round((dx / lookahead_px) * (width - 1))))
        if index in occupied:
            cells[index] = "*"
        else:
            cells[index] = marker
            occupied.add(index)

    place(enemy_dx, "E")
    place(gap_dx, "G")
    place(obstacle_dx, "O")
    return "[M] " + "".join(cells)


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fami Pixel Live</title>
<style>
:root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
* { box-sizing: border-box; }
body { margin: 0; background: #101113; color: #eee; }
main { width: min(100% - 32px, 1220px); margin: 24px auto; display: grid; grid-template-columns: minmax(0, 768px) 350px; align-items: start; justify-content: center; gap: 18px; }
.card { background: #1b1c1f; border: 1px solid #34363b; border-radius: 12px; padding: 14px; }
.game-card { width: 100%; }
#frame-wrap { width: min(100%, 768px); aspect-ratio: 256 / 240; margin: 0 auto; background: #000; overflow: hidden; }
#frame { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; display: block; }
h1 { margin: 0 0 12px; font-size: 18px; }
h2 { margin: 0 0 10px; font-size: 14px; color: #d7d9df; letter-spacing: .04em; }
.section { border-top: 1px solid #34363b; margin-top: 14px; padding-top: 14px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px 12px; }
.k { color: #9b9ea6; }
.v { text-align: right; color: #f4f4f5; }
#action { font-size: 18px; margin: 8px 0 12px; min-height: 22px; }
#status { margin-top: 12px; color: #8d9098; font-size: 12px; line-height: 1.4; }
.radar-grid { display: grid; grid-template-columns: 1fr auto; gap: 8px 14px; }
.radar-value { text-align: right; font-weight: 700; }
#radar-strip { margin-top: 12px; padding: 10px; border-radius: 8px; background: #101113; border: 1px solid #303238; white-space: pre; overflow-x: auto; color: #dfe3ea; }
.badge { display: inline-block; padding: 3px 7px; border-radius: 999px; border: 1px solid #3b3d43; background: #24262a; font-size: 11px; margin-top: 8px; }
.badge.hazard { border-color: #8f554d; background: #35211f; color: #ffd3cc; }
.badge.clear { border-color: #446d50; background: #1e3023; color: #ccebd3; }
.small { font-size: 12px; color: #a5a8b0; line-height: 1.45; word-break: break-word; }
@media (max-width: 1120px) { main { grid-template-columns: minmax(0, 640px) 320px; } }
@media (max-width: 850px) { main { width: min(100% - 20px, 640px); grid-template-columns: 1fr; margin: 10px auto; } }
</style>
</head>
<body>
<main>
  <section class="card game-card">
    <h1>Fami Pixel / SMB1 — authoritative trajectory</h1>
    <div id="frame-wrap"><img id="frame" alt="NES authoritative framebuffer"></div>
  </section>
  <aside class="card">
    <h2>CONTROL</h2>
    <div class="k">Applied action</div>
    <div id="action">waiting...</div>
    <div class="grid">
      <div class="k">Generation</div><div class="v" id="decision">-</div>
      <div class="k">Planner mode</div><div class="v" id="mode">-</div>
      <div class="k">Native frame</div><div class="v" id="native_frame">-</div>
      <div class="k">Mario X</div><div class="v" id="x">-</div>
      <div class="k">Mario Y</div><div class="v" id="y">-</div>
      <div class="k">VX</div><div class="v" id="vx">-</div>
      <div class="k">VY</div><div class="v" id="vy">-</div>
      <div class="k">Engine</div><div class="v" id="engine">-</div>
    </div>

    <div class="section">
      <h2>📡 RADAR</h2>
      <div class="radar-grid">
        <div class="k">Enemy</div><div class="radar-value" id="radar_enemy_dx">--</div>
        <div class="k">Gap</div><div class="radar-value" id="radar_gap_dx">--</div>
        <div class="k">Obstacle</div><div class="radar-value" id="radar_obstacle_dx">--</div>
      </div>
      <div id="radar-strip">[M] ------------------------------</div>
      <div id="hazard-badge" class="badge clear">scene clear</div>
      <div class="small" style="margin-top:8px">Reason: <span id="radar_reason">-</span></div>
    </div>

    <div class="section">
      <h2>PLANNER / SURROGATE</h2>
      <div class="grid">
        <div class="k">Guard mode</div><div class="v" id="guard_mode">-</div>
        <div class="k">Risk</div><div class="v" id="risk_probability">-</div>
        <div class="k">No-progress</div><div class="v" id="no_progress_probability">-</div>
        <div class="k">Plan root frame</div><div class="v" id="plan_root_frame">-</div>
        <div class="k">Plan age</div><div class="v" id="plan_age">-</div>
        <div class="k">Plan compute</div><div class="v" id="plan_compute_ms">-</div>
        <div class="k">Planner state</div><div class="v" id="planner_state">-</div>
        <div class="k">Playback buffer</div><div class="v" id="buffered">-</div>
      </div>
    </div>
    <div id="status">authoritative playback; counterfactual rollouts are hidden</div>
  </aside>
</main>
<script>
let version = -1;
function distance(v) { return (v === null || v === undefined) ? '--' : `${v} px →`; }
function score(v) { return (v === null || v === undefined) ? '-' : Number(v).toFixed(3); }
async function tick() {
  try {
    const r = await fetch('/state.json', {cache:'no-store'});
    const s = await r.json();
    if (s.version !== version) {
      version = s.version;
      for (const k of ['decision','mode','action','native_frame','x','y','vx','vy','engine','plan_root_frame','plan_age','plan_compute_ms','planner_state','buffered','guard_mode','radar_reason']) {
        const el = document.getElementById(k);
        if (el) el.textContent = s[k] ?? '-';
      }
      document.getElementById('radar_enemy_dx').textContent = distance(s.radar_enemy_dx);
      document.getElementById('radar_gap_dx').textContent = distance(s.radar_gap_dx);
      document.getElementById('radar_obstacle_dx').textContent = distance(s.radar_obstacle_dx);
      document.getElementById('radar-strip').textContent = s.radar_strip || '[M] ------------------------------';
      document.getElementById('risk_probability').textContent = score(s.risk_probability);
      document.getElementById('no_progress_probability').textContent = score(s.no_progress_probability);
      const badge = document.getElementById('hazard-badge');
      const hazard = !!s.hazard_ahead;
      badge.className = 'badge ' + (hazard ? 'hazard' : 'clear');
      badge.textContent = hazard ? 'hazard ahead' : 'scene clear';
      if (s.has_frame) document.getElementById('frame').src = '/frame.bmp?v=' + version;
    }
  } catch (_) {}
  setTimeout(tick, 25);
}
tick();
</script>
</body>
</html>"""


class NesWebViewer:
    """Local browser observer with buffered authoritative playback."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, playback_fps: float = 15.0):
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("NesWebViewer is local-only; bind to 127.0.0.1/localhost")
        if playback_fps <= 0:
            raise ValueError("playback_fps must be > 0")
        self.host = host
        self.port = int(port)
        self.playback_fps = float(playback_fps)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._version = 0
        self._frame = b""
        self._queue = deque(maxlen=240)
        self._stopping = False
        self._state = {
            "version": 0,
            "has_frame": False,
            "decision": None,
            "mode": None,
            "action": None,
            "native_frame": None,
            "x": None,
            "y": None,
            "vx": None,
            "vy": None,
            "engine": None,
            "plan_root_frame": None,
            "plan_age": None,
            "plan_compute_ms": None,
            "planner_state": None,
            "guard_mode": None,
            "risk_probability": None,
            "no_progress_probability": None,
            "radar_enemy_dx": None,
            "radar_gap_dx": None,
            "radar_obstacle_dx": None,
            "radar_reason": None,
            "radar_strip": format_radar_strip(None, None, None),
            "hazard_ahead": False,
            "buffered": 0,
        }
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._playback_thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> None:
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/" or self.path.startswith("/index.html"):
                    self._send(200, "text/html; charset=utf-8", _HTML.encode("utf-8"))
                    return
                if self.path.startswith("/state.json"):
                    with viewer._lock:
                        payload = json.dumps(viewer._state, separators=(",", ":")).encode("utf-8")
                    self._send(200, "application/json", payload)
                    return
                if self.path.startswith("/frame.bmp"):
                    with viewer._lock:
                        payload = viewer._frame
                    if not payload:
                        self._send(404, "text/plain", b"no frame yet")
                    else:
                        self._send(200, "image/bmp", payload)
                    return
                self._send(404, "text/plain", b"not found")

            def _send(self, code: int, content_type: str, payload: bytes):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()

    def _playback_loop(self) -> None:
        interval = 1.0 / self.playback_fps
        next_tick = time.monotonic()
        while True:
            with self._condition:
                while not self._queue and not self._stopping:
                    self._condition.wait(timeout=0.25)
                    next_tick = time.monotonic()
                if self._stopping:
                    return
                width, height, packed, state = self._queue.popleft()
                buffered = len(self._queue)

            bmp = _packed_frame_to_bmp(width, height, packed)
            with self._lock:
                self._version += 1
                self._frame = bmp
                state["version"] = self._version
                state["has_frame"] = True
                state["buffered"] = buffered
                self._state = state

            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    def publish_core(self, core, observation, *, decision: int, mode: str, action: str, metadata: dict | None = None) -> None:
        frame = copy_nes_raw_frame(core)
        packed = array("H", frame.pixels).tobytes()
        vx = observation.player_x_speed
        vy = observation.player_y_speed
        vx = vx - 256 if vx >= 128 else vx
        vy = vy - 256 if vy >= 128 else vy
        metadata = metadata or {}
        enemy_dx = metadata.get("radar_enemy_dx")
        gap_dx = metadata.get("radar_gap_dx")
        obstacle_dx = metadata.get("radar_obstacle_dx")
        state = {
            "version": 0,
            "has_frame": True,
            "decision": decision,
            "mode": mode,
            "action": action,
            "native_frame": observation.native_frame_id,
            "x": observation.mario_x_abs,
            "y": observation.mario_y,
            "vx": vx,
            "vy": vy,
            "engine": f"0x{observation.game_engine_subroutine:02X}",
            "plan_root_frame": metadata.get("plan_root_frame"),
            "plan_age": metadata.get("plan_age"),
            "plan_compute_ms": metadata.get("plan_compute_ms"),
            "planner_state": metadata.get("planner_state"),
            "guard_mode": metadata.get("guard_mode"),
            "risk_probability": metadata.get("risk_probability"),
            "no_progress_probability": metadata.get("no_progress_probability"),
            "radar_enemy_dx": enemy_dx,
            "radar_gap_dx": gap_dx,
            "radar_obstacle_dx": obstacle_dx,
            "radar_reason": metadata.get("radar_reason"),
            "radar_strip": metadata.get("radar_strip") or format_radar_strip(enemy_dx, gap_dx, obstacle_dx),
            "hazard_ahead": bool(metadata.get("hazard_ahead", False)),
            "buffered": 0,
        }
        with self._condition:
            self._queue.append((frame.width, frame.height, packed, state))
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

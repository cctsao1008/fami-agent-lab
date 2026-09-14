"""Local-only browser observer for authoritative NES frames and SMB1 telemetry."""

from __future__ import annotations

from array import array
from collections import deque
import json
import struct
import threading
import time
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


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fami Pixel Live</title>
<style>
:root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
* { box-sizing: border-box; }
body { margin: 0; background: #111; color: #eee; }
main { width: min(100% - 32px, 1160px); margin: 24px auto; display: grid; grid-template-columns: minmax(0, 768px) 320px; align-items: start; justify-content: center; gap: 18px; }
.card { background: #1b1b1b; border: 1px solid #333; border-radius: 12px; padding: 14px; }
.game-card { width: 100%; }
#frame-wrap { width: min(100%, 768px); aspect-ratio: 256 / 240; margin: 0 auto; background: #000; overflow: hidden; }
#frame { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; display: block; }
h1 { margin: 0 0 12px; font-size: 18px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.k { color: #999; }
.v { text-align: right; }
#action { font-size: 18px; margin: 8px 0 14px; }
#status { margin-top: 12px; color: #aaa; font-size: 12px; }
@media (max-width: 1120px) { main { grid-template-columns: minmax(0, 640px) 300px; } }
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
      <div class="k">Plan root frame</div><div class="v" id="plan_root_frame">-</div>
      <div class="k">Plan age</div><div class="v" id="plan_age">-</div>
      <div class="k">Plan compute</div><div class="v" id="plan_compute_ms">-</div>
      <div class="k">Planner state</div><div class="v" id="planner_state">-</div>
      <div class="k">Playback buffer</div><div class="v" id="buffered">-</div>
    </div>
    <div id="status">authoritative playback; counterfactual rollouts are hidden</div>
  </aside>
</main>
<script>
let version = -1;
async function tick() {
  try {
    const r = await fetch('/state.json', {cache:'no-store'});
    const s = await r.json();
    if (s.version !== version) {
      version = s.version;
      for (const k of ['decision','mode','action','native_frame','x','y','vx','vy','engine','plan_root_frame','plan_age','plan_compute_ms','planner_state','buffered']) {
        const el = document.getElementById(k);
        if (el) el.textContent = s[k] ?? '-';
      }
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

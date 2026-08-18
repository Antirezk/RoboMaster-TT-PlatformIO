"""Browser-based supervised keyboard controller for props-off bench testing."""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    import serial
except ImportError as exc:
    raise SystemExit("pyserial is required: python -m pip install pyserial") from exc


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboMaster TT Bench Controller</title>
<style>
:root{color-scheme:dark;font-family:Segoe UI,Arial,sans-serif;background:#0b1118;color:#e5edf5}
body{margin:0;padding:20px;max-width:1100px;margin:auto}.danger{color:#ff6b6b}.ok{color:#62d68b}
h1{margin:0 0 6px}.card{background:#151e29;border:1px solid #293747;border-radius:10px;padding:14px;margin:12px 0}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}button{font-size:17px;padding:10px 22px;border:0;border-radius:7px;cursor:pointer}
#arm{background:#2e7d32;color:white}#stop{background:#c62828;color:white;font-weight:bold}.pill{background:#263545;padding:7px 11px;border-radius:7px}
input[type=number]{width:58px;font-size:16px;padding:5px}.big{font:700 20px Consolas,monospace}
pre{height:310px;overflow:auto;background:#080c11;border-radius:7px;padding:12px;white-space:pre-wrap;color:#d7e2ec}
kbd{background:#344658;border-radius:4px;padding:3px 7px;font-weight:bold}
</style></head>
<body>
<h1>RoboMaster TT Supervised Controller</h1>
<div class="danger"><b>PROPELLERS-OFF BENCH TEST ONLY</b></div>

<div class="card row">
  <button id="arm">ARM</button><button id="stop">STOP / DISARM</button>
  <span class="pill">Serial: <b id="serial">connecting</b></span>
  <span class="pill">Arm: <b id="armState">DISARMED</b></span>
  <span class="pill">Deadman: <b id="deadman">RELEASED</b></span>
</div>

<div class="card">
  <div class="row">
    Horizontal <input id="hSpeed" type="number" min="5" max="20" value="15">
    Vertical <input id="vSpeed" type="number" min="5" max="20" value="10">
    Yaw <input id="ySpeed" type="number" min="5" max="20" value="15">
  </div>
  <p>Hold <kbd>Space</kbd> + <kbd>W/S</kbd> forward/back, <kbd>A/D</kbd> left/right,
     <kbd>R/F</kbd> up/down, <kbd>Q/E</kbd> yaw. <kbd>Esc</kbd> stops and disarms.</p>
  <p class="danger">Changing tab, minimizing, or losing window focus automatically disarms.</p>
  <div>Keys: <span id="keys" class="big">-</span></div>
  <div>TX: <span id="tx" class="big">rc 0 0 0 0</span></div>
</div>

<div class="card"><b>ESP32 diagnostics</b><pre id="logs"></pre></div>

<script>
const relevant=new Set(['Space','KeyW','KeyS','KeyA','KeyD','KeyR','KeyF','KeyQ','KeyE']);
const pressed=new Set(); let armed=false; let sending=false;
const $=id=>document.getElementById(id);

function clampSpeed(id){let v=Number($(id).value)||0;return Math.max(0,Math.min(20,v));}
async function sendState(){
  if(sending)return; sending=true;
  try{
    await fetch('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      armed,keys:[...pressed],horizontal:clampSpeed('hSpeed'),vertical:clampSpeed('vSpeed'),yaw:clampSpeed('ySpeed')
    })});
  }catch(e){armed=false;pressed.clear();}finally{sending=false;renderLocal();}
}
function renderLocal(){
  $('armState').textContent=armed?'ARMED':'DISARMED'; $('armState').className=armed?'danger':'';
  $('arm').textContent=armed?'DISARM':'ARM'; $('deadman').textContent=armed&&pressed.has('Space')?'HELD':'RELEASED';
  $('keys').textContent=[...pressed].map(x=>x.replace('Key','')).sort().join(' + ')||'-';
}
function stop(){armed=false;pressed.clear();renderLocal();sendState();}
$('arm').onclick=()=>{
  if(!armed&&!confirm('Confirm all four propellers are removed. ARM bench controller?'))return;
  armed=!armed;pressed.clear();renderLocal();sendState();window.focus();
};
$('stop').onclick=stop;
window.addEventListener('keydown',e=>{if(e.code==='Escape'){stop();return}if(relevant.has(e.code)){e.preventDefault();pressed.add(e.code);renderLocal();sendState();}});
window.addEventListener('keyup',e=>{if(relevant.has(e.code)){e.preventDefault();pressed.delete(e.code);renderLocal();sendState();}});
window.addEventListener('blur',stop);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});

async function poll(){
  try{
    const s=await (await fetch('/api/status')).json();
    $('serial').textContent=s.connected?`${s.port} @ 115200`:'DISCONNECTED'; $('serial').className=s.connected?'ok':'danger';
    $('tx').textContent=s.tx; $('logs').textContent=s.logs.join('\n'); $('logs').scrollTop=$('logs').scrollHeight;
    if(!s.armed&&armed){armed=false;pressed.clear();renderLocal();}
  }catch(e){$('serial').textContent='SERVER LOST';$('serial').className='danger';stop();}
}
setInterval(sendState,100); setInterval(poll,200); renderLocal();sendState();poll();
</script></body></html>"""


class Controller:
    SEND_PERIOD = 0.1
    BROWSER_TIMEOUT = 0.35

    def __init__(self, port: str) -> None:
        self.port_name = port
        self.port = serial.Serial(port, 115200, timeout=0.05, write_timeout=0.2)
        self.lock = threading.Lock()
        self.running = True
        self.armed = False
        self.keys: set[str] = set()
        self.horizontal = 15
        self.vertical = 10
        self.yaw_speed = 15
        self.last_browser_state = 0.0
        self.tx = "rc 0 0 0 0"
        self.logs: deque[str] = deque(maxlen=300)
        self.rx_buffer = bytearray()

        threading.Thread(target=self._serial_reader, daemon=True).start()
        threading.Thread(target=self._control_loop, daemon=True).start()

    def update_browser_state(self, payload: dict[str, Any]) -> None:
        allowed = {"Space", "KeyW", "KeyS", "KeyA", "KeyD", "KeyR", "KeyF", "KeyQ", "KeyE"}
        with self.lock:
            self.armed = bool(payload.get("armed", False))
            self.keys = set(payload.get("keys", [])) & allowed
            self.horizontal = self._speed(payload.get("horizontal"), 15)
            self.vertical = self._speed(payload.get("vertical"), 10)
            self.yaw_speed = self._speed(payload.get("yaw"), 15)
            self.last_browser_state = time.monotonic()

    @staticmethod
    def _speed(value: Any, fallback: int) -> int:
        try:
            return max(0, min(20, int(value)))
        except (TypeError, ValueError):
            return fallback

    def _command(self) -> tuple[int, int, int, int]:
        with self.lock:
            fresh = time.monotonic() - self.last_browser_state <= self.BROWSER_TIMEOUT
            if not fresh:
                self.armed = False
                self.keys.clear()
            if not self.armed or "Space" not in self.keys:
                return (0, 0, 0, 0)
            lr = self.horizontal * (int("KeyD" in self.keys) - int("KeyA" in self.keys))
            fb = self.horizontal * (int("KeyW" in self.keys) - int("KeyS" in self.keys))
            ud = self.vertical * (int("KeyR" in self.keys) - int("KeyF" in self.keys))
            yaw = self.yaw_speed * (int("KeyE" in self.keys) - int("KeyQ" in self.keys))
            return (lr, fb, ud, yaw)

    def _control_loop(self) -> None:
        deadline = time.monotonic()
        while self.running:
            deadline += self.SEND_PERIOD
            command = self._command()
            line = f"rc {command[0]} {command[1]} {command[2]} {command[3]}"
            self.tx = line
            self._write(line)
            time.sleep(max(0.0, deadline - time.monotonic()))

    def _write(self, line: str) -> None:
        try:
            self.port.write((line + "\n").encode("ascii"))
        except (serial.SerialException, serial.SerialTimeoutException) as exc:
            self.logs.append(f"[PC] Serial write failed: {exc}")
            self.running = False

    def _serial_reader(self) -> None:
        while self.running:
            try:
                chunk = self.port.read(512)
            except serial.SerialException as exc:
                self.logs.append(f"[PC] Serial read failed: {exc}")
                self.running = False
                return
            if not chunk:
                continue
            self.rx_buffer.extend(chunk)
            while b"\n" in self.rx_buffer:
                raw, _, remainder = self.rx_buffer.partition(b"\n")
                self.rx_buffer = bytearray(remainder)
                line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
                if line:
                    self.logs.append(line)

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "connected": self.running and self.port.is_open,
                "port": self.port_name,
                "armed": self.armed,
                "tx": self.tx,
                "logs": list(self.logs),
            }

    def stop(self) -> None:
        with self.lock:
            self.armed = False
            self.keys.clear()
        for _ in range(5):
            self._write("rc 0 0 0 0")
            time.sleep(0.03)
        self.running = False
        try:
            self.port.close()
        except serial.SerialException:
            pass


class Handler(BaseHTTPRequestHandler):
    controller: Controller

    def do_GET(self) -> None:
        if self.path == "/":
            self._reply(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif self.path == "/api/status":
            body = json.dumps(self.controller.status(), ensure_ascii=False).encode("utf-8")
            self._reply(200, "application/json", body)
        else:
            self._reply(404, "text/plain", b"Not found")

    def do_POST(self) -> None:
        if self.path != "/api/state":
            self._reply(404, "text/plain", b"Not found")
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length) or b"{}")
            self.controller.update_browser_state(payload)
            self._reply(204, "text/plain", b"")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, "text/plain", b"Bad request")

    def _reply(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RoboMaster TT supervised bench controller")
    parser.add_argument("--port", default="COM8", help="ESP32 serial port")
    parser.add_argument("--http-port", type=int, default=8765, help="local web UI port")
    parser.add_argument("--no-browser", action="store_true", help="do not open browser automatically")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        controller = Controller(args.port)
    except serial.SerialException as exc:
        raise SystemExit(
            f"Cannot open {args.port}. Close PlatformIO Serial Monitor and retry.\n{exc}"
        ) from exc

    Handler.controller = controller
    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    url = f"http://127.0.0.1:{args.http_port}/"
    print(f"Connected to {args.port} @ 115200")
    print(f"Controller UI: {url}")
    print("Press Ctrl+C here to stop and send zero RC.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping controller...")
    finally:
        server.server_close()
        controller.stop()


if __name__ == "__main__":
    main()

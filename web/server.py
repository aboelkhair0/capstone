"""
Zero-dependency local web server & bridge for D:\\G12_Capstone\\web.
- Serves the website on http://127.0.0.1:8080
- Proxies local AI requests (Ollama / LM Studio) with streaming & zero CORS issues
- Relays AiPi-CAM-D200 /stream and caches the latest JPEG frame so /capture works
  even while live video is open (the board only allows 1 connection at a time).
"""

import http.client
import ipaddress
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

# Latest cached JPEG frame per board IP: { ip: (bytes, timestamp) }
FRAME_CACHE = {}
FRAME_LOCK = threading.Lock()


def is_allowed_local_host(hostname: str) -> bool:
    if not hostname:
        return False
    h = hostname.strip().lower()
    if h in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return h.endswith(".local")


def extract_jpegs_from_buffer(buf: bytearray):
    """Yield complete JPEG frames (FFD8..FFD9) and trim buffer in place."""
    frames = []
    while True:
        start = buf.find(b"\xff\xd8")
        if start == -1:
            if len(buf) > 2:
                del buf[:-2]
            break
        if start > 0:
            del buf[:start]
        end = buf.find(b"\xff\xd9", 2)
        if end == -1:
            if len(buf) > 2_000_000:
                buf.clear()
            break
        frames.append(bytes(buf[: end + 2]))
        del buf[: end + 2]
    return frames


class CapstoneHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        sys.stdout.write(f"[web] {self.address_string()} - {fmt % args}\n")

    def send_json(self, status_code: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/health":
            self.send_json(200, {"ok": True, "runtime": f"python {sys.version.split()[0]}"})
            return

        if parsed.path == "/api/proxy":
            target = (qs.get("target") or [""])[0]
            self.handle_proxy("GET", target)
            return

        if parsed.path == "/api/camera/status":
            ip = (qs.get("ip") or [""])[0]
            self.handle_proxy("GET", f"http://{ip}/status")
            return

        if parsed.path == "/api/camera/capture":
            ip = (qs.get("ip") or [""])[0]
            self.handle_camera_capture(ip)
            return

        if parsed.path == "/api/camera/stream":
            ip = (qs.get("ip") or [""])[0]
            self.handle_camera_stream(ip)
            return

        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/proxy":
            target = (qs.get("target") or [""])[0]
            self.handle_proxy("POST", target)
            return
        self.send_json(404, {"error": "Not found"})

    def handle_camera_capture(self, ip: str):
        if not is_allowed_local_host(ip):
            self.send_json(400, {"error": "Only local/LAN camera IPs are allowed."})
            return

        # If a fresh frame is already cached from an active /stream, return it immediately
        with FRAME_LOCK:
            cached = FRAME_CACHE.get(ip)
        if cached and (time.time() - cached[1]) < 5.0:
            jpg = cached[0]
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpg)
            return

        # Otherwise request /capture directly from the board
        self.handle_proxy("GET", f"http://{ip}/capture")

    def handle_camera_stream(self, ip: str):
        if not is_allowed_local_host(ip):
            self.send_json(400, {"error": "Only local/LAN camera IPs are allowed."})
            return

        conn = None
        try:
            conn = http.client.HTTPConnection(ip, 80, timeout=10)
            conn.request("GET", "/stream")
            resp = conn.getresponse()

            self.send_response(resp.status)
            ct = resp.getheader("Content-Type") or "multipart/x-mixed-replace;boundary=123456789000000000000987654321"
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            scan_buf = bytearray()
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                scan_buf.extend(chunk)
                frames = extract_jpegs_from_buffer(scan_buf)
                if frames:
                    with FRAME_LOCK:
                        FRAME_CACHE[ip] = (frames[-1], time.time())
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.send_json(502, {"error": f"Camera stream error: {exc}"})
            except Exception:
                pass
        finally:
            if conn:
                conn.close()

    def handle_proxy(self, method: str, target: str):
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https") or not is_allowed_local_host(parsed.hostname):
            self.send_json(400, {"error": "Proxy only allows local/LAN targets (localhost or private IP)."})
            return

        content_len = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_len) if content_len > 0 else None

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = None
        try:
            conn = conn_cls(parsed.hostname, port, timeout=120)
            headers = {}
            if self.headers.get("Content-Type"):
                headers["Content-Type"] = self.headers["Content-Type"]
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            ct = resp.getheader("Content-Type") or "application/octet-stream"
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            while True:
                chunk = resp.read(2048)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.send_json(502, {"error": f"Cannot reach {target}: {exc}"})
            except Exception:
                pass
        finally:
            if conn:
                conn.close()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), CapstoneHandler)
    print(f"Student Assistant Website running at: http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.server_close()

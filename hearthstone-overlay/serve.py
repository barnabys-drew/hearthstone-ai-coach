#!/usr/bin/env python3
"""Serve the coach overlay as a plain web page — no Node/Electron required.

Run in WSL:
  python3 serve.py

Open on Windows (WSL2 forwards localhost automatically):
  http://localhost:8420

Pin it over the game with PowerToys "Always on Top" (Win+Ctrl+T), or put it
on a second monitor. Same data files as the Electron overlay: live.json is
mirrored by `hst live`, advice.json is written by coach_publish.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hearthstone-tracker"))
from hstracker.overlay import resolve_overlay_dir  # noqa: E402

RENDERER_DIR = Path(__file__).resolve().parent / "renderer"
DATA_FILES = {"live.json", "advice.json", "lessons.json"}
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
ART_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ART_REMOTE = "https://art.hearthstonejson.com/v1/tiles/{card_id}.png"


class OverlayHandler(BaseHTTPRequestHandler):
    overlay_dir: Path
    poll_ms: int
    stale_advice_seconds: int

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        # Reject non-local Host headers: blocks DNS-rebinding pages from
        # reading game state, and keeps this safe-by-default if anyone ever
        # changes the bind address away from loopback.
        host = (self.headers.get("Host") or "").split(":", 1)[0].lower()
        if host not in ("localhost", "127.0.0.1", "[::1]", ""):
            return self._send_error(403, "forbidden host")
        path = self.path.split("?", 1)[0]
        if path == "/config":
            self._send_json({
                "overlayDir": str(self.overlay_dir),
                "pollMs": self.poll_ms,
                "staleAdviceSeconds": self.stale_advice_seconds,
            })
        elif path.startswith("/data/"):
            self._send_data(path.removeprefix("/data/"))
        elif path.startswith("/art/"):
            self._send_art(path.removeprefix("/art/").removesuffix(".png"))
        else:
            self._send_static("index.html" if path == "/" else path.lstrip("/"))

    def _send_art(self, card_id: str) -> None:
        """Serve a card-art tile, caching it on disk so rows work offline."""
        if not ART_ID_RE.match(card_id):
            return self._send_error(404, "bad card id")
        cache = self.overlay_dir / "art-cache" / f"{card_id}.png"
        if not cache.is_file():
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(ART_REMOTE.format(card_id=card_id), timeout=10) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- constant host, card_id regex-gated
                    body = resp.read()
                tmp = cache.with_suffix(".tmp")
                tmp.write_bytes(body)
                tmp.replace(cache)
            except OSError as exc:
                return self._send_error(502, f"art fetch failed: {exc}")
        body = cache.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=604800")
        self.end_headers()
        self.wfile.write(body)

    def _send_data(self, file_name: str) -> None:
        if file_name not in DATA_FILES:
            return self._send_error(404, "unknown data file")
        file_path = self.overlay_dir / file_name
        try:
            stat = file_path.stat()
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._send_error(404, "not written yet")
        except (OSError, json.JSONDecodeError) as exc:
            return self._send_error(503, str(exc))
        self._send_json({"fileName": file_name, "path": str(file_path),
                         "mtimeMs": stat.st_mtime * 1000.0, "data": data})

    def _send_static(self, name: str) -> None:
        file_path = (RENDERER_DIR / name).resolve()
        if file_path.parent != RENDERER_DIR or not file_path.is_file():
            return self._send_error(404, "not found")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass  # keep the terminal quiet; errors surface via HTTP status codes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--overlay-dir", help="Folder holding live.json/advice.json (default: same as the Electron overlay)")
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--stale-advice-seconds", type=int, default=75)
    args = parser.parse_args(argv)

    OverlayHandler.overlay_dir = resolve_overlay_dir(args.overlay_dir)
    OverlayHandler.poll_ms = args.poll_ms
    OverlayHandler.stale_advice_seconds = args.stale_advice_seconds

    server = ThreadingHTTPServer(("127.0.0.1", args.port), OverlayHandler)
    print(f"Overlay page:  http://localhost:{args.port}")
    print(f"Data folder:   {OverlayHandler.overlay_dir}")
    print("Open the URL in a browser on Windows. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

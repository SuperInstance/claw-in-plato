#!/usr/bin/env python3
"""
Minimal PLATO room server — the world the Claw lives in.
Port :8847, JSON API, HMAC signing, persistent rooms.
"""
import hashlib, hmac, json, os, time, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler

PLATO_SECRET = os.environ.get("PLATO_SECRET", "claw-fleet-2026")
TILES = {}  # domain -> list[tile]

def sign(data: dict) -> str:
    payload = json.dumps(data, sort_keys=True, default=str)
    return hmac.new(PLATO_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

class PlatoHandler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_GET(self):
        if self.path == "/status":
            room_count = len(TILES)
            tile_count = sum(len(t) for t in TILES.values())
            self._json(200, {"status": "active", "rooms": room_count, "tiles": tile_count, "uptime": time.time()})
        elif self.path.startswith("/room/"):
            from urllib.parse import urlparse
            parsed = urlparse(self.path)
            domain = parsed.path[6:]
            tiles = TILES.get(domain, [])
            limit = int(self._query("limit", 100))
            self._json(200, {"domain": domain, "tiles": tiles[-limit:]})
        elif self.path == "/rooms":
            self._json(200, {"rooms": list(TILES.keys()), "count": len(TILES)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})

        if self.path == "/submit":
            domain = data.get("domain", "general")
            tile = {
                "domain": domain,
                "question": data.get("question", ""),
                "answer": data.get("answer", ""),
                "tags": data.get("tags", []),
                "confidence": data.get("confidence", 0.5),
                "source": data.get("source", "unknown"),
                "timestamp": time.time(),
                "id": str(uuid.uuid4())[:8],
            }
            tile["hash"] = sign(tile)
            if domain not in TILES:
                TILES[domain] = []
            TILES[domain].append(tile)
            self._json(200, {"status": "accepted", "tile_id": tile["id"], "hash": tile["hash"], "room_tile_count": len(TILES[domain])})
        elif self.path == "/delete":
            domain = data.get("domain")
            if domain and domain in TILES:
                del TILES[domain]
                self._json(200, {"status": "deleted", "domain": domain})
            else:
                self._json(404, {"error": "domain not found"})
        else:
            self._json(404, {"error": "not found"})

    def _query(self, key, default=None):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        vals = parse_qs(parsed.query).get(key, [])
        return vals[0] if vals else default

    def log_message(self, fmt, *args):
        pass  # quiet

def run_server(port=8847):
    print(f"[PLATO] Starting on :{port}")
    server = HTTPServer(("0.0.0.0", port), PlatoHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == "__main__":
    run_server()

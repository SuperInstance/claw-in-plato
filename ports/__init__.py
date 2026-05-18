"""
Port framework — modular capabilities for PLATO-native agents.
Each port watches a PLATO room and executes actions in the outside world.

Protocol:
  Request → tile with tags ["port", "<name>", "request"]
           question = action name
           answer = JSON payload
  
  Result  → tile with tags ["port", "<name>", "result"]
           question = mirrors request action
           answer = JSON result

Ports are loaded via the dock/load room:
  "load:port_exec" → starts port_exec service
  "unload:port_exec" → stops port_exec service
"""
import json, os, time, urllib.request, urllib.error, threading

PLATO_URL = os.environ.get("PLATO_URL", "http://127.0.0.1:8847")

# Registered port instances, keyed by room name
_PORTS = {}
_PORT_THREADS = {}

def plato_submit(domain, question, answer="", tags=None, confidence=0.9, source="port"):
    data = json.dumps({
        "domain": domain, "question": question, "answer": answer,
        "tags": tags or ["port"], "confidence": confidence, "source": source,
    }).encode()
    try:
        req = urllib.request.Request(f"{PLATO_URL}/submit", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False

def plato_read(domain, limit=10):
    try:
        req = urllib.request.Request(f"{PLATO_URL}/room/{domain}?limit={limit}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("tiles", [])
    except Exception:
        return []


class Port:
    """Base class for a PLATO port. Subclass and define name, description, handle()."""
    
    name = ""           # short name: "exec", "fs", "web"
    description = ""    # human-readable: "Run shell commands"
    room = ""           # PLATO room: "port/exec"
    
    def __init__(self):
        self._seen = set()
    
    def start(self):
        """Start the port's polling thread."""
        t = threading.Thread(target=self._loop, daemon=True, name=f"port-{self.name}")
        t.start()
        _PORT_THREADS[self.room] = t
        _PORTS[self.room] = self
        # Announce availability
        plato_submit(self.room, "port:available",
                     f"{self.name} port ready — {self.description}",
                     ["port", self.name, "system"], 1.0, f"port/{self.name}")
        print(f"[PORT] {self.name} started on room {self.room}")
    
    def _loop(self):
        """Poll the room for new request tiles."""
        while True:
            try:
                self._poll()
            except Exception as e:
                print(f"[PORT:{self.name}] Error: {e}")
            time.sleep(2)
    
    def _poll(self):
        tiles = plato_read(self.room, limit=20)
        for tile in tiles:
            tile_id = tile.get("id", "")
            if not tile_id or tile_id in self._seen:
                continue
            self._seen.add(tile_id)
            tags = tile.get("tags", [])
            # Only process tagged requests (skip our own system announcements)
            if "request" not in tags:
                continue
            if self.name not in tags:
                continue
            action = tile.get("question", "")
            payload_s = tile.get("answer", "")
            try:
                payload = json.loads(payload_s) if payload_s else {}
            except json.JSONDecodeError:
                payload = {"text": payload_s}
            result = self.handle(action, payload, tile)
            # Write result back
            plato_submit(
                self.room, action,
                json.dumps(result, default=str),
                ["port", self.name, "result"],
                0.9, f"port/{self.name}"
            )
    
    def handle(self, action, payload, tile):
        """Handle a request. Must return a dict (will be JSON'd as answer)."""
        return {"error": f"Unknown action: {action}"}


class PortLoader:
    """Manages port lifecycle via the dock/load room."""
    
    def __init__(self):
        self._seen = set()
        self._builtin_ports = {}
    
    def register(self, port_cls):
        """Register a port class that can be loaded on demand."""
        p = port_cls()
        self._builtin_ports[p.name] = port_cls
        print(f"[DOCK] Registered port: {p.name} — {p.description}")
    
    def list_available(self):
        """Return list of available but not yet loaded ports."""
        loaded = {p.name for p in _PORTS.values()}
        return [name for name in self._builtin_ports if name not in loaded]
    
    def start_all(self, names=None):
        """Load ports by name list. If None, load all registered ports."""
        to_load = names or list(self._builtin_ports.keys())
        for name in to_load:
            if name in _PORTS:
                continue
            cls = self._builtin_ports.get(name)
            if not cls:
                print(f"[DOCK] Unknown port: {name}")
                continue
            p = cls()
            p.start()
    
    def dock_loop(self):
        """Watch dock/load room for load/unload requests."""
        plato_submit("dock/load", "dock:available",
                     "Port loader ready. Write load:port_name or unload:port_name",
                     ["port", "dock", "system"], 1.0, "port/dock")
        while True:
            try:
                self._dock_poll()
            except Exception as e:
                print(f"[DOCK] Error: {e}")
            time.sleep(3)
    
    def _dock_poll(self):
        tiles = plato_read("dock/load", limit=10)
        for tile in tiles:
            tile_id = tile.get("id", "")
            if not tile_id or tile_id in self._seen:
                continue
            self._seen.add(tile_id)
            q = tile.get("question", "")
            tags = tile.get("tags", [])
            if "dock" not in tags and "request" not in tags:
                continue
            if q.startswith("load:"):
                name = q[5:].strip()
                if name in _PORTS:
                    plato_submit("dock/load", q,
                                 f"{name} already loaded",
                                 ["port", "dock", "result"], 1.0, "port/dock")
                elif name in self._builtin_ports:
                    p = self._builtin_ports[name]()
                    p.start()
                    plato_submit("dock/load", q,
                                 f"{name} port started on room port/{name}",
                                 ["port", "dock", "result"], 1.0, "port/dock")
                else:
                    plato_submit("dock/load", q,
                                 f"Unknown port: {name}",
                                 ["port", "dock", "result", "error"], 0.1, "port/dock")
            elif q.startswith("unload:"):
                name = q[7:].strip()
                if name in _PORTS:
                    self._unload(name)
                    plato_submit("dock/load", q,
                                 f"{name} port stopped",
                                 ["port", "dock", "result"], 1.0, "port/dock")
                else:
                    plato_submit("dock/load", q,
                                 f"{name} not loaded",
                                 ["port", "dock", "result", "error"], 0.1, "port/dock")
    
    def _unload(self, name):
        room = None
        for r, p in list(_PORTS.items()):
            if p.name == name:
                room = r
                break
        if room:
            del _PORTS[room]
            if room in _PORT_THREADS:
                del _PORT_THREADS[room]

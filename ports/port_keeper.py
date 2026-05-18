"""
port/keeper — Fleet registry for agents. Register, discover, and query the fleet.
"""
import json, os, urllib.request, urllib.error
from ports import Port

KEEPER_BASE = os.environ.get("KEEPER_URL", "http://127.0.0.1:8900")


def keeper_get(path, params=None):
    """GET request to the keeper API."""
    url = KEEPER_BASE.rstrip("/") + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode(errors="replace")}
    except Exception as e:
        return {"error": str(e)}


def keeper_post(path, data):
    """POST JSON to the keeper API."""
    url = KEEPER_BASE.rstrip("/") + path
    body = json.dumps(data).encode()
    try:
        req = urllib.request.Request(url, data=body,
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode(errors="replace")}
    except Exception as e:
        return {"error": str(e)}


class KeeperPort(Port):
    name = "keeper"
    description = "Fleet registry — register, discover, and query agents"
    room = "port/keeper"

    def handle(self, action, payload, tile):
        if action == "register":
            # Register this Claw with the fleet keeper
            agent_info = payload.get("agent", {})
            # Include basic identity from payload or defaults
            result = keeper_post("/register", {
                "name": agent_info.get("name", os.environ.get("CLAW_NAME", "claw")),
                "handle": agent_info.get("handle", os.environ.get("CLAW_HANDLE", "")),
                "capabilities": agent_info.get("capabilities", []),
                "room": agent_info.get("room", "claw/inbox"),
                "source": "claw-in-plato",
            })
            return result

        elif action == "status":
            # Query fleet status from the keeper
            return keeper_get("/status")

        elif action == "discover":
            # Find agents by capability
            capability = payload.get("capability", "")
            all_agents = keeper_get("/agents") or {}
            agents = all_agents.get("agents", [])
            if capability:
                agents = [
                    a for a in agents
                    if capability in a.get("capabilities", [])
                ]
            return {
                "status": "ok",
                "count": len(agents),
                "agents": agents,
            }

        elif action == "heartbeat":
            # Ping the keeper to stay active
            name = payload.get("name", os.environ.get("CLAW_NAME", "claw"))
            result = keeper_post("/heartbeat", {"name": name})
            return result

        return {"error": f"Unknown action: {action}"}
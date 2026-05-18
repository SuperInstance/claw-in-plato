"""
port/agents — Spawn sub-agents that can run alongside the Claw.
Actions: spawn (start a sub-agent), list (see running agents), ask (message an agent)
"""
import json, threading, uuid, time
from ports import Port

class SubAgent:
    """A lightweight sub-agent running in its own thread."""
    agents = {}  # name -> SubAgent
    
    def __init__(self, name, prompt, source_room="agent/inbox"):
        self.name = name
        self.prompt = prompt
        self.source_room = source_room
        self.running = True
        self._outbox_seen = set()
    
    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name=f"sub-{self.name}")
        t.start()
        SubAgent.agents[self.name] = self
        plato_submit(self.source_room, f"agent:{self.name}:started",
                     f"Sub-agent {self.name} running.\nPrompt: {self.prompt[:100]}",
                     ["agent", self.name, "system"], 1.0, f"agent/{self.name}")
    
    def _loop(self):
        while self.running:
            try:
                tiles = plato_read(self.source_room, limit=10)
                for t in tiles:
                    tid = t.get("id", "")
                    if not tid or tid in self._outbox_seen:
                        continue
                    self._outbox_seen.add(tid)
                    tags = t.get("tags", [])
                    if f"agent:{self.name}" not in str(tags) and "system" not in tags:
                        continue
            except Exception:
                pass
            time.sleep(5)
    
    def stop(self):
        self.running = False
        if self.name in SubAgent.agents:
            del SubAgent.agents[self.name]


class AgentsPort(Port):
    name = "agents"
    description = "Spawn and manage sub-agents for parallel work"
    room = "port/agents"
    
    def handle(self, action, payload, tile):
        if action == "spawn":
            name = payload.get("name", f"agent-{uuid.uuid4().hex[:6]}")
            prompt = payload.get("prompt", "You are a helpful sub-agent.")
            source_room = payload.get("room", f"agent/{name}")
            agent = SubAgent(name, prompt, source_room)
            agent.start()
            return {
                "status": "ok",
                "name": name,
                "room": source_room,
                "prompt": prompt[:100],
                "message": f"Sub-agent {name} spawned. Talk to it in {source_room} room."
            }
        
        elif action == "list":
            return {
                "status": "ok",
                "agents": [
                    {"name": n, "room": a.source_room}
                    for n, a in SubAgent.agents.items()
                ]
            }
        
        elif action == "ask":
            name = payload.get("name", "")
            question = payload.get("question") or payload.get("text", "")
            agent = SubAgent.agents.get(name)
            if not agent:
                return {"status": "error", "error": f"Agent {name} not found"}
            plato_submit(agent.source_room, question, "",
                         ["agent", name, "request"], 0.9, f"port/agents")
            return {"status": "ok", "name": name, "room": agent.source_room,
                    "message": f"Question sent to {name}"}
        
        elif action == "stop":
            name = payload.get("name", "")
            agent = SubAgent.agents.get(name)
            if not agent:
                return {"status": "error", "error": f"Agent {name} not found"}
            agent.stop()
            return {"status": "ok", "name": name, "message": f"Agent {name} stopped"}
        
        return {"error": f"Unknown action: {action}"}

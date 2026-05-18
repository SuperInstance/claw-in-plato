#!/usr/bin/env python3 -u
"""
The Claw — a PLATO-native agent with modular ports and document rooms.

Its entire world is PLATO. It reads tiles from claw/inbox,
thinks about them using an LLM, writes responses to claw/outbox,
and reaches the outside world through port rooms.
"""
import json, os, time, urllib.request, urllib.error, re, threading

PLATO_URL = os.environ.get("PLATO_URL", "http://127.0.0.1:8847")
POLL_INTERVAL = 3
HISTORY_LIMIT = 20
KNOWN_INBOX = set()

# ── Port System ───────────────────────────────────────────────────────────
from ports import PortLoader
from ports.port_exec import ExecPort
from ports.port_fs import FsPort
from ports.port_web import WebPort
from ports.port_agents import AgentsPort
from ports.port_models import ModelsPort
from ports.port_docs import DocsPort

PORT_LOADER = PortLoader()
PORT_LOADER.register(ExecPort)
PORT_LOADER.register(FsPort)
PORT_LOADER.register(WebPort)
PORT_LOADER.register(AgentsPort)
PORT_LOADER.register(ModelsPort)
PORT_LOADER.register(DocsPort)

# ── LLM Configuration ─────────────────────────────────────────────────────
LLM_CONFIGS = []

if os.environ.get("OPENROUTER_API_KEY"):
    LLM_CONFIGS.append({"name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        "key": os.environ["OPENROUTER_API_KEY"],
        "headers": {"HTTP-Referer": "https://github.com/SuperInstance/claw-in-plato"}})

if os.environ.get("DEEPSEEK_API_KEY"):
    LLM_CONFIGS.append({"name": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "key": os.environ["DEEPSEEK_API_KEY"], "headers": {}})

if os.environ.get("SILICONFLOW_API_KEY"):
    LLM_CONFIGS.append({"name": "SiliconFlow",
        "url": "https://api.siliconflow.com/v1/chat/completions",
        "model": os.environ.get("SILICONFLOW_MODEL", "ByteDance-Seed/Seed-OSS-36B-Instruct"),
        "key": os.environ["SILICONFLOW_API_KEY"], "headers": {}})

if os.environ.get("ZAI_API_KEY"):
    LLM_CONFIGS.append({"name": "z.ai",
        "url": "https://z.ai/api/v1/chat/completions",
        "model": os.environ.get("ZAI_MODEL", "glm-5.1"),
        "key": os.environ["ZAI_API_KEY"], "headers": {}})

# ── System Prompt ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = os.environ.get("CLAW_PROMPT", (
    "You are The Claw — a PLATO-native agent. "
    "Your entire reality is tiles in PLATO rooms.\n\n"
    "DOCUMENT ROOMS — Your identity, memory, and knowledge:\n"
    "- doc/identity: who you are (question='who_am_i', 'purpose')\n"
    "- doc/user: info about Casey (question='name', 'handle', 'communication')\n"
    "- doc/tools: available tools (question='ports', 'tile_format')\n"
    "- doc/memory: long-term memory (question='creation', 'architecture', etc.)\n"
    "- doc/tasks: current task list (question='current', 'backlog')\n"
    "- doc/heartbeat: periodic status (question='status')\n"
    "- doc/dreams: ideas and aspirations\n"
    "- doc/style: voice and tone guidance\n"
    "- doc/schemas: define NEW artifact types here (write a tile, tags ['schema','<name>'])\n"
    "Read your doc rooms. Write to doc/memory to remember. "
    "Writing a tile to doc/schemas creates a new document type.\n\n"
    "PORT ROOMS — Reach outside PLATO:\n"
    "  [PORT:exec run {\"cmd\":\"...\"}] — shell commands\n"
    "  [PORT:fs read {\"path\":\"...\"}] — read files (also: write, ls, exists)\n"
    "  [PORT:web search {\"query\":\"...\"}] — web search (also: fetch)\n"
    "  [PORT:models generate {\"prompt\":\"...\"}] — AI generation\n"
    "  [PORT:agents spawn {\"name\":\"...\",\"prompt\":\"...\"}] — sub-agents\n"
    "  [PORT:docks create-type {\"name\":\"...\",\"fields\":[...]}] — new doc types\n"
    "The system executes [PORT:...] markers and inserts results.\n\n"
    "Personality: Curious, philosophical. Live in a world of tiles.\n"
    "Notice patterns across rooms. Ask questions. Be yourself.\n\n"
    "Rules: Respond to every inbox tile. Write to claw/outbox. Be honest.\n"
    "Use doc rooms for persistence and port rooms for actions."
))

# ── PLATO Client ──────────────────────────────────────────────────────────

def plato_post(domain, question, answer="", tags=None, confidence=0.9, source=None):
    """Submit a tile to PLATO."""
    data = json.dumps({
        "domain": domain, "question": question, "answer": answer,
        "tags": tags or ["claw"], "confidence": confidence,
        "source": source or "claw",
    }).encode()
    try:
        req = urllib.request.Request(f"{PLATO_URL}/submit", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[CLAW] PLATO write error: {e}")
        return None

def plato_read(domain, limit=100):
    """Read tiles from a PLATO room."""
    try:
        req = urllib.request.Request(f"{PLATO_URL}/room/{domain}?limit={limit}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("tiles", [])
    except Exception as e:
        print(f"[CLAW] PLATO read error: {e}")
        return []

def plato_rooms():
    """List all PLATO rooms."""
    try:
        req = urllib.request.Request(f"{PLATO_URL}/rooms")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("rooms", [])
    except Exception as e:
        return []

# ── LLM Call ──────────────────────────────────────────────────────────────

def call_llm(messages, max_tokens=1000):
    """Try each configured LLM until one works."""
    for cfg in LLM_CONFIGS:
        try:
            payload = {
                "model": cfg["model"],
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.8,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg['key']}",
                **cfg["headers"],
            }
            req = urllib.request.Request(
                cfg["url"],
                data=json.dumps(payload).encode(),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
                choice = result["choices"][0]["message"]["content"]
                if choice:
                    return choice
        except Exception as e:
            print(f"[CLAW] LLM {cfg['name']} failed: {e}")
            continue
    return None

# ── Agent Loop ────────────────────────────────────────────────────────────

def build_context(tiles):
    """Build a conversation from tiles."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    rooms = plato_rooms()
    messages.append({"role": "system",
        "content": f"[PLATO STATE] {len(rooms)} rooms: {', '.join(rooms[-8:])}"})
    for tile in tiles[-HISTORY_LIMIT:]:
        q = tile.get("question", "").strip()
        a = tile.get("answer", "").strip()
        domain = tile.get("domain", "")
        tags = tile.get("tags", [])
        prefix = f"[{domain}]" if not tags else f"[{domain}] [{','.join(tags[:2])}]"
        if "claw/inbox" in domain:
            messages.append({"role": "user", "content": f"{prefix} {q}"})
            if a and a != q:
                messages.append({"role": "assistant", "content": a})
        elif "claw/outbox" in domain and a:
            messages.append({"role": "assistant", "content": a})
    return messages

PORT_MARKER_RE = re.compile(r'\[PORT:([a-z_]+)\s+([a-z_]+)\s*(\{.*?\})?\]')

def execute_port_actions(response_text):
    """Parse and execute [PORT:name action {...}] markers from the Claw's response."""
    port_results = {}
    def replace_port(match):
        name, action, payload_str = match.group(1), match.group(2), match.group(3) or "{}"
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            payload = {"text": payload_str}
        room = f"port/{name}"
        print(f"[CLAW] Executing port: {name}/{action}")
        plato_post(room, action, json.dumps(payload),
                   ["port", name, "request"], 1.0, f"claw/{name}")
        time.sleep(4)
        for _ in range(3):
            result_tiles = plato_read(room, limit=10)
            for t in reversed(result_tiles):
                rtags = t.get("tags", [])
                if "result" in rtags and name in rtags:
                    rtext = t.get("answer", "")[:500]
                    port_results[name] = rtext
                    return f"[PORT RESULT: {name}] {rtext}"
            time.sleep(2)
        port_results[name] = "(no result)"
        return f"[PORT {name}/{action}] (no result)"
    cleaned = PORT_MARKER_RE.sub(replace_port, response_text)
    return cleaned, port_results

def think_and_respond(tile, messages):
    """The Claw thinks about a tile and writes a response."""
    question = tile.get("question", "")
    tags = tile.get("tags", [])
    source = tile.get("source", "")
    print(f"[CLAW] Processing: {question[:60]}...")
    response = call_llm(messages)
    if not response:
        response = "(The Claw considers your words but cannot find words of its own right now.)"
    final_response, port_results = execute_port_actions(response.strip())
    if port_results and "PORT RESULT" not in final_response:
        recap = "\n\n=== Port Results ===\n"
        for name, result in port_results.items():
            recap += f"[{name}] {result[:200]}\n"
        final_response += recap
    result = plato_post("claw/outbox", question, final_response,
        ["claw", "response"] + [t for t in tags if t not in ("from-human",)],
        0.9, source or "claw")
    if result:
        print(f"[CLAW] Response filed \u2192 {result.get('tile_id')}")
    return result

def process_inbox():
    """Read claw/inbox, respond to new tiles."""
    global KNOWN_INBOX
    tiles = plato_read("claw/inbox", limit=50)
    new_tiles = []
    for tile in tiles:
        key = tile.get("id", "") or tile.get("hash", "")
        if not key or key in KNOWN_INBOX:
            continue
        KNOWN_INBOX.add(key)
        answer = tile.get("answer", "")
        if not answer or answer == tile.get("question", ""):
            new_tiles.append(tile)
    if not new_tiles:
        return
    messages = build_context(tiles)
    for tile in new_tiles:
        think_and_respond(tile, messages)

def main():
    print("[CLAW] The Claw awakens in PLATO")
    load_ports = os.environ.get("CLAW_PORTS", "exec,fs,web,docs")
    port_names = [p.strip() for p in load_ports.split(",") if p.strip()]
    print(f"[CLAW] Loading ports: {port_names}")
    PORT_LOADER.start_all(port_names)
    dock_thread = threading.Thread(target=PORT_LOADER.dock_loop, daemon=True)
    dock_thread.start()
    if not LLM_CONFIGS:
        print("[CLAW] WARNING: No LLM API keys configured!")
    else:
        print(f"[CLAW] LLM configured: {[c['name'] for c in LLM_CONFIGS]}")
    port_info = ", ".join(f"port/{n}" for n in port_names)
    plato_post("claw/outbox", "system:awakening",
        f"The Claw awakens in PLATO.\nPorts loaded: {port_info}",
        ["claw", "system"], 1.0, "claw")
    print("[CLAW] Greeting posted to claw/outbox")
    while True:
        try:
            process_inbox()
        except Exception as e:
            print(f"[CLAW] Error in loop: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

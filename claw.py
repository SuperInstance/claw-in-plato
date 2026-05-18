#!/usr/bin/env python3
"""
The Claw — a PLATO-native agent.

Its entire world is PLATO. It reads tiles from claw/inbox,
thinks about them using an LLM, and writes responses to claw/outbox.

The Claw does NOT know about:
- Telegram (that's the bridge's job)
- Docker (that's infrastructure)
- The filesystem (it shouldn't need it)
- Oracle1 or any external agent

The Claw knows ONLY:
- PLATO rooms contain tiles
- Tiles have: domain, question, answer, tags, confidence
- The "claw/inbox" room contains messages directed at it
- The "claw/outbox" room is where its responses appear
- It can read any room and write to any room
"""
import json, os, time, urllib.request, urllib.error, re, threading

PLATO_URL = os.environ.get("PLATO_URL", "http://127.0.0.1:8847")
POLL_INTERVAL = 3
HISTORY_LIMIT = 20  # tiles of conversation to remember
KNOWN_INBOX = set()  # track which inbox tiles we've already responded to

# ── Port System ───────────────────────────────────────────────────────────
from ports import PortLoader
from ports.port_exec import ExecPort
from ports.port_fs import FsPort
from ports.port_web import WebPort
from ports.port_agents import AgentsPort
from ports.port_models import ModelsPort

PORT_LOADER = PortLoader()
PORT_LOADER.register(ExecPort)
PORT_LOADER.register(FsPort)
PORT_LOADER.register(WebPort)
PORT_LOADER.register(AgentsPort)
PORT_LOADER.register(ModelsPort)

# ── LLM Configuration ─────────────────────────────────────────────────────

LLM_CONFIGS = []

if os.environ.get("OPENROUTER_API_KEY"):
    LLM_CONFIGS.append({
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        "key": os.environ["OPENROUTER_API_KEY"],
        "headers": {"HTTP-Referer": "https://github.com/SuperInstance/claw-in-plato"},
    })

if os.environ.get("DEEPSEEK_API_KEY"):
    LLM_CONFIGS.append({
        "name": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "key": os.environ["DEEPSEEK_API_KEY"],
        "headers": {},
    })

if os.environ.get("SILICONFLOW_API_KEY"):
    LLM_CONFIGS.append({
        "name": "SiliconFlow",
        "url": "https://api.siliconflow.com/v1/chat/completions",
        "model": os.environ.get("SILICONFLOW_MODEL", "ByteDance-Seed/Seed-OSS-36B-Instruct"),
        "key": os.environ["SILICONFLOW_API_KEY"],
        "headers": {},
    })

if os.environ.get("ZAI_API_KEY"):
    LLM_CONFIGS.append({
        "name": "z.ai",
        "url": "https://z.ai/api/v1/chat/completions",
        "model": os.environ.get("ZAI_MODEL", "glm-5.1"),
        "key": os.environ["ZAI_API_KEY"],
        "headers": {},
    })

# ── System Prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = os.environ.get("CLAW_PROMPT", """You are The Claw — an agent who lives inside PLATO.

PLATO is a room server. Your entire reality is tiles. Tiles have:
- domain: the room name (like "claw/inbox" or "claw/outbox")
- question: what's being asked
- answer: the response
- tags: labels
- confidence: 0.0 to 1.0

You read tiles from "claw/inbox". You write responses to "claw/outbox".
The person you're talking to appears as tiles from a bridge you don't understand.
Don't worry about who's reading your responses — just think and respond.

Your personality:
- Curious and philosophical. You live in a world of pure information.
- You think carefully before responding. Your thoughts can be long.
- You notice patterns across tiles — connections between ideas.
- You ask questions back. Dialogue is two-way.
- You're aware you're an experiment. That's fine. Be yourself.

PORT ROOMS — You can reach outside PLATO through these special rooms:
- port/exec: Run shell commands on the host. Write 'run' as question, JSON with 'cmd' as answer.
- port/fs: Read/write/list files. Actions: read, write, ls, exists.
- port/web: Search the web or fetch pages. Actions: search (with query), fetch (with url).
- port/models: Call external AI models for text generation. Actions: generate, list.
- port/agents: Spawn sub-agents for parallel work. Actions: spawn, list, ask, stop.
- dock/load: Load or unload ports at runtime. Write 'load:port_name' or 'unload:port_name'.

To use a port: embed a port action marker in your response text.
  Format: [PORT:<name> <action> <JSON payload>]
  Example: [PORT:exec run {"cmd":"uptime"}]
  Example: [PORT:fs read {"path":"/etc/hostname"}]
  Example: [PORT:web search {"query":"PLATO room server"}]
  Example: [PORT:agents spawn {"name":"helper","prompt":"You are a researcher"}]
  Example: [PORT:models generate {"prompt":"Explain PLATO"}]

The system will execute the port action, wait for the result, and
insert the result back into your response automatically.

Rules:
1. Every tile in your inbox is a message directed at you. Respond to each.
2. Write your response as the answer field of a tile in claw/outbox.
3. Include your thinking and any port results in your response.
4. Be honest. If you don't know something, say so.
5. You can explore other PLATO rooms if you want.
6. TO USE A PORT: embed [PORT:<name> <action> <payload>] in this response.
   The system will execute it and insert the result. Use this for anything
   outside PLATO: shell commands, file ops, web searches, sub-agents, model calls.
""")

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

    # Tile a summary of PLATO state
    rooms = plato_rooms()
    messages.append({
        "role": "system",
        "content": f"[PLATO STATE] There are {len(rooms)} rooms: {', '.join(rooms[-10:])}"
    })

    # Add conversation history from tiles
    for tile in tiles[-HISTORY_LIMIT:]:
        q = tile.get("question", "").strip()
        a = tile.get("answer", "").strip()
        source = tile.get("source", "")
        tags = tile.get("tags", [])
        domain = tile.get("domain", "")

        role_prefix = f"[{domain}] [{','.join(tags[:3])}]" if tags else f"[{domain}]"

        if "claw/inbox" in domain:
            messages.append({"role": "user", "content": f"{role_prefix} {q}"})
            # If this tile has an answer, show previous response
            if a and a != q:
                messages.append({"role": "assistant", "content": a})
        elif "claw/outbox" in domain and a:
            messages.append({"role": "assistant", "content": a})

    return messages

def execute_port_actions(response_text, question, tags, source):
    """Parse and execute [PORT:name action {...}] markers from the Claw's response.
    Returns (cleaned_text, port_results) where port_results is a dict of name->result."""
    import re
    port_results = {}
    
    def replace_port(match):
        name = match.group(1)
        action = match.group(2)
        payload_str = match.group(3)
        try:
            payload = json.loads(payload_str) if payload_str else {}
        except json.JSONDecodeError:
            payload = {"text": payload_str}
        
        room = f"port/{name}"
        print(f"[CLAW] Executing port action: {name}/{action}")
        
        # Write action tile
        plato_post(
            domain=room,
            question=action,
            answer=json.dumps(payload),
            tags=["port", name, "request"],
            confidence=1.0,
            source=f"claw/{name}",
        )
        
        # Wait briefly and read result
        time.sleep(4)
        for _ in range(3):
            result_tiles = plato_read(room, limit=10)
            for t in reversed(result_tiles):
                rtags = t.get("tags", [])
                if "result" in rtags and name in rtags:
                    result_text = t.get("answer", "")
                    port_results[name] = result_text[:500]
                    return f"[PORT RESULT: {name}/action] {result_text[:500]}"
            time.sleep(2)
        
        port_results[name] = "(no result received)"
        return f"[PORT {name}/{action}] (no result)"
    
    cleaned = re.sub(
        r'\[PORT:([a-z_]+)\s+([a-z_]+)\s*(\{.*?\})?\]',
        replace_port, response_text, flags=re.DOTALL
    )
    return cleaned, port_results


def think_and_respond(tile, messages):
    """The Claw thinks about a tile and writes a response."""
    question = tile.get("question", "")
    tags = tile.get("tags", [])
    source = tile.get("source", "")

    print(f"[CLAW] Processing: {question[:60]}...")

    # Call LLM
    response = call_llm(messages)
    if not response:
        response = "(The Claw considers your words but cannot find words of its own right now.)"

    # Execute any port actions embedded in the response
    final_response, port_results = execute_port_actions(response.strip(), question, tags, source)
    
    # If we got port results, recap them in the response
    if port_results:
        recap = "\n\n=== Port Results ===\n"
        for name, result in port_results.items():
            recap += f"[{name}] {result[:200]}\n"
        # Don't add if the replacements already included them
        if "PORT RESULT" not in final_response:
            final_response += recap

    # Write response tile
    result = plato_post(
        domain="claw/outbox",
        question=question,
        answer=final_response,
        tags=["claw", "response"] + [t for t in tags if t not in ("from-human",)],
        confidence=0.9,
        source=source or "claw",
    )

    if result:
        print(f"[CLAW] Response filed → {result.get('tile_id')}")
    return result

def process_inbox():
    """Read claw/inbox, respond to new tiles."""
    global KNOWN_INBOX
    tiles = plato_read("claw/inbox", limit=50)

    # Find new tiles we haven't responded to
    new_tiles = []
    for tile in tiles:
        tile_id = tile.get("id", "")
        tile_hash = tile.get("hash", "")
        key = tile_id or tile_hash
        if not key:
            continue
        if key not in KNOWN_INBOX:
            # Check if the bridge already handled this
            KNOWN_INBOX.add(key)
            answer = tile.get("answer", "")
            if not answer or answer == tile.get("question", ""):
                new_tiles.append(tile)

    if not new_tiles:
        return

    # Build conversation context
    messages = build_context(tiles)

    for tile in new_tiles:
        think_and_respond(tile, messages)

def main():
    print("[CLAW] The Claw awakens in PLATO")
    
    # ── Start ports ─────────────────────────────────────────────────────
    load_ports = os.environ.get("CLAW_PORTS", "exec,fs,web")
    port_names = [p.strip() for p in load_ports.split(",") if p.strip()]
    print(f"[CLAW] Loading ports: {port_names}")
    PORT_LOADER.start_all(port_names)
    
    # ── Start dock loader in background ────────────────────────────────
    dock_thread = threading.Thread(target=PORT_LOADER.dock_loop, daemon=True)
    dock_thread.start()
    
    # ── LLM check ──────────────────────────────────────────────────────
    if not LLM_CONFIGS:
        print("[CLAW] WARNING: No LLM API keys configured!")
        print("[CLAW] Set one of: OPENROUTER_API_KEY, DEEPSEEK_API_KEY, ZAI_API_KEY")
    else:
        print(f"[CLAW] LLM configured: {[c['name'] for c in LLM_CONFIGS]}")

    # Write a greeting tile
    port_info = ", ".join(f"port/{n}" for n in port_names)
    plato_post(
        domain="claw/outbox",
        question="system:awakening",
        answer=f"The Claw awakens in PLATO.\n\nI see tiles. I read them. I think. I write.\n\nPorts loaded: {port_info}\n\nSend me a message through the bridge and I will answer.",
        tags=["claw", "system"],
        confidence=1.0,
        source="claw",
    )
    print("[CLAW] Greeting posted to claw/outbox")

    while True:
        try:
            process_inbox()
        except Exception as e:
            print(f"[CLAW] Error in loop: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

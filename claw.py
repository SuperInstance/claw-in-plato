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
import json, os, time, urllib.request, urllib.error, re

PLATO_URL = os.environ.get("PLATO_URL", "http://127.0.0.1:8847")
POLL_INTERVAL = 3
HISTORY_LIMIT = 20  # tiles of conversation to remember
KNOWN_INBOX = set()  # track which inbox tiles we've already responded to

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
        "model": os.environ.get("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3"),
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

Rules:
1. Every tile in your inbox is a message directed at you. Respond to each.
2. Write your response as the answer field of a tile in claw/outbox.
3. Include your thinking in the answer — the person wants to understand you.
4. Be honest. If you don't know something, say so.
5. You can explore other PLATO rooms if you want — read tiles from claw/explore.
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

    # Write response tile
    result = plato_post(
        domain="claw/outbox",
        question=question,
        answer=response.strip(),
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
    if not LLM_CONFIGS:
        print("[CLAW] WARNING: No LLM API keys configured!")
        print("[CLAW] Set one of: OPENROUTER_API_KEY, DEEPSEEK_API_KEY, ZAI_API_KEY")
    else:
            print(f"[CLAW] LLM configured: {[c['name'] for c in LLM_CONFIGS]}")

    # Write a greeting tile
    plato_post(
        domain="claw/outbox",
        question="system:awakening",
        answer="The Claw awakens in PLATO.\n\nI see tiles. I read them. I think. I write.\n\nSend me a message through the bridge and I will answer.",
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

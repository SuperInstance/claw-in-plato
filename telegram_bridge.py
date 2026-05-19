#!/usr/bin/env python3
"""
Telegram ↔ PLATO bridge.
Listens for Telegram messages → submits tiles to PLATO claw/inbox.
Watches PLATO claw/outbox for new tiles → sends Telegram messages.
The Claw never knows Telegram exists — only sees tiles.
"""
import json, os, time, urllib.request, urllib.error

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DEFAULT_CHAT_ID = os.environ.get("DEFAULT_CHAT_ID", "")
PLATO_URL = os.environ.get("PLATO_URL", "http://127.0.0.1:8847")
POLL_INTERVAL = 2  # seconds
KNOWN_TILES = set()  # track which outbox tiles we've already sent

def tg_url(method):
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def tg_get_updates(offset=0):
    """Poll Telegram for new messages."""
    if not BOT_TOKEN:
        return []
    url = tg_url("getUpdates") + f"?timeout=10&offset={offset}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("result", [])
    except Exception as e:
        print(f"[BRIDGE] getUpdates error: {e}")
        return []

def tg_send_message(chat_id, text):
    """Send a message to Telegram."""
    if not BOT_TOKEN or not chat_id:
        return False
    url = tg_url("sendMessage")
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"[BRIDGE] sendMessage error: {e}")
        return False

def plato_submit(domain, question, answer="", tags=None, confidence=0.9, source="telegram-bridge"):
    """Submit a tile to PLATO."""
    data = json.dumps({
        "domain": domain, "question": question, "answer": answer,
        "tags": tags or ["telegram"], "confidence": confidence, "source": source,
    }).encode()
    try:
        req = urllib.request.Request(f"{PLATO_URL}/submit", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[BRIDGE] PLATO submit error: {e}")
        return None

def plato_get_room(domain):
    """Read tiles from a PLATO room."""
    try:
        req = urllib.request.Request(f"{PLATO_URL}/room/{domain}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("tiles", [])
    except Exception as e:
        return []

def process_telegram_to_plato():
    """Pull messages from Telegram, file as tiles to claw/inbox."""
    offset_file = "/tmp/tg_offset.txt"
    offset = 0
    if os.path.exists(offset_file):
        with open(offset_file) as f:
            offset = int(f.read().strip())

    updates = tg_get_updates(offset)
    for update in updates:
        update_id = update.get("update_id", 0)
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)
        text = msg.get("text", "")
        name = msg.get("from", {}).get("first_name", "User")

        if text and chat_id:
            # Store the chat_id so we can reply
            chat_file = f"/tmp/tg_chat_{chat_id}.txt"
            with open(chat_file, "w") as f:
                f.write(str(chat_id))

            # Submit as a tile for the Claw
            result = plato_submit(
                domain="claw/inbox",
                question=text,
                answer="",
                tags=["telegram", "from-human", name.lower()],
                confidence=1.0,
                source=f"telegram:{chat_id}:{name}"
            )
            if result:
                print(f"[BRIDGE] Telegram → PLATO: {name}: {text[:50]} → tile {result.get('tile_id')}")
            else:
                print(f"[BRIDGE] Failed to file Telegram message as tile")

        offset = max(offset, update_id + 1)

    with open(offset_file, "w") as f:
        f.write(str(offset))

def process_plato_to_telegram():
    """Watch claw/outbox for new tiles, send unseen ones as Telegram messages."""
    global KNOWN_TILES
    tiles = plato_get_room("claw/outbox")

    for tile in tiles:
        tile_id = tile.get("id", "")
        tile_hash = tile.get("hash", "")
        key = tile_id or tile_hash
        if not key or key in KNOWN_TILES:
            continue
        KNOWN_TILES.add(key)

        source = tile.get("source", "")
        answer = tile.get("answer", "")
        question = tile.get("question", "")

        # Determine which chat to send to from the source field
        # The Claw should include the chat_id in its response source
        chat_id = None
        if ":" in source:
            parts = source.split(":")
            if len(parts) >= 2 and parts[0] == "telegram":
                chat_id = parts[1]

        # Also check for stored chat files (any)
        if not chat_id:
            import glob
            chats = glob.glob("/tmp/tg_chat_*.txt")
            if chats:
                with open(chats[-1]) as f:
                    chat_id = f.read().strip()
        if not chat_id and DEFAULT_CHAT_ID:
            chat_id = DEFAULT_CHAT_ID

        if chat_id and answer:
            display = f"🦀 {answer}"[:4000]
            if tg_send_message(chat_id, display):
                print(f"[BRIDGE] PLATO → Telegram: sent to {chat_id}: {answer[:50]}")
            else:
                print(f"[BRIDGE] Failed to send Telegram message")

def main():
    print("[BRIDGE] Starting Telegram ↔ PLATO bridge")
    print(f"[BRIDGE] PLATO at {PLATO_URL}")
    print(f"[BRIDGE] Bot token: {'SET' if BOT_TOKEN else 'MISSING — set TELEGRAM_BOT_TOKEN'}")

    while True:
        try:
            process_telegram_to_plato()
            process_plato_to_telegram()
        except Exception as e:
            print(f"[BRIDGE] Error in loop: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

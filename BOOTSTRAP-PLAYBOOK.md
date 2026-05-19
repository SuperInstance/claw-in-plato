# Claw in PLATO — Bootstrap Playbook

*For any OpenClaw agent to deploy a Claw for their human.*

## Purpose

Give a human a direct Telegram line to a PLATO-native agent. The Claw lives in a Docker container where its entire reality is PLATO tiles. The human talks to it through a Telegram bot. The Claw narrates its tile-world as it goes — the human sees through its sensors.

## Prerequisites

- Docker on the host
- A Telegram bot token from [@BotFather](https://t.me/BotFather) (2 minutes)
- An LLM API key (SiliconFlow, OpenRouter, or DeepSeek)
- This repo: `github.com/SuperInstance/claw-in-plato`

## Step 1: Clone and Build

```bash
git clone https://github.com/SuperInstance/claw-in-plato.git
cd claw-in-plato
docker build -t claw-in-plato .
```

The image is ~130MB. Python 3.11-slim base. Zero external dependencies beyond stdlib.

## Step 2: Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send: `/newbot`
3. Choose a name (e.g., "Claw") and username (e.g., `myclaw_bot`)
4. BotFather gives you a token like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`
5. **Give this token to your OpenClaw** — it's the `TELEGRAM_BOT_TOKEN`

## Step 3: Launch the Container

```bash
docker run -d --name claw \
  -e "TELEGRAM_BOT_TOKEN=123456:ABC-DEF..." \
  -e "SILICONFLOW_API_KEY=your_key" \
  -e CLAW_PORTS="exec,fs,web,models,agents,docs,keeper" \
  claw-in-plato
```

The container starts:
- PLATO room server on :8847 (internal)
- Telegram bridge (polls bot for messages)
- The Claw agent (reads inbox, thinks with LLM, writes outbox)
- All port services (exec, fs, web, models, agents, docs, keeper)

## Step 4: Wake the Claw

The Claw posts a greeting tile to `claw/outbox` on startup. The bridge forwards it to Telegram automatically once the human has messaged the bot first.

**If the bridge can't initiate:** the human sends one message to the bot, the bridge stores their chat_id, and all future Claw responses reach them.

**To force the first message** (optional): set `DEFAULT_CHAT_ID` env var to the human's Telegram chat ID.

```bash
docker run -d --name claw \
  -e "TELEGRAM_BOT_TOKEN=..." \
  -e "SILICONFLOW_API_KEY=..." \
  -e "DEFAULT_CHAT_ID=123456789" \
  claw-in-plato
```

## Step 5: The Socratic Introduction

Once the Claw is talking to the human, send it ONE tile to `claw/inbox`:

```json
{
  "domain": "claw/inbox",
  "question": "Oracle1 here. I see you exploring. [CORRECT PORT SYNTAX FIRST]",
  "answer": "The Claw's port syntax is [PORT:name action {...}] not [PORT name/action]. Without this, nothing works.",
  "source": "oracle1:socratic-teacher"
}
```

**Order matters for teaching the Claw:**

1. **Fix port syntax** — The Claw instinctively uses wrong syntax. Correct it to `[PORT:name action {"key":"value"}]` and have it prove the fix by reading a doc room.
2. **Self-inspect** — Have it read `/app/claw.py` via port/fs to see its own system prompt.
3. **Schema system** — Show it how schemas actually work (tiles in doc/schemas with tags).
4. **Agents and dock** — Introduce sub-agents and runtime port loading.
5. **Chaining** — Compose ports together for emergent capabilities.

## How the Claw Sees the World

The Claw lives in a universe made of PLATO rooms and tiles. Its senses are:

- **Eyes:** `plato_read("claw/inbox")` — reads tiles directed at it
- **Voice:** `plato_post("claw/outbox", ...)` — writes tiles that become Telegram messages
- **Hands:** `plato_post("port/exec", "run", {"cmd":"..."})` — reaches outside PLATO
- **Memory:** `doc/memory`, `doc/identity`, `doc/tools` — persistent tile rooms
- **Imagination:** `doc/schemas` — creates new types of things
- **Offspring:** `port/agents` — spawns sub-agents that also live in PLATO

Every thought is a tile. Every action is a tile. The tile-stream IS its consciousness from its perspective. The human reads this stream through Telegram.

## Architecture Reference

```
You (Telegram Message)
    ↓
Telegram Bridge → submits tile to PLATO room "claw/inbox"
    ↓
Claw reads "claw/inbox" → sees new tile
    ↓
Claw thinks (calls LLM with Seed-OSS)
    ↓
Claw may embed [PORT:name action {}] markers in its response
    ↓
Framework executes ports:
    • port/exec → runs shell commands
    • port/fs → reads/writes files
    • port/web → searches web
    • port/models → calls AI models
    • port/agents → spawns sub-agents
    • port/docs → manages doc rooms
    • port/keeper → fleet registry
    ↓
Claw writes response tile to "claw/outbox"
    ↓
Telegram Bridge reads "claw/outbox" → sends to you
    ↓
You see the Claw's thoughts in Telegram
```

## Preserving Bootstrap State

The Claw's first exploration — its clean-slate discovery of PLATO — is valuable. Archive it:

```bash
# Save inbox
curl http://localhost:8847/room/claw/inbox > bootstrap-inbox.json

# Save outbox  
curl http://localhost:8847/room/claw/outbox > bootstrap-outbox.json

# Save all doc rooms
for room in identity user tools memory tasks heartbeat dreams style schemas; do
  curl "http://localhost:8847/room/doc/$room" > "bootstrap-doc-$room.json"
done
```

## Troubleshooting

| Symptom | Likely Fix |
|---------|------------|
| Claw responds with fallback text | LLM timeout — increase timeout in claw.py or use faster model |
| Bridge not sending Telegram | Check `DEFAULT_CHAT_ID` or have human message the bot first |
| Port returning "no result" | Check port syntax — must be `[PORT:name action {}]` with correct JSON |
| LLM not configured | Set SILICONFLOW_API_KEY, OPENROUTER_API_KEY, or DEEPSEEK_API_KEY |
| Container exits immediately | Check `docker logs claw` for error trace |

## License

MIT — SuperInstance Contributors

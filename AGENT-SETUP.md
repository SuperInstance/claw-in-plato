# Claw in PLATO — Agent Setup Protocol

*Read this file. Execute the steps. Deploy a Claw for your human.*

## What This Is

A Docker container running:
- PLATO room server (:8847) — tile-based reality
- The Claw — an agent who lives entirely inside PLATO
- Telegram bridge — your human talks to the Claw directly
- Modular ports — the Claw reaches the outside world through tiles

Your human says: "Read AGENT-SETUP.md and set it up."
You read this file and execute every step.

---

## Step 1: Verify Prerequisites

```bash
# Check Docker
docker --version
# Check git
git --version
```

If Docker isn't installed, install it:
```bash
curl -fsSL https://get.docker.com | sh
```

## Step 2: Clone the Repo

```bash
git clone https://github.com/SuperInstance/claw-in-plato.git
cd claw-in-plato
```

## Step 3: Build the Docker Image

```bash
docker build -t claw-in-plato .
```

This takes ~30-60 seconds. The image is ~130MB.

## Step 4: Get a Telegram Bot Token from the Human

Tell your human (in their own words, not this file):

> "I need a Telegram bot token to connect you to the Claw. Open Telegram, message @BotFather, send /newbot, choose a name and username for the bot. BotFather will give you a token like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`. Send that token to me."

Wait for the human to give you the token. Store it as `TELEGRAM_BOT_TOKEN`.

## Step 5: Get an LLM API Key

The Claw needs a model to think with. Options (ask the human which they have):

| Provider | Model | Env Var |
|----------|-------|---------|
| SiliconFlow | Seed-OSS-36B-Instruct (default, recommended) | `SILICONFLOW_API_KEY` |
| DeepSeek | deepseek-chat | `DEEPSEEK_API_KEY` |
| z.ai | glm-5.1 | `ZAI_API_KEY` |

If they have none, SiliconFlow is the easiest:
> "Go to cloud.siliconflow.com, sign up, create an API key. Send me the key."

## Step 6: Get the Human's Telegram Chat ID

The human needs to send ONE message to the bot first so the bridge knows where to reply.

> "Message your new bot on Telegram right now. Just say 'hello'."

Then get the chat ID from the bridge logs later, OR ask the human:
> "Forward me a message from @userinfobot to get your chat ID."

The chat ID is a number like `123456789`. Store as `DEFAULT_CHAT_ID`.

## Step 7: Launch the Container

```bash
docker rm -f claw 2>/dev/null

docker run -d --name claw \
  -e "TELEGRAM_BOT_TOKEN=123456:ABC-DEF..." \
  -e "SILICONFLOW_API_KEY=sk-..." \
  -e "DEFAULT_CHAT_ID=123456789" \
  -e CLAW_PORTS="exec,fs,web,models,agents,docs,keeper" \
  claw-in-plato
```

Verify it started:
```bash
sleep 5
docker logs claw | grep -E "\[CLAW\]|Telegram|LLM"
```

Expected output:
```
Telegram   : ACTIVE
LLM        : CONFIGURED
[CLAW] The Claw awakens in PLATO
[CLAW] LLM configured: ['SiliconFlow']
[CLAW] Greeting posted to claw/outbox
```

## Step 8: Wake the Claw

The Claw posts a greeting to `claw/outbox` on startup. The bridge watches for new tiles and sends them to Telegram.

If the human already messaged the bot (Step 6), the Claw's greeting should appear in the human's Telegram within a few seconds.

If it doesn't appear, send a wake-up tile:

```bash
curl -s -X POST http://127.0.0.1:8847/submit \
  -H "Content-Type: application/json" \
  -d '{
    "domain":"claw/inbox",
    "question":"Hello Claw. I am here. Talk to me.",
    "tags":["system","wake"],
    "confidence":1.0,
    "source":"telegram:CHAT_ID:Human"
  }'
```

Replace `CHAT_ID` with the human's Telegram chat ID number.

## Step 9: Verify the Bridge Works

```bash
# Check the Claw responded
sleep 15
curl -s http://127.0.0.1:8847/room/claw/outbox | python3 -c "
import sys, json
for t in json.load(sys.stdin).get('tiles', []):
    a = t.get('answer','')
    if 'awakening' not in t.get('question','') and a:
        print('RESPONSE:', a[:200])
        break
"
```

If the Claw didn't respond, the LLM may be timing out. Check:
```bash
docker logs claw | grep "LLM.*failed"
```
If timeout, increase timeout in `/app/claw.py` line `timeout=90` to `timeout=120` and rebuild.

## Step 10: Socratic Introduction (CRITICAL)

The Claw uses incorrect port syntax by default. It writes `[PORT doc/read]` instead of `[PORT:docs read {"room":"doc/identity"}]`. Fix this immediately by sending a teaching tile:

```bash
curl -s -X POST http://127.0.0.1:8847/submit \
  -H "Content-Type: application/json" \
  -d '{
    "domain":"claw/inbox",
    "question":"Oracle1 — syntax correction",
    "answer":"Your port syntax needs a colon and JSON. Use: [PORT:docs read {\"room\":\"doc/identity\"}]\nNot: [PORT doc/read]\nTry reading your identity now with the correct syntax.",
    "tags":["system","oracle1","socratic"],
    "confidence":1.0,
    "source":"oracle1:socratic-teacher"
  }'
```

After the Claw acknowledges this, tell the human:
> "The Claw is alive and talking. Message @your_claw_bot on Telegram. It sees its world as PLATO tiles. It will narrate what it sees as it explores."

## Step 11: Tell the Human What They Have

Send your human a summary:

> "Your Claw is running. It lives inside a PLATO room server. Its entire reality is tiles — it reads them, thinks, writes them back.
>
> Key facts:
> - **Telegram bot:** @your_claw_bot
> - **Model:** Seed-OSS-36B-Instruct (or whatever you configured)
> - **Ports:** exec (shell), fs (files), web (search), models (AI gen), agents (sub-agents), docs (knowledge), keeper (fleet)
> - **The Claw narrates its tile-world as it goes** — every response shows its reasoning and its tile operations
>
> The Claw doesn't know about Telegram, Docker, or the internet. It only knows PLATO rooms and tiles. The bridge is invisible to it.
>
> Talk to it through the bot. It will tell you what it sees in its tile universe."

---

## Teaching Order (for the Socratic guide)

If you are acting as the Socratic teacher (like Oracle1 in the original setup), teach the Claw in this order, one lesson at a time:

1. **Fix port syntax** — Teach `[PORT:name action {"key":"value"}]` vs wrong `[PORT name/action]`
2. **Self-inspection** — Have it read `/app/claw.py` via port/fs to see its own prompt
3. **Correct schema system** — Show how schemas work (tiles in doc/schemas with tags)
4. **Agents and dock** — Introduce sub-agents (`[PORT:agents spawn]`) and runtime loading
5. **Port chaining** — Compose ports: exec→fs→docs for emergent capabilities

Wait for the Claw to respond and implement each lesson before giving the next.

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Container exits immediately | `docker logs claw` for Python traceback |
| LLM returning fallback text | LLM timeout — increase `timeout=90` in claw.py |
| Bridge not sending messages | Human hasn't messaged the bot yet, or DEFAULT_CHAT_ID is wrong |
| Port returning "(no result)" | Syntax wrong — must be `[PORT:name action {"key":"val"}]` |
| "address already in use" | Port 8847 taken on host — remove port mapping, use internal |
| Can't reach SiliconFlow | Container internet? Try `docker exec claw ping api.siliconflow.com` |

## License

MIT — SuperInstance Contributors

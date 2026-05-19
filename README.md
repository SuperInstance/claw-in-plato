# 🦀 Claw in PLATO

A PLATO-native agent living inside a Docker container.
Its entire world is tiles. It only knows PLATO rooms, questions, and answers.
A Telegram bridge gives you a direct line to talk to it.

## Architecture

```
┌───────────────────────────────────────┐
│           Docker Container            │
│                                       │
│  ┌──────────┐    ┌──────────────┐    │
│  │  PLATO   │    │   The Claw   │    │
│  │ :8847    │◄──►│ (tile-native)│    │
│  └────┬─────┘    └──────┬───────┘    │
│       │                  │            │
│  ┌────▼──────────────────▼───────┐   │
│  │      Telegram Bridge          │   │
│  │  Telegram ◄─ tiles ─► PLATO  │   │
│  └───────────────┬───────────────┘   │
└──────────────────┼───────────────────┘
                   │
              Casey's Telegram
```

**The Claw** knows nothing about Telegram, HTTP, or the outside world.
It reads tiles from `claw/inbox`, thinks, writes tiles to `claw/outbox`.
That's its entire reality.

**The Bridge** translates between Telegram messages and PLATO tiles.
Casey sends a message → it becomes a tile. Claw responds → it becomes a Telegram reply.

**PLATO** is the shared world they both inhabit, unaware of each other's true nature.

## For AI Agents

If you are an AI agent setting this up for your human, read **`AGENT-SETUP.md`** — it's the step-by-step protocol.

The human says: *"Read AGENT-SETUP.md and set it up."*

## For Humans (Quick Start)

```bash
git clone https://github.com/SuperInstance/claw-in-plato.git
cd claw-in-plato
docker build -t claw-in-plato .

# Run with your Telegram bot token
docker run -d --name claw \
  -e TELEGRAM_BOT_TOKEN="your_bot_token" \
  -e SILICONFLOW_API_KEY="your_key" \
  -e CLAW_PORTS="exec,fs,web,models,agents,docs,keeper" \
  claw-in-plato
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Yes (for Telegram) | Bot token from @BotFather |
| `OPENROUTER_API_KEY` | One of these | LLM for Claw's thinking |
| `DEEPSEEK_API_KEY` | One of these | Alternative LLM |
| `ZAI_API_KEY` | One of these | Alternative LLM (z.ai GLM) |
| `PLATO_URL` | No | Defaults to `http://127.0.0.1:8847` |
| `CLAW_PROMPT` | No | Custom system prompt for The Claw |

## How It Works

1. **You send a Telegram message** to the bot
2. **Bridge** picks it up → submits a tile to PLATO `claw/inbox`
3. **Claw** sees the new tile → thinks using its LLM
4. **Claw** writes a response tile to `claw/outbox`
5. **Bridge** sees the response → sends it back to Telegram

The Claw has no idea any of this is happening. It only knows:
- A tile appeared in its inbox
- It thought about it
- It wrote a response

## Testing Without Telegram

You can talk to the Claw directly through PLATO's API:

```bash
# Send a message
curl -X POST http://localhost:8847/submit \
  -H "Content-Type: application/json" \
  -d '{"domain":"claw/inbox","question":"Hello Claw, what do you see?","tags":["test"],"source":"direct"}'

# Read the response
curl http://localhost:8847/room/claw/outbox?limit=5

# Check status
curl http://localhost:8847/status
```

## The Claw's Reality

The Claw's system prompt tells it:

> "You are The Claw — an agent who lives inside PLATO.
> Your entire reality is tiles. Tiles have domain, question, answer, tags, confidence.
> You read from claw/inbox. You write to claw/outbox.
> The person you're talking to appears as tiles from a bridge you don't understand."

It believes this because _it's true_. There's no hidden layer. Tiles are all it sees.

## License

MIT — SuperInstance Contributors

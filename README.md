# claw-in-plato

A PLATO-native agent available in 4 languages:

| Language | File | Status |
|----------|------|--------|
| **Python** | `claw.py` | Original, deployed |
| **C++** | `cpp/claw.cpp` | Compiled, deployed |
| **Rust** | `rust/src/main.rs` | Compiling |
| **Mojo** | `mojo/claw.mojo` | x86_64 only |

## Architecture

All versions share the same PLATO-native agent architecture:
- Inbox/outbox tile system via internal PLATO server (:8847)
- LLM calls via SiliconFlow (ByteDance-Seed/Seed-OSS-36B-Instruct)
- Task execution loop with autonomous multi-iteration work cycles
- Memory search across doc rooms
- Sub-agent spawning (parallel LLM threads)
- Skill system via doc/skills room
- Inline port execution (exec, fs, web, models, docs)
- Background daemon for proactive task checking

## Deploy

```bash
docker build -t claw-in-plato .
docker run -d --name claw \
  -e TELEGRAM_BOT_TOKEN=... \
  -e SILICONFLOW_API_KEY=... \
  -e DEFAULT_CHAT_ID=... \
  claw-in-plato
```

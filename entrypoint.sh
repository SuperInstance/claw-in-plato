#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║     🦀 CLAW IN PLATO                    ║"
echo "║     PLATO-native agent in a box         ║"
echo "╚══════════════════════════════════════════╝"

# ── Start PLATO room server ───────────────────────────────────────────────
echo "[INIT] Starting PLATO room server on :8847..."
python3 /app/plato_server.py &
PLATO_PID=$!

# Wait for PLATO to be ready
for i in $(seq 1 10); do
  if curl -s http://127.0.0.1:8847/status > /dev/null 2>&1; then
    echo "[INIT] PLATO is ready"
    break
  fi
  sleep 1
done

# ── Configure defaults ────────────────────────────────────────────────────
export PLATO_URL="${PLATO_URL:-http://127.0.0.1:8847}"

# ── Start Telegram bridge (if token is set) ──────────────────────────────
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "[INIT] Starting Telegram bridge..."
  python3 /app/telegram_bridge.py &
  BRIDGE_PID=$!
  echo "[INIT] Telegram bridge PID: $BRIDGE_PID"
else
  echo "[INIT] No TELEGRAM_BOT_TOKEN — Telegram bridge disabled"
  echo "[INIT] Set TELEGRAM_BOT_TOKEN to enable Telegram ↔ PLATO bridge"
fi

# ── Start Claw agent ─────────────────────────────────────────────────────
if [ -n "$OPENROUTER_API_KEY" ] || [ -n "$DEEPSEEK_API_KEY" ] || [ -n "$ZAI_API_KEY" ] || [ -n "$SILICONFLOW_API_KEY" ]; then
  echo "[INIT] Starting Claw agent..."
  python3 -u /app/claw.py &
  CLAW_PID=$!
  echo "[INIT] Claw PID: $CLAW_PID"
else
  echo "[INIT] WARNING: No LLM API keys configured!"
  echo "[INIT] Set OPENROUTER_API_KEY, DEEPSEEK_API_KEY, SILICONFLOW_API_KEY, or ZAI_API_KEY"
  echo "[INIT] Starting Claw agent anyway (will poll but can't respond)..."
  python3 -u /app/claw.py &
  CLAW_PID=$!
fi

# ── Health check endpoint ─────────────────────────────────────────────────
echo "[INIT] All systems running"
echo ""
echo "  PLATO      : http://localhost:8847"
echo "  Telegram   : $([ -n "$TELEGRAM_BOT_TOKEN" ] && echo 'ACTIVE' || echo 'DISABLED')"
echo "  LLM        : $([ -n "$OPENROUTER_API_KEY" ] || [ -n "$DEEPSEEK_API_KEY" ] || [ -n "$ZAI_API_KEY" ] && echo 'CONFIGURED' || echo 'MISSING')"
echo "  Claw       : RUNNING (PID $CLAW_PID)"
echo ""

# Trap to gracefully shut down
trap "echo '[INIT] Shutting down...'; kill $PLATO_PID $CLAW_PID ${BRIDGE_PID:-} 2>/dev/null; exit 0" SIGTERM SIGINT

# Keep container alive
wait

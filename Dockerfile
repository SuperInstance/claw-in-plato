# Claw in PLATO — minimal container
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application
COPY plato_server.py .
COPY claw.py .
COPY telegram_bridge.py .
COPY entrypoint.sh .
COPY ports/ ./ports/
RUN chmod +x entrypoint.sh

# Expose PLATO port
EXPOSE 8847

# Health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -sf http://127.0.0.1:8847/status || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

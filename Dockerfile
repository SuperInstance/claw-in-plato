# claw-in-plato — Multi-stage build
FROM rust:latest AS rust-builder
WORKDIR /build
COPY rust/ .
RUN cargo build --release

FROM python:3.11-slim AS base
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY --from=rust-builder /build/target/release/claw /app/claw
COPY claw.py entrypoint.sh plato_server.py telegram_bridge.py /app/
COPY ports/ /app/ports/
ENV PLATO_URL=http://127.0.0.1:8847
ENV CLAW_PORTS=exec,fs,web,models,agents,docs,keeper
EXPOSE 8847
CMD ["/app/claw"]

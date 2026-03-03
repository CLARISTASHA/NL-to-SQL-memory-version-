#!/bin/bash
# start.sh — Container entrypoint
# Starts Ollama in background, waits for it, pulls model, then starts FastAPI

set -e

LOG_DIR="/var/log/task_report_agent"
mkdir -p "$LOG_DIR"

echo "[start.sh] Starting Ollama service..."
ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
OLLAMA_PID=$!

# FIX: Check Ollama actually started — exit if it died immediately
sleep 2
if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    echo "[start.sh] ERROR: Ollama failed to start. Check $LOG_DIR/ollama.log"
    exit 1
fi

# Wait for Ollama API to be ready (up to 60 seconds)
echo "[start.sh] Waiting for Ollama to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "[start.sh] Ollama is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "[start.sh] ERROR: Ollama did not become ready in 60s. Exiting."
        exit 1
    fi
    echo "[start.sh] Attempt $i/30 — waiting..."
    sleep 2
done

# Pull phi3:mini only if not already present (avoids re-downloading on restart)
if ollama list | grep -q "phi3:mini"; then
    echo "[start.sh] phi3:mini already present, skipping pull."
else
    echo "[start.sh] Pulling phi3:mini model (this may take a few minutes)..."
    ollama pull phi3:mini
fi

echo "[start.sh] Starting FastAPI application..."
exec uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1

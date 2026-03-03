# ── Base image ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install system dependencies
# libmysqlclient-dev needed for mysql-connector-python to build
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gcc \
    g++ \
    pkg-config \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Install Ollama ─────────────────────────────────────────────────────────────
RUN curl -fsSL https://ollama.com/install.sh | sh

# ── Create log directory before app starts ─────────────────────────────────────
# FIX: app.py creates this at runtime but root ownership causes crash
# Creating it here with correct permissions avoids PermissionError
RUN mkdir -p /var/log/task_report_agent

# ── Set working directory ──────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application files ─────────────────────────────────────────────────────
COPY task_report_agent.py .
COPY app.py .
COPY schema_index/ ./schema_index/

# ── Copy startup script ────────────────────────────────────────────────────────
COPY start.sh /start.sh
RUN chmod +x /start.sh

# ── Expose FastAPI port ────────────────────────────────────────────────────────
EXPOSE 8000

# ── Health check ───────────────────────────────────────────────────────────────
# FIX: start_period increased to 120s — phi3:mini pull takes ~2 min on first run
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# ── Entrypoint ─────────────────────────────────────────────────────────────────
CMD ["/start.sh"]

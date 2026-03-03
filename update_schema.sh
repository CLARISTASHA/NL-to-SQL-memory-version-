#!/bin/bash
# update_schema.sh
# Run this whenever the database schema changes.
# Re-extracts schema and rebuilds the FAISS vector store.
#
# Usage (run from /opt/task_report_agent):
#   chmod +x scripts/update_schema.sh
#   ./scripts/update_schema.sh

set -e

APP_DIR="/opt/task_report_agent"

# FIX: Create logs dir if it doesn't exist yet
mkdir -p "$APP_DIR/logs"
LOG="$APP_DIR/logs/schema_update.log"

echo "================================================"
echo "Schema Update — $(date)"
echo "================================================"

# FIX: Check we are in the right directory
if [ ! -f "$APP_DIR/extract_schema.py" ]; then
    echo "ERROR: extract_schema.py not found in $APP_DIR"
    echo "Make sure you are running this from the correct server."
    exit 1
fi

cd "$APP_DIR"

# FIX: Check venv exists before activating
if [ ! -f "venv/bin/activate" ]; then
    echo "ERROR: Virtual environment not found at $APP_DIR/venv"
    echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source venv/bin/activate

# Step 1: Re-extract schema from database
echo "[1/3] Extracting schema from database..."
python extract_schema.py 2>&1 | tee -a "$LOG"
echo "      Done. schema_final.txt updated."

# Step 2: Rebuild vector store
echo "[2/3] Rebuilding FAISS vector store..."
python build_schema_vectorstore.py 2>&1 | tee -a "$LOG"
echo "      Done. schema_index/ updated."

# Step 3: Restart the FastAPI service
echo "[3/3] Restarting task-report-agent service..."
sudo systemctl restart task-report-agent
sleep 3

# Verify it came back up
if systemctl is-active --quiet task-report-agent; then
    echo "      Service restarted successfully."
else
    echo "      ERROR: Service failed to restart."
    echo "      Run: journalctl -u task-report-agent -n 50"
    exit 1
fi

echo ""
echo "Schema update complete at $(date)"
echo "================================================"

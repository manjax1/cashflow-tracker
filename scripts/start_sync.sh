#!/bin/bash
PROJECT_DIR="/Users/manjax/Documents/Code/AI/spending-tracker"
LOG_FILE="$PROJECT_DIR/logs/sync.log"
ERROR_FILE="$PROJECT_DIR/logs/sync_error.log"
PYTHON="$PROJECT_DIR/.venv/bin/python"

mkdir -p "$PROJECT_DIR/logs"

echo "========================================" >> "$LOG_FILE"
echo "Spending Tracker Sync - $(date)" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

cd "$PROJECT_DIR"
"$PYTHON" src/main.py >> "$LOG_FILE" 2>> "$ERROR_FILE"

EXIT_CODE=$?
echo "Sync completed at $(date) with exit code $EXIT_CODE" >> "$LOG_FILE"
exit $EXIT_CODE

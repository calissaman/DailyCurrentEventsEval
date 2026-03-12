#!/bin/bash

PROJ_DIR="/home/cal/current-affairs-eval"
VENV_PYTHON="/home/cal/.venv/evalproj/bin/python3"
LOG_DIR="$PROJ_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

echo "=== $(date) ===" >> "$LOG"
cd "$PROJ_DIR"

# Scrape and generate questions (haiku is the first to evaluate too)
if "$VENV_PYTHON" eval.py --model haiku >> "$LOG" 2>&1; then
    echo "haiku: ok" >> "$LOG"
else
    echo "haiku: FAILED (exit $?)" >> "$LOG"
fi

# Sonnet and opus reuse today's questions — run independently so one failure doesn't block the other
if "$VENV_PYTHON" eval.py --model sonnet --eval-only >> "$LOG" 2>&1; then
    echo "sonnet: ok" >> "$LOG"
else
    echo "sonnet: FAILED (exit $?)" >> "$LOG"
fi

if "$VENV_PYTHON" eval.py --model opus --eval-only >> "$LOG" 2>&1; then
    echo "opus: ok" >> "$LOG"
else
    echo "opus: FAILED (exit $?)" >> "$LOG"
fi

echo "=== done $(date) ===" >> "$LOG"

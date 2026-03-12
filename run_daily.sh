#!/bin/bash
set -e

PROJ_DIR="/home/cal/current-affairs-eval"
VENV_PYTHON="/home/cal/.venv/evalproj/bin/python3"
LOG_DIR="$PROJ_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

echo "=== $(date) ===" >> "$LOG"
cd "$PROJ_DIR"
"$VENV_PYTHON" eval.py --model haiku >> "$LOG" 2>&1
"$VENV_PYTHON" eval.py --model sonnet --eval-only >> "$LOG" 2>&1
"$VENV_PYTHON" eval.py --model opus --eval-only >> "$LOG" 2>&1
echo "=== done $(date) ===" >> "$LOG"

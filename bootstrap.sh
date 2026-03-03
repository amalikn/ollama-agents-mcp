#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/venv"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$ROOT_DIR"

echo "Bootstrap complete"
echo "Run with: $VENV_DIR/bin/python $ROOT_DIR/run_server.py"

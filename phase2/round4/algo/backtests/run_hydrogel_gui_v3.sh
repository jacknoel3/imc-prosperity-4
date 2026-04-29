#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
TRADER_PATH="${TRADER_PATH:-$ROOT/phase2/round4/algo/strategy/hydrogel_gui_v3.py}"
BACKTESTER_DIR="$ROOT/forks/prosperity_rust_backtester"
DATASET_DIR="${DATASET_DIR:-$ROOT/phase2/round4/algo/data}"

ARGS=(
  --trader "$TRADER_PATH"
  --dataset "$DATASET_DIR"
  --products full
  --calibration round4-oos
  --artifact-mode diagnostic
)

if command -v rust_backtester >/dev/null 2>&1; then
  rust_backtester "${ARGS[@]}" "$@"
elif command -v cargo >/dev/null 2>&1; then
  cd "$BACKTESTER_DIR"
  ./scripts/cargo_local.sh run -- "${ARGS[@]}" "$@"
else
  echo "Neither rust_backtester nor cargo is available. Install one to run this helper." >&2
  exit 127
fi

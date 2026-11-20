#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-$(command -v python3)}"
for d in store library embed discovery media_sync; do
  echo "=== $d ==="
  "$PY" -m unittest discover -s "$d" -q
done
echo "=== pipeline syntax ==="
if command -v zsh >/dev/null 2>&1; then
  zsh -n pipeline/m2-nightly.sh
  zsh -n pipeline/m2-place.sh
  zsh -n pipeline/m5-sync.sh
fi
"$PY" -m py_compile pipeline/check.py discovery/itunes.py
echo "ALL PUBLIC TESTS PASSED"

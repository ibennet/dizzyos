#!/bin/bash
# The green gate — run before pushing; CI runs exactly this (see
# .github/workflows/check.yml). Creates/uses the repo venv, byte-compiles
# everything, then runs the no-hardware smoke test (dev/smoke.py).

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-.venv/bin/python}
if [ ! -x "$PYTHON" ]; then
  python3 -m venv .venv
  PYTHON=.venv/bin/python
fi
"$PYTHON" -m pip install -q -r requirements.txt

echo "==> byte-compiling"
"$PYTHON" -m compileall -q kernel apps run.py tools/pi/dizzyos-update

echo "==> smoke test"
"$PYTHON" dev/smoke.py

echo "==> check.sh: green"

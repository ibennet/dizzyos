#!/usr/bin/env bash
# Preview dizzyos on this machine (Mac emulator). No hardware needed.
#
#   ./preview.sh          Live browser preview at http://localhost:8888 (Ctrl-C to stop)
#   ./preview.sh png      Render one static frame to a PNG and open it
#   ./preview.sh <flags>  Any other args pass through to run.py (e.g. --led-chain=2)
#
# Creates the virtualenv and installs deps on first run.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: creating .venv and installing deps..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi
PY=./.venv/bin/python

if [ "${1:-}" = "png" ]; then
  "$PY" run.py --dump-frames frames/ --frames 1 --duration 0
  "$PY" -c "from PIL import Image; Image.open('frames/frame_000.png').resize((768,384), Image.NEAREST).save('frames/preview.png')"
  echo "wrote frames/preview.png (6x upscale of the 128x64 panel)"
  command -v open >/dev/null 2>&1 && open frames/preview.png || true
else
  echo "Starting emulator -> http://localhost:8888  (Ctrl-C to stop)"
  exec "$PY" run.py "$@"
fi

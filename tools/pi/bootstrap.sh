#!/bin/bash
# dizzyos platform bootstrap — runs once, on the first boot *with network*
# (installed + enabled by firstrun.sh, disables itself when done).
#
# This is the "platform" half of the platform/app split: apt packages, the
# compiled rpi-rgb-led-matrix Python bindings, the venv, and the systemd
# units. It changes rarely and is installed once. The "app" half (kernel/,
# apps/, fonts/) comes from GitHub release tarballs and self-updates via
# dizzyos-update on a timer.

set -euo pipefail
exec > /var/log/dizzyos-bootstrap.log 2>&1
echo "dizzyos bootstrap: $(date)"

ROOT=/opt/dizzyos
PAYLOAD=$ROOT/payload

# Wait for actual connectivity — network-online.target can be satisfied
# before WiFi has an address.
for i in $(seq 1 60); do
  if curl -fsI --max-time 5 https://github.com >/dev/null 2>&1; then break; fi
  echo "waiting for network ($i)"; sleep 5
done

# --- packages ---------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  git python3-venv python3-dev build-essential curl ca-certificates

# --- venv (shared across releases; platform-managed) ------------------------
[ -d "$ROOT/venv" ] || python3 -m venv "$ROOT/venv"
# Cython is a build-time requirement of the matrix bindings below.
"$ROOT/venv/bin/pip" install --upgrade pip wheel Cython

# --- compiled matrix driver (the reason releases carry no artifacts:
#     the only compiled piece is built right here, once) ---------------------
if ! "$ROOT/venv/bin/python" -c 'import rgbmatrix' 2>/dev/null; then
  SRC=$ROOT/rpi-rgb-led-matrix
  [ -d "$SRC" ] || git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix "$SRC"
  # librgbmatrix first, then the bindings against it. build-python/
  # install-python are targets of bindings/python/Makefile — the repo root
  # Makefile has no such target.
  make -C "$SRC/lib"
  make -C "$SRC/bindings/python" build-python PYTHON="$ROOT/venv/bin/python"
  make -C "$SRC/bindings/python" install-python PYTHON="$ROOT/venv/bin/python"
fi

# --- first dizzyos release --------------------------------------------------
"$PAYLOAD/dizzyos-update" --install

# User config lives OUTSIDE the release dir so updates never clobber it.
if [ ! -f /etc/dizzyos/config.yaml ]; then
  cp "$ROOT/current/config.yaml" /etc/dizzyos/config.yaml
fi

# --- services ---------------------------------------------------------------
cp "$PAYLOAD/dizzyos.service" "$PAYLOAD/dizzyos-update.service" \
   "$PAYLOAD/dizzyos-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dizzyos-update.timer
systemctl enable --now dizzyos

touch /etc/dizzyos/provisioned
systemctl disable dizzyos-bootstrap.service
echo "dizzyos bootstrap: done"

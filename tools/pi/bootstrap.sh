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
  PIL_INC=$ROOT/.pillow-headers
  [ -d "$SRC" ] || git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix "$SRC"

  # Upstream builds via scikit-build-core (pyproject at the repo root — there
  # is no build-python make target), and unconditionally compiles a Pillow
  # shim that needs Imaging.h. Those headers exist only in Pillow's source
  # tree, never in the wheel, so fetch them for the installed version.
  "$ROOT/venv/bin/pip" install --upgrade Pillow
  if [ ! -f "$PIL_INC/Imaging.h" ]; then
    VER=$("$ROOT/venv/bin/python" -c 'import PIL; print(PIL.__version__)')
    rm -rf /tmp/pilsrc "$PIL_INC"; mkdir -p /tmp/pilsrc "$PIL_INC"
    curl -fsSL "https://github.com/python-pillow/Pillow/archive/refs/tags/${VER}.tar.gz" \
      -o /tmp/pilsrc/pillow.tar.gz
    tar xf /tmp/pilsrc/pillow.tar.gz -C /tmp/pilsrc
    cp /tmp/pilsrc/Pillow-*/src/libImaging/*.h "$PIL_INC/"
  fi

  CMAKE_ARGS="-DCMAKE_C_FLAGS=-I$PIL_INC -DCMAKE_CXX_FLAGS=-I$PIL_INC" \
    "$ROOT/venv/bin/pip" install --no-cache-dir "$SRC"
fi

# rpi-rgb-led-matrix refuses to start while snd_bcm2835 is loaded — it drives
# the same PWM peripheral. dtparam=audio=off (set by flash.sh) stops the
# device-tree node but NOT the module, so blacklist it explicitly. Takes
# effect on the next boot.
cat > /etc/modprobe.d/dizzyos-no-snd.conf <<'EOF'
# dizzyos: the HUB75 driver and the Pi's onboard sound share the PWM
# peripheral; the driver exits at startup if this module is present.
blacklist snd_bcm2835
EOF

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

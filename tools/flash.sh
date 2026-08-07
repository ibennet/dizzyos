#!/bin/bash
# Flash an SD card with everything a dizzyos sign needs — one command, no
# monitor or keyboard ever attached to the Pi.
#
#   tools/flash.sh                        # interactive: picks disk, asks for WiFi
#   tools/flash.sh --device disk6 --ssid Home --hostname lobby-sign
#
# What it does:
#   1. Downloads (and caches + SHA256-verifies) the latest Raspberry Pi OS
#      Lite arm64 image.
#   2. Writes it to the SD card — with guardrails: internal disks are refused
#      outright and you must retype the device and its size to confirm.
#   3. Injects onto the boot partition:
#        - hardware config for HUB75 panels (audio OFF — it shares the PWM
#          peripheral with the matrix; gpu_mem=16; bluetooth off; isolcpus=3)
#        - WiFi credentials, SSH key, hostname, user (via firstrun.sh)
#        - the dizzyos bootstrap payload (tools/pi/) that installs the matrix
#          driver and the latest dizzyos release on first boot
#
# First boot takes ~10 minutes (one reboot + driver compile), then the sign
# starts on its own and is reachable at http://<hostname>.local:8080.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CACHE_DIR="${DIZZYOS_FLASH_CACHE:-$HOME/Library/Caches/dizzyos-flash}"
IMAGE_URL_LATEST="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"

# ----------------------------------------------------------------------------
# defaults / args
DEVICE=""
SSID=""
WIFI_PASS=""
WIFI_COUNTRY="US"
PI_HOSTNAME="dizzyos"
PI_USER="dizzy"
PI_PASS=""
SSH_KEY_FILE=""
IMAGE_FILE=""            # --image to skip the download and use a local .img/.img.xz
REPO="${DIZZYOS_REPO:-ibennet/dizzyos}"
REF=""                   # --ref <branch>: install that branch instead of the
                         # latest release — for testing unmerged work on hardware

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --device)    DEVICE="$2"; shift 2 ;;
    --ssid)      SSID="$2"; shift 2 ;;
    --wifi-pass) WIFI_PASS="$2"; shift 2 ;;
    --country)   WIFI_COUNTRY="$2"; shift 2 ;;
    --hostname)  PI_HOSTNAME="$2"; shift 2 ;;
    --user)      PI_USER="$2"; shift 2 ;;
    --password)  PI_PASS="$2"; shift 2 ;;
    --ssh-key)   SSH_KEY_FILE="$2"; shift 2 ;;
    --image)     IMAGE_FILE="$2"; shift 2 ;;
    --repo)      REPO="$2"; shift 2 ;;
    --ref)       REF="$2"; shift 2 ;;
    -h|--help)   usage ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

die()  { echo "error: $*" >&2; exit 1; }
note() { printf '\033[1m==>\033[0m %s\n' "$*"; }

[ "$(uname)" = "Darwin" ] || die "this script drives macOS diskutil; run it on a Mac"
command -v xz >/dev/null || die "xz is required to unpack the image: brew install xz"

# ----------------------------------------------------------------------------
# gather inputs up front so the flash itself runs unattended
if [ -z "$SSID" ]; then
  # SSIDs are case- and punctuation-sensitive, and a typo here costs a full
  # boot-and-wonder-why cycle (ask us how we know). macOS redacts the
  # current SSID from every CLI, so the best we can do is point at the UI.
  echo "SSID is CASE-SENSITIVE — check the exact spelling in your WiFi menu bar."
  read -r -p "WiFi network name (SSID): " SSID
  [ -n "$SSID" ] || die "an SSID is required for a headless setup"
fi
if [ -z "$WIFI_PASS" ]; then
  read -r -s -p "WiFi password for '$SSID': " WIFI_PASS; echo
fi

# SSH public key: explicit flag, else the usual suspects.
if [ -z "$SSH_KEY_FILE" ]; then
  for k in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub; do
    [ -f "$k" ] && SSH_KEY_FILE="$k" && break
  done
fi
[ -n "$SSH_KEY_FILE" ] && [ -f "$SSH_KEY_FILE" ] || die "no SSH public key found; pass --ssh-key <path>"
SSH_PUBKEY="$(cat "$SSH_KEY_FILE")"

# Login password (SSH is key-only in practice, but the console needs one).
if [ -z "$PI_PASS" ]; then
  read -r -s -p "login password for user '$PI_USER' (blank = random): " PI_PASS; echo
  if [ -z "$PI_PASS" ]; then
    # No pipeline here: under pipefail, `urandom | head` dies of SIGPIPE.
    PI_PASS="$(openssl rand -hex 8)"
    note "generated console password for '$PI_USER': $PI_PASS  (SSH uses your key)"
  fi
fi
PASS_HASH="$(openssl passwd -6 "$PI_PASS")"

# ----------------------------------------------------------------------------
# image: download the latest Lite arm64 (cached), verify its SHA256
if [ -z "$IMAGE_FILE" ]; then
  note "resolving latest Raspberry Pi OS Lite (arm64) image"
  FINAL_URL="$(curl -sILo /dev/null -w '%{url_effective}' "$IMAGE_URL_LATEST")"
  [ -n "$FINAL_URL" ] || die "could not resolve $IMAGE_URL_LATEST"
  IMAGE_FILE="$CACHE_DIR/$(basename "$FINAL_URL")"
  mkdir -p "$CACHE_DIR"
  if [ ! -f "$IMAGE_FILE" ]; then
    note "downloading $(basename "$FINAL_URL")"
    curl -fL --progress-bar -o "$IMAGE_FILE.part" "$FINAL_URL"
    mv "$IMAGE_FILE.part" "$IMAGE_FILE"
  else
    note "using cached image: $IMAGE_FILE"
  fi
  note "verifying SHA256"
  WANT="$(curl -fsL "$FINAL_URL.sha256" | awk '{print $1}')"
  GOT="$(shasum -a 256 "$IMAGE_FILE" | awk '{print $1}')"
  [ "$WANT" = "$GOT" ] || { rm -f "$IMAGE_FILE"; die "SHA256 mismatch — cached image deleted, re-run to re-download"; }
fi
[ -f "$IMAGE_FILE" ] || die "image not found: $IMAGE_FILE"

# ----------------------------------------------------------------------------
# device: pick + guardrails. A dd typo here overwrites a Mac disk, so we
# refuse internal disks and make you retype what you're about to erase.
if [ -z "$DEVICE" ]; then
  # A Mac's built-in SD slot reports as *internal* removable media, so
  # `diskutil list external` would hide it — enumerate removable disks.
  note "removable disks:"
  for d in $(diskutil list -plist physical | plutil -extract WholeDisks json -o - - | tr -d '[]"' | tr ',' ' '); do
    if [ "$(diskutil info -plist "$d" | plutil -extract RemovableMediaOrExternalDevice raw -o - - 2>/dev/null)" = "true" ]; then
      diskutil list "$d"
    fi
  done
  read -r -p "device to flash (e.g. disk6): " DEVICE
fi
DEVICE="${DEVICE#/dev/}"
[[ "$DEVICE" =~ ^disk[0-9]+$ ]] || die "expected a whole-disk identifier like disk6, got '$DEVICE'"

PLIST="$(diskutil info -plist "$DEVICE")" || die "no such disk: $DEVICE"
pval() { echo "$PLIST" | plutil -extract "$1" raw -o - - 2>/dev/null; }

# Removable-or-external is the predicate that admits SD cards in both the
# built-in reader (which macOS calls Internal) and USB readers, while
# refusing fixed disks. Belt and braces: never the disk backing "/".
[ "$(pval RemovableMediaOrExternalDevice)" = "true" ] || \
  die "refusing: $DEVICE is not removable media / an external device"
BOOT_DISK="$(diskutil info -plist / | plutil -extract ParentWholeDisk raw -o - -)"
[ "$DEVICE" != "$BOOT_DISK" ] || die "refusing: $DEVICE is the boot disk"
[ "$(pval VirtualOrPhysical)" != "Virtual" ] || die "refusing: $DEVICE is a virtual disk"
SIZE_BYTES="$(pval TotalSize)"
SIZE_GB=$(( (SIZE_BYTES + 500000000) / 1000000000 ))
MEDIA_NAME="$(pval MediaName)"
[ "$SIZE_BYTES" -le 2000000000000 ] || die "refusing: $DEVICE is ${SIZE_GB}GB — that is not an SD card"

echo
echo "  about to ERASE:  /dev/$DEVICE"
echo "  media:           ${MEDIA_NAME:-unknown}"
echo "  size:            ${SIZE_GB} GB"
echo
read -r -p "type the device again to confirm: " CONFIRM_DEV
[ "$CONFIRM_DEV" = "$DEVICE" ] || die "device mismatch — aborting"
read -r -p "type its size in GB to confirm: " CONFIRM_GB
[ "$CONFIRM_GB" = "$SIZE_GB" ] || die "size mismatch — aborting"

# ----------------------------------------------------------------------------
# write
note "unmounting /dev/$DEVICE"
diskutil unmountDisk "/dev/$DEVICE"

note "writing image (this is the slow part — a few minutes)"
case "$IMAGE_FILE" in
  *.xz) xz -dc "$IMAGE_FILE" | sudo dd of="/dev/r$DEVICE" bs=4m status=progress ;;
  *)    sudo dd if="$IMAGE_FILE" of="/dev/r$DEVICE" bs=4m status=progress ;;
esac
sync

note "remounting boot partition"
diskutil mountDisk "/dev/$DEVICE" >/dev/null
BOOT=""
for _ in $(seq 1 10); do
  for cand in /Volumes/bootfs /Volumes/boot; do
    [ -f "$cand/config.txt" ] && BOOT="$cand" && break 2
  done
  sleep 1
done
[ -n "$BOOT" ] || die "boot partition did not mount — is the card OK?"

# ----------------------------------------------------------------------------
# inject: hardware config
note "writing HUB75 hardware config"
# The stock image ships dtparam=audio=on; onboard audio and the matrix driver
# share the PWM peripheral, and leaving it on is the #1 cause of flicker.
sed -i '' 's/^dtparam=audio=on/dtparam=audio=off/' "$BOOT/config.txt"
cat >> "$BOOT/config.txt" <<'EOF'

# --- dizzyos (written by tools/flash.sh) ------------------------------------
dtparam=audio=off        # audio shares the PWM peripheral with the HUB75 driver
gpu_mem=16               # headless sign; keep the RAM for the CPU
dtoverlay=disable-bt     # provisioning is over LAN; a quiet radio = less jitter
EOF

# cmdline.txt is a single line. Add: a dedicated core for the matrix refresh
# thread, the WiFi regulatory domain (what rpi-imager does), and the one-shot
# firstrun hook (removed by firstrun.sh itself once it has run).
CMDLINE="$(cat "$BOOT/cmdline.txt")"
printf '%s isolcpus=3 cfg80211.ieee80211_regdom=%s systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n' \
  "$CMDLINE" "$WIFI_COUNTRY" > "$BOOT/cmdline.txt"

# inject: cloud-init network config (Trixie images provision networking via
# a cloud-init seed on this partition; the NM keyfile firstrun writes is
# ignored by that regime, so WiFi must ALSO be declared here — learned the
# hard way on a Pi 3B+ that booted, provisioned, and never joined WiFi).
if [ -f "$BOOT/meta-data" ]; then
  note "writing cloud-init network-config (Trixie+)"
  yesc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  cat > "$BOOT/network-config" <<EOF
# Written by dizzyos tools/flash.sh — netplan v2 consumed by cloud-init on
# first boot. The wifi here is the same network firstrun.sh writes for
# NetworkManager (that path covers pre-cloud-init images).
network:
  version: 2
  wifis:
    wlan0:
      dhcp4: true
      optional: true
      regulatory-domain: $WIFI_COUNTRY
      access-points:
        "$(yesc "$SSID")":
          password: "$(yesc "$WIFI_PASS")"
EOF
fi

# inject: first-boot provisioning + the dizzyos payload
note "writing firstrun.sh + payload"
sed -e "s|{{HOSTNAME}}|$PI_HOSTNAME|g" \
    -e "s|{{USERNAME}}|$PI_USER|g" \
    -e "s|{{PASS_HASH}}|$(printf '%s' "$PASS_HASH" | sed 's/[&|]/\\&/g')|g" \
    -e "s|{{SSID}}|$SSID|g" \
    -e "s|{{PSK}}|$(printf '%s' "$WIFI_PASS" | sed 's/[&|]/\\&/g')|g" \
    -e "s|{{SSH_PUBKEY}}|$SSH_PUBKEY|g" \
    -e "s|{{REPO}}|$REPO|g" \
    -e "s|{{REF}}|$REF|g" \
    "$HERE/pi/firstrun.sh.tmpl" > "$BOOT/firstrun.sh"
chmod +x "$BOOT/firstrun.sh"

mkdir -p "$BOOT/dizzyos"
cp "$HERE/pi/bootstrap.sh" "$HERE/pi/dizzyos-update" "$BOOT/dizzyos/"
cp "$HERE"/pi/systemd/*.service "$HERE"/pi/systemd/*.timer "$BOOT/dizzyos/"

# ----------------------------------------------------------------------------
note "ejecting"
diskutil eject "/dev/$DEVICE"

cat <<EOF

done. next steps:
  1. put the card in the Pi and power it on
  2. first boot: provisions itself, reboots, compiles the matrix driver,
     installs the latest dizzyos release — allow ~10 minutes
  3. the sign starts on its own; settings UI: http://$PI_HOSTNAME.local:8080
     ssh: ssh $PI_USER@$PI_HOSTNAME.local
EOF

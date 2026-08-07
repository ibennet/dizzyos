# 0002 — Flashing, headless provisioning, and self-updating releases

**Status:** Accepted (2026-08)

## Context

Getting dizzyos onto a sign used to be a manual afternoon: flash Raspberry Pi OS by
hand, attach a keyboard and monitor, join WiFi, run the Adafruit driver installer,
clone the repo, write a systemd unit. Every step was a place to get it subtly wrong,
and none of it was written down anywhere but the README.

The constraints that shape the answer:

- **The sign has no keyboard, no monitor, and no browser.** It has a 128×64 LED
  matrix, WiFi, and whatever we bake onto the SD card.
- **But it does have a display.** Anything the sign needs to *tell* a nearby human —
  a code, a status — it can simply draw.
- **The matrix driver is compiled C.** `hzeller/rpi-rgb-led-matrix` plus its Python
  bindings takes minutes to build on a Pi and pins to the kernel/hardware. Everything
  above it (`kernel/`, `apps/`, `fonts/`, `config.yaml`) is pure Python.
- **Signs get put on a shelf and forgotten.** An update path that requires SSHing in
  is an update path that doesn't happen.
- Solo project, a handful of signs, all on networks I control.

## Decision

### (a) Flashing is one fully scripted command

`tools/flash.sh` takes a blank SD card to a sign that boots, joins WiFi, and starts
running — no monitor or keyboard ever attached. It downloads and SHA256-verifies the
latest Raspberry Pi OS Lite arm64 image (cached in `~/Library/Caches`), writes it, and
injects onto the boot partition: HUB75 hardware config (onboard audio off — it shares
the PWM peripheral with the matrix; `gpu_mem=16`; Bluetooth off; `isolcpus=3`), the
first-boot provisioning script, and the bootstrap payload from `tools/pi/`.

Because a `dd` typo overwrites a Mac disk, the guardrails are part of the decision, not
polish: internal disks are refused outright, and you retype both the device node and
its size to confirm.

### (b) Provisioning is headless; the settings page is LAN-only and authorized by the sign itself

Credentials are baked at flash time — hostname, user, SSH public key, and a
NetworkManager WiFi profile — so the first boot is unattended. To *change* things later
(rejoin WiFi, edit config) the sign serves a settings page on the LAN at
`http://<hostname>.local:8080`.

Authorization is **physical presence**: opening the page makes the sign display a
one-time PIN for 30 seconds. You type what you can see on the sign. That is the whole
auth model, and it's a good fit — the threat isn't a remote attacker (the port is
LAN-only), it's a housemate on the same WiFi. Being in the room is exactly the
credential we want to check.

**Rejected: Bluetooth LE provisioning.** The obvious "phone talks to the sign" answer,
and the one commercial devices use. But it requires a native app, because iOS Safari
has no Web Bluetooth and never has — that alone kills it for a project with no app.
Worse, the Pi Zero 2 W shares one radio between BLE and WiFi, so the provisioning
channel contends with the thing being provisioned. We turn Bluetooth off in
`config.txt` for exactly this reason.

**Rejected: WiFi AP + captive portal.** The other standard answer: the sign brings up
its own access point, you join it, a captive portal takes your credentials. It's the
only option that works with *no* existing network — but it means juggling the radio
between AP and client mode, running hostapd/dnsmasq, and detecting the "am I
configured yet" state, all so a human can read a form. The sign's own display makes
all of that unnecessary: it can show a PIN, so LAN plus PIN gets us the same physical-
presence guarantee with a fraction of the moving parts. If a sign ever has to be set up
on a network it has never seen, this is the thing to revisit.

### (c) Split the platform from the app; only the app self-updates

Two layers with two lifecycles:

- **Platform** — `tools/pi/bootstrap.sh`, run once on first boot: apt dependencies,
  compiles the matrix driver into `/opt/dizzyos/venv`, installs the first release, and
  enables the systemd units. Slow, compiled, rarely changes.
- **App** — everything pure-Python, updated by `tools/pi/dizzyos-update` on a nightly
  timer (04:00, jittered).

The updater fetches the **auto-generated GitHub source tarball for the tag**. This is
the deliberate part: because the app layer needs no build step, releases store **zero
artifacts**. There is nothing to build, nothing to publish, nothing to keep in sync —
the tag *is* the release, and `git` is already storing it.

Deploys are an **atomic symlink flip**: unpack to `/opt/dizzyos/releases/<tag>/`, point
`/opt/dizzyos/current` at it, restart. Health is proven, not assumed — the launcher
touches a heartbeat file every frame (`DIZZYOS_HEARTBEAT`, in a systemd
`RuntimeDirectory`), and the updater waits for it after the flip. No heartbeat means the
new release doesn't render, so it flips `current` back, marks the tag `.bad` so the
timer won't retry it, and the sign keeps running the version it was running. Old
releases are pruned.

User configuration lives in **`/etc/dizzyos/config.yaml`**, outside the release tree, so
it survives every update. `/etc/dizzyos/update.conf` names the repo to track.

### (d) A/B root partitions are deferred, not dismissed

The industrial answer to "update an appliance safely" is two root partitions plus a
bootloader that can fall back — **RAUC** or **SWUpdate**. That makes the *whole OS*
atomic, kernel and driver included, and survives a power cut mid-write.

We're not doing it. It requires a custom partition layout, a build pipeline producing
signed bundles, bootloader integration, and roughly doubles the SD footprint — real
infrastructure, in exchange for atomicity over a layer (OS + compiled driver) that we
change approximately never. The symlink flip already covers the layer that changes
nightly, with rollback. Tracked in issue #8 for when the tradeoff shifts.

## Consequences

- A blank SD card to a running sign is one command plus ~10 minutes of unattended
  first boot (provision → reboot → compile driver → install latest release → start).
- Signs update themselves and roll back on their own. Shipping is merging a
  release-please PR; nothing is pushed to any sign.
- Release infrastructure is nearly free — no artifact build, storage, or signing.
- The blast radius of a bad release is bounded to the Python layer. A bad *platform*
  change (driver, OS, systemd units) still means re-flashing, which is the price of
  (d) and the reason (d) is written down.
- Provisioning assumes a working LAN. A sign cannot currently be configured on a
  network it has never joined.

## Revisit when (any one)

- A sign has to be set up somewhere its WiFi credentials weren't baked in → revisit
  the rejected AP/captive-portal path in (b).
- Signs are handed to third parties, where "SSH in and re-flash" isn't an answer →
  revisit A/B partitions in (d).
- The app layer grows a build step (compiled assets, non-pure-Python deps) → source
  tarballs stop being sufficient and releases need real artifacts.
- More than a handful of signs, or signs on networks I don't control.

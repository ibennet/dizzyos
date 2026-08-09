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
polish: fixed disks are refused, anything larger than a card (>512 GB) is refused, and a
target carrying an APFS/HFS/Time Machine volume — i.e. a Mac disk with data on it — is
refused too (all overridable only with `--force`). The partition map is printed so the
human can recognise the wrong disk by its volume names, the device identity is re-checked
immediately before the write (closing the reseat/reassign TOCTOU window), and you retype
the device node to confirm. The SHA256 check is integrity, not authenticity — it comes
from the same host over the same TLS as the image, so it catches corruption, not a
compromised mirror.

### (b) Provisioning is headless; the settings page is LAN-only and authorized by the sign itself

Credentials are baked at flash time — hostname, user, SSH public key, and WiFi:
a cloud-init `network-config` on the boot partition (the mechanism Raspberry Pi
OS Trixie+ actually honors — hard-won knowledge) plus a NetworkManager keyfile
for pre-cloud-init images — so the first boot is unattended. To *change* things later
(rejoin WiFi, edit config) the sign serves a settings page on the LAN at
`http://<hostname>.local:8080`.

Authorization is **physical presence**: pressing "show PIN" makes the sign display a
one-time 6-digit PIN for 30 seconds. You type what you can see on the sign.

**What this actually protects.** Be honest about the boundary: the PIN gates against
someone *off the LAN* or *not in the room*. It is **not** a real boundary against a
trusted peer on the same WiFi — a housemate can see the sign, and on a shared-PSK
network could capture the PIN off cleartext HTTP. We treat the LAN peer as trusted;
if that ever stops being true, add a second credential (an admin passphrase generated
at flash time) rather than leaning on the PIN. What the server *does* defend, even
LAN-only, is the class of bug that turns "LAN-only, so we're fine" into a remote hole:
brute force (6-digit PIN, a global 1/second throttle, lockout after repeated
failures), cross-origin abuse (a `Host`-header allowlist defeats DNS rebinding; a
per-session CSRF token guards the write forms; issuing the PIN is a POST so a
cross-site `<img>` can't park a banner on the display), and privilege (the render
process runs as `daemon` and reaches root only through three fixed-purpose sudo
helpers — restart, config write, WiFi join — never as a general shell).

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
- **App** — everything pure-Python, updated by `tools/pi/dizzyos-update` on a
  five-minute timer (jittered). The poll is ETag-conditional: the updater replays
  the previous response's ETag and GitHub answers an unchanged `/releases/latest`
  with 304, which is free against the unauthenticated rate limit — so near-instant
  release pickup costs no infrastructure and no auth token.

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
it survives every update. `/etc/dizzyos/update.conf` names the repo to track. Because it
survives updates, release code must stay **forward-compatible with any prior config
shape**: `run.py` deep-merges the user file over the release's default `config.yaml`, so
a new release's added keys always resolve to a value against an old user file. The updater
also reinstalls the target release's pinned `requirements-pi.txt` on rollback, so a
rollback restores dependencies too (the venv is shared) — hence the pins are exact.

**Trust.** There is no signature on releases (see Consequences): the updater trusts
GitHub over HTTPS and pins each fetch to the commit SHA the tag points at *now* (so a
re-cut tag can't silently ship two signs different code). For a solo project on networks
I control this is acceptable, but it means a GitHub account compromise, or a mis-push to a
tag, deploys to every sign automatically within minutes with no human in the loop — the
five-minute poll removed even the overnight delay the old nightly timer provided — which
is why "releases are cut by anyone but me" is a revisit trigger below. The heartbeat
rollback bounds a *broken* release; it does nothing against a malicious one that renders.

### (d) A/B root partitions are deferred, not dismissed

The industrial answer to "update an appliance safely" is two root partitions plus a
bootloader that can fall back — **RAUC** or **SWUpdate**. That makes the *whole OS*
atomic, kernel and driver included, and survives a power cut mid-write.

We're not doing it. It requires a custom partition layout, a build pipeline producing
signed bundles, bootloader integration, and roughly doubles the SD footprint — real
infrastructure, in exchange for atomicity over a layer (OS + compiled driver) that we
change approximately never. The symlink flip already covers the layer that actually
changes, with rollback. Tracked in issue #8 for when the tradeoff shifts.

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
- Releases are cut by anyone but me, or the account/CI that can push tags is shared →
  the unsigned-tarball trust model in (c) needs revisiting (signing, or pinned SHAs in
  a reviewed manifest).
- A settings-page peer on the LAN stops being trusted (shared flat, guest WiFi) →
  add the flash-time admin passphrase noted in (b).
- More than a handful of signs, or signs on networks I don't control.
- A second co-hosted service appears alongside the order server → generalize the
  amendment below into a real "extra services" mechanism instead of a second
  hand-rolled payload subdirectory.

## Amendment (2026-08): opt-in co-hosted order server

The cafe sign's Pi also hosts the order backend (a FastAPI app from
`ibennet/izzybennett.com`, exposed via a Cloudflare Tunnel). Provisioning installs
it only when `flash.sh --order-server` staged a `dizzyos/order-server/` payload
subdirectory — its presence *is* the opt-in flag, and the tunnel credentials ride
inside it, following the `network-config` lifecycle exactly: plaintext on FAT until
first boot, `go-rwx` the moment they land on the rootfs, one final root-only copy in
`/etc/cloudflared`, payload copy deleted.

It deliberately does **not** reuse the release-tarball updater: the order server
lives in a different repo with no release cadence, so it updates by git — a
five-minute timer whose no-change path is a single `git ls-remote` (the git analog
of the ETag-conditional poll above), with a `/health`-probe rollback to the previous
commit. It runs as a dedicated `izzy` system user with its own venv under
`/opt/izzy-orders`, so a dizzyos rollback reinstalling `requirements-pi.txt` can
never touch the order server's dependencies, and vice versa. The trust note in (c)
sharpens accordingly: a push to the order repo's default branch reaches the public
internet within minutes, gated only by the health probe.

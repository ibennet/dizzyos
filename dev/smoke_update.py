#!/usr/bin/env python3
"""Updater state-machine smoke test — no network, no systemd, no hardware.

Drives dizzyos-update's deploy / rollback / prune / reconcile logic against a
temp ROOT, with a fake fetch and a fake `restart` that either does or doesn't
leave a heartbeat (i.e. a release that renders vs one that doesn't). This is the
highest-consequence code on the sign and the hardest to see fail on hardware, so
it gets exercised here. Exits non-zero on the first failure.
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
UPDATER = os.path.join(os.path.dirname(HERE), "tools", "pi", "dizzyos-update")

# The updater has no .py extension, so give importlib an explicit source loader.
_loader = importlib.machinery.SourceFileLoader("dizzyos_update", UPDATER)
up = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(up)

passed = 0


def check(name, cond):
    global passed
    if not cond:
        sys.exit(f"FAIL: {name}")
    passed += 1
    print(f"  ok: {name}")


def rel(*parts):
    return os.path.join(up.RELEASES, *parts)


# --- redirect every path at a throwaway tree, shrink the health wait ---------
work = tempfile.mkdtemp()
up.ROOT = work
up.RELEASES = os.path.join(work, "releases")
up.CURRENT = os.path.join(work, "current")
up.HEARTBEAT = os.path.join(work, "heartbeat")
up.PENDING = os.path.join(up.RELEASES, ".pending")
up.CONF = os.path.join(work, "update.conf")
up.HEALTH_TIMEOUT = 2
os.makedirs(up.RELEASES, exist_ok=True)
with open(up.CONF, "w", encoding="utf-8") as fh:
    fh.write("REPO=example/dizzyos\n")

# --- fakes: no network, no pip, no systemd ----------------------------------
up.resolve_sha = lambda repo, ref: "deadbeef"
up.pip_install = lambda d: None
up.warm_font_cache = lambda d: None


def fake_fetch(repo, ref, name):
    dest = rel(name)
    os.makedirs(dest, exist_ok=True)
    return dest


up.fetch_release = fake_fetch

render_ok = [True]  # does the "render loop" beat after a restart?


def fake_restart():
    if render_ok[0]:
        open(up.HEARTBEAT, "w").close()


up.restart = fake_restart

# --- a healthy deploy --------------------------------------------------------
print("deploy")
up.latest_tag = lambda repo: "v1"
sys.argv = ["dizzyos-update", "--install"]
render_ok[0] = True
check("healthy deploy returns 0", up.main() == 0)
check("current points at v1", up.current_tag() == "v1")
check("nothing left pending", not os.path.exists(up.PENDING))

# --- a broken deploy: first failure strikes and rolls back -------------------
print("rollback")
up.latest_tag = lambda repo: "v2"
render_ok[0] = False
check("broken deploy returns 1", up.main() == 1)
check("rolled back to v1", up.current_tag() == "v1")
check("v2 struck once but not yet bad",
      os.path.exists(rel("v2.strike")) and not os.path.exists(rel("v2.bad")))
check("no pending left after rollback", not os.path.exists(up.PENDING))

# --- second failure marks it bad --------------------------------------------
check("second broken deploy returns 1", up.main() == 1)
check("v2 is now marked bad", os.path.exists(rel("v2.bad")))
check("bad release is then skipped",
      up.main() == 0 and up.current_tag() == "v1")

# --- orphan staging dirs are swept ------------------------------------------
print("housekeeping")
os.makedirs(rel("tmpXYZ"), exist_ok=True)
up.sweep_orphans()
check("sweep removes orphan staging dirs", not os.path.exists(rel("tmpXYZ")))

# --- reconcile an interrupted deploy that is rendering (keep it) -------------
print("reconcile")
os.makedirs(rel("v10"), exist_ok=True)
up.flip("v10")
with open(up.PENDING, "w", encoding="utf-8") as fh:
    fh.write("v10 v1")
open(up.HEARTBEAT, "w").close()          # it is rendering
render_ok[0] = True
up.reconcile_pending()
check("reconcile keeps a healthy interrupted deploy", up.current_tag() == "v10")
check("reconcile clears the pending record", not os.path.exists(up.PENDING))

# --- reconcile an interrupted deploy that is NOT rendering (roll back) --------
os.makedirs(rel("v11"), exist_ok=True)
up.flip("v11")
with open(up.PENDING, "w", encoding="utf-8") as fh:
    fh.write("v11 v1")
if os.path.exists(up.HEARTBEAT):
    os.remove(up.HEARTBEAT)              # it is NOT rendering
render_ok[0] = False
up.reconcile_pending()
check("reconcile rolls back a non-rendering interrupted deploy",
      up.current_tag() == "v1")
check("the interrupted release is struck (two-strike, not yet bad)",
      os.path.exists(rel("v11.strike")) and not os.path.exists(rel("v11.bad")))

print(f"\nsmoke_update: all {passed} checks passed")

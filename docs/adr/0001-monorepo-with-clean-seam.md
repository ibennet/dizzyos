# 0001 — Monorepo with a clean kernel/app seam

**Status:** Accepted (2026-08)

## Context

dizzyos is a small kernel that hosts self-contained apps on an LED-matrix sign. The
question came up of whether each app should live in its own repository.

Today apps are tightly coupled to the kernel:

- Apps import the kernel contract directly — the `App` base class (`kernel.app`) and
  shared render helpers (`kernel.render`, e.g. `glyph_height` in
  `apps/cafe_menu/app.py`).
- The loader discovers apps by importing `apps.<name>.app` off the **local filesystem**
  (`kernel/loader.py`). There is no packaging, versioning, or distribution channel for
  an app that lives elsewhere.
- The `App` contract is young and **unversioned** — `services`, `render(t)`, and the
  manifest merge are all still evolving.
- This is a solo project.

Splitting apps into separate repos now would mean version-pinning the kernel, doing
coordinated cross-repo changes for every kernel tweak, and maintaining N+1 repos — all
overhead with no payoff at this stage.

## Decision

Keep dizzyos as a **monorepo**. Apps live in-repo under `apps/<name>/`. Modularity
comes from the in-repo plugin convention (one `App` subclass per `app.py` plus a
`manifest.yaml`), not from repo boundaries.

The kernel↔app seam stays clean — apps return a `PIL.Image` and never touch the matrix
directly; everything else flows through the single `services` handle. This is what
keeps a future extraction cheap, so we lose nothing by deferring the split.

## Consequences

- Kernel changes and the app fixups they require land in a single PR.
- No cross-repo version coordination while the `App` contract is still moving.
- The seam rules that keep extraction cheap are documented in `apps/__init__.py`.

## Revisit when (any one)

- The `App` contract stabilizes and gets versioned.
- A third party wants to publish apps.
- An app grows heavy, independent dependencies that shouldn't ship with the core.
- We want per-app CI or release cadence.

## Escape hatch

When that day comes: make `kernel/` a pip-installable package and switch the loader
from filesystem scanning to Python **entry-point** discovery, so apps can live in their
own repos and `pip install` in. Because the boundary is already clean, this is a
mechanical change rather than a rewrite.

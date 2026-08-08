# cad — the enclosure, as numbers

The sign lives in a wooden case. `params.py` is the single source of truth for its
dimensions; `test_fit.py` asserts that the design still goes together.

```bash
python cad/params.py      # print the derived dimensions and the cut list
python cad/test_fit.py    # assert the design fits (also run by dev/check.sh)
```

## Why there is no CAD kernel here

The valuable part of "put it in CAD" was never the solid geometry — it was being
able to *prove* the parts fit rather than trusting arithmetic done once at 3am.
That needs a parametric dimension model, not a B-rep kernel, so `params.py` is
plain Python with no dependencies and the fit checks run in CI on every push.

This is not an argument against solid modelling, just against putting it on the
critical path. A `sign_case.py` built on [build123d](https://build123d.readthedocs.io/)
would import `params.py` rather than restate any of it, and could then export STEP
for fabrication, STL for printed brackets, and SVG projections to replace the
hand-drawn figures in the build guide. It belongs in an optional dev extra —
build123d pulls in OpenCASCADE, which is a few hundred megabytes and has no
business near the Raspberry Pi's venv.

## Changing the design

Edit the constants in `params.py`, then run the checks. The interesting failures
are the ones you would otherwise discover with the wood already cut:

| Change | What fails |
|---|---|
| Panels deeper than the frame stock | `standoffs have positive length` |
| A cavity no stock standoff clears | `the standoff fits the cavity` |
| Swap 1×3 for 1×2 | `Pi stack clears the panels` — the cavity gets too shallow |
| A third panel in the chain | `frame comes out of one board`, `face comes out of one acrylic sheet` |
| A taller HAT or an extra riser | `cable headroom behind the stack` |

`panel.depth` is the number most worth measuring rather than trusting: it varies
between manufacturers more than anything else here, and it sets the standoff
length directly.

Note that `standoff_length` is not that exact length but the nearest stock size
*below* it, chosen from `STOCK_STANDOFFS`; the difference falls out as
`panel_recess`. Rounding down is deliberate — a standoff longer than the cavity
stands the panels proud of the frame and bows the acrylic, whereas a short one
just leaves them sitting a few millimetres behind it. Quote the stock number in
the build guide, never the exact one: nobody sells a 48.5 mm standoff.

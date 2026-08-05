# Fonts

**Bitmap fonts (`.bdf`) are preferred** — they render 1:1 with no antialiasing, which
is the crispest, most legible option at tiny sizes and exactly how a real LED sign
looks. Bundled here (from hzeller/rpi-rgb-led-matrix): `4x6`, `5x8`, `6x10`, and
`tom-thumb`. The renderer uses the first by name (`4x6.bdf`) unless an app sets its
own `font:` in config. BDFs are compiled once to Pillow's format under `.cache/`.

You can also drop a scalable `.ttf`/`.otf` here (e.g. Pixel Operator, Cozette). If no
bundled font is present the renderer falls back to a system monospace (DejaVu Sans
Mono on the Pi, Menlo on macOS), then to Pillow's built-in font — so the sign always
renders, just less sharply.

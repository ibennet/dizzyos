# Fonts

Drop a `.ttf` or `.otf` here and dizzyos picks it up automatically (first match wins).
At a 64px panel height, crisp pixel fonts read best. Good choices:

- **Pixel Operator** — clean, very legible, free (OFL)
- **Cozette** — 6px bitmap, great for dense text
- **Press Start 2P** — chunky retro look

If this folder is empty, the renderer falls back to a system monospace font
(DejaVu Sans Mono on the Pi, Menlo on macOS), then to Pillow's built-in font — so the
sign always renders, just less sharply.

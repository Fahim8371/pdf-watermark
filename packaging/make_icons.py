"""
make_icons.py - Draw the app icon and write it in the formats each platform wants.

    python packaging/make_icons.py      (needs Pillow: pip install pillow)

Writes icon.png (1024 px), icon.ico (Windows) and icon.icns (macOS) next to
this file. The results are committed, so building the app does not need
Pillow - only re-running this after changing the design does.
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
S = 1024
X = 4  # drawn at 4x, then scaled down, for smooth edges


def draw() -> Image.Image:
    n = S * X
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Rounded dark tile, the shape macOS uses for app icons.
    d.rounded_rectangle([n * 0.06, n * 0.06, n * 0.94, n * 0.94], radius=n * 0.2, fill=(27, 27, 32))
    # The page, with a few lines of text on it.
    d.rounded_rectangle([n * 0.30, n * 0.20, n * 0.70, n * 0.80], radius=n * 0.045, fill=(247, 247, 250))
    for i, w in enumerate((0.24, 0.28, 0.20, 0.28, 0.16)):
        y = n * (0.30 + i * 0.075)
        d.rounded_rectangle([n * 0.37, y - n * 0.011, n * (0.37 + w), y + n * 0.011],
                            radius=n * 0.011, fill=(200, 202, 212))
    # The watermark: a round-ended stroke corner to corner, blue to violet.
    a, b = (n * 0.28, n * 0.73), (n * 0.72, n * 0.27)
    width = n * 0.085
    mask = Image.new("L", (n, n), 0)
    m = ImageDraw.Draw(mask)
    m.line([a, b], fill=255, width=int(width))
    for cx, cy in (a, b):
        m.ellipse([cx - width / 2, cy - width / 2, cx + width / 2, cy + width / 2], fill=255)
    gradient = Image.new("RGBA", (n, n))
    g = ImageDraw.Draw(gradient)
    start, end = (42, 163, 255), (139, 123, 255)
    for k in range(0, 2 * n, 2):
        t = k / (2 * n)
        colour = tuple(round(s0 + (e0 - s0) * t) for s0, e0 in zip(start, end, strict=True)) + (255,)
        # Lines of equal colour run perpendicular to the stroke.
        g.line([(k - n, 0), (k, n)], fill=colour, width=3)
    img.paste(gradient, (0, 0), mask)
    return img.resize((S, S), Image.LANCZOS)


def main() -> None:
    img = draw()
    img.save(HERE / "icon.png")
    img.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(HERE / "icon.icns")
    print("wrote icon.png, icon.ico, icon.icns")


if __name__ == "__main__":
    main()

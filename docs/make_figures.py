"""
make_figures.py - Regenerate the images used in the README.

Every figure is drawn by the real watermarking engine onto the sample
document from examples/make_sample.py, so the pictures can never drift from
what the tool actually produces. Run it after changing how watermarks look:

    python docs/make_figures.py
"""

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

import make_sample  # noqa: E402

import watermark as wm  # noqa: E402

OUT = Path(__file__).resolve().parent
BG = (0.965, 0.965, 0.97)
INK = (0.13, 0.13, 0.16)
MUTED = (0.5, 0.5, 0.55)
DPI = 60


def sample_page(kind: str) -> pymupdf.Document:
    doc = pymupdf.open()
    (make_sample.report_page if kind == "report" else make_sample.drawing_page)(doc)
    return doc


def render(style: wm.Style | None, kind: str = "report", text: str | None = None) -> pymupdf.Pixmap:
    doc = sample_page(kind)
    page = doc[0]
    if style:
        page = wm.watermark_page(page, style, wm.expand_text(text or style.text, 1, 1, "Facility Design Report"))
    return page.get_pixmap(dpi=DPI, alpha=False)


def sheet(panels: list[tuple[str, str, pymupdf.Pixmap]], name: str, gap: int = 60) -> None:
    """Lay pixmaps out in a row on a light background, each with a title and caption."""
    pad, head = 40, 96
    width = pad * 2 + sum(p.width for _, _, p in panels) + gap * (len(panels) - 1)
    height = head + max(p.height for _, _, p in panels) + pad
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=BG)
    x = pad
    for title, caption, pix in panels:
        page.insert_text((x, 50), title, fontname="hebo", fontsize=20, color=INK)
        if caption:
            page.insert_text((x, 78), caption, fontname="cour", fontsize=13, color=MUTED)
        box = pymupdf.Rect(x, head, x + pix.width, head + pix.height)
        page.draw_rect(box + (-1, -1, 1, 1), color=(0.85, 0.85, 0.88), width=1)
        page.insert_image(box, pixmap=pix)
        x += pix.width + gap
    page.get_pixmap(dpi=72, alpha=False).save(OUT / name)
    print("wrote", OUT / name)


def main() -> None:
    acme = "CONFIDENTIAL - Acme Construction"

    sheet([("Original", "", render(None)),
           ("Watermarked", '--company "Acme Construction"', render(wm.Style(text=acme)))],
          "before-after.png")

    sheet([("Diagonal", "default", render(wm.Style(text=acme))),
           ("Stacked", "--stack", render(wm.Style(text=acme, stack=True))),
           ("Tiled", "--layout tile", render(wm.Style(text=acme, layout="tile"))),
           ("Stamp", "--position bottom-right", render(wm.Style(
               text=acme + " - {date}", layout="position", position=(1, 1), size=1.4, opacity=0.8,
               color=wm.parse_color("red")))),
           ("Outline, serif", "--outline --font Times", render(wm.Style(
               text=acme, stack=True, outline=True, font="Times", opacity=0.5)))],
          "options.png", gap=44)

    sheet([("A4 report", "", render(wm.Style(text=acme))),
           ("A3 drawing", "", render(wm.Style(text=acme), kind="drawing"))],
          "page-sizes.png")


if __name__ == "__main__":
    main()

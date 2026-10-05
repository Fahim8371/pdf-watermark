"""
Shared helpers. Every test PDF is generated on the fly - no documents are
committed to the repository.
"""

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

A4 = (595, 842)
A3 = (842, 1191)


def make_page(doc: pymupdf.Document, size=A4, rotation=0, crop=None, origin=(0, 0),
              label="Sample page") -> pymupdf.Page:
    """
    Add a page with some visible content in its top-left corner.

    `origin` shifts the MediaBox away from (0, 0), as some generators do.
    `crop` is a CropBox in raw PDF coordinates (x0, y0, x1, y1).
    """
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_text((40, 60), label, fontsize=18)
    page.draw_rect(pymupdf.Rect(40, 80, 200, 160), color=(0, 0, 1), width=2)
    xref = page.xref
    if origin != (0, 0):
        ox, oy = origin
        # Shift the content with the box so it stays on the page.
        content = page.read_contents()
        doc.update_stream(page.get_contents()[0],
                          f"1 0 0 1 {ox} {oy} cm\n".encode() + content)
        doc.xref_set_key(xref, "MediaBox", f"[{ox} {oy} {ox + size[0]} {oy + size[1]}]")
    if crop:
        doc.xref_set_key(xref, "CropBox", "[{} {} {} {}]".format(*crop))
    if rotation:
        page.set_rotation(rotation)
    return doc.reload_page(page)


def save(doc: pymupdf.Document, path: Path, **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path), **kwargs)
    return path


def red_centroid(page: pymupdf.Page, dpi=40):
    """
    Centre of the strongly red pixels on the rendered page, as fractions of
    the page width and height, plus the red pixel count.
    """
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    w, h, n = pix.width, pix.height, pix.n
    data = pix.samples
    sx = sy = count = 0
    for y in range(h):
        row = y * pix.stride
        for x in range(w):
            r, g, b = data[row + x * n: row + x * n + 3]
            if r > 180 and g < 90 and b < 90:
                sx += x
                sy += y
                count += 1
    if not count:
        return None, None, 0
    return sx / count / w, sy / count / h, count


def red_extent_centre(page: pymupdf.Page, dpi=40):
    """
    Midpoint of the bounding box of the red pixels, as page fractions.

    Unlike red_centroid this ignores how much ink each part carries, so
    mixed text - heavy bold Latin beside thin CJK strokes - still measures
    as centred when it is.
    """
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            r, g, b = pix.pixel(x, y)
            if r > 180 and g < 90 and b < 90:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None, None
    return (min(xs) + max(xs)) / 2 / pix.width, (min(ys) + max(ys)) / 2 / pix.height


@pytest.fixture
def red_style():
    import watermark as wm
    return wm.Style(text="CONFIDENTIAL", color=(1, 0, 0), opacity=1.0, angle=45, tile=1)

"""
watermark.py - The watermarking engine: page geometry, rotation handling, drawing.

This module does the actual work of stamping text onto a PDF page, and exposes
a small command line of its own for quick one-off jobs. For anything more
involved (several watermarks in one run, output folders per client, arbitrary
text from the command line) use batch_watermark.py, which imports this module
and drives it. The desktop app (app.py) drives it the same way.

Requires:
    pip install pymupdf

Usage:
    # Single file
    python watermark.py report.pdf

    # Entire directory (recursive - mirrors folder structure in output)
    python watermark.py contracts/

    # Preview without writing anything
    python watermark.py contracts/ --dry-run

Library use - pass a Style, or set the CONFIG values below and pass nothing:

    import watermark as wm
    style = wm.Style(text="CONFIDENTIAL - {date}", opacity=0.3)
    wm.watermark_file("report.pdf", "report_watermarked.pdf", style)

The text may contain placeholders, filled in per page: {page}, {pages},
{file} (the source file name, without .pdf) and {date} (today, YYYY-MM-DD).

Output:
    Watermarked copies are saved to a 'watermarked/' subfolder inside
    the input directory. Originals are never modified.
"""

from __future__ import annotations

import argparse
import datetime
import math
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from math import cos, hypot, radians, sin
from pathlib import Path

import pymupdf

import fonts

# ---- CONFIG ---------------------------------------------------------------
# Defaults for every watermark. A Style built with no arguments takes its
# values from here, at the moment it is built - so changing one of these
# between documents takes effect for the next Style. batch_watermark.py and
# the app override most of them per run.

# The text to stamp on each page. It is scaled to fit the page, so a long
# string simply becomes a smaller one. Placeholders: {page} {pages} {file} {date}
WATERMARK_TEXT   = "CONFIDENTIAL"

# A font family: "Helvetica", "Times" or "Courier" are built into every PDF
# reader; any font installed on the computer works too ("Arial", "Georgia"),
# and is embedded in the copy. Characters the font lacks (Cyrillic, Greek,
# Chinese, Japanese, Korean...) are drawn from a fallback font automatically.
FONT_FAMILY      = "Helvetica"

# Bold reads far better at the low opacity a watermark uses.
BOLD             = True
ITALIC           = False

# Reference size in points (72 points = 1 inch). This is NOT the size that gets
# drawn: the size is worked out per page so an A0 drawing and an A4 letter both
# end up proportional. It only sets the proportions being measured.
FONT_SIZE        = 72

# RGB as floats from 0 to 1, not 0-255. (0, 0, 0) is black, (1, 1, 1) white,
# (0.6, 0.6, 0.6) a mid grey that stays readable over both text and drawings.
FONT_COLOR       = (0.6, 0.6, 0.6)

# 0 is invisible, 1 fully opaque. Around 0.3-0.5 marks the document clearly
# while leaving the content underneath legible.
OPACITY          = 0.4

# Degrees, counter-clockwise from horizontal - or None to run corner to corner
# along each page's own diagonal (about 55 degrees on portrait A4, 35 on
# landscape). A fixed 45 never quite lines up with a real page; None does.
# 0 gives a straight horizontal band across the middle of the page.
ROTATION_DEGREES = None

# How the text is arranged on the page:
#   "diagonal"  one large line across the centre
#   "tile"      repeated in REPEAT_LINES bands edge to edge - much harder to
#               crop or paint out, but busier to read through
#   "position"  a small flat stamp at POSITION - a corner, an edge, the
#               centre, or anywhere in between
# (The older names still work: "stacked" is diagonal with STACK on, "header"
# and "footer" are a position at the top or bottom centre.)
LAYOUT           = "diagonal"

# Where a "position" stamp goes, as fractions across and down the page inside
# a small margin: (0, 0) is the top-left corner, (1, 1) bottom-right,
# (0.5, 1) bottom centre. Any value in between places it proportionally, so
# it lands in the same spot on an A4 letter and an A0 drawing.
POSITION         = (0.5, 1.0)

# Split the text onto two lines at the first " - ", so "CONFIDENTIAL - Acme"
# becomes CONFIDENTIAL over Acme. Explicit line breaks ("\n") always split.
STACK            = False

# Number of bands when the layout is "tile". (On the command line, --tile N
# with N above 1 picks the tile layout too, as it always has.)
REPEAT_LINES     = 4

# Only used when tiling: the space between repeats along a band, in multiples
# of the font size. Alternate bands are offset by half a step, brick-style.
TILE_GAP         = 3.0

# Multiplies the automatic size. For "diagonal" and "tile", 1.0 is as large
# as fits and the maximum. For "position", 1.0 is about 12pt on an A4 page,
# and it can go up to 4.0.
SIZE             = 1.0

# Draw only the outline of each letter instead of filling it - lighter over
# dense drawings, and still clearly there.
OUTLINE          = False

# Subfolder created inside the input directory by this file's own CLI.
# batch_watermark.py names its output folders differently, but still skips
# anything found inside a folder with this name so it never re-watermarks
# its own output.
OUTPUT_FOLDER    = "watermarked"

# Appended to every output filename, before the .pdf extension.
OUTPUT_SUFFIX    = "_watermarked"
# ---------------------------------------------------------------------------


@dataclass
class Style:
    """
    Everything that decides how a watermark looks.

    Fields left out take the current CONFIG value above. Passing a Style
    around, rather than setting module globals, is what lets the app render a
    preview while a batch is running without the two interfering.
    """
    text: str = field(default_factory=lambda: WATERMARK_TEXT)
    font: str = field(default_factory=lambda: FONT_FAMILY)
    bold: bool = field(default_factory=lambda: BOLD)
    italic: bool = field(default_factory=lambda: ITALIC)
    color: tuple[float, float, float] = field(default_factory=lambda: FONT_COLOR)
    opacity: float = field(default_factory=lambda: OPACITY)
    angle: float | None = field(default_factory=lambda: ROTATION_DEGREES)
    layout: str = field(default_factory=lambda: LAYOUT)
    position: tuple[float, float] = field(default_factory=lambda: POSITION)
    stack: bool = field(default_factory=lambda: STACK)
    tile: int = field(default_factory=lambda: REPEAT_LINES)
    tile_gap: float = field(default_factory=lambda: TILE_GAP)
    size: float = field(default_factory=lambda: SIZE)
    outline: bool = field(default_factory=lambda: OUTLINE)

    def __post_init__(self):
        # Older settings: a single font code such as "hebo", and the
        # "stacked" / "header" / "footer" layouts.
        if self.font in fonts.LEGACY:
            self.font, self.bold, self.italic = fonts.LEGACY[self.font]
        if self.layout == "stacked":
            self.layout, self.stack = "diagonal", True
        elif self.layout in ("header", "footer"):
            self.position = (0.5, 0.0 if self.layout == "header" else 1.0)
            self.layout = "position"
        if self.layout not in LAYOUTS:
            raise ValueError(f"unknown layout '{self.layout}' - use one of {', '.join(LAYOUTS)}")
        x, y = self.position
        self.position = (min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0))

    def mode(self) -> str:
        """"single", "tile" or "stamp" - what the drawing code has to do."""
        return {"diagonal": "single", "tile": "tile", "position": "stamp"}[self.layout]

    def bands(self) -> int:
        return max(self.tile, 1) if self.mode() == "tile" else 1


LAYOUTS = ("diagonal", "tile", "position")

# Named spots for a "position" stamp: the 3 x 3 grid of corners, edges and centre.
POSITIONS = {
    f"{v}-{h}" if (v, h) != ("middle", "center") else "center": (x, y)
    for v, y in (("top", 0.0), ("middle", 0.5), ("bottom", 1.0))
    for h, x in (("left", 0.0), ("center", 0.5), ("right", 1.0))
}


class WatermarkError(Exception):
    """A problem with one document, phrased for the person running the tool."""


# ---- Text -----------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{(page|pages|file|date)\}")

# Hebrew, Arabic, Syriac, Thaana, NKo and the Arabic presentation forms.
_RTL = re.compile(r"[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]")


def expand_text(template: str, page: int = 1, pages: int = 1, file: str = "",
                date: datetime.date | None = None) -> str:
    """Fill in {page}, {pages}, {file} and {date}. Other braces are left alone."""
    values = {
        "page": str(page),
        "pages": str(pages),
        "file": file,
        "date": (date or datetime.date.today()).isoformat(),
    }
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def parse_color(value: str) -> tuple[float, float, float]:
    """
    A colour name or a hex code ("#cc0000", "c00") as 0-1 RGB floats.

    Raises ValueError with a readable message for anything else.
    """
    named = {
        "grey": "#999999", "gray": "#999999", "red": "#d93333",
        "blue": "#2659d9", "black": "#000000", "green": "#2f8f4e",
    }
    raw = named.get(value.strip().lower(), value.strip()).lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        raise ValueError(f"'{value}' is not a colour - use a name like red, or a hex code like #cc0000")
    return tuple(int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _text_width(font: pymupdf.Font, text: str, fontsize: float) -> float:
    """
    The width the text really takes when drawn.

    Font.text_length() only knows the primary font's own glyphs; characters it
    lacks are drawn from a fallback font with different widths, which threw
    the centring off for anything beyond Latin script. Laying the text out in
    a scratch TextWriter measures what will actually be drawn.
    """
    tw = pymupdf.TextWriter(pymupdf.Rect(0, 0, 1, 1))
    tw.append((0, 0), text, font=font, fontsize=fontsize,
              right_to_left=bool(_RTL.search(text)))
    return tw.text_rect.width


# ---- Geometry -------------------------------------------------------------

def _page_perp_span(page: pymupdf.Page, angle_rad: float) -> float:
    """
    Total space the page has in the direction perpendicular to the watermark text.
    Found by projecting all four page corners onto the perpendicular axis.
    """
    W, H   = page.rect.width, page.rect.height
    px, py = sin(angle_rad), -cos(angle_rad)
    projs  = [0, W * px, H * py, W * px + H * py]
    return max(projs) - min(projs)


# Distance between baselines when the text runs over several lines, in
# multiples of the font size.
LEADING = 1.25


def text_lines(style: Style, text: str) -> list[str]:
    """Split the text into the lines to draw."""
    lines = [line.strip() for line in text.splitlines() if line.strip()] or [text]
    if style.stack and len(lines) == 1 and " - " in text:
        lines = [part.strip() for part in text.split(" - ", 1)]
    return lines


def style_font(style: Style, text: str = "") -> pymupdf.Font:
    """The font to draw `text` in. Installed fonts come back subset to `text`."""
    return fonts.load(style.font, style.bold, style.italic, text)


def page_angle(page: pymupdf.Page, style: Style) -> float:
    """The angle to draw at on this page, in degrees."""
    if style.mode() == "stamp":
        return 0.0
    if style.angle is None:
        # Corner to corner: bottom-left to top-right.
        return math.degrees(math.atan2(page.rect.height, page.rect.width))
    return style.angle


def effective_font_size(page: pymupdf.Page, style: Style | None = None,
                        text: str | None = None, font: pymupdf.Font | None = None) -> float:
    """
    Auto-scales font size purely from page geometry so the watermark always
    looks proportional regardless of page size (A4, A3, A2, custom, etc.).
    The result is then multiplied by style.size.

    single  ->  scale so the rotated block of text fills 90% of the page in
                both axes - no clipping, no wasted space.
    tile    ->  text height takes ~45% of each band's share of the page, so
                bands stay apart, capped so one copy of the text spans no
                more than ~55% of the page diagonal. Short text is therefore
                sized by the band, long text by the page - a long recipient
                name repeats less often instead of shrinking to nothing.
    stamp   ->  about 2% of the page's shorter side (12pt on A4) times
                style.size, shrunk if needed to fit across the page.
    """
    style = style or Style()
    text = style.text if text is None else text
    font      = font or style_font(style, text)
    angle_rad = radians(page_angle(page, style))
    lines     = text_lines(style, text)
    font_h    = font.ascender - font.descender
    block_h   = font_h + (len(lines) - 1) * LEADING      # in font sizes
    tw_ref    = max(_text_width(font, line, FONT_SIZE) for line in lines)
    W, H      = page.rect.width, page.rect.height
    if tw_ref <= 0:
        return FONT_SIZE

    if style.mode() == "stamp":
        fits = FONT_SIZE * W * 0.9 / tw_ref
        return min(min(W, H) * 0.02 * min(style.size, 4.0), fits)

    if style.mode() == "single":
        th    = block_h * FONT_SIZE
        bb_w  = abs(tw_ref * cos(angle_rad)) + abs(th * sin(angle_rad))
        bb_h  = abs(tw_ref * sin(angle_rad)) + abs(th * cos(angle_rad))
        scale = min(W * 0.90 / bb_w, H * 0.90 / bb_h)
        return FONT_SIZE * scale * min(style.size, 1.0)

    slot      = _page_perp_span(page, angle_rad) / style.bands()
    by_band   = slot * 0.45 / block_h
    by_length = FONT_SIZE * (hypot(W, H) * 0.55) / tw_ref
    return min(by_band, by_length) * min(style.size, 1.0)


def _tile_origins_for_band(page: pymupdf.Page, bcx: float, bcy: float,
                           step: float, phase: float,
                           cos_a: float, sin_a: float) -> list[float]:
    """
    Positions along one band (as distances from the band centre, in the text
    direction) at which a copy of the text should be centred, covering the
    page edge to edge with one spare copy beyond each end.
    """
    W, H = page.rect.width, page.rect.height
    # Text runs in direction (cos, -sin) in PyMuPDF's y-down page space.
    t_vals = [(cx - bcx) * cos_a - (cy - bcy) * sin_a
              for cx, cy in ((0, 0), (W, 0), (0, H), (W, H))]
    t_min, t_max = min(t_vals) - step, max(t_vals) + step
    t = t_min - ((t_min - phase) % step)
    out = []
    while t <= t_max:
        out.append(t)
        t += step
    return out


def _inherited(doc: pymupdf.Document, xref: int, key: str) -> str | None:
    """A page attribute's raw value, following /Parent for inherited ones."""
    seen = set()
    while xref and xref not in seen:
        seen.add(xref)
        kind, value = doc.xref_get_key(xref, key)
        if kind != "null":
            return value
        kind, parent = doc.xref_get_key(xref, "Parent")
        xref = int(parent.split()[0]) if kind == "xref" else 0
    return None


def _box(value: str | None) -> tuple[float, float, float, float] | None:
    """Parse a raw PDF rectangle like '[0 0 595 842]', normalised."""
    if not value:
        return None
    nums = [float(n) for n in re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", value)]
    if len(nums) != 4:
        return None
    x0, y0, x1, y1 = nums
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _normalise_page(page: pymupdf.Page) -> None:
    """
    Bring the page to a clean, rotation-free state before watermarking.

    Two problems can corrupt appended watermark text:

    1. Unbalanced CTM - some PDFs (especially CAD-generated ones) start their
       content stream with a bare `cm` instruction (e.g. a 0.12 scale factor)
       that is never closed by a matching q/Q.  Any content appended afterwards
       inherits that transform, placing the watermark in the wrong coordinate
       space.  Bracketing the existing stream in q/Q fixes this.

    2. Non-zero /Rotate - pages with a /Rotate entry are rendered in a rotated
       coordinate space.  Drawing on them with TextWriter + morph applies the
       rotation matrix in the wrong physical space, producing a mirrored or
       wrongly-angled watermark.  The standard fix (clean_contents) also
       de-rotates the coordinate system but re-encodes font streams in the
       process, corrupting CAD PDFs.  Instead, an explicit compensating `cm`
       matrix is prepended to the content stream, every page box is mapped
       through the same transform, and /Rotate is set to 0 - all without
       touching any font or resource objects.

    The page boxes matter: the crop box decides what a viewer shows. Dropping
    it would reveal whatever the author had cropped away - in a copy that is
    about to be sent to someone outside.
    """
    rotation = page.rotation % 360

    if rotation not in (90, 180, 270):
        if not page.is_wrapped:
            page.wrap_contents()
        return

    doc, xref = page.parent, page.xref
    media = _box(_inherited(doc, xref, "MediaBox"))
    if media is None:
        if not page.is_wrapped:
            page.wrap_contents()
        return
    x0, y0, x1, y1 = media

    # Compensating matrix [a b c d e f] in PDF space (y up). It maps the
    # mediabox onto [0 0 W H] the way a viewer would display it, so the page
    # renders identically with /Rotate 0.
    if rotation == 90:      # (x, y) -> (y - y0, x1 - x)
        a, b, c, d, e, f = 0, -1, 1, 0, -y0, x1
    elif rotation == 180:   # (x, y) -> (x1 - x, y1 - y)
        a, b, c, d, e, f = -1, 0, 0, -1, x1, y1
    else:                   # 270: (x, y) -> (y1 - y, x - x0)
        a, b, c, d, e, f = 0, 1, -1, 0, y1, -x0

    def mapped(box):
        (bx0, by0, bx1, by1) = box
        pts = [(a * x + c * y + e, b * x + d * y + f)
               for x, y in ((bx0, by0), (bx1, by1))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    boxes = {key: _box(_inherited(doc, xref, key)) if key == "CropBox"
             else _box(doc.xref_get_key(xref, key)[1])
             for key in ("CropBox", "TrimBox", "BleedBox", "ArtBox")}

    content = page.read_contents()
    new_stream = f"q\n{a} {b} {c} {d} {e:g} {f:g} cm\n".encode() + content + b"\nQ\n"
    xrefs = page.get_contents()
    if xrefs:
        doc.update_stream(xrefs[0], new_stream, compress=True)
        if len(xrefs) > 1:
            page.set_contents(xrefs[0])

    _, _, nw, nh = mapped(media)
    # Raw keys are written directly: PyMuPDF's set_*box helpers work in
    # top-left page coordinates and set_mediabox() discards the other boxes.
    doc.xref_set_key(xref, "MediaBox", f"[0 0 {nw:g} {nh:g}]")
    for key, box in boxes.items():
        if box is not None:
            bx0, by0, bx1, by1 = mapped(box)
            # Keep every box inside the new mediabox, as the PDF spec requires.
            bx0, by0 = max(bx0, 0), max(by0, 0)
            bx1, by1 = min(bx1, nw), min(by1, nh)
            doc.xref_set_key(xref, key, f"[{bx0:g} {by0:g} {bx1:g} {by1:g}]")
    doc.xref_set_key(xref, "Rotate", "0")


# ---- Drawing --------------------------------------------------------------

def stamp_margin(page: pymupdf.Page) -> float:
    """Distance kept between a positioned stamp and the page edge."""
    return min(page.rect.width, page.rect.height) * 0.035


def layout_page(page: pymupdf.Page, style: Style, text: str,
                font: pymupdf.Font | None = None) -> dict:
    """
    Work out where every line of every copy of the text goes on this page.

    Returns the font, size, rotation matrix and a list of (origin, line), plus
    for a positioned stamp its bounding box - which the app uses to let the
    stamp be dragged on the preview. The page must already be normalised.
    """
    font   = font or style_font(style, text)
    angle_rad = radians(page_angle(page, style))
    cos_a, sin_a = cos(angle_rad), sin(angle_rad)
    fsize  = effective_font_size(page, style, text, font)
    lines  = text_lines(style, text)
    widths = [_text_width(font, line, fsize) for line in lines]
    block_w = max(widths)
    W, H = page.rect.width, page.rect.height

    # Text runs along (cos, -sin) in y-down page space; (sin, cos) points from
    # the top of the letters to the bottom. Each baseline is dropped by roughly
    # half the cap height so the letters, not the baseline, sit on centre.
    dx, dy = cos_a, -sin_a
    down_x, down_y = sin_a, cos_a
    drop = 0.35 * fsize
    lead = LEADING * fsize
    # Lines in a stamp line up with whichever side the stamp hugs.
    align = style.position[0] if style.mode() == "stamp" else 0.5

    def block(center_x: float, center_y: float) -> list[tuple[pymupdf.Point, str]]:
        """Where each line of one copy of the text starts, centred on a point."""
        out = []
        for i, (line, width) in enumerate(zip(lines, widths, strict=True)):
            off = (i - (len(lines) - 1) / 2) * lead
            lx, ly = center_x + off * down_x, center_y + off * down_y
            shift = (block_w - width) * (align - 0.5)
            out.append((pymupdf.Point(lx + (shift - width / 2) * dx + drop * down_x,
                                      ly + (shift - width / 2) * dy + drop * down_y), line))
        return out

    box = None
    mode = style.mode()
    if mode == "stamp":
        margin = stamp_margin(page)
        block_h = (len(lines) - 1) * lead + fsize
        ax, ay = style.position
        left = margin + ax * max(W - 2 * margin - block_w, 0)
        top = margin + ay * max(H - 2 * margin - block_h, 0)
        placed = block(left + block_w / 2, top + block_h / 2)
        box = pymupdf.Rect(left, top, left + block_w, top + block_h)
    elif mode == "single":
        placed = block(W / 2, H / 2)
    else:
        # Tiling: N evenly spaced bands, each filled with repeating text
        bands   = style.bands()
        spacing = _page_perp_span(page, angle_rad) / bands
        step    = block_w + style.tile_gap * fsize
        placed  = []
        for i in range(bands):
            offset = (i - (bands - 1) / 2) * spacing
            bcx = W / 2 - offset * down_x
            bcy = H / 2 - offset * down_y
            phase = (step / 2) * (i % 2)
            for t in _tile_origins_for_band(page, bcx, bcy, step, phase, cos_a, sin_a):
                placed.extend(block(bcx + t * dx, bcy + t * dy))

    return {
        "font": font, "fontsize": fsize, "placed": placed, "box": box,
        "matrix": pymupdf.Matrix(cos_a, sin_a, -sin_a, cos_a, 0, 0),
    }


def watermark_page(page: pymupdf.Page, style: Style | None = None,
                   text: str | None = None, font: pymupdf.Font | None = None) -> pymupdf.Page:
    """
    Apply the watermark to a single PDF page.

    `text` overrides style.text, for callers that have already filled in
    placeholders for this page; `font` lets a whole document share one loaded
    font. Returns the page object to keep using: rotated pages are rebuilt,
    and PyMuPDF wants a fresh handle afterwards.
    """
    style = style or Style()
    text = style.text if text is None else text
    if not text.strip():
        return page

    rotated = page.rotation % 360 != 0
    _normalise_page(page)
    if rotated:
        page = page.parent.reload_page(page)

    plan = layout_page(page, style, text, font)
    # TextWriter + morph supports arbitrary angles; insert_text() only does 0/90/180/270
    for origin, line in plan["placed"]:
        tw = pymupdf.TextWriter(page.rect)
        tw.append(origin, line, font=plan["font"], fontsize=plan["fontsize"],
                  right_to_left=bool(_RTL.search(line)))
        tw.write_text(page, color=style.color, opacity=style.opacity,
                      morph=(origin, plan["matrix"]), render_mode=1 if style.outline else 0)
    return page


def watermark_doc(doc: pymupdf.Document, style: Style | None = None,
                  file_label: str = "") -> None:
    """Watermark every page of an open document, filling in placeholders."""
    style = style or Style()
    pages = doc.page_count
    today = datetime.date.today()
    texts = [expand_text(style.text, index + 1, pages, file_label, today) for index in range(pages)]
    # One font for the whole document, holding every character any page needs.
    font = style_font(style, "".join(set("".join(texts))))
    for index, text in enumerate(texts):
        watermark_page(doc[index], style, text, font)


def flatten(doc: pymupdf.Document, dpi: int = 150) -> pymupdf.Document:
    """
    Return a copy of the document with every page turned into a picture.

    The watermark can then no longer be selected, searched for or deleted as
    text - it is part of the image. The cost is bigger files and no
    selectable text. Bookmarks and metadata are carried over; links and form
    fields cannot survive the conversion.
    """
    out = pymupdf.open()
    for page in doc:
        # Cap the pixel count so an A0 drawing does not need gigabytes of RAM.
        area_in2 = (page.rect.width / 72) * (page.rect.height / 72)
        page_dpi = min(dpi, int((40_000_000 / max(area_in2, 1e-6)) ** 0.5))
        pix = page.get_pixmap(dpi=page_dpi, alpha=False)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("jpg", jpg_quality=88))
    out.set_toc(doc.get_toc(simple=False))
    out.set_metadata({k: v for k, v in doc.metadata.items() if k not in ("format", "encryption")})
    return out


def long_path(path: Path | str) -> str:
    r"""
    Return a path string that is safe to hand to the filesystem on Windows.

    Windows silently caps ordinary paths at 260 characters, and a deep folder
    tree plus a long watermark name reaches that surprisingly fast - the
    failure shows up as a confusing "cannot open file" error partway through an
    otherwise healthy run. Prefixing an absolute path with \\?\ opts out of the
    limit, and both os.makedirs and PyMuPDF understand the prefix. Network
    paths (\\server\share\...) need the \\?\UNC\ form instead.

    The prefix requires a fully-qualified path with backslash separators, so
    the path is resolved first. macOS and Linux have no such limit, so this is
    a no-op there.
    """
    if sys.platform != "win32":
        return str(path)
    text = str(Path(path).resolve()) if not str(path).startswith("\\\\?\\") else str(path)
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def watermark_file(source: Path | str, destination: Path | str,
                   style: Style | None = None, *, flatten_dpi: int | None = None,
                   password: str | None = None) -> int:
    """
    Watermark every page of one PDF and save the result as a new file.

    The copy is written to a temporary name next to the destination and only
    renamed into place once complete, so a crash, a full disk or a cancelled
    run never leaves a half-written PDF behind looking like a finished one.

    A PDF that only restricts editing or printing (an "owner password") opens
    without a password; its copy gets the same restrictions back, under a new
    random owner password. A PDF that needs a password just to open is
    refused unless `password` is given.

    Returns the number of pages. Raises WatermarkError on failure.
    """
    source, destination = Path(source), Path(destination)
    try:
        doc = pymupdf.open(long_path(source))
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise WatermarkError("not a readable PDF - the file may be damaged") from exc
    except FileNotFoundError as exc:
        raise WatermarkError("file not found") from exc

    try:
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                raise WatermarkError("password-protected - open it, save an unprotected copy, and try again")
        if doc.page_count == 0:
            raise WatermarkError("the PDF has no pages")

        restricted = bool(doc.metadata.get("encryption")) and not doc.needs_pass
        permissions = doc.permissions

        watermark_doc(doc, style, source.stem)
        result = flatten(doc, flatten_dpi) if flatten_dpi else doc

        os.makedirs(long_path(destination.parent), exist_ok=True)
        temp = destination.with_name(f".{destination.stem}.{secrets.token_hex(4)}.tmp")
        save_args = {"garbage": 1, "deflate": True}
        if restricted:
            save_args.update(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                             owner_pw=secrets.token_urlsafe(24), user_pw="",
                             permissions=permissions)
        try:
            result.save(long_path(temp), **save_args)
            os.replace(long_path(temp), long_path(destination))
        except PermissionError as exc:
            raise WatermarkError("could not save - the output file may be open in another program") from exc
        except OSError as exc:
            raise WatermarkError(f"could not save - {exc.strerror or exc}") from exc
        finally:
            if os.path.exists(long_path(temp)):
                os.remove(long_path(temp))
        pages = doc.page_count
        if result is not doc:
            result.close()
        return pages
    finally:
        doc.close()


def check_pdf(path: Path | str) -> str | None:
    """
    None if the PDF can be watermarked, otherwise a short reason why not.

    Cheap - PyMuPDF opens lazily - so it can run over a whole folder the
    moment it is added, and problems show up before anything is written.
    """
    try:
        doc = pymupdf.open(long_path(path))
    except Exception:  # noqa: BLE001 - any failure to open means unreadable
        return "damaged or not really a PDF"
    try:
        if doc.needs_pass:
            return "password-protected"
        if doc.page_count == 0:
            return "has no pages"
        return None
    finally:
        doc.close()


def render_preview(source: Path | str, style: Style | None = None, page_index: int = 0,
                   max_px: int = 900, password: str | None = None,
                   with_box: bool = False) -> bytes | tuple[bytes, dict | None]:
    """
    PNG bytes of one page with the watermark applied, for on-screen preview.
    Nothing is written to disk.

    With with_box, also returns where a positioned stamp landed, as fractions
    of the page: {"x0", "y0", "x1", "y1", "mx", "my"} (mx/my being the margin)
    - or None for the other layouts.
    """
    source = Path(source)
    doc = pymupdf.open(long_path(source))
    try:
        if doc.needs_pass and not (password and doc.authenticate(password)):
            raise WatermarkError("password-protected")
        style = style or Style()
        index = max(0, min(page_index, doc.page_count - 1))
        text = expand_text(style.text, index + 1, doc.page_count, source.stem)
        font = style_font(style, text)
        page = watermark_page(doc[index], style, text, font)
        zoom = max_px / max(page.rect.width, page.rect.height)
        png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        if not with_box:
            return png
        box = layout_page(page, style, text, font)["box"]
        if box is None:
            return png, None
        W, H, m = page.rect.width, page.rect.height, stamp_margin(page)
        return png, {"x0": box.x0 / W, "y0": box.y0 / H, "x1": box.x1 / W, "y1": box.y1 / H,
                     "mx": m / W, "my": m / H}
    finally:
        doc.close()


# ---- Minimal CLI ----------------------------------------------------------

def collect_pdf_paths(inputs: list[str]) -> tuple[list[Path], dict[Path, Path]]:
    """
    Resolve CLI arguments to a flat list of PDF paths and a mapping of each
    path to its 'root' (the directory the user originally pointed at).
    """
    pdf_paths: list[Path] = []
    roots: dict[Path, Path] = {}

    for arg in inputs:
        p = Path(arg).resolve()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if (f.is_file() and f.suffix.lower() == ".pdf"
                        and not f.name.startswith(".") and OUTPUT_FOLDER not in f.parts):
                    pdf_paths.append(f)
                    roots[f] = p
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdf_paths.append(p)
            roots[p] = p.parent
        elif p.is_file():
            print(f"  WARNING: skipping '{arg}' - not a .pdf file")
        else:
            print(f"  WARNING: skipping '{arg}' - path not found")

    return list(dict.fromkeys(pdf_paths)), roots


def get_output_path(pdf_path: Path, root: Path) -> Path:
    """Mirror the original folder structure under root/watermarked/."""
    relative = pdf_path.relative_to(root)
    return root / OUTPUT_FOLDER / relative.parent / (relative.stem + OUTPUT_SUFFIX + ".pdf")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Apply a diagonal text watermark to PDF files. "
                    "For more options use batch_watermark.py.",
    )
    parser.add_argument("inputs", nargs="+", help="PDF files or directories to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without writing files")
    args = parser.parse_args()

    pdf_paths, roots = collect_pdf_paths(args.inputs)
    if not pdf_paths:
        print("No PDF files found. Nothing to do.")
        sys.exit(0)

    print(f"Found {len(pdf_paths)} PDF(s) to process.{' [DRY RUN]' if args.dry_run else ''}\n")
    success = 0
    for path in pdf_paths:
        output = get_output_path(path, roots[path])
        if args.dry_run:
            print(f"  [dry-run] {path}  ->  {output}")
            success += 1
            continue
        try:
            watermark_file(path, output)
            print(f"  OK  {path.name}  ->  {output}")
            success += 1
        except WatermarkError as exc:
            print(f"  ERR {path.name} - {exc}")

    print(f"\nDone. {success}/{len(pdf_paths)} file(s) watermarked successfully.")
    sys.exit(0 if success == len(pdf_paths) else 1)


if __name__ == "__main__":
    main()

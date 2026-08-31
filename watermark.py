"""
watermark.py - The watermarking engine: page geometry, rotation handling, drawing.

This module does the actual work of stamping text onto a PDF page, and exposes
a small command line of its own for quick one-off jobs. For anything more
involved (several watermarks in one run, output folders per client, arbitrary
text from the command line) use batch_watermark.py, which imports this module
and drives it.

Requires:
    pip install pymupdf

Usage:
    # Single file
    python watermark.py report.pdf

    # Entire directory (recursive - mirrors folder structure in output)
    python watermark.py contracts/

    # Multiple directories or mixed inputs
    python watermark.py contracts/ invoices/ extra.pdf

    # Preview without writing anything
    python watermark.py contracts/ --dry-run

The text and appearance come from the CONFIG block below - edit it, or set the
values at runtime the way batch_watermark.py does:

    import watermark as wm
    wm.WATERMARK_TEXT = "CONFIDENTIAL"
    wm.watermark_page(page)

Every value is read at call time, so changing one between documents takes
effect immediately.

Output:
    Watermarked copies are saved to a 'watermarked/' subfolder inside
    the input directory. Originals are never modified.

    contracts/
    |-- q1/deal_a.pdf              <- untouched
    `-- watermarked/
        `-- q1/deal_a_watermarked.pdf
"""

import argparse
import sys
from math import cos, radians, sin
from pathlib import Path

import pymupdf

# ---- CONFIG ---------------------------------------------------------------
# Defaults for every watermark. batch_watermark.py overrides several of these
# from command-line flags; change them here to move the defaults themselves.

# The text to stamp on each page. Kept short - it is scaled to fit the page,
# so a long string simply becomes a smaller one.
WATERMARK_TEXT   = "CONFIDENTIAL"

# One of PyMuPDF's 14 built-in PDF fonts, so no font file has to ship with the
# tool: helv (Helvetica), tiro (Times), cour (Courier), and their bold/italic
# variants such as hebo, tibo, cobo. Anything else needs a real font file.
FONT_NAME        = "helv"

# Reference size in points (72 points = 1 inch). This is NOT the size that gets
# drawn: effective_font_size() rescales it per page so an A0 drawing and an A4
# letter both end up proportional. It only sets the proportions being measured.
FONT_SIZE        = 72

# RGB as floats from 0 to 1, not 0-255. (0, 0, 0) is black, (1, 1, 1) white,
# (0.6, 0.6, 0.6) a mid grey that stays readable over both text and drawings.
FONT_COLOR       = (0.6, 0.6, 0.6)

# 0 is invisible, 1 fully opaque. Around 0.3-0.5 marks the document clearly
# while leaving the content underneath legible.
OPACITY          = 0.4

# Any angle in degrees, counter-clockwise from horizontal. 45 is the classic
# diagonal; 0 gives a straight horizontal band across the middle of the page.
ROTATION_DEGREES = 45

# 1 draws a single large line across the centre of the page. Values above 1
# switch to tiling: N diagonal bands, each repeating the text edge to edge,
# which is much harder to crop or paint out but busier to read through.
REPEAT_LINES     = 1

# Only used when REPEAT_LINES > 1: the gap between repeats along a band, as a
# multiple of the text width. Larger values spread the tiles further apart.
LINE_GAP         = 1.5

# Subfolder created inside the input directory by this file's own CLI.
# batch_watermark.py names its output folders differently, but still skips
# anything found inside a folder with this name so it never re-watermarks
# its own output.
OUTPUT_FOLDER    = "watermarked"

# Appended to every output filename, before the .pdf extension.
OUTPUT_SUFFIX    = "_watermarked"
# ---------------------------------------------------------------------------


def collect_pdf_paths(inputs: list[str]) -> tuple[list[Path], dict[Path, Path]]:
    """
    Resolve CLI arguments to a flat list of PDF paths and a mapping of each
    path to its 'root' (the directory the user originally pointed at).

    The root is used later to reconstruct the relative folder structure inside
    the watermarked/ output directory.
    """
    pdf_paths: list[Path] = []
    roots: dict[Path, Path] = {}

    for arg in inputs:
        p = Path(arg).resolve()

        if p.is_dir():
            found = sorted(p.rglob("*.pdf"))
            for f in found:
                # Skip anything already inside a watermarked/ folder so we
                # don't accidentally re-process our own output.
                if OUTPUT_FOLDER in f.parts:
                    continue
                pdf_paths.append(f)
                roots[f] = p

        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdf_paths.append(p)
            roots[p] = p.parent

        elif p.is_file():
            print(f"  WARNING: skipping '{arg}' - not a .pdf file")

        else:
            print(f"  WARNING: skipping '{arg}' - path not found")

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in pdf_paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    return unique, roots


def get_output_path(pdf_path: Path, root: Path) -> Path:
    """
    Compute the output path for a watermarked copy, mirroring the original
    folder structure under root/watermarked/.

    Example:
        root     = /docs/contracts
        pdf_path = /docs/contracts/q1/deal_a.pdf
        output   = /docs/contracts/watermarked/q1/deal_a_watermarked.pdf
    """
    relative = pdf_path.relative_to(root)
    output = root / OUTPUT_FOLDER / relative.parent / (relative.stem + OUTPUT_SUFFIX + ".pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _page_perp_span(page: pymupdf.Page, angle_rad: float) -> float:
    """
    Total space the page has in the direction perpendicular to the watermark text.
    Found by projecting all four page corners onto the perpendicular axis.
    """
    W, H   = page.rect.width, page.rect.height
    px, py = sin(angle_rad), -cos(angle_rad)
    projs  = [0, W * px, H * py, W * px + H * py]
    return max(projs) - min(projs)


def effective_font_size(page: pymupdf.Page) -> float:
    """
    Auto-scales font size purely from page geometry so the watermark always
    looks proportional regardless of page size (A4, A3, A2, custom, etc.).

    REPEAT_LINES = 1  ->  scale so the rotated bounding box fills 90% of the
                         page in both axes - no clipping, no wasted space.
    REPEAT_LINES > 1  ->  scale so ~2 full text instances are visible per
                         diagonal band, capped so N bands don't overlap.

    FONT_SIZE is used only as a reference to measure text proportions;
    the returned size may be larger or smaller than FONT_SIZE.
    """
    font      = pymupdf.Font(FONT_NAME)
    angle_rad = radians(ROTATION_DEGREES)
    font_h    = font.ascender - font.descender

    if REPEAT_LINES == 1:
        tw    = font.text_length(WATERMARK_TEXT, fontsize=FONT_SIZE)
        th    = font_h * FONT_SIZE
        bb_w  = abs(tw * cos(angle_rad)) + abs(th * sin(angle_rad))
        bb_h  = abs(tw * sin(angle_rad)) + abs(th * cos(angle_rad))
        # No upper cap - large pages get proportionally larger text
        scale = min(page.rect.width * 0.90 / bb_w,
                    page.rect.height * 0.90 / bb_h)
        return FONT_SIZE * scale

    # Tiling: target ~2 visible instances per diagonal band.
    page_diag  = (page.rect.width ** 2 + page.rect.height ** 2) ** 0.5
    tw_at_fs   = font.text_length(WATERMARK_TEXT, fontsize=FONT_SIZE)
    tw_target  = page_diag / (2.0 * (1.0 + LINE_GAP))
    tile_fs    = FONT_SIZE * tw_target / tw_at_fs

    # Cap so N bands fit the perpendicular span without overlapping each other
    perp_span  = _page_perp_span(page, angle_rad)
    slot       = perp_span / REPEAT_LINES
    max_fs     = slot * 0.85 / font_h

    # No FONT_SIZE upper cap - let geometry decide for all page sizes
    return min(tile_fs, max_fs)


def _tile_origins_for_band(page: pymupdf.Page, bcx: float, bcy: float,
                            text_width: float, step: float,
                            cos_a: float, sin_a: float) -> list[pymupdf.Point]:
    """
    Return all text anchor points needed to tile one diagonal band edge-to-edge.

    The band centre is (bcx, bcy). Text instances are placed every `step` points
    along the text direction, starting before the page and ending after it so the
    pattern is seamless at both edges.
    """
    W, H = page.rect.width, page.rect.height
    corners = [(0, 0), (W, 0), (0, H), (W, H)]

    # Project page corners onto the text direction (relative to band centre)
    t_vals = [(cx - bcx) * cos_a + (cy - bcy) * sin_a for cx, cy in corners]
    t_min, t_max = min(t_vals) - step, max(t_vals) + step

    # Align grid so instances land at consistent positions across bands
    t_start = t_min - (t_min % step)

    origins = []
    t = t_start
    while t <= t_max:
        # Anchor = t position along text direction, shifted left by half text width
        # so that the visual midpoint of each instance is at t
        ox = bcx + (t - text_width / 2) * cos_a
        oy = bcy + (t - text_width / 2) * sin_a
        origins.append(pymupdf.Point(ox, oy))
        t += step
    return origins


def _normalise_page(page: pymupdf.Page) -> None:
    """
    Bring the page to a clean, rotation-free state before watermarking.

    Two problems can corrupt appended watermark text:

    1. Unbalanced CTM - some PDFs (especially CAD-generated ones) start their
       content stream with a bare `cm` instruction (e.g. a 0.12 scale factor)
       that is never closed by a matching q/Q.  Any content appended afterwards
       inherits that transform, placing the watermark in the wrong coordinate
       space.  wrap_contents() brackets the existing stream in q/Q to fix this.

    2. Non-zero /Rotate - pages with a /Rotate entry are rendered in a rotated
       coordinate space.  Drawing on them with TextWriter + morph applies the
       rotation matrix in the wrong physical space, producing a mirrored or
       wrongly-angled watermark.  The standard fix (clean_contents) also
       de-rotates the coordinate system but re-encodes font streams in the
       process, corrupting CAD PDFs.  Instead, we prepend an explicit
       compensating `cm` matrix to the content stream, update the MediaBox to
       the post-rotation dimensions, and set /Rotate to 0 - all without
       touching any font or resource objects.
    """
    rotation = page.rotation

    if rotation == 0:
        if not page.is_wrapped:
            page.wrap_contents()
        return

    # Physical (pre-rotation) dimensions from the MediaBox.
    pw = page.mediabox.width
    ph = page.mediabox.height

    # Compensating cm matrix and new MediaBox for each standard rotation.
    # The matrix maps physical PDF coordinates to the post-rotation display
    # space so that the page renders identically with /Rotate: 0.
    if rotation == 90:
        # Viewer rotates 90 deg CW -> compensate with 90 deg CW in content.
        # (x,y) -> (y, pw-x);  new bounds [0,0, ph, pw]
        cm = f"0 -1 1 0 0 {pw}"
        new_box = pymupdf.Rect(0, 0, ph, pw)
    elif rotation == 180:
        # Viewer rotates 180 deg -> compensate with 180 deg in content.
        # (x,y) -> (pw-x, ph-y);  new bounds [0,0, pw, ph]
        cm = f"-1 0 0 -1 {pw} {ph}"
        new_box = pymupdf.Rect(0, 0, pw, ph)
    elif rotation == 270:
        # Viewer rotates 270 deg CW -> compensate with 270 deg CW in content.
        # (x,y) -> (ph-y, x);  new bounds [0,0, ph, pw]
        cm = f"0 1 -1 0 {ph} 0"
        new_box = pymupdf.Rect(0, 0, ph, pw)
    else:
        # Uncommon angle - fall back to safe wrap only.
        if not page.is_wrapped:
            page.wrap_contents()
        return

    # Read the full (possibly multi-stream) content, wrap it with the CTM.
    content = page.read_contents()
    new_stream = (f"q\n{cm} cm\n").encode() + content + b"\nQ\n"

    # Consolidate into the first existing content xref (which is already a
    # stream object) so we don't need to allocate and initialise a new one.
    xrefs = page.get_contents()
    if xrefs:
        page.parent.update_stream(xrefs[0], new_stream, compress=True)
        if len(xrefs) > 1:
            page.set_contents(xrefs[0])
    else:
        # Blank page - nothing to de-rotate; just mark as normalised.
        pass

    # Commit the geometry changes.
    page.set_mediabox(new_box)
    page.set_rotation(0)


def watermark_page(page: pymupdf.Page) -> None:
    """Apply the watermark to a single PDF page."""

    _normalise_page(page)

    angle_rad = radians(ROTATION_DEGREES)
    cos_a, sin_a = cos(angle_rad), sin(angle_rad)

    # TextWriter + morph supports arbitrary angles; insert_text() only does 0/90/180/270
    rot_matrix = pymupdf.Matrix(cos_a, sin_a, -sin_a, cos_a, 0, 0)
    font  = pymupdf.Font(FONT_NAME)
    fsize = effective_font_size(page)

    text_width = pymupdf.get_text_length(WATERMARK_TEXT, fontname=FONT_NAME, fontsize=fsize)
    step       = text_width * (1.0 + LINE_GAP)   # distance between repeated instances

    cx, cy         = page.rect.width / 2, page.rect.height / 2
    perp_x, perp_y = sin_a, -cos_a

    if REPEAT_LINES == 1:
        # Single line: one instance centred on the page, no clipping
        origin = pymupdf.Point(
            cx - (text_width / 2) * cos_a,
            cy + (text_width / 2) * sin_a,
        )
        all_origins = [origin]
    else:
        # Tiling: N evenly spaced diagonal bands, each filled with repeating text
        perp_span = _page_perp_span(page, angle_rad)
        spacing   = perp_span / REPEAT_LINES
        offsets   = [i - (REPEAT_LINES - 1) / 2 for i in range(REPEAT_LINES)]

        all_origins = []
        for offset in offsets:
            bcx = cx + offset * spacing * perp_x
            bcy = cy + offset * spacing * perp_y
            all_origins.extend(
                _tile_origins_for_band(page, bcx, bcy, text_width, step, cos_a, sin_a)
            )

    for origin in all_origins:
        tw = pymupdf.TextWriter(page.rect)
        tw.append(origin, WATERMARK_TEXT, font=font, fontsize=fsize)
        tw.write_text(page, color=FONT_COLOR, opacity=OPACITY, morph=(origin, rot_matrix))


def watermark_document(pdf_path: Path, root: Path, dry_run: bool = False) -> bool:
    """
    Open one PDF, watermark every page, and save a copy to the output folder.
    Returns True on success, False on error (error is printed but not raised).
    """
    output_path = get_output_path(pdf_path, root)

    if dry_run:
        print(f"  [dry-run] {pdf_path}  ->  {output_path}")
        return True

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as e:
        print(f"  ERROR: could not open '{pdf_path}' as a PDF - {e}")
        return False

    for page in doc:
        watermark_page(page)

    try:
        doc.save(str(output_path))
    except PermissionError:
        print(f"  ERROR: permission denied writing '{output_path}'")
        doc.close()
        return False

    doc.close()
    print(f"  OK  {pdf_path.name}  ->  {output_path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply a diagonal text watermark to PDF files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python watermark.py report.pdf\n"
            "  python watermark.py contracts/\n"
            "  python watermark.py contracts/ invoices/ extra.pdf\n"
            "  python watermark.py contracts/ --dry-run"
        ),
    )
    parser.add_argument("inputs", nargs="+", help="PDF files or directories to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without writing files")
    args = parser.parse_args()

    pdf_paths, roots = collect_pdf_paths(args.inputs)

    if not pdf_paths:
        print("No PDF files found. Nothing to do.")
        sys.exit(0)

    mode = " [DRY RUN]" if args.dry_run else ""
    print(f"Found {len(pdf_paths)} PDF(s) to process.{mode}\n")

    success = 0
    for path in pdf_paths:
        if watermark_document(path, roots[path], dry_run=args.dry_run):
            success += 1

    print(f"\nDone. {success}/{len(pdf_paths)} file(s) watermarked successfully.")


if __name__ == "__main__":
    main()

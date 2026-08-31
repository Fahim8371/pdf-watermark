"""
make_sample.py - Generate a sample PDF to try the watermarker on.

Running this writes sample_report.pdf next to this file: a two-page document
with an A4 portrait report page and an A3 landscape drawing page. The mismatch
is deliberate - it is the case that trips up naive watermarking, and it is what
produces the "same document, two page sizes" figure in the README.

    python examples/make_sample.py
    python batch_watermark.py examples/sample_report.pdf --text "CONFIDENTIAL"

Nothing in here is needed to use the tool. It exists so anyone cloning the
repo has something safe to experiment on without reaching for a real document.
"""

from pathlib import Path

import pymupdf

# PDF measurements are in points: 72 points to an inch.
A4_PORTRAIT = (595, 842)
A3_LANDSCAPE = (1191, 842)

INK = (0.1, 0.1, 0.12)
MUTED = (0.45, 0.45, 0.5)
RULE = (0.8, 0.8, 0.83)

BODY = (
    "This document is generated sample content. It exists so the watermarking "
    "tool in this repository can be demonstrated on a realistic page without "
    "using a real document. The paragraphs below carry no meaning; they are "
    "here to give the watermark something to sit over, so its opacity and "
    "contrast can be judged the way they would be judged on a real page."
)


def report_page(doc: pymupdf.Document) -> None:
    """An A4 portrait page: heading, body copy, and a small table."""
    page = doc.new_page(width=A4_PORTRAIT[0], height=A4_PORTRAIT[1])
    left, right = 60, A4_PORTRAIT[0] - 60

    page.insert_textbox(pymupdf.Rect(left, 60, right, 84), "SAMPLE DOCUMENT",
                        fontsize=9, fontname="hebo", color=MUTED)
    page.insert_textbox(pymupdf.Rect(left, 88, right, 130), "Facility Design Report",
                        fontsize=24, fontname="hebo", color=INK)
    page.insert_textbox(pymupdf.Rect(left, 132, right, 152),
                        "Revision B  |  Prepared for demonstration purposes",
                        fontsize=10, fontname="helv", color=MUTED)
    page.draw_line(pymupdf.Point(left, 164), pymupdf.Point(right, 164),
                   color=RULE, width=1)

    # insert_textbox draws nothing at all if the text does not fit the rect it
    # is given, so each box below is comfortably taller than its content.
    y = 190
    for number, heading in enumerate(("Scope", "Basis of design", "Assumptions"), start=1):
        page.insert_textbox(pymupdf.Rect(left, y, right, y + 26),
                            f"{number}.0  {heading}",
                            fontsize=13, fontname="hebo", color=INK)
        page.insert_textbox(pymupdf.Rect(left, y + 26, right, y + 126), BODY,
                            fontsize=10.5, fontname="helv", color=INK, lineheight=1.5)
        y += 138

    # A small table, to give the watermark some ruled lines to cross.
    table_top = y + 10
    rows = [
        ("Reference", "Description", "Status"),
        ("DR-001", "General arrangement, Level 01", "Issued"),
        ("DR-002", "Services coordination", "In review"),
        ("DR-003", "Elevations and sections", "Draft"),
    ]
    row_height = 26
    columns = (left, left + 110, right - 100, right)

    for index, row in enumerate(rows):
        top = table_top + index * row_height
        font = "hebo" if index == 0 else "helv"
        if index == 0:
            page.draw_rect(pymupdf.Rect(left, top, right, top + row_height),
                           color=None, fill=(0.94, 0.94, 0.96))
        for column, cell in enumerate(row):
            page.insert_textbox(
                pymupdf.Rect(columns[column] + 8, top + 8, columns[column + 1], top + row_height),
                cell, fontsize=9.5, fontname=font, color=INK,
            )
        page.draw_line(pymupdf.Point(left, top + row_height),
                       pymupdf.Point(right, top + row_height), color=RULE, width=0.7)


def drawing_page(doc: pymupdf.Document) -> None:
    """
    An A3 landscape page standing in for a CAD sheet.

    Deliberately a different size and orientation from page one: a watermark
    scaled for A4 would look lost here, which is exactly what the per-page
    sizing in watermark.py is for.
    """
    page = doc.new_page(width=A3_LANDSCAPE[0], height=A3_LANDSCAPE[1])
    margin = 40
    frame = pymupdf.Rect(margin, margin, A3_LANDSCAPE[0] - margin, A3_LANDSCAPE[1] - margin)
    page.draw_rect(frame, color=INK, width=1.2)

    # A schematic floor plan: outer walls, a grid, and a few rooms.
    plan = pymupdf.Rect(90, 110, 780, 700)
    page.draw_rect(plan, color=INK, width=2)

    for i in range(1, 6):
        x = plan.x0 + (plan.width / 6) * i
        page.draw_line(pymupdf.Point(x, plan.y0), pymupdf.Point(x, plan.y1),
                       color=RULE, width=0.6)
    for i in range(1, 4):
        y = plan.y0 + (plan.height / 4) * i
        page.draw_line(pymupdf.Point(plan.x0, y), pymupdf.Point(plan.x1, y),
                       color=RULE, width=0.6)

    for rect, label in (
        (pymupdf.Rect(120, 140, 380, 380), "PLANT ROOM"),
        (pymupdf.Rect(400, 140, 750, 300), "MAIN HALL"),
        (pymupdf.Rect(400, 320, 560, 500), "STORE"),
        (pymupdf.Rect(120, 410, 340, 660), "WORKSHOP"),
    ):
        page.draw_rect(rect, color=INK, width=1.4)
        page.insert_textbox(pymupdf.Rect(rect.x0 + 10, rect.y0 + 10, rect.x1, rect.y0 + 30),
                            label, fontsize=10, fontname="hebo", color=INK)

    # Title block, bottom right, the way a real sheet would carry one.
    block = pymupdf.Rect(830, 480, frame.x1 - 20, 700)
    page.draw_rect(block, color=INK, width=1.2)
    page.insert_textbox(pymupdf.Rect(block.x0 + 14, block.y0 + 16, block.x1, block.y0 + 60),
                        "GENERAL ARRANGEMENT\nLEVEL 01",
                        fontsize=14, fontname="hebo", color=INK, lineheight=1.3)
    page.insert_textbox(pymupdf.Rect(block.x0 + 14, block.y0 + 80, block.x1, block.y1),
                        "Sample drawing\nScale 1:100 @ A3\nDrawing no. DR-001\nRevision B",
                        fontsize=10, fontname="helv", color=MUTED, lineheight=1.6)


def main() -> None:
    doc = pymupdf.open()
    report_page(doc)
    drawing_page(doc)

    output = Path(__file__).with_name("sample_report.pdf")
    doc.save(str(output))
    doc.close()
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()

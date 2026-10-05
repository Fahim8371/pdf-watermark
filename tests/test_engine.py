"""The drawing engine: geometry, rotation, fonts, placeholders, saving."""

import pymupdf
import pytest
from conftest import A3, A4, make_page, red_centroid, red_extent_centre, save

import watermark as wm

ROTATIONS = [0, 90, 180, 270]


def pixels(page, dpi=30):
    return page.get_pixmap(dpi=dpi, alpha=False).samples


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("crop", [None, (0, 0, 500, 700)])
@pytest.mark.parametrize("origin", [(0, 0), (50, -30)])
def test_normalising_does_not_change_how_the_page_looks(rotation, crop, origin):
    """De-rotation must render pixel-identically - including the crop box."""
    if crop and origin != (0, 0):
        crop = (crop[0] + origin[0], crop[1] + origin[1], crop[2] + origin[0], crop[3] + origin[1])
    doc = pymupdf.open()
    page = make_page(doc, rotation=rotation, crop=crop, origin=origin)
    before = (page.rect.width, page.rect.height, pixels(page))

    wm._normalise_page(page)
    page = doc.reload_page(page)

    assert page.rotation == 0
    assert (page.rect.width, page.rect.height) == pytest.approx(before[:2])
    assert pixels(page) == before[2]


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("size", [A4, A3, (1191, 842)])
def test_watermark_is_centred_on_the_visible_page(rotation, size, red_style):
    doc = pymupdf.open()
    page = make_page(doc, size=size, rotation=rotation)
    page = wm.watermark_page(page, red_style)
    cx, cy, count = red_centroid(page)
    assert count > 50, "watermark not visible"
    assert cx == pytest.approx(0.5, abs=0.04)
    assert cy == pytest.approx(0.5, abs=0.04)


def test_watermark_is_centred_on_a_cropped_rotated_page(red_style):
    doc = pymupdf.open()
    page = make_page(doc, rotation=90, crop=(0, 100, 595, 742))
    page = wm.watermark_page(page, red_style)
    cx, cy, count = red_centroid(page)
    assert count > 50
    assert (cx, cy) == pytest.approx((0.5, 0.5), abs=0.04)


def test_unbalanced_transform_in_content_stream_is_contained(red_style):
    """CAD-style PDFs leave a bare `cm` open; the watermark must not inherit it."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    xref = page.get_contents()[0] if page.get_contents() else None
    if xref is None:
        page.insert_text((40, 60), "x")
        xref = page.get_contents()[0]
    doc.update_stream(xref, b"0.12 0 0 0.12 0 0 cm\n" + page.read_contents())
    page = wm.watermark_page(doc.reload_page(page), red_style)
    cx, cy, count = red_centroid(page)
    assert count > 50
    assert (cx, cy) == pytest.approx((0.5, 0.5), abs=0.04)


@pytest.mark.parametrize("text", ["CONFIDENTIAL - Иванов", "CONFIDENTIAL - 株式会社トヨタ",
                                  "Für Łukasz Wójcik"])
def test_non_latin_text_is_drawn_and_centred(text, red_style):
    doc = pymupdf.open()
    page = make_page(doc)
    style = wm.Style(**{**red_style.__dict__, "text": text, "angle": 0})
    page = wm.watermark_page(page, style)
    cx, cy = red_extent_centre(page)
    assert cx == pytest.approx(0.5, abs=0.02)
    assert cy == pytest.approx(0.5, abs=0.02)
    assert text in page.get_text()


def test_tiling_keeps_long_text_readable():
    doc = pymupdf.open()
    page = make_page(doc)
    long = wm.Style(text="CONFIDENTIAL - Acme Construction Holdings", tile=4)
    short = wm.Style(text="DRAFT", tile=4)
    assert wm.effective_font_size(page, long) >= 18
    assert wm.effective_font_size(page, short) > wm.effective_font_size(page, long)


def test_tiling_covers_the_page(red_style):
    doc = pymupdf.open()
    page = make_page(doc)
    style = wm.Style(**{**red_style.__dict__, "tile": 4})
    page = wm.watermark_page(page, style)
    pix = page.get_pixmap(dpi=20, alpha=False)
    # Every quarter of the page should carry some of the mark.
    for qx in (0, 1):
        for qy in (0, 1):
            clip = pymupdf.Rect(qx * page.rect.width / 2, qy * page.rect.height / 2,
                                (qx + 1) * page.rect.width / 2, (qy + 1) * page.rect.height / 2)
            sub = page.get_pixmap(dpi=20, clip=clip, alpha=False)
            reds = sum(1 for i in range(0, len(sub.samples), 3)
                       if sub.samples[i] > 180 and sub.samples[i + 1] < 90)
            assert reds > 5, f"quarter {qx},{qy} has no watermark"
    assert pix.width > 0


def test_placeholders_are_filled_per_page(tmp_path):
    doc = pymupdf.open()
    for _ in range(3):
        make_page(doc)
    src = save(doc, tmp_path / "Tender pack.pdf")
    out = tmp_path / "out.pdf"
    wm.watermark_file(src, out, wm.Style(text="{file} - page {page} of {pages} - {date}"))
    result = pymupdf.open(out)
    assert "Tender pack - page 2 of 3 - " in result[1].get_text()
    assert wm.expand_text("x {date}", date=__import__("datetime").date(2026, 1, 2)) == "x 2026-01-02"
    assert wm.expand_text("{unknown} {page}", page=4) == "{unknown} 4"


def test_file_keeps_pages_sizes_bookmarks_and_links(tmp_path):
    doc = pymupdf.open()
    make_page(doc, size=A4)
    make_page(doc, size=A3, rotation=90)
    make_page(doc, size=A4)
    doc.set_toc([[1, "Intro", 1], [2, "Drawings", 2], [1, "End", 3]])
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(40, 40, 200, 70),
                        "uri": "https://example.com"})
    sizes = [(p.rect.width, p.rect.height) for p in doc]
    src = save(doc, tmp_path / "in.pdf")

    pages = wm.watermark_file(src, tmp_path / "out.pdf", wm.Style(text="X"))

    out = pymupdf.open(tmp_path / "out.pdf")
    assert pages == 3 == out.page_count
    assert [(p.rect.width, p.rect.height) for p in out] == pytest.approx(sizes)
    assert out.get_toc() == [[1, "Intro", 1], [2, "Drawings", 2], [1, "End", 3]]
    assert out[0].get_links()[0]["uri"] == "https://example.com"
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_editing_restrictions_are_kept(tmp_path):
    doc = pymupdf.open()
    make_page(doc)
    perms = pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY
    src = save(doc, tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
               owner_pw="secret-owner", user_pw="", permissions=perms)

    wm.watermark_file(src, tmp_path / "out.pdf", wm.Style(text="X"))

    out = pymupdf.open(tmp_path / "out.pdf")
    assert not out.needs_pass
    assert out.metadata["encryption"]
    assert not out.permissions & pymupdf.PDF_PERM_MODIFY
    assert not out.authenticate("secret-owner") & 4  # original owner password no longer applies


def test_password_protected_pdf_gives_a_clear_error(tmp_path):
    doc = pymupdf.open()
    make_page(doc)
    src = save(doc, tmp_path / "secret.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
               owner_pw="o", user_pw="u")
    with pytest.raises(wm.WatermarkError, match="password-protected"):
        wm.watermark_file(src, tmp_path / "out.pdf")
    wm.watermark_file(src, tmp_path / "out.pdf", password="u")
    assert (tmp_path / "out.pdf").exists()


def test_damaged_pdf_gives_a_clear_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\nthis is not really a pdf")
    with pytest.raises(wm.WatermarkError, match="damaged|no pages"):
        wm.watermark_file(bad, tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


def test_flatten_removes_selectable_text_but_keeps_structure(tmp_path):
    doc = pymupdf.open()
    make_page(doc)
    make_page(doc, size=A3)
    doc.set_toc([[1, "First", 1], [1, "Second", 2]])
    src = save(doc, tmp_path / "in.pdf")
    wm.watermark_file(src, tmp_path / "flat.pdf", wm.Style(text="SECRET"), flatten_dpi=72)
    out = pymupdf.open(tmp_path / "flat.pdf")
    assert out.page_count == 2
    assert out.get_toc() == [[1, "First", 1], [1, "Second", 2]]
    assert "SECRET" not in "".join(p.get_text() for p in out)
    assert out[1].rect.width == pytest.approx(A3[0])


def test_preview_returns_png(tmp_path):
    doc = pymupdf.open()
    make_page(doc, rotation=90)
    src = save(doc, tmp_path / "in.pdf")
    png = wm.render_preview(src, wm.Style(text="X"), max_px=300)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # Previewing must never touch the source.
    assert pymupdf.open(src)[0].rotation == 90


@pytest.mark.parametrize("value,expected", [
    ("red", (0xd9 / 255, 0x33 / 255, 0x33 / 255)),
    ("#000", (0, 0, 0)),
    ("FFFFFF", (1, 1, 1)),
])
def test_parse_color(value, expected):
    assert wm.parse_color(value) == pytest.approx(expected)


def test_parse_color_rejects_nonsense():
    with pytest.raises(ValueError, match="not a colour"):
        wm.parse_color("blurple")


def test_default_angle_follows_the_page_diagonal():
    doc = pymupdf.open()
    make_page(doc, size=A4)
    make_page(doc, size=(842, 595))
    portrait, landscape = doc[0], doc[1]
    style = wm.Style()
    assert wm.page_angle(portrait, style) == pytest.approx(54.75, abs=0.1)
    assert wm.page_angle(landscape, style) == pytest.approx(35.25, abs=0.1)
    assert wm.page_angle(portrait, wm.Style(angle=30)) == 30


def test_stacked_layout_splits_at_the_first_dash():
    style = wm.Style(layout="stacked")
    assert (style.layout, style.stack) == ("diagonal", True)
    assert wm.text_lines(style, "CONFIDENTIAL - Acme - Ltd") == ["CONFIDENTIAL", "Acme - Ltd"]
    assert wm.text_lines(style, "DRAFT") == ["DRAFT"]
    assert wm.text_lines(wm.Style(), "CONFIDENTIAL - Acme") == ["CONFIDENTIAL - Acme"]
    assert wm.text_lines(wm.Style(), "one\ntwo") == ["one", "two"]
    # Stamps can stack too, now that they can sit in a corner.
    assert wm.text_lines(wm.Style(layout="footer", stack=True), "A - B") == ["A", "B"]


def test_stacked_layout_draws_two_centred_lines(red_style):
    doc = pymupdf.open()
    page = make_page(doc)
    # At 0 degrees: rotated, a long line over a short one has a lopsided
    # bounding box even when the block itself is centred.
    style = wm.Style(**{**red_style.__dict__, "layout": "stacked", "angle": 0,
                        "text": "CONFIDENTIAL - Acme"})
    page = wm.watermark_page(page, style)
    assert "CONFIDENTIAL" in page.get_text() and "Acme" in page.get_text()
    cx, cy = red_extent_centre(page)
    assert (cx, cy) == pytest.approx((0.5, 0.5), abs=0.03)


@pytest.mark.parametrize("layout,lo,hi", [("header", 0.0, 0.1), ("footer", 0.9, 1.0)])
@pytest.mark.parametrize("rotation", [0, 90])
def test_header_and_footer_sit_on_the_edge(layout, lo, hi, rotation, red_style):
    doc = pymupdf.open()
    page = make_page(doc, rotation=rotation)
    style = wm.Style(**{**red_style.__dict__, "layout": layout, "text": "Prepared for Acme - page {page}"})
    page = wm.watermark_page(page, style, wm.expand_text(style.text, page=3))
    cx, cy = red_extent_centre(page)
    assert lo < cy < hi
    assert cx == pytest.approx(0.5, abs=0.02)
    assert 8 < wm.effective_font_size(page, style) < 16
    assert "page 3" in page.get_text()


def test_size_scales_the_text():
    doc = pymupdf.open()
    page = make_page(doc)
    full = wm.effective_font_size(page, wm.Style(text="DRAFT"))
    half = wm.effective_font_size(page, wm.Style(text="DRAFT", size=0.5))
    assert half == pytest.approx(full / 2)


def test_outline_draws_hollow_letters():
    doc = pymupdf.open()
    page = wm.watermark_page(make_page(doc), wm.Style(text="DRAFT", outline=True))
    assert b"1 Tr" in page.read_contents()


@pytest.mark.parametrize("name", list(wm.POSITIONS))
@pytest.mark.parametrize("rotation", [0, 90])
def test_every_grid_position_lands_in_its_spot(name, rotation, red_style):
    doc = pymupdf.open()
    page = make_page(doc, rotation=rotation)
    style = wm.Style(**{**red_style.__dict__, "layout": "position", "position": wm.POSITIONS[name],
                        "text": "CONFIDENTIAL - Acme", "size": 1.5})
    page = wm.watermark_page(page, style)
    cx, cy = red_extent_centre(page)
    want_x, want_y = wm.POSITIONS[name]
    # The stamp's centre moves from near one edge, through the middle, to the other.
    assert abs(cx - (0.15 + 0.7 * want_x)) < 0.2, f"{name}: x {cx:.2f}"
    assert abs(cy - (0.05 + 0.9 * want_y)) < 0.06, f"{name}: y {cy:.2f}"


def test_position_box_is_reported_for_dragging(tmp_path):
    doc = pymupdf.open()
    make_page(doc)
    src = save(doc, tmp_path / "in.pdf")
    png, box = wm.render_preview(src, wm.Style(text="X", layout="position", position=(1, 0)),
                                 max_px=200, with_box=True)
    assert png[:4] == b"\x89PNG"
    assert box["x1"] == pytest.approx(1 - box["mx"], abs=0.01)
    assert box["y0"] == pytest.approx(box["my"], abs=0.01)
    _, none = wm.render_preview(src, wm.Style(text="X"), max_px=200, with_box=True)
    assert none is None


def test_legacy_font_codes_still_work():
    style = wm.Style(font="tibo")
    assert (style.font, style.bold, style.italic) == ("Times", True, False)


def test_builtin_bold_and_italic_change_the_font():
    regular = wm.style_font(wm.Style(font="Helvetica", bold=False, italic=False))
    bold = wm.style_font(wm.Style(font="Helvetica", bold=True))
    italic = wm.style_font(wm.Style(font="Helvetica", bold=False, italic=True))
    assert len({regular.name, bold.name, italic.name}) == 3


@pytest.mark.skipif(not __import__("fonts").find_face("Arial", False, False),
                    reason="Arial is not installed here")
def test_installed_font_is_embedded_as_a_small_subset(tmp_path):
    doc = pymupdf.open()
    for _ in range(5):
        make_page(doc)
    src = save(doc, tmp_path / "in.pdf")
    wm.watermark_file(src, tmp_path / "out.pdf", wm.Style(text="CONFIDENTIAL - page {page}", font="Arial"))
    out = pymupdf.open(tmp_path / "out.pdf")
    assert "CONFIDENTIAL - page 3" in out[2].get_text()
    assert any("Arial" in f[3] for f in out.get_page_fonts(0))
    assert (tmp_path / "out.pdf").stat().st_size < 150_000   # the whole Arial file is ~1 MB


def test_unknown_font_falls_back_instead_of_failing(tmp_path):
    doc = pymupdf.open()
    make_page(doc)
    src = save(doc, tmp_path / "in.pdf")
    wm.watermark_file(src, tmp_path / "out.pdf", wm.Style(text="DRAFT", font="No Such Font 123"))
    assert "DRAFT" in pymupdf.open(tmp_path / "out.pdf")[0].get_text()

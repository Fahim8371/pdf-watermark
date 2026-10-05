"""
batch_watermark.py - Apply a text watermark to PDFs, in bulk or one at a time.

Point it at folders, at individual PDFs, or at a mix of both. Folders are
searched recursively and every non-PDF file is ignored, so you can aim it at a
messy document dump without sorting it first. Originals are never modified -
each watermarked file is written as a new copy.

    pip install pymupdf


QUICK START

    # Every PDF in a folder, and its subfolders
    python batch_watermark.py my_folder --text "CONFIDENTIAL"

    # A single PDF
    python batch_watermark.py report.pdf --text "DRAFT"

    # Folders and files together
    python batch_watermark.py my_folder extra.pdf --text "DRAFT"

    # One run, several watermarks - a separate copy of everything per text.
    # Useful when the same pack goes to several recipients and each copy needs
    # to be traceable back to whoever it was sent to.
    python batch_watermark.py my_folder --text "For Acme" --text "For Globex"

    # Shorthand for the line above: each name becomes "CONFIDENTIAL - <name>"
    python batch_watermark.py my_folder --company Acme Globex

    # See exactly what would be written, without writing it
    python batch_watermark.py my_folder --text "DRAFT" --dry-run

    # Placeholders are filled in per page: {page} {pages} {file} {date}
    python batch_watermark.py my_folder --text "For Acme - {date} - page {page} of {pages}"

    # Red, tiled, and flattened so the mark cannot be selected or deleted
    python batch_watermark.py my_folder --company Acme --color red --tile 4 --flatten


WHERE THE OUTPUT GOES

    A folder input is copied to a sibling folder next to it, with the original
    subfolder structure preserved:

        my_folder/                                  <- untouched
            drawings/plan.pdf
        my_folder _CONFIDENTIAL_Watermarked/        <- created
            drawings/plan_watermarked.pdf

    A file input is written next to the original:

        report.pdf                                  <- untouched
        report_watermarked.pdf                      <- created

    Pass --out DIR to send either one somewhere else instead.

    When several watermark texts are given, folder names already differ by
    text, and single files get the text added to their name too, so nothing
    from one text overwrites another.


HOW IT RELATES TO watermark.py

    watermark.py is the engine - it works out the font size for each page,
    handles rotated and CAD-generated pages, and draws the text. This file is
    the command line around it: collecting paths, naming outputs, looping.
    Appearance defaults (colour, opacity, angle, font) live in watermark.py's
    CONFIG block; the flags below override them for a single run.
"""

import argparse
import re
import sys
from pathlib import Path

import fonts
import watermark as wm
from watermark import long_path  # noqa: F401 - re-exported for older imports

# Appended to the name of each output folder, e.g. "Contracts _DRAFT_Watermarked".
# It doubles as the marker used to recognise - and skip - output folders on a
# second run, so watermarks never get stacked on top of each other.
FOLDER_SUFFIX = "_Watermarked"

# Windows forbids these characters in file and folder names, and the rest are no
# better as path components. Watermark text is free-form, so it has to be
# sanitised before any of it can end up in a path. (The trailing range covers
# control characters, which are illegal in names on every platform.)
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Longest slug used in a folder or file name. Most filesystems cap a single
# name at 255 characters; the slug shares that with the source name.
_SLUG_MAX = 80


def slugify(text: str) -> str:
    """
    Turn arbitrary watermark text into something usable as part of a path.

    Placeholders such as {page} are dropped, since they differ per page and
    mean nothing in a folder name ({date} is filled in, as it is the same for
    the whole run). Illegal characters become hyphens, runs of whitespace
    collapse to a single space, and leading or trailing spaces, dots and
    hyphens are trimmed - a trailing dot or space makes a Windows folder name
    unusable. Overlong text is cut short. Text that is nothing but illegal
    characters falls back to "Watermarked", so the run can still produce a
    valid folder instead of failing.
    """
    text = re.sub(r"\{(page|pages|file)\}", "", wm.expand_text(text, page=0, pages=0)
                  if "{date}" in text else text)
    cleaned = _ILLEGAL.sub("-", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    cleaned = cleaned[:_SLUG_MAX].rstrip(" .-")
    return cleaned or "Watermarked"


def is_pdf(path: Path) -> bool:
    """
    A real PDF file worth processing - matched case-insensitively.

    rglob("*.pdf") is case-sensitive on macOS and Linux, which silently
    skipped every REPORT.PDF there. Hidden files are skipped too: macOS
    leaves "._report.pdf" metadata files on USB sticks, network shares and
    unzipped archives, and they are not PDFs at all.
    """
    return path.suffix.lower() == ".pdf" and not path.name.startswith(".")


def source_label(folder: Path) -> str:
    """
    The folder's name, with a trailing "_Originals" style tag trimmed off.

    A common convention is to keep untouched source documents in a folder
    marked as the originals. Without this, the output would be named
    "Pack _Originals _DRAFT_Watermarked", which reads badly and is also wrong:
    the copy is not the originals.
    """
    name = folder.name
    for tag in ("_Originals", " _Originals", "_Original", "_originals"):
        if name.endswith(tag):
            return name[: -len(tag)].rstrip(" _")
    return name


def is_output_path(path: Path, folder_suffix: str) -> bool:
    """
    True if any part of the path is a watermarked output folder.

    This keeps a second run from picking up the first run's output and
    watermarking it again.
    """
    return any(
        part.endswith(folder_suffix) or part == wm.OUTPUT_FOLDER
        for part in path.parts
    )


def collect_sources(raw_paths: list[str]) -> tuple[list[Path], list[Path]]:
    """
    Split the command-line arguments into folders and individual PDF files.

    Bad arguments are reported and skipped rather than aborting the run: one
    typo in a long command should not throw away the rest of the batch.

    A file that also sits inside one of the given folders is dropped from the
    file list, since the folder scan will already pick it up - otherwise it
    would be watermarked twice and the second write would clash with the first.
    """
    folders: list[Path] = []
    files: list[Path] = []

    for raw in raw_paths:
        # resolve() makes the path absolute and removes any "..", which both
        # long_path() and relative_to() below depend on.
        path = Path(raw).expanduser().resolve()

        if path.is_dir():
            folders.append(path)
        elif path.is_file() and is_pdf(path):
            files.append(path)
        elif path.is_file():
            print(f"  WARNING: skipping '{raw}' - not a .pdf file")
        else:
            print(f"  WARNING: skipping '{raw}' - path not found")

    files = [f for f in files if not any(f.is_relative_to(d) for d in folders)]

    # Deduplicate while preserving the order the paths were typed in.
    def unique(paths: list[Path]) -> list[Path]:
        seen: set[Path] = set()
        return [p for p in paths if not (p in seen or seen.add(p))]

    return unique(folders), unique(files)


def find_pdfs(folder: Path, folder_suffix: str) -> list[Path]:
    """
    Every PDF under `folder`, ignoring any previous watermarked output.

    Two things get skipped. Anything inside an output folder, and any file
    whose name already ends in the output suffix - single-file runs write their
    copy next to the original, so without the second check a later folder scan
    would pick that copy up and watermark it all over again.

    A file named explicitly on the command line is still processed either way,
    on the basis that asking for it by name is a deliberate choice.
    """
    return sorted(
        p for p in folder.rglob("*")
        if is_pdf(p) and p.is_file()
        and not is_output_path(p.relative_to(folder), folder_suffix)
        and not p.stem.endswith(wm.OUTPUT_SUFFIX)
    )


def ignored_files(folder: Path, folder_suffix: str) -> list[Path]:
    """Files under `folder` that are not PDFs (hidden files aside)."""
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and not is_pdf(p) and not p.name.startswith(".")
        and not is_output_path(p.relative_to(folder), folder_suffix)
    )


def count_ignored(folder: Path, folder_suffix: str) -> int:
    return len(ignored_files(folder, folder_suffix))


def build_jobs(text: str, folder_pdfs: dict[Path, list[Path]], files: list[Path],
               out_dir: Path | None, folder_suffix: str,
               tag_filenames: bool) -> list[tuple[Path, Path, str]]:
    """
    Work out every (source, destination, display name) for one watermark text.

    Building the whole list up front is what makes --dry-run trustworthy: the
    preview runs through exactly the same path logic as a real run, so what it
    prints is what would actually be written.

    `tag_filenames` adds the watermark text to single-file output names. It is
    only switched on when several texts are being applied, so the common case
    of one watermark stays clean: report.pdf -> report_watermarked.pdf.
    """
    slug = slugify(text)
    jobs: list[tuple[Path, Path, str]] = []

    # Two sources can map to the same output: two folders that are both called
    # "Docs", or two files called "report.pdf", once --out sends everything to
    # one place. Without this the second would silently overwrite the first.
    taken: set[str] = set()

    def unique(path: Path, numbered) -> Path:
        n = 1
        candidate = path
        while candidate.as_posix().casefold() in taken:
            n += 1
            candidate = numbered(n)
        taken.add(candidate.as_posix().casefold())
        return candidate

    for folder, pdfs in folder_pdfs.items():
        # Output folders are siblings of the source folder unless --out says
        # otherwise, which keeps watermarked copies next to what they came from
        # without ever writing inside the source folder itself.
        parent = out_dir or folder.parent
        name = f"{source_label(folder)} _{slug}{folder_suffix}"
        root = unique(parent / name,
                      lambda n, p=parent, f=folder: p / f"{source_label(f)} ({n}) _{slug}{folder_suffix}")
        for pdf in pdfs:
            relative = pdf.relative_to(folder)
            destination = root / relative.parent / (relative.stem + wm.OUTPUT_SUFFIX + ".pdf")
            taken.add(destination.as_posix().casefold())
            jobs.append((pdf, destination, str(relative)))

    for pdf in files:
        parent = out_dir or pdf.parent
        stem = pdf.stem + (f" _{slug}" if tag_filenames else "")
        destination = unique(parent / (stem + wm.OUTPUT_SUFFIX + ".pdf"),
                             lambda n, p=parent, s=stem: p / f"{s} ({n}){wm.OUTPUT_SUFFIX}.pdf")
        jobs.append((pdf, destination, pdf.name))

    return jobs


def apply_watermark(source: Path, destination: Path, style: wm.Style | None = None,
                    flatten_dpi: int | None = None) -> int:
    """
    Watermark every page of one PDF and save the result as a new file.

    Raises wm.WatermarkError on failure; the caller decides whether one bad
    file should stop the batch. Returns the page count.
    """
    return wm.watermark_file(source, destination, style, flatten_dpi=flatten_dpi)


def position_arg(value: str) -> tuple[float, float]:
    """--position takes a named spot (top-left ... bottom-right) or X,Y fractions."""
    name = value.strip().lower().replace(" ", "-").replace("centre", "center")
    if name in wm.POSITIONS:
        return wm.POSITIONS[name]
    try:
        x, y = (float(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a position - use e.g. top-right, or X,Y like 0.9,0.05") from None
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise argparse.ArgumentTypeError("X and Y must each be between 0 and 1")
    return x, y


def angle_arg(value: str) -> float | None:
    """--angle takes degrees, or 'diagonal' for each page's own diagonal."""
    if value.strip().lower() in ("diagonal", "auto"):
        return None
    try:
        return float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number of degrees or 'diagonal'") from None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a text watermark to PDFs, in bulk or one at a time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python batch_watermark.py my_folder --text "CONFIDENTIAL"\n'
            '  python batch_watermark.py report.pdf --text "DRAFT"\n'
            '  python batch_watermark.py my_folder extra.pdf --text "DRAFT" --dry-run\n'
            "  python batch_watermark.py my_folder --company Acme Globex\n"
        ),
    )
    parser.add_argument(
        "sources", nargs="+", metavar="PATH",
        help="folders and/or PDF files to watermark (folders are searched recursively)",
    )
    parser.add_argument(
        "--text", "-t", action="append", default=[], metavar="TEXT",
        help="watermark text; repeat the flag to produce one set of copies per text",
    )
    parser.add_argument(
        "--company", "-c", nargs="+", default=[], metavar="NAME",
        help='shorthand - each NAME becomes the text "CONFIDENTIAL - NAME"',
    )
    parser.add_argument(
        "--prefix", default="CONFIDENTIAL", metavar="TEXT",
        help='what --company puts before each name, e.g. "DRAFT" or "FOR REVIEW"; '
             'an empty string leaves just the name (default: CONFIDENTIAL)',
    )
    parser.add_argument(
        "--out", "-o", metavar="DIR",
        help="write output here instead of alongside each source",
    )
    parser.add_argument(
        "--layout", choices=[*wm.LAYOUTS, "stacked", "header", "footer"], default=wm.LAYOUT,
        help="diagonal: one large line across the middle; tile: repeated all "
             "over; position: a small flat stamp at --position. (stacked, header "
             f"and footer are shorthands for the older layouts) (default: {wm.LAYOUT})",
    )
    parser.add_argument(
        "--position", type=position_arg, default=None, metavar="WHERE",
        help="where a stamp goes - one of " + ", ".join(wm.POSITIONS) + ", or "
             "X,Y fractions of the page like 0.8,0.1; implies --layout position "
             "(default: bottom-center)",
    )
    parser.add_argument(
        "--stack", action="store_true",
        help="split the text onto two lines at the first ' - '",
    )
    parser.add_argument(
        "--tile", type=int, default=None, metavar="N",
        help="repeat the text as N diagonal bands - shorthand for --layout tile; "
             f"--tile 1 means a single centred line (default bands: {wm.REPEAT_LINES})",
    )
    parser.add_argument(
        "--size", type=float, default=100, metavar="PERCENT",
        help="text size as a percentage of the automatic size: 20-100 for "
             "diagonal and tile, 20-400 for a positioned stamp (default: 100)",
    )
    parser.add_argument(
        "--outline", action="store_true",
        help="draw hollow letters instead of solid ones",
    )
    parser.add_argument(
        "--opacity", type=float, default=wm.OPACITY, metavar="0-1",
        help=f"0 is invisible, 1 is fully opaque (default: {wm.OPACITY})",
    )
    parser.add_argument(
        "--angle", type=angle_arg, default=wm.ROTATION_DEGREES, metavar="DEG",
        help="rotation in degrees, counter-clockwise from horizontal, or "
             "'diagonal' to run corner to corner on every page (default: diagonal)",
    )
    parser.add_argument(
        "--color", default=None, metavar="COLOUR",
        help="a name (grey, red, blue, black, green) or hex code like #cc0000 "
             "(default: grey)",
    )
    parser.add_argument(
        "--font", default=wm.FONT_FAMILY, metavar="FAMILY",
        help="Helvetica, Times or Courier, or the name of any font installed "
             f'on this computer, e.g. "Arial" (default: {wm.FONT_FAMILY})',
    )
    parser.add_argument(
        "--bold", action=argparse.BooleanOptionalAction, default=wm.BOLD,
        help=f"bold text (default: {'on' if wm.BOLD else 'off'}; --no-bold to turn off)",
    )
    parser.add_argument(
        "--italic", action=argparse.BooleanOptionalAction, default=wm.ITALIC,
        help="italic text (default: off)",
    )
    parser.add_argument(
        "--flatten", nargs="?", const=150, type=int, default=None, metavar="DPI",
        help="turn each page into an image after marking, so the watermark "
             "cannot be selected, searched or deleted; bigger files and no "
             "selectable text (default DPI when given: 150)",
    )
    parser.add_argument(
        "--suffix", default=FOLDER_SUFFIX, metavar="TEXT",
        help=f'appended to each output folder name (default: "{FOLDER_SUFFIX}")',
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print what would be written, without writing anything",
    )

    args = parser.parse_args()

    try:
        args.rgb = wm.parse_color(args.color) if args.color else wm.FONT_COLOR
    except ValueError as exc:
        parser.error(str(exc))
    if args.flatten is not None and not 36 <= args.flatten <= 600:
        parser.error("--flatten DPI must be between 36 and 600")

    # --text and --company are two ways of supplying the same thing, so they
    # are merged into one list here and nothing downstream has to know which
    # flag a given text came from.
    prefix = args.prefix.strip()
    args.texts = list(args.text) + [f"{prefix} - {name}" if prefix else name for name in args.company]

    if not args.texts:
        parser.error('no watermark text given - use --text "YOUR TEXT" or --company NAME')
    if not 0.0 <= args.opacity <= 1.0:
        parser.error("--opacity must be between 0 and 1")
    if args.tile is not None:
        if args.tile < 1:
            parser.error("--tile must be 1 or more")
        # --tile predates --layout: N > 1 means tiling, 1 means a single line.
        args.layout = "tile" if args.tile > 1 else "diagonal"
    if args.position is not None:
        args.layout = "position"
    if args.layout in ("header", "footer"):
        args.position = wm.POSITIONS["top-center" if args.layout == "header" else "bottom-center"]
        args.layout = "position"
    if args.layout == "stacked":
        args.layout, args.stack = "diagonal", True
    top = 400 if args.layout == "position" else 100
    if not 20 <= args.size <= top:
        parser.error(f"--size must be between 20 and {top} for this layout")
    if args.font not in fonts.BUILTIN and args.font not in fonts.LEGACY:
        known = {f["family"].casefold(): f["family"] for f in fonts.families()}
        if args.font.casefold() not in known:
            close = [name for key, name in known.items() if args.font.casefold() in key][:5]
            parser.error(f"no font called '{args.font}' on this computer"
                         + (f" - did you mean {', '.join(close)}?" if close else ""))
        args.font = known[args.font.casefold()]

    return args


def main() -> None:
    # Watermark text and file names can be in any script; a Windows console
    # using a legacy code page would otherwise crash printing "Łukasz".
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")

    args = parse_args()

    folders, files = collect_sources(args.sources)
    if not folders and not files:
        print("Nothing to do - no readable folders or PDFs in the arguments given.")
        sys.exit(1)

    out_dir = Path(args.out).expanduser().resolve() if args.out else None

    # Scan each folder once and reuse the result for every watermark text.
    folder_pdfs = {folder: find_pdfs(folder, args.suffix) for folder in folders}
    pdf_count = sum(len(pdfs) for pdfs in folder_pdfs.values()) + len(files)

    if pdf_count == 0:
        print("No PDFs found. Nothing to do.")
        sys.exit(0)

    # Count what was passed over, so that a folder full of Word documents does
    # not look like a silent failure.
    ignored = sum(count_ignored(folder, args.suffix) for folder in folders)

    mode = " [DRY RUN]" if args.dry_run else ""
    print(f"{pdf_count} PDF(s) x {len(args.texts)} watermark(s) = "
          f"{pdf_count * len(args.texts)} file(s) to write{mode}")
    if ignored:
        print(f"({ignored} non-PDF file(s) ignored)")
    print()

    succeeded = 0
    failed = 0

    for text in args.texts:
        style = wm.Style(text=text, font=args.font, bold=args.bold, italic=args.italic,
                         color=args.rgb, opacity=args.opacity, angle=args.angle,
                         layout=args.layout, position=args.position or wm.POSITION,
                         stack=args.stack,
                         tile=args.tile if args.tile and args.tile > 1 else wm.REPEAT_LINES,
                         size=args.size / 100, outline=args.outline)
        jobs = build_jobs(text, folder_pdfs, files, out_dir,
                          args.suffix, tag_filenames=len(args.texts) > 1)

        print(f'["{text}"]')
        # Show each distinct destination folder once, rather than repeating a
        # long path on every line of output. Sorted so a parent folder is
        # listed before the subfolders inside it.
        for destination in sorted({job[1].parent for job in jobs}):
            print(f"  -> {destination}")

        for source, destination, label in jobs:
            if args.dry_run:
                print(f"  [dry-run] {label}")
                continue
            try:
                apply_watermark(source, destination, style, args.flatten)
                print(f"  OK  {label}")
                succeeded += 1
            except Exception as exc:
                # One unreadable or password-protected PDF should not abandon
                # the rest of the batch.
                print(f"  ERR {label} - {exc}")
                failed += 1

        print()

    if args.dry_run:
        print("Dry run complete - no files written.")
    else:
        print(f"Done. {succeeded} succeeded, {failed} failed.")
        # A non-zero exit code lets scripts and CI notice partial failures.
        if failed:
            sys.exit(1)


if __name__ == "__main__":
    main()

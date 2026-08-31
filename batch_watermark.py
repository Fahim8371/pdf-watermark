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
import os
import re
import sys
from pathlib import Path

import pymupdf

import watermark as wm

# Appended to the name of each output folder, e.g. "Contracts _DRAFT_Watermarked".
# It doubles as the marker used to recognise - and skip - output folders on a
# second run, so watermarks never get stacked on top of each other.
FOLDER_SUFFIX = "_Watermarked"

# Windows forbids these characters in file and folder names, and the rest are no
# better as path components. Watermark text is free-form, so it has to be
# sanitised before any of it can end up in a path. (The trailing range covers
# control characters, which are illegal in names on every platform.)
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows' extended-length path prefix - see long_path() below.
_LONG_PREFIX = "\\\\?\\"


def long_path(path: Path) -> str:
    r"""
    Return a path string that is safe to hand to the filesystem on Windows.

    Windows silently caps ordinary paths at 260 characters, and a deep folder
    tree plus a long watermark name reaches that surprisingly fast - the
    failure shows up as a confusing "cannot open file" error partway through an
    otherwise healthy run. Prefixing an absolute path with \\?\ opts out of the
    limit, and both os.makedirs and PyMuPDF understand the prefix.

    The prefix requires a fully-qualified path with backslash separators, which
    is why every path is resolved before it gets here. macOS and Linux have no
    such limit, so this is a no-op there.
    """
    text = str(path)
    if sys.platform == "win32" and not text.startswith(_LONG_PREFIX):
        return _LONG_PREFIX + text
    return text


def slugify(text: str) -> str:
    """
    Turn arbitrary watermark text into something usable as part of a path.

    Illegal characters become hyphens, runs of whitespace collapse to a single
    space, and leading or trailing spaces, dots and hyphens are trimmed - a
    trailing dot or space makes a Windows folder name unusable. Text that is
    nothing but illegal characters falls back to "Watermarked", so the run can
    still produce a valid folder instead of failing.
    """
    cleaned = _ILLEGAL.sub("-", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned or "Watermarked"


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
        elif path.is_file() and path.suffix.lower() == ".pdf":
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
        p for p in folder.rglob("*.pdf")
        if not is_output_path(p, folder_suffix)
        and not p.stem.endswith(wm.OUTPUT_SUFFIX)
    )


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

    for folder, pdfs in folder_pdfs.items():
        # Output folders are siblings of the source folder unless --out says
        # otherwise, which keeps watermarked copies next to what they came from
        # without ever writing inside the source folder itself.
        root = (out_dir or folder.parent) / f"{source_label(folder)} _{slug}{folder_suffix}"
        for pdf in pdfs:
            relative = pdf.relative_to(folder)
            destination = root / relative.parent / (relative.stem + wm.OUTPUT_SUFFIX + ".pdf")
            jobs.append((pdf, destination, str(relative)))

    for pdf in files:
        stem = pdf.stem + (f" _{slug}" if tag_filenames else "")
        destination = (out_dir or pdf.parent) / (stem + wm.OUTPUT_SUFFIX + ".pdf")
        jobs.append((pdf, destination, pdf.name))

    return jobs


def apply_watermark(source: Path, destination: Path) -> None:
    """
    Watermark every page of one PDF and save the result as a new file.

    Raises on failure; the caller decides whether one bad file should stop the
    batch. All filesystem access goes through long_path() - see its docstring.
    """
    os.makedirs(long_path(destination.parent), exist_ok=True)

    doc = pymupdf.open(long_path(source))
    try:
        for page in doc:
            wm.watermark_page(page)
        doc.save(long_path(destination))
    finally:
        doc.close()


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
        "--out", "-o", metavar="DIR",
        help="write output here instead of alongside each source",
    )
    parser.add_argument(
        "--tile", type=int, default=wm.REPEAT_LINES, metavar="N",
        help="repeat the text as N diagonal bands; 1 is a single centred line "
             f"(default: {wm.REPEAT_LINES})",
    )
    parser.add_argument(
        "--opacity", type=float, default=wm.OPACITY, metavar="0-1",
        help=f"0 is invisible, 1 is fully opaque (default: {wm.OPACITY})",
    )
    parser.add_argument(
        "--angle", type=float, default=wm.ROTATION_DEGREES, metavar="DEG",
        help="rotation in degrees, counter-clockwise from horizontal "
             f"(default: {wm.ROTATION_DEGREES})",
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

    # --text and --company are two ways of supplying the same thing, so they
    # are merged into one list here and nothing downstream has to know which
    # flag a given text came from.
    args.texts = list(args.text) + [f"CONFIDENTIAL - {name}" for name in args.company]

    if not args.texts:
        parser.error('no watermark text given - use --text "YOUR TEXT" or --company NAME')
    if not 0.0 <= args.opacity <= 1.0:
        parser.error("--opacity must be between 0 and 1")
    if args.tile < 1:
        parser.error("--tile must be 1 or more")

    return args


def main() -> None:
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
    ignored = sum(
        1 for folder in folders for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() != ".pdf" and not is_output_path(p, args.suffix)
    )

    mode = " [DRY RUN]" if args.dry_run else ""
    print(f"{pdf_count} PDF(s) x {len(args.texts)} watermark(s) = "
          f"{pdf_count * len(args.texts)} file(s) to write{mode}")
    if ignored:
        print(f"({ignored} non-PDF file(s) ignored)")
    print()

    # watermark.py reads these module-level values at draw time, so setting
    # them here is enough to change how the next page is rendered.
    wm.REPEAT_LINES = args.tile
    wm.OPACITY = args.opacity
    wm.ROTATION_DEGREES = args.angle

    succeeded = 0
    failed = 0

    for text in args.texts:
        wm.WATERMARK_TEXT = text
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
                apply_watermark(source, destination)
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

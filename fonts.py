"""
fonts.py - The fonts a watermark can be drawn in.

Two kinds:

  * Built in: Helvetica, Times and Courier. Every PDF reader has them, they
    need no file, and they always have bold and italic versions.
  * Installed: any font on this computer, found in the usual system and user
    font folders on Windows, macOS and Linux.

An installed font is embedded in each watermarked PDF, so the copy looks the
same wherever it is opened. Only the characters the watermark actually uses
are embedded - a whole font file can run to megabytes, the subset is a few
kilobytes - and only the watermark's own font is touched: subsetting every
font in the document (PyMuPDF's subset_fonts) can damage the fonts in CAD
drawings, so that is deliberately not used.

Scanning a few hundred font files takes a couple of seconds, so the result is
cached in a JSON file and only files that changed are read again.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
from functools import lru_cache
from pathlib import Path

import pymupdf

# fontTools reports every oddity it meets in a font file; a system font folder
# holds hundreds of files, and none of it matters for reading names or subsetting.
logging.getLogger("fontTools").setLevel(logging.ERROR)

# Family name -> PyMuPDF built-in font code for (bold, italic).
BUILTIN = {
    "Helvetica": {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"},
    "Times":     {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"},
    "Courier":   {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"},
}

# The old single-code font setting, mapped onto family + bold + italic.
LEGACY = {code: (family, bool(b), bool(i))
          for family, codes in BUILTIN.items() for (b, i), code in codes.items()}

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")


def font_dirs() -> list[Path]:
    """Where this operating system keeps installed fonts."""
    home = Path.home()
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return [windir / "Fonts", local / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            home / ".local" / "share" / "fonts", home / ".fonts"]


def _read_faces(path: Path) -> list[dict]:
    """Family, bold and italic of every face in one font file."""
    from fontTools.ttLib import TTCollection, TTFont

    faces = []
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            fonts = TTCollection(str(path), lazy=True).fonts
        else:
            fonts = [TTFont(str(path), lazy=True)]
    except Exception:  # noqa: BLE001 - an unreadable font is simply not offered
        return []
    for index, font in enumerate(fonts):
        try:
            names = font["name"]
            # nameID 1 is the "style-linked" family: exactly the grouping that
            # Bold and Italic buttons work within (Arial / Arial Bold / ...).
            family = names.getDebugName(1)
            if not family or family.startswith("."):
                continue
            selection = font["OS/2"].fsSelection if "OS/2" in font else 0
            mac = font["head"].macStyle if "head" in font else 0
            faces.append({
                "family": family.strip(),
                "bold": bool(selection & 0x20 or mac & 0x1),
                "italic": bool(selection & 0x01 or mac & 0x2),
                "path": str(path),
                "index": index,
            })
        except Exception:  # noqa: BLE001
            continue
    return faces


_lock = threading.Lock()
_faces: list[dict] | None = None


def scan(cache_file: Path | None = None) -> list[dict]:
    """
    Every installed font face. Uses and refreshes `cache_file` when given.
    Safe to call from several threads; the scan only ever runs once.
    """
    global _faces
    with _lock:
        if _faces is not None:
            return _faces
        cache: dict = {}
        if cache_file and cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache = {}
        fresh: dict = {}
        for folder in font_dirs():
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if path.suffix.lower() not in FONT_SUFFIXES:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = str(path)
                stamp = [stat.st_mtime, stat.st_size]
                entry = cache.get(key)
                if entry and entry.get("stamp") == stamp:
                    fresh[key] = entry
                else:
                    fresh[key] = {"stamp": stamp, "faces": _read_faces(path)}
        if cache_file:
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(fresh), encoding="utf-8")
            except OSError:
                pass
        _faces = [face for entry in fresh.values() for face in entry["faces"]]
        return _faces


def families(cache_file: Path | None = None) -> list[dict]:
    """
    The font list to offer: built-in families first, then installed ones
    alphabetically, each with which of bold / italic it actually has.
    """
    out = [{"family": name, "builtin": True, "bold": True, "italic": True}
           for name in BUILTIN]
    grouped: dict[str, set] = {}
    for face in scan(cache_file):
        grouped.setdefault(face["family"], set()).add((face["bold"], face["italic"]))
    for family in sorted(grouped, key=str.casefold):
        if family in BUILTIN:
            continue
        styles = grouped[family]
        out.append({"family": family, "builtin": False,
                    "bold": any(b for b, _ in styles), "italic": any(i for _, i in styles)})
    return out


def find_face(family: str, bold: bool, italic: bool) -> dict | None:
    """The closest installed face: exact style, else the same family's regular."""
    faces = [f for f in scan() if f["family"].casefold() == family.casefold()]
    if not faces:
        return None
    for want in ((bold, italic), (bold, False), (False, italic), (False, False)):
        for face in faces:
            if (face["bold"], face["italic"]) == want:
                return face
    return faces[0]


@lru_cache(maxsize=64)
def _subset(path: str, index: int, chars: str) -> bytes:
    """A copy of one font face holding only `chars`, as font file bytes."""
    from fontTools import subset
    from fontTools.ttLib import TTCollection, TTFont

    font = (TTCollection(path).fonts[index]
            if Path(path).suffix.lower() in (".ttc", ".otc") else TTFont(path))
    options = subset.Options()
    options.layout_features = ["*"]        # keep kerning and ligatures
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.drop_tables += ["DSIG"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=chars)
    subsetter.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


# Always included, so page numbers and dates never fall back to another font.
_ALWAYS = "0123456789-–·/ "


def load(family: str, bold: bool = False, italic: bool = False,
         text: str = "") -> pymupdf.Font:
    """
    A PyMuPDF font ready to draw `text` with.

    Unknown families fall back to Helvetica rather than failing the run - the
    watermark still goes on, just not in the font asked for.
    """
    if family in LEGACY:
        family, bold, italic = LEGACY[family]
    if family in BUILTIN:
        return _builtin(BUILTIN[family][(int(bold), int(italic))])
    face = find_face(family, bold, italic)
    if face is None:
        return _builtin(BUILTIN["Helvetica"][(int(bold), int(italic))])
    chars = "".join(sorted(set(text + _ALWAYS)))
    try:
        return pymupdf.Font(fontbuffer=_subset(face["path"], face["index"], chars))
    except Exception:  # noqa: BLE001 - a font that will not subset is used whole
        return pymupdf.Font(fontfile=face["path"])


@lru_cache(maxsize=16)
def _builtin(code: str) -> pymupdf.Font:
    return pymupdf.Font(code)


def has_style(family: str, bold: bool, italic: bool) -> bool:
    """Whether the family really has this style (built-ins always do)."""
    if family in BUILTIN or family in LEGACY:
        return True
    face = find_face(family, bold, italic)
    return bool(face) and (face["bold"], face["italic"]) == (bold, italic)

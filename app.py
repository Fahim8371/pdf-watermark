"""
app.py - The desktop app: a pywebview window around the watermarking engine.

The interface itself is plain HTML/CSS/JS in ui/. This file is the bridge:
every method on Api is callable from JavaScript as window.pywebview.api.<name>,
and long-running work reports back by calling the global JS functions
onRunStart / onRunProgress / onRunDone.

    pip install -r requirements.txt
    python app.py                    # open the app
    python app.py report.pdf pack/   # open it with these already added

PDFs dropped onto the app's icon (or "Open with" on Windows) arrive the same
way, as command-line arguments.
"""

from __future__ import annotations

import base64
import faulthandler
import json
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

import batch_watermark as bw
import fonts
import watermark as wm

APP_NAME = "PDF Watermark"
HELP_URL = "https://github.com/Fahim8371/pdf-watermark#readme"
COLORS = {"grey": "#999999", "red": "#d93333", "blue": "#2659d9", "black": "#000000"}


def resource(relative: str) -> Path:
    """A bundled file - next to this script, or inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def data_dir() -> Path:
    """Where the app keeps settings, saved companies and its log."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def settings_file() -> Path:
    return data_dir() / "settings.json"


def companies_file() -> Path:
    return data_dir() / "companies.json"


log = logging.getLogger("pdf-watermark")


def start_logging() -> None:
    """
    Keep a small log next to the settings, so a crash on someone's machine
    leaves something to diagnose. Native crashes (inside PyMuPDF or the web
    view) are caught by faulthandler, which writes a traceback before dying.
    """
    try:
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "app.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(folder / "app.old.log")
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        faulthandler.enable(open(folder / "crash.log", "a", encoding="utf-8"))  # noqa: SIM115 - must stay open
    except OSError:
        pass


def describe(raw: str) -> dict | None:
    """
    What the Files step shows for one dropped or picked path.

    Every PDF is opened once here, so damaged and password-protected files
    are flagged straight away - and kept out of the preview and the export -
    instead of surfacing as failures at the very end.
    """
    path = Path(raw).expanduser().resolve()
    if path.is_dir():
        pdfs = bw.find_pdfs(path, bw.FOLDER_SUFFIX)
        ignored = [str(p.relative_to(path)) for p in bw.ignored_files(path, bw.FOLDER_SUFFIX)]
        name, kind = path.name, "folder"
    elif path.is_file() and bw.is_pdf(path):
        pdfs, ignored = [path], []
        name, kind = path.name, "file"
    else:
        return None
    files, problems = [], []
    for pdf in pdfs:
        rel = str(pdf.relative_to(path)) if kind == "folder" else pdf.name
        reason = wm.check_pdf(pdf)
        if reason:
            problems.append({"rel": rel, "path": str(pdf), "reason": reason})
        else:
            files.append(rel)
    # Non-PDF files are listed so the app can say what was passed over and
    # why; a folder of thousands is summarised rather than sent in full.
    return {"path": str(path), "name": name, "kind": kind, "pdfs": len(files),
            "ignored": len(ignored), "ignored_files": ignored[:50],
            "files": files, "problems": problems}


# The current UI's font buttons, until the Design step's font picker lands.
FONT_BUTTONS = {"sans": ("Helvetica", False), "bold": ("Helvetica", True),
                "serif": ("Times", True), "mono": ("Courier", True)}


def style_from(settings: dict, text: str) -> wm.Style:
    angle = settings.get("angle")
    font = settings.get("font") or wm.FONT_FAMILY
    bold = settings.get("bold", wm.BOLD)
    if font in FONT_BUTTONS:
        font, bold = FONT_BUTTONS[font]
    position = settings.get("position") or wm.POSITION
    return wm.Style(
        text=text,
        font=font,
        bold=bool(bold),
        italic=bool(settings.get("italic", False)),
        color=wm.parse_color(COLORS.get(settings.get("color"), settings.get("color") or "grey")),
        opacity=float(settings.get("opacity", wm.OPACITY)),
        angle=None if angle in (None, "diagonal") else float(angle),
        layout=settings.get("layout", wm.LAYOUT),
        position=(float(position[0]), float(position[1])),
        stack=bool(settings.get("stack", False)),
        size=float(settings.get("size", 1.0)),
        outline=bool(settings.get("outline", False)),
    )


def open_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opening a folder the user just created
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Api:
    def __init__(self, initial: list[str]):
        self._initial = initial
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._window: webview.Window | None = None

    # ---- Files ---------------------------------------------------------

    def initial_sources(self) -> list[dict]:
        # The first call the page makes, so it doubles as "the UI is up".
        log.info("ui ready")
        return self.inspect(self._initial)

    def inspect(self, paths: list[str]) -> list[dict]:
        return [d for d in (describe(p) for p in paths) if d]

    def pick_files(self) -> list[dict]:
        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=("PDF files (*.pdf)",))
        return self.inspect(list(chosen or []))

    def pick_folder(self) -> list[dict]:
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return self.inspect(list(chosen or []))

    def pick_output(self) -> str | None:
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else None

    # ---- Planning and preview -----------------------------------------

    def _jobs(self, settings: dict):
        folders, files = bw.collect_sources(settings["sources"])
        folder_pdfs = {f: bw.find_pdfs(f, bw.FOLDER_SUFFIX) for f in folders}
        out_dir = Path(settings["out_dir"]) if settings.get("out_dir") else None
        texts = settings["texts"]
        # Files already flagged as unreadable on the Files step are left out.
        skip = {Path(p).resolve() for p in settings.get("skip", [])}
        folder_pdfs = {f: [p for p in pdfs if p not in skip] for f, pdfs in folder_pdfs.items()}
        files = [f for f in files if f not in skip]
        return [(text, bw.build_jobs(text, folder_pdfs, files, out_dir, bw.FOLDER_SUFFIX,
                                     tag_filenames=len(texts) > 1))
                for text in texts]

    def plan(self, settings: dict) -> list[dict]:
        """Every file a run would write, grouped by watermark text."""
        groups = []
        for text, jobs in self._jobs(settings):
            files = []
            for _, destination, label in jobs:
                # The label is the path below the output root (a folder
                # source's relative path, or just the name for a single file),
                # so stripping that many parts gives the root the tree hangs from.
                root = destination.parents[len(Path(label).parts) - 1]
                files.append({"root": str(root), "rel": str(destination.relative_to(root)),
                              "label": label})
            groups.append({"text": text, "files": files})
        return groups

    def preview(self, settings: dict) -> str | None:
        source = Path(settings["source"])
        path = source / settings["rel"] if source.is_dir() else source
        try:
            png, box = wm.render_preview(path, style_from(settings, settings["text"]),
                                         page_index=int(settings.get("page", 0)), max_px=1000,
                                         with_box=True)
        except Exception:  # noqa: BLE001 - an unreadable file just shows no preview
            log.warning("preview failed for %s", path, exc_info=True)
            return None
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        # The current UI expects just the image; box (where a positioned stamp
        # landed) is for the drag handle in the Design step.
        return {"png": url, "box": box} if settings.get("want_box") else url

    def list_fonts(self) -> list[dict]:
        """Built-in and installed font families, for the font picker."""
        return fonts.families(data_dir() / "fonts.json")

    # ---- Running -------------------------------------------------------

    def run(self, settings: dict) -> None:
        if not self._busy.acquire(blocking=False):
            return
        self._cancel.clear()
        threading.Thread(target=self._run, args=(settings,), daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _call(self, fn: str, *args) -> None:
        self._window.evaluate_js(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def _run(self, settings: dict) -> None:
        log.info("run: %d text(s), flatten=%s, layout=%s",
                 len(settings.get("texts", [])), settings.get("flatten"), settings.get("layout"))
        try:
            first_output = None
            done = ok = failed = 0
            groups = self._jobs(settings)
            total = sum(len(jobs) for _, jobs in groups)
            flatten = 150 if settings.get("flatten") else None
            self._call("onRunStart", total)
            for text, jobs in groups:
                style = style_from(settings, text)
                for source, destination, label in jobs:
                    if self._cancel.is_set():
                        break
                    try:
                        wm.watermark_file(source, destination, style, flatten_dpi=flatten)
                        ok += 1
                        error = ""
                        if first_output is None:
                            first_output = str(destination.parent)
                    except Exception as exc:  # noqa: BLE001 - reported per file, batch carries on
                        failed += 1
                        error = str(exc)
                        log.warning("failed %s: %s", source, exc, exc_info=not isinstance(exc, wm.WatermarkError))
                    done += 1
                    self._call("onRunProgress", done, label, text, not error, error)
            self._call("onRunDone", ok, failed, first_output, self._cancel.is_set())
        except Exception:
            # Anything unexpected must still end the run in the UI, or the
            # window would sit on "Creating your copies..." forever.
            log.exception("run crashed")
            self._call("onRunDone", ok, failed + 1, first_output, False)
        finally:
            self._busy.release()

    # ---- Misc ----------------------------------------------------------

    def open_path(self, path: str | None) -> None:
        if path and Path(path).exists():
            open_in_file_manager(path)

    def open_help(self) -> None:
        webbrowser.open(HELP_URL)

    def load_settings(self) -> dict:
        try:
            return json.loads(settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self, settings: dict) -> None:
        _write_json(settings_file(), settings)

    # ---- Saved companies -----------------------------------------------
    # Kept on this computer only, in companies.json beside the settings, so
    # the people a team sends documents to are one click away next time.

    def load_companies(self) -> list[str]:
        try:
            names = json.loads(companies_file().read_text(encoding="utf-8"))
            return [n for n in names if isinstance(n, str) and n.strip()]
        except (OSError, ValueError, TypeError):
            return []

    def save_companies(self, names: list[str]) -> None:
        _write_json(companies_file(), sorted(set(names), key=str.casefold))

    def log_js_error(self, message: str) -> None:
        log.error("ui: %s", message)


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        log.warning("could not write %s", path)  # a convenience, never a reason to fail


def on_drop(api: Api, event: dict) -> None:
    """Files dropped on the window. Only Python can see their real paths."""
    files = event.get("dataTransfer", {}).get("files", [])
    paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
    if paths:
        api._call("addSources", api.inspect(paths))


def main() -> None:
    start_logging()
    log.info("start %s", sys.argv[1:])
    initial = [a for a in sys.argv[1:] if not a.startswith("-")]
    api = Api(initial)
    window = webview.create_window(
        APP_NAME, str(resource("ui/index.html")), js_api=api,
        width=1120, height=780, min_size=(820, 620), background_color="#09090b",
    )
    api._window = window

    # Reading the installed fonts takes a moment the first time; do it while
    # the window opens, so the font list is ready when it is wanted.
    threading.Thread(target=fonts.families, args=(data_dir() / "fonts.json",), daemon=True).start()

    def bind(win: webview.Window) -> None:
        win.dom.document.events.drop += DOMEventHandler(lambda e: on_drop(api, e), True, True)

    webview.start(bind, window, debug="--debug" in sys.argv)
    log.info("closed")


if __name__ == "__main__":
    main()

# PyInstaller build for the desktop app.
#
#     pip install -r requirements.txt pyinstaller
#     pyinstaller packaging/pdf-watermark.spec --noconfirm
#
# Windows: dist/PDF Watermark.exe - one file, nothing to install.
# macOS:   dist/PDF Watermark.app - packed into a .dmg by the release workflow.
#
# Run it on the platform you are building for; PyInstaller does not cross-build.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is provided by PyInstaller
NAME = "PDF Watermark"
VERSION = (ROOT / "VERSION").read_text().strip()
MAC = sys.platform == "darwin"

a = Analysis(  # noqa: F821
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "ui"), "ui")],
    # fontTools loads its table and subsetting modules lazily, by name.
    hiddenimports=["fontTools.subset", "fontTools.ttLib.tables", "fontTools.ttLib.ttCollection"],
    excludes=["tkinter", "test", "unittest", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

if MAC:
    exe = EXE(  # noqa: F821
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=NAME,
        console=False,
        # Turns "open these files with the app" (dropping PDFs on its icon,
        # or Open With in Finder) into command-line arguments for app.py.
        argv_emulation=True,
        icon=str(ROOT / "packaging" / "icon.icns"),
    )
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME)  # noqa: F821
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{NAME}.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="io.github.fahim8371.pdfwatermark",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PDF document",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.adobe.pdf", "public.folder"],
            }],
        },
    )
else:
    exe = EXE(  # noqa: F821
        pyz, a.scripts, a.binaries, a.datas, [],
        name=NAME,
        console=False,
        icon=str(ROOT / "packaging" / "icon.ico"),
        version=str(ROOT / "packaging" / "version_info.txt"),
        upx=False,
    )

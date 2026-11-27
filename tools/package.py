"""Build a zip that runs the bot on a PC without Python or Tesseract: one E7Bot folder for both clients.

Run from the project folder, with the dev .venv (PyInstaller is a dev tool only):
    .venv\\Scripts\\python.exe tools\\package.py

The zip lands in dist\\. It never contains state files, settings.json, accounts or logs.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "E7Bot"
TESSERACT = Path(r"C:\Program Files\Tesseract-OCR")
# Files the bot reads at run time, next to the .exe. No text for the user: the instructions are
# the release notes, tools/release_notes.md.
DATA = ["config.yaml", "templates", "data", "ui"]


def run_pyinstaller() -> Path:
    """Build the .exe and its libraries with PyInstaller. Returns the built folder."""
    shutil.rmtree(BUILD, ignore_errors=True)
    command = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "main.py"),
        "--name", NAME, "--onedir", "--noconsole", "--noconfirm", "--clean",
        # The .exe's icon in the Explorer; the window sets its own from ui/icon.ico.
        "--icon", str(ROOT / "ui" / "icon.ico"),
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        # No --uac-admin: the bot asks for administrator rights itself, only for STOVE, whose game runs as one.
        # The handlers are imported by name, which PyInstaller cannot follow on its own.
        "--collect-submodules", "states", "--collect-submodules", "core",
        # The bot never uses them, and their PyInstaller hook fails on a missing backports module.
        "--exclude-module", "pkg_resources", "--exclude-module", "setuptools",
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return BUILD / "dist" / NAME


def copy_data(folder: Path) -> None:
    """Put what the bot reads at run time next to the .exe. The window asks for the client at the first launch."""
    for name in DATA:
        source = ROOT / name
        target = folder / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)


def copy_tesseract(folder: Path) -> None:
    """Ship Tesseract's program, its libraries and the English data, all the OCR needs."""
    if not (TESSERACT / "tesseract.exe").exists():
        raise SystemExit(f"Tesseract is not installed in {TESSERACT}: nothing to ship")
    target = folder / "tesseract"
    (target / "tessdata").mkdir(parents=True, exist_ok=True)
    shutil.copy2(TESSERACT / "tesseract.exe", target)
    for library in TESSERACT.glob("*.dll"):
        shutil.copy2(library, target)
    shutil.copy2(TESSERACT / "tessdata" / "eng.traineddata", target / "tessdata")


def git_version() -> str:
    """Return the version tag on this commit without its "v" (v1.0.0 gives 1.0.0), else the short commit."""
    tag = subprocess.run(["git", "describe", "--tags", "--exact-match"], cwd=ROOT, capture_output=True, text=True)
    if tag.returncode == 0:
        return tag.stdout.strip().removeprefix("v")
    done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return done.stdout.strip() or "unknown"


def main() -> int:
    """Build the program, add its data and Tesseract, then zip the E7Bot folder into dist.

    One folder drives either client. To run STOVE and Steam at once, the user copies the folder:
    each copy keeps its own settings.json and logs.
    """
    built = run_pyinstaller()
    release = BUILD / "release"
    shutil.rmtree(release, ignore_errors=True)
    folder = release / NAME
    shutil.copytree(built, folder)
    copy_data(folder)
    copy_tesseract(folder)
    DIST.mkdir(exist_ok=True)
    archive = shutil.make_archive(str(DIST / f"{NAME}-{git_version()}"), "zip", release)
    print(f"Package ready: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

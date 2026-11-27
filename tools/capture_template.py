"""Hotkey tool to build templates/: F8 saves the full client area, F9 a selected crop, F10 quits."""

from __future__ import annotations

import argparse
import ctypes
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.window import enable_dpi_awareness, open_game_window

enable_dpi_awareness()

import cv2
import numpy as np
import win32con

from core.capture import Capture, is_blank
from core.config import ConfigError, load_config
from core.logger import setup_logging
from core.manifest import Manifest, load_manifest, save_manifest
from core.window import WindowError

HOTKEY_FULL = 1
HOTKEY_CROP = 2
HOTKEY_QUIT = 3
HOTKEYS = {HOTKEY_FULL: win32con.VK_F8, HOTKEY_CROP: win32con.VK_F9, HOTKEY_QUIT: win32con.VK_F10}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
SELECT_WINDOW = "Drag the template, Enter to confirm, C to cancel"

user32 = ctypes.WinDLL("user32", use_last_error=True)


def register_hotkeys() -> None:
    """Register the global hotkeys, raising OSError if another program holds one."""
    for hotkey_id, key in HOTKEYS.items():
        if not user32.RegisterHotKey(None, hotkey_id, MOD_NOREPEAT, key):
            raise OSError(f"hotkey {key:#x} is already used by another program")


def unregister_hotkeys() -> None:
    for hotkey_id in HOTKEYS:
        user32.UnregisterHotKey(None, hotkey_id)


def wait_for_hotkey() -> int:
    """Block until a hotkey is pressed and return its id. Ctrl+C still works while waiting."""
    message = wintypes.MSG()
    while True:
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
            if message.message == WM_HOTKEY:
                return int(message.wParam)
        time.sleep(0.02)


def clean_name(text: str) -> str | None:
    """Return a valid template name from user input, or None if it cannot be one."""
    name = text.strip().removesuffix(".png").lower()
    return name if re.fullmatch(r"[a-z0-9_]+", name) else None


def resolve_name(answer: str, missing: list[str]) -> str | None:
    """Turn the prompt's answer into a template name: a number from the list, or a name."""
    answer = answer.strip()
    if answer.isdigit():
        index = int(answer) - 1
        return missing[index] if 0 <= index < len(missing) else None
    return clean_name(answer)


def crop_frame(frame: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
    """Return a copy of an (x, y, width, height) region of a frame."""
    x, y, width, height = region
    return frame[y : y + height, x : x + width].copy()


def save_template(image: np.ndarray, name: str, templates_dir: Path) -> Path:
    """Write a template PNG and return its path."""
    templates_dir.mkdir(parents=True, exist_ok=True)
    path = templates_dir / f"{name}.png"
    encoded, buffer = cv2.imencode(".png", image)
    if not encoded:
        raise ValueError(f"could not encode template {name}")
    path.write_bytes(buffer.tobytes())
    return path


def select_region(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """Let the user drag a rectangle over a frozen frame. Returns None if cancelled."""
    region = cv2.selectROI(SELECT_WINDOW, frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(SELECT_WINDOW)
    x, y, width, height = (int(value) for value in region)
    return (x, y, width, height) if width > 0 and height > 0 else None


def print_missing(missing: list[str]) -> None:
    """Show the templates still to capture, numbered for the name prompt."""
    print(f"\n{len(missing)} templates missing:")
    for index, name in enumerate(missing, 1):
        print(f"  {index:2}. {name}")


def ask_name(missing: list[str], templates_dir: Path) -> str | None:
    """Ask for a template name and confirm an overwrite. Returns None if cancelled."""
    print_missing(missing)
    answer = input("Number or name (empty to cancel): ")
    if not answer.strip():
        return None
    name = resolve_name(answer, missing)
    if name is None:
        print(f"Invalid name: {answer!r}. Use lowercase letters, digits and underscores.")
        return None
    if (templates_dir / f"{name}.png").exists():
        if input(f"{name}.png exists, overwrite? [y/N] ").strip().lower() != "y":
            return None
    return name


def handle_capture(
    frame: np.ndarray, crop: bool, manifest: Manifest, manifest_path: Path, templates_dir: Path
) -> None:
    """Take a captured frame through selection, naming and saving."""
    if is_blank(frame):
        print("Warning: this frame is a single flat color (loading screen, or capture blocked?).")
    region = (0, 0, frame.shape[1], frame.shape[0])
    if crop:
        selected = select_region(frame)
        if selected is None:
            print("Cancelled.")
            return
        region = selected
    name = ask_name(manifest.missing(templates_dir), templates_dir)
    if name is None:
        print("Cancelled.")
        return
    path = save_template(crop_frame(frame, region), name, templates_dir)
    manifest.set_region(name, region)
    save_manifest(manifest, manifest_path)
    print(f"Saved {path} with region {region}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--list", action="store_true", help="print missing templates and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the hotkey loop and return the process exit code."""
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        manifest_path = ROOT / config.vision.manifest
        templates_dir = ROOT / config.vision.templates_dir
        manifest = load_manifest(manifest_path)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if args.list:
        print_missing(manifest.missing(templates_dir))
        return 0

    logger = setup_logging(config.logging.model_copy(update={"log_dir": ROOT / config.logging.log_dir}))
    try:
        game = open_game_window(config.window, logger)
        register_hotkeys()
    except (WindowError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print("Ready. Put the game on the screen you want, then: F8 full frame, F9 crop, F10 quit.")
    try:
        with Capture(game) as capture:
            while (hotkey := wait_for_hotkey()) != HOTKEY_QUIT:
                frame = capture.grab().copy()
                handle_capture(frame, hotkey == HOTKEY_CROP, manifest, manifest_path, templates_dir)
                print("Ready. F8 full frame, F9 crop, F10 quit.")
    except WindowError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        unregister_hotkeys()
    return 0


if __name__ == "__main__":
    sys.exit(main())

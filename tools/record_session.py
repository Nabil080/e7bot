"""Records a manual playthrough until F10: clicks with their position and duration, Esc, frames."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.window import enable_dpi_awareness, open_game_window

enable_dpi_awareness()

import cv2
import numpy as np

from core.capture import Capture
from core.config import ConfigError, load_config
from core.coords import CoordMapper
from core.input import cursor_position
from core.logger import setup_logging
from core.stop import STOP_KEY_NAME, StopWatcher
from core.vision import frame_difference
from core.window import WindowError

VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B
POLL_SECONDS = 0.03
GRAB_SECONDS = 0.1
AUTO_SAVE_SECONDS = 2.0
AUTO_SAVE_MIN_DIFFERENCE = 3.0
JPEG_QUALITY = 88
DEFAULT_MAX_SECONDS = 3 * 60 * 60


def click_in_game(screen: tuple[int, int], mapper: CoordMapper) -> tuple[int, int] | None:
    """Return a click's game coordinates, or None if it landed outside the game."""
    x, y = mapper.screen_to_game(*screen)
    return (round(x), round(y)) if mapper.in_game_bounds(x, y) else None


def worth_saving(frame: np.ndarray, last_saved: np.ndarray | None) -> bool:
    """Return whether the picture changed enough to keep it between two clicks."""
    return last_saved is None or frame_difference(frame, last_saved) >= AUTO_SAVE_MIN_DIFFERENCE


class Recording:
    """A folder of numbered frames, plus events.jsonl describing them."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.clicks = 0
        self.start = time.monotonic()
        self.last_saved: np.ndarray | None = None

    def save(self, frame: np.ndarray, kind: str, point: tuple[int, int] | None = None) -> None:
        self.count += 1
        suffix = f"_{point[0]}_{point[1]}" if point else ""
        name = f"{self.count:05d}_{kind}{suffix}.jpg"
        encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not encoded:
            raise ValueError("could not encode the frame")
        (self.folder / name).write_bytes(buffer.tobytes())
        event = {"seconds": round(time.monotonic() - self.start, 2), "kind": kind, "point": point, "file": name}
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write(json.dumps(event) + "\n")
        self.last_saved = frame


def is_pressed(key: int) -> bool:
    """Return whether a key or mouse button is held right now."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000)


def record(game, recording: Recording, stop_event, max_seconds: float) -> str:
    """Poll the mouse and the screen until the stop key, the time limit or the game closing."""
    was_down = False
    down_since = 0.0
    esc_since: float | None = None
    last_grab = 0.0
    last_auto = 0.0
    frame = None
    with Capture(game) as capture:
        while not stop_event.is_set():
            now = time.monotonic()
            if now - recording.start > max_seconds:
                return "time limit"
            if frame is None or now - last_grab >= GRAB_SECONDS:
                frame = capture.grab()
                last_grab = now
            is_down = is_pressed(VK_LBUTTON)
            if is_down and not was_down:
                down_since = now
            if was_down and not is_down:
                recording.save(frame, f"release_{now - down_since:.1f}s")
            if is_pressed(VK_ESCAPE) and game.is_foreground():
                if esc_since is None:
                    esc_since = now
                    recording.save(frame, "esc_down")
            elif esc_since is not None:
                recording.save(frame, f"esc_up_{now - esc_since:.1f}s")
                esc_since = None
            if is_down and not was_down and game.is_foreground():
                point = click_in_game(cursor_position(), CoordMapper(game.client_rect_on_screen()))
                if point is not None:
                    # The frame from just before the press shows what was clicked.
                    recording.save(frame, "click", point)
                    recording.clicks += 1
                    last_auto = now
            was_down = is_down
            if now - last_auto >= AUTO_SAVE_SECONDS and worth_saving(frame, recording.last_saved):
                recording.save(frame, "auto")
                last_auto = now
            time.sleep(POLL_SECONDS)
    return "stop key"


def main(argv: list[str] | None = None) -> int:
    """Record until stopped and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--seconds", type=float, default=DEFAULT_MAX_SECONDS, help="time limit")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    logger = setup_logging(config.logging.model_copy(update={"log_dir": ROOT / config.logging.log_dir}))
    folder = ROOT / config.logging.log_dir / "recordings" / datetime.now().strftime("%Y%m%d_%H%M%S")
    watcher = StopWatcher()
    try:
        watcher.start()
        game = open_game_window(config.window, logger)
        recording = Recording(folder)
        print(f"Recording to {folder}. Play normally, {STOP_KEY_NAME} stops.", flush=True)
        reason = record(game, recording, watcher.event, args.seconds)
    except (WindowError, OSError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    finally:
        watcher.close()
    print(f"Stopped ({reason}): {recording.clicks} clicks, {recording.count} frames in {folder}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

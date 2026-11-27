"""Live view of the capture with a game-space grid, the mouse's game coordinates and match boxes."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.window import enable_dpi_awareness, open_game_window

enable_dpi_awareness()

import cv2
import numpy as np

from core.capture import Capture, is_blank
from core.config import ConfigError, load_config
from core.logger import frame_hash, setup_logging
from core.manifest import load_manifest
from core.vision import TemplateLibrary
from core.window import WindowError

WINDOW_NAME = "e7bot overlay (Q to quit)"
GRID_STEP = 100
GRID_COLOR = (90, 90, 90)
BOX_COLOR = (0, 255, 0)
TEXT_COLOR = (0, 255, 255)
ESC_KEY = 27
MATCH_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True)
class OverlayBox:
    """A labelled rectangle in game space."""

    label: str
    x: int
    y: int
    width: int
    height: int


def draw_overlay(
    frame: np.ndarray,
    scale: float,
    status: str,
    boxes: list[OverlayBox] | None = None,
    mouse: tuple[int, int] | None = None,
) -> np.ndarray:
    """Return a scaled, annotated copy of a game-space frame."""
    height, width = frame.shape[:2]
    image = cv2.resize(frame, (round(width * scale), round(height * scale)))

    def to_view(value: float) -> int:
        return round(value * scale)

    for x in range(GRID_STEP, width, GRID_STEP):
        cv2.line(image, (to_view(x), 0), (to_view(x), image.shape[0]), GRID_COLOR, 1)
        cv2.putText(image, str(x), (to_view(x) + 2, 12), cv2.FONT_HERSHEY_PLAIN, 0.8, GRID_COLOR, 1)
    for y in range(GRID_STEP, height, GRID_STEP):
        cv2.line(image, (0, to_view(y)), (image.shape[1], to_view(y)), GRID_COLOR, 1)
        cv2.putText(image, str(y), (2, to_view(y) - 2), cv2.FONT_HERSHEY_PLAIN, 0.8, GRID_COLOR, 1)

    for box in boxes or []:
        top_left = (to_view(box.x), to_view(box.y))
        bottom_right = (to_view(box.x + box.width), to_view(box.y + box.height))
        cv2.rectangle(image, top_left, bottom_right, BOX_COLOR, 1)
        cv2.putText(image, box.label, (top_left[0], max(top_left[1] - 3, 10)), cv2.FONT_HERSHEY_PLAIN, 0.9, BOX_COLOR, 1)

    text = status if mouse is None else f"{status} | mouse {mouse[0]},{mouse[1]}"
    cv2.putText(image, text, (6, image.shape[0] - 8), cv2.FONT_HERSHEY_PLAIN, 1.0, TEXT_COLOR, 1)
    return image


def find_boxes(frame: np.ndarray, library: TemplateLibrary) -> list[OverlayBox]:
    """Match every loaded template and return a box for each hit."""
    boxes = []
    for name in library.templates:
        match = library.find(frame, name)
        if match is not None:
            label = f"{name} {match.score:.2f}"
            boxes.append(OverlayBox(label, match.x, match.y, match.width, match.height))
    return boxes


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--scale", type=float, default=0.5, help="view size relative to the game")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this time (0: until Q)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Show the overlay until the user quits. Returns the process exit code."""
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    logger = setup_logging(config.logging.model_copy(update={"log_dir": ROOT / config.logging.log_dir}))

    library = TemplateLibrary(config.vision, logger)
    try:
        library.load(load_manifest(ROOT / config.vision.manifest), ROOT / config.vision.templates_dir)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    mouse: list[tuple[int, int] | None] = [None]

    def on_mouse(_event: int, x: int, y: int, _flags: int, _param: object) -> None:
        mouse[0] = (round(x / args.scale), round(y / args.scale))

    try:
        game = open_game_window(config.window, logger)
        with Capture(game) as capture:
            rect = game.client_rect_on_screen()
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
            # Beside the game, because anything covering it would end up in the capture.
            cv2.moveWindow(WINDOW_NAME, rect.left + rect.width + 8, rect.top)
            cv2.setMouseCallback(WINDOW_NAME, on_mouse)
            start = time.monotonic()
            last = start
            boxes: list[OverlayBox] = []
            last_match = 0.0
            while True:
                frame = capture.grab()
                now = time.monotonic()
                fps = 1 / max(now - last, 1e-3)
                last = now
                # Matching every template takes longer than a frame, so it runs less often.
                if now - last_match >= MATCH_INTERVAL_SECONDS:
                    boxes = find_boxes(frame, library)
                    last_match = now
                blank = " | BLANK" if is_blank(frame) else ""
                status = f"{fps:4.1f} fps | {frame_hash(frame)}{blank}"
                cv2.imshow(WINDOW_NAME, draw_overlay(frame, args.scale, status, boxes, mouse[0]))
                key = cv2.waitKey(30) & 0xFF
                if key in (ord("q"), ESC_KEY):
                    break
                if args.seconds and now - start >= args.seconds:
                    break
    except WindowError as error:
        logger.error("%s", error)
        return 1
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())

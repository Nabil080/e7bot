"""Read every piece of the open Inventory and write what the gear rules would do with each one. Changes nothing."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.window import enable_dpi_awareness, open_game_window

enable_dpi_awareness()

import cv2
import numpy as np

from core.awake import keep_awake
from core.capture import Capture
from core.config import load_config
from core.gear import GearPolicy, gear_score, speed_potential
from core.settings import gear_policy
from core.input import InputController
from core.inventory import (
    GRID_COLUMNS_X,
    SCROLL_FROM,
    SCROLL_TO,
    TITLE_REGION,
    PieceReading,
    has_card,
    panel_changed,
    panel_of,
    inventory_count,
    read_piece,
    scroll_shift,
    verdict,
    visible_rows,
)
from core.settings import apply_settings, load_settings
from core.vision import Ocr

AFTER_CLICK_SECONDS = 0.3
# The panel can take a moment to show the clicked piece: wait for it to change, click again once.
PANEL_WAIT_SECONDS = 2.5
PANEL_POLL_SECONDS = 0.15
AFTER_SCROLL_SECONDS = 1.2
# Below this, a scroll did not move the grid: its end is reached.
MIN_SCROLL_PIXELS = 10
MAX_SCROLLS = 120
DEBUG_DIR: Path | None = None
REPORT_PATH = ROOT / "logs" / "gear_report.csv"


def scroll_to_top(capture: Capture, controller: InputController) -> None:
    """Drag the grid down until it stops moving: the scan starts from the first row."""
    for _ in range(MAX_SCROLLS):
        before = capture.grab()
        controller.drag(*SCROLL_TO, *SCROLL_FROM)
        time.sleep(AFTER_SCROLL_SECONDS)
        if scroll_shift(capture.grab(), before) < MIN_SCROLL_PIXELS:
            return


def select_card(capture: Capture, controller: InputController, x: int, y: int) -> np.ndarray | None:
    """Click a card and return the frame once the panel shows it, or None if the panel never changed.

    Without this wait, a slow panel is read twice: once for its old piece, once for the new one.
    """
    before = panel_of(capture.grab())
    for _ in range(2):
        controller.click(x, y)
        time.sleep(AFTER_CLICK_SECONDS)
        deadline = time.monotonic() + PANEL_WAIT_SECONDS
        while time.monotonic() < deadline:
            frame = capture.grab()
            if panel_changed(before, panel_of(frame)):
                # One more beat: the panel draws its rows one after the other.
                time.sleep(PANEL_POLL_SECONDS)
                return capture.grab()
            time.sleep(PANEL_POLL_SECONDS)
    return None


def scan(capture: Capture, controller: InputController, ocr: Ocr, rules: GearPolicy, logger: logging.Logger) -> list[dict[str, str]]:
    """Click every card of the grid from the top, read its panel, and return one report row per card."""
    frame = capture.grab()
    if "inventory" not in ocr.read(frame, TITLE_REGION).casefold():
        raise SystemExit("open the game's Inventory first, on the tab that shows every piece")
    scroll_to_top(capture, controller)
    count = inventory_count(frame, ocr)
    logger.info("inventory count: %s", count)
    rows: list[dict[str, str]] = []
    offset, done_rows, scrolls = 0, set(), 0
    while True:
        frame = capture.grab()
        for row, y in visible_rows(offset):
            if row in done_rows:
                continue
            done_rows.add(row)
            for column, x in enumerate(GRID_COLUMNS_X):
                if not has_card(capture.grab(), x, y):
                    logger.info("row %d column %d is empty: end of the grid", row + 1, column + 1)
                    return rows
                shown = select_card(capture, controller, x, y)
                if shown is None and not rows:
                    # The first card may already be the selected one: its panel is in view and cannot change.
                    shown = capture.grab()
                reading = read_piece(shown, ocr) if shown is not None else None
                if DEBUG_DIR is not None and shown is not None:
                    cv2.imwrite(str(DEBUG_DIR / f"{len(rows) + 1:03d}.jpg"), shown, [cv2.IMWRITE_JPEG_QUALITY, 70])
                rows.append(report_row(len(rows) + 1, reading, rules))
                logger.info("%d: %s -> %s", len(rows), reading.describe() if reading else "unread", rows[-1]["verdict"])
        if scrolls >= MAX_SCROLLS:
            logger.warning("stopped after %d scrolls", scrolls)
            return rows
        before = capture.grab()
        controller.drag(*SCROLL_FROM, *SCROLL_TO)
        time.sleep(AFTER_SCROLL_SECONDS)
        shift = scroll_shift(before, capture.grab())
        scrolls += 1
        if shift < MIN_SCROLL_PIXELS:
            logger.info("the grid did not move: end of the grid")
            return rows
        offset += shift


def report_row(index: int, reading: PieceReading | None, rules: GearPolicy) -> dict[str, str]:
    if reading is None:
        return {"index": str(index), "verdict": "unread", "reason": "the panel could not be read in full"}
    action, reason = verdict(reading, rules)
    return {
        "index": str(index),
        "grade": reading.grade,
        "slot": reading.slot,
        "level": str(reading.level),
        "set": reading.set_name,
        "main": reading.main_stat,
        "substats": " ".join(f"{name}={value:g}" for name, value in reading.substats.items()),
        "gear_score": f"{gear_score(reading.substats):g}",
        "speed": f"{reading.substats.get('speed', 0):g}",
        "speed_potential": f"{speed_potential(reading.piece()):g}",
        "locked": str(reading.locked),
        "equipped": str(reading.equipped),
        "verdict": action,
        "reason": reason,
    }


def main() -> None:
    """Scan the Inventory open in the game and write logs/gear_report.csv."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--frames", type=Path, help="save the frame of every card read in this folder")
    args = parser.parse_args()
    global DEBUG_DIR
    if args.frames:
        args.frames.mkdir(parents=True, exist_ok=True)
        DEBUG_DIR = args.frames
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logger = logging.getLogger("gear_scan")
    config = load_config(args.config)
    config = apply_settings(config, load_settings(ROOT / "settings.json", config))
    rules = gear_policy(config.gear)
    game = open_game_window(config.window, logger, foreground=False)
    controller = InputController(game, config.input, logger)
    ocr = Ocr(config.vision)
    with keep_awake(), Capture(game, background=config.input.background) as capture:
        started = time.monotonic()
        rows = scan(capture, controller, ocr, rules, logger)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ["index", "grade", "slot", "level", "set", "main", "substats", "gear_score", "speed", "speed_potential", "locked", "equipped", "verdict", "reason"]
    with REPORT_PATH.open("w", newline="", encoding="utf-8") as report:
        writer = csv.DictWriter(report, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tally: dict[str, int] = {}
    for row in rows:
        tally[row["verdict"]] = tally.get(row["verdict"], 0) + 1
    logger.info("%d pieces in %.0f s: %s. Report: %s", len(rows), time.monotonic() - started, tally, REPORT_PATH)


if __name__ == "__main__":
    main()

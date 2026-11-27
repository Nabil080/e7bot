"""Reading the Enhance Equipment screen, where a piece shows its substats with their roll counts, and the grid's selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from core.gear import Piece
from core.inventory import GRADES, SLOTS, badge_level, find_word, parse_value, stat_key
from core.vision import Ocr

# The Enhance Equipment screen, in the 1280x720 game space.
HEADER_REGION = (136, 106, 260, 26)
BADGE_REGION = (100, 84, 36, 20)
MAIN_NAME_REGION = (66, 219, 175, 30)
MAIN_VALUE_REGION = (230, 219, 140, 30)
SUB_TOP = 275
SUB_PITCH = 25
SUB_HEIGHT = 25
SUB_NAME_X, SUB_NAME_WIDTH = 36, 230
SUB_VALUE_X, SUB_VALUE_WIDTH = 255, 110
SET_REGION = (70, 439, 230, 28)
LOCK_REGION = (182, 640, 44, 44)
# The lock button is lit (177) when the piece is locked, dark (33) otherwise.
LOCKED_MEAN = 100
GOLD_REGION = (748, 20, 125, 28)
VALUE_WHITE_LEVEL = 160
# A missing substat shows its row as "Added on +9 Enhance".
ADDED_ROW = re.compile(r"added on", re.IGNORECASE)
ROLL_COUNT = re.compile(r"\((\d)\)")
FIRST_NUMBER = re.compile(r"^\s*([\d,]+%?)")
# Substats a piece is crafted with, by grade: the others are added at +12 (and +9 for a rare).
START_SUBSTATS = {"epic": 4, "heroic": 3, "rare": 2}
# The highest value one roll can give at T85, with a margin for the reading: anything over is a misread.
ROLL_MAX = {
    "attack_percent": 8, "defense_percent": 8, "health_percent": 8, "effectiveness": 8, "effect_resistance": 8,
    "crit_chance": 5, "crit_damage": 7, "speed": 5, "attack": 46, "defense": 34, "health": 201,
}
# The selected card of the Inventory grid is framed by a pale yellow line: its top edge is this long.
FRAME_MIN_PIXELS = 120
CARD_LEFT_X = (145, 315, 485, 655)
CARD_BAND_WIDTH = 170
# From the frame's top edge to the card's center, where the bot clicks.
FRAME_TO_CENTER = 50
GRID_TOP, GRID_BOTTOM = 140, 716


@dataclass(frozen=True)
class EnhanceReading:
    """Everything the bot read of the piece on the Enhance Equipment screen."""

    grade: str
    slot: str
    level: int
    main_stat: str
    substats: dict[str, float] = field(default_factory=dict)
    rolls: dict[str, int] = field(default_factory=dict)
    set_name: str = ""
    locked: bool = False

    def piece(self) -> Piece:
        """Return the piece as the gear rules judge it, with its roll counts."""
        return Piece(self.slot, self.main_stat, self.level, dict(self.substats), self.grade, self.set_name, dict(self.rolls))

    def describe(self) -> str:
        """Return a short line for the log: grade, slot, level, set, main stat and substats with their rolls."""
        subs = ", ".join(
            f"{name} {value:g}" + (f" ({self.rolls[name]})" if self.rolls.get(name) else "") for name, value in self.substats.items()
        )
        return f"{self.grade} {self.slot} +{self.level} {self.set_name or '?'} set, main {self.main_stat}: {subs}"


def plausible(reading: EnhanceReading) -> str:
    """Return why a reading cannot be a real piece, or "" when it can: a misread must never be acted on."""
    start = START_SUBSTATS.get(reading.grade)
    if start is None:
        return f"grade {reading.grade} is never judged"
    added = len(reading.substats) - start
    if added < 0 or len(reading.substats) > 4:
        return f"{len(reading.substats)} substats on a {reading.grade} piece"
    tiers = min(5, reading.level // 3)
    if sum(reading.rolls.values()) + added != tiers:
        return f"{sum(reading.rolls.values())} rolls and {added} added substats at +{reading.level}"
    for name, value in reading.substats.items():
        most = ROLL_MAX.get(name)
        if most is None or not 1 <= value <= most * (1 + reading.rolls.get(name, 0)):
            return f"{name} {value:g} is out of range"
    return ""


def read_enhance(frame: np.ndarray, ocr: Ocr, known_level: int | None = None) -> EnhanceReading | None:
    """Read the piece of the Enhance Equipment screen, or None if it cannot be read in full.

    `known_level`: the level the bot enhanced the piece to; it is used whenever the +N badge shows. Otherwise the
    badge is read, and when its small digits cannot be, the level is the one the rolls tell: 3 per roll received or
    substat added. The rules only ever use the level in steps of 3, so +4 and +3 are judged alike.
    """
    header = ocr.read(frame, HEADER_REGION)
    grade, slot = find_word(header, GRADES), find_word(header, SLOTS)
    badge = badge_level(frame, ocr, BADGE_REGION)
    if not grade or not slot:
        return None
    main_name = ocr.read(frame, MAIN_NAME_REGION)
    main_value = ocr.read(frame, MAIN_VALUE_REGION, whitelist="0123456789,%", white_level=VALUE_WHITE_LEVEL)
    main_stat = stat_key(main_name, main_value)
    if main_stat is None or parse_value(main_value) is None:
        return None
    substats: dict[str, float] = {}
    rolls: dict[str, int] = {}
    for index in range(4):
        y = SUB_TOP + index * SUB_PITCH
        name = ocr.read(frame, (SUB_NAME_X, y, SUB_NAME_WIDTH, SUB_HEIGHT))
        if ADDED_ROW.search(name):
            # The rows below a missing substat are missing too.
            break
        value_text = ocr.read(frame, (SUB_VALUE_X, y, SUB_VALUE_WIDTH, SUB_HEIGHT))
        first = FIRST_NUMBER.match(value_text)
        count = ROLL_COUNT.search(name)
        clean_name = ROLL_COUNT.sub("", name)
        key = stat_key(clean_name, first.group(1) if first else "")
        number = parse_value(first.group(1)) if first else None
        if key is None and number is None and not name.strip():
            break
        if key is None or number is None or key in substats:
            return None
        substats[key] = number
        rolls[key] = int(count.group(1)) if count else 0
    if not substats:
        return None
    if badge == 0:
        level = 0
    elif known_level:
        # Bulk Enhancement enhances to the exact level: what the bot asked for beats a small badge's reading.
        level = known_level
    elif badge is not None:
        level = badge
    else:
        added = max(0, len(substats) - START_SUBSTATS.get(grade, 4))
        level = min(15, 3 * (sum(rolls.values()) + added))
        if level == 0:
            # A badge shows, yet no roll: the reading cannot be trusted.
            return None
    set_text = ocr.read(frame, SET_REGION)
    set_match = re.match(r"([A-Za-z ]+?)\s*Set\b", set_text)
    return EnhanceReading(
        grade=grade,
        slot=slot,
        level=level,
        main_stat=main_stat,
        substats=substats,
        rolls=rolls,
        set_name=set_match.group(1).strip().casefold() if set_match else "",
        locked=is_locked(frame),
    )


def is_locked(frame: np.ndarray) -> bool:
    """Return whether the Enhance screen's lock button shows the piece locked."""
    x, y, w, h = LOCK_REGION
    return float(frame[y : y + h, x : x + w].mean()) > LOCKED_MEAN


def read_amount(frame: np.ndarray, ocr: Ocr, region: tuple[int, int, int, int]) -> int | None:
    """Read an amount of gold such as "17,600", or None."""
    text = ocr.read(frame, region, whitelist="0123456789,")
    digits = text.replace(",", "")
    return int(digits) if digits.isdigit() else None


def selected_card(frame: np.ndarray) -> tuple[int, int] | None:
    """Return (column, center y) of the Inventory card framed as selected, or None if none is in view.

    The center can be below the screen when only the frame's top edge shows.
    """
    area = frame[GRID_TOP:GRID_BOTTOM]
    bright = (area[:, :, 0] > 190) & (area[:, :, 1] > 220) & (area[:, :, 2] > 220)
    for column, left in enumerate(CARD_LEFT_X):
        counts = bright[:, left : left + CARD_BAND_WIDTH].sum(axis=1)
        rows = np.nonzero(counts >= FRAME_MIN_PIXELS)[0]
        if len(rows):
            return column, int(rows[0]) + GRID_TOP + FRAME_TO_CENTER
    return None

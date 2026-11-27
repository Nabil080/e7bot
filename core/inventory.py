"""Reading a piece of gear from the inventory's detail panel, where every stat is written in full."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from core.gear import GearPolicy, Piece, judge
from core.vision import Ocr

# The detail panel on the right of the Inventory, in the 1280x720 game space.
HEADER_REGION = (972, 172, 170, 90)
BADGE_REGION = (935, 170, 36, 18)
MAIN_NAME_REGION = (878, 330, 220, 26)
MAIN_VALUE_REGION = (1100, 330, 128, 26)
SUB_TOP = 371
SUB_PITCH = 22.7
SUB_NAME_X, SUB_NAME_WIDTH = 878, 220
SUB_VALUE_X, SUB_VALUE_WIDTH = 1100, 128
SUB_HEIGHT = 23
SET_REGION = (912, 553, 200, 30)
LOCK_REGION = (1178, 170, 30, 28)
UNEQUIP_REGION = (895, 660, 75, 35)
# Values are white over a dark panel: a white level keeps "7%" from being read "71%".
VALUE_WHITE_LEVEL = 160
VALUE_CHARACTERS = "0123456789,%"
# A piece whose +N badge shows but cannot be read: enhanced, at a level the panel does not tell.
UNREAD_LEVEL = -1
# The +N badge is white over a red (+15) or orange pill.
BADGE_WHITE = 150
BADGE_PILL_PIXELS = 100
BADGE_SCALES = (3, 4)
BADGE_MORE_SCALES = (2, 5, 6)
# A locked piece's lock button is light; an unlocked one is dark.
LOCKED_BRIGHTNESS = 150
# The Unequip button is lit only when a hero wears the piece.
EQUIPPED_BRIGHT_PIXELS = 100

GRADES = ("normal", "good", "rare", "heroic", "epic")
SLOTS = ("weapon", "helmet", "armor", "necklace", "ring", "boots")
# Stat names as the panel writes them. Attack, defense and health are flat without "%".
STAT_NAMES = {
    "attack": "attack",
    "defense": "defense",
    "health": "health",
    "speed": "speed",
    "critical hit chance": "crit_chance",
    "critical hit damage": "crit_damage",
    "effectiveness": "effectiveness",
    "effect resistance": "effect_resistance",
}
PERCENT_CAN_BE_FLAT = ("attack", "defense", "health")


@dataclass(frozen=True)
class PieceReading:
    """Everything the bot read of one piece in the detail panel."""

    grade: str
    slot: str
    level: int
    main_stat: str
    substats: dict[str, float] = field(default_factory=dict)
    set_name: str = ""
    locked: bool = False
    equipped: bool = False

    def piece(self) -> Piece:
        """Return the piece as the gear rules judge it. The panel does not show the roll counts."""
        return Piece(self.slot, self.main_stat, self.level, dict(self.substats), self.grade, self.set_name)

    def describe(self) -> str:
        """Return a short line for the log: slot, level, set and stats."""
        subs = ", ".join(f"{name} {value:g}" for name, value in self.substats.items())
        return f"{self.grade} {self.slot} +{self.level} {self.set_name or '?'} set, main {self.main_stat}: {subs}"


def stat_key(name: str, value: str) -> str | None:
    """Return the stat key of a panel row, such as attack_percent, or None for an unknown name."""
    cleaned = re.sub(r"[^a-z ]", "", name.casefold()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    key = STAT_NAMES.get(cleaned)
    if key is None:
        return None
    if key in PERCENT_CAN_BE_FLAT and value.endswith("%"):
        return f"{key}_percent"
    return key


def parse_value(text: str) -> float | None:
    """Return the number of a stat value such as "2,765" or "12%", or None if there is none."""
    digits = text.replace(",", "").replace("%", "").strip()
    if not digits.isdigit():
        return None
    return float(digits)


def find_word(text: str, words: tuple[str, ...]) -> str:
    """Return the first of the words found in a text, or "" if none is."""
    folded = text.casefold()
    for word in words:
        if re.search(rf"\b{word}\b", folded):
            return word
    return ""


def badge_level(frame: np.ndarray, ocr: Ocr, region: tuple[int, int, int, int] = BADGE_REGION) -> int | None:
    """Return the +N enhancement of a piece's icon: 0 without a badge, None if the badge is unreadable."""
    x, y, w, h = region
    badge = frame[y : y + h, x : x + w]
    red = badge[:, :, 2].astype(int)
    pill = (red > 170) & (badge[:, :, 0] < 100) & (red - badge[:, :, 1] > 60)
    if int(pill.sum()) < BADGE_PILL_PIXELS:
        return 0
    white = (badge.min(axis=2) > BADGE_WHITE).astype(np.uint8) * 255
    # The digits are small: read them dark on white, bigger and with a margin. Two sizes first; when they do not
    # agree (Tesseract drops a "4" at some sizes), three more. A level counts only when two readings give it and no
    # reading gives another one.
    readings: list[int] = []
    for scales in (BADGE_SCALES, BADGE_MORE_SCALES):
        for scale in scales:
            big = cv2.resize(white, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            page = cv2.copyMakeBorder(255 - big, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
            text = ocr.read(cv2.cvtColor(page, cv2.COLOR_GRAY2BGR), None, whitelist="+0123456789")
            match = re.fullmatch(r"\+?(\d{1,2})", text)
            if match is not None and 1 <= int(match.group(1)) <= 15:
                readings.append(int(match.group(1)))
        if len(readings) >= 2 and len(set(readings)) == 1:
            return readings[0]
    return None


def _row(frame: np.ndarray, ocr: Ocr, y: int, name_x: int, name_width: int, value_x: int, value_width: int, height: int) -> tuple[str, str]:
    name = ocr.read(frame, (name_x, y, name_width, height))
    value = ocr.read(frame, (value_x, y, value_width, height), whitelist=VALUE_CHARACTERS, white_level=VALUE_WHITE_LEVEL)
    return name, value


def is_locked(frame: np.ndarray) -> bool:
    """Return whether the panel's lock button shows the piece locked."""
    x, y, w, h = LOCK_REGION
    gray = cv2.cvtColor(frame[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
    return float(gray.mean()) > LOCKED_BRIGHTNESS


def is_equipped(frame: np.ndarray) -> bool:
    """Return whether a hero wears the panel's piece: only then is Unequip lit."""
    x, y, w, h = UNEQUIP_REGION
    return int((frame[y : y + h, x : x + w].min(axis=2) > 200).sum()) > EQUIPPED_BRIGHT_PIXELS


def read_piece(frame: np.ndarray, ocr: Ocr) -> PieceReading | None:
    """Read the piece shown in the Inventory's detail panel, or None if it cannot be read in full.

    A piece the bot is unsure of is never judged: a wrong reading could sell a good piece.
    """
    header = ocr.read(frame, HEADER_REGION, single_line=False)
    grade, slot = find_word(header, GRADES), find_word(header, SLOTS)
    level = badge_level(frame, ocr)
    if level is None:
        # Enhanced, its small badge unread: the Enhance screen tells the level from the rolls.
        level = UNREAD_LEVEL
    if not grade or not slot:
        return None
    main_name, main_value = _row(frame, ocr, MAIN_NAME_REGION[1], MAIN_NAME_REGION[0], MAIN_NAME_REGION[2], MAIN_VALUE_REGION[0], MAIN_VALUE_REGION[2], MAIN_NAME_REGION[3])
    main_stat = stat_key(main_name, main_value)
    if main_stat is None or parse_value(main_value) is None:
        return None
    substats: dict[str, float] = {}
    for index in range(4):
        y = SUB_TOP + round(index * SUB_PITCH)
        name, value = _row(frame, ocr, y, SUB_NAME_X, SUB_NAME_WIDTH, SUB_VALUE_X, SUB_VALUE_WIDTH, SUB_HEIGHT)
        key, number = stat_key(name, value), parse_value(value)
        if key is None and number is None:
            # Pieces under Epic start with fewer than four substats: the rest of the rows are empty.
            break
        if key is None or number is None or key in substats:
            return None
        substats[key] = number
    if not substats:
        return None
    set_text = ocr.read(frame, SET_REGION)
    set_match = re.match(r"([A-Za-z ]+?)\s*Set\b", set_text)
    return PieceReading(
        grade=grade,
        slot=slot,
        level=level,
        main_stat=main_stat,
        substats=substats,
        set_name=set_match.group(1).strip().casefold() if set_match else "",
        locked=is_locked(frame),
        equipped=is_equipped(frame),
    )


# The Inventory's grid: four columns of cards, a row every ROW_PITCH pixels, scrolled by dragging.
GRID_COLUMNS_X = (190, 360, 530, 700)
FIRST_ROW_Y = 200
ROW_PITCH = 129
# Card centers the bot clicks: lower ones are cut by the bottom of the screen.
CLICK_Y_RANGE = (175, 680)
GRID_REGION = (140, 150, 690, 550)
# Dragging this far scrolls three rows: the grid follows the drag pixel for pixel.
SCROLL_FROM, SCROLL_TO = (445, 620), (445, 233)
CARD_HALF = 40
# A card slot without a piece is flat background.
CARD_MIN_DEVIATION = 25.0
TITLE_REGION = (60, 14, 130, 40)
COUNT_REGION = (1026, 92, 96, 30)
COUNT_VOTES = 3


PANEL_REGION = (872, 160, 360, 420)
# Two pieces can differ by one digit: count the pixels that changed a lot, not the mean change.
PANEL_PIXEL_CHANGE = 60
PANEL_CHANGED_PIXELS = 30


def panel_of(frame: np.ndarray) -> np.ndarray:
    x, y, w, h = PANEL_REGION
    return cv2.cvtColor(frame[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)


def panel_changed(before: np.ndarray, after: np.ndarray) -> bool:
    """Return whether the detail panel shows something else than before."""
    changed = np.abs(before.astype(np.int16) - after.astype(np.int16)) > PANEL_PIXEL_CHANGE
    return int(changed.sum()) > PANEL_CHANGED_PIXELS


def visible_rows(offset: int) -> list[tuple[int, int]]:
    """Return the (row index, center y) of the rows the bot can click with the grid scrolled by offset."""
    rows = []
    first = max(0, (offset + CLICK_Y_RANGE[0] - FIRST_ROW_Y) // ROW_PITCH)
    row = first
    while True:
        y = FIRST_ROW_Y + row * ROW_PITCH - offset
        if y > CLICK_Y_RANGE[1]:
            return rows
        if y >= CLICK_Y_RANGE[0]:
            rows.append((row, y))
        row += 1


def has_card(frame: np.ndarray, x: int, y: int) -> bool:
    """Return whether a piece sits in the grid slot centered on (x, y)."""
    area = frame[y - CARD_HALF : y + CARD_HALF, x - CARD_HALF : x + CARD_HALF]
    return float(cv2.cvtColor(area, cv2.COLOR_BGR2GRAY).std()) > CARD_MIN_DEVIATION


# A band of the new frame is looked for in the old one: two bands that agree give the shift at once.
SHIFT_BAND = 90
SHIFT_BAND_TOPS = (0, 40)


def scroll_shift(before: np.ndarray, after: np.ndarray, most: int = 450) -> int:
    """Return how many pixels the grid moved up between two frames, found by matching their overlap.

    Two bands of the new frame are found in the old one (15 ms); when they disagree (frames that do not overlap,
    a panel that opened), every shift is compared in full (250 ms), as before.
    """
    x, y, w, h = GRID_REGION
    old_gray = cv2.cvtColor(before[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
    new_gray = cv2.cvtColor(after[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
    found = set()
    for band_top in SHIFT_BAND_TOPS:
        reach = min(most, h - SHIFT_BAND - band_top)
        scores = cv2.matchTemplate(old_gray[band_top : band_top + reach + SHIFT_BAND], new_gray[band_top : band_top + SHIFT_BAND], cv2.TM_SQDIFF)
        found.add(int(scores.argmin()))
    if len(found) == 1:
        return found.pop()
    old = old_gray.astype(np.int16)
    new = new_gray.astype(np.int16)
    best_shift, best_error = 0, float("inf")
    for shift in range(0, min(most, h - 60)):
        error = float(np.abs(old[shift:] - new[: h - shift]).mean())
        if error < best_error:
            best_shift, best_error = shift, error
    return best_shift


def inventory_count(frame: np.ndarray, ocr: Ocr) -> tuple[int, int] | None:
    """Return the (pieces, room) count shown above the grid, such as (242, 400)."""
    # Read five ways (white levels, bigger): a count the bot acts on must be what three readings at least agree on.
    x, y, w, h = COUNT_REGION
    bigger = cv2.resize(frame[y : y + h, x : x + w], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    texts = [ocr.read(frame, COUNT_REGION, whitelist="0123456789/", white_level=level) for level in (VALUE_WHITE_LEVEL, 130, None)]
    texts += [ocr.read(bigger, None, whitelist="0123456789/", white_level=level) for level in (VALUE_WHITE_LEVEL, None)]
    votes: dict[tuple[int, int], int] = {}
    for text in texts:
        match = re.fullmatch(r"(\d+)/(\d+)", text)
        if match is not None and int(match.group(1)) <= int(match.group(2)):
            key = (int(match.group(1)), int(match.group(2)))
            votes[key] = votes.get(key, 0) + 1
    best = max(votes.items(), key=lambda item: item[1], default=None)
    return best[0] if best is not None and best[1] >= COUNT_VOTES else None


def verdict(reading: PieceReading, policy: GearPolicy) -> tuple[str, str]:
    """Return what the gear rules would do with a piece, and why: enhance, keep, lock, extract, sell, or leave it alone."""
    if reading.equipped:
        return "leave", "worn by a hero"
    if reading.locked:
        return "leave", "locked"
    if reading.level == UNREAD_LEVEL:
        return "leave", "level unread"
    decision = judge(reading.piece(), policy)
    return decision.action, decision.reason


# The Steel Workshop's result for a single piece: a panel in the middle of the screen, the stats written out (main
# stat on a lighter bar, then the substats), and Lock, Sell and Extract buttons on its right. Its rows are found by
# the bright text of the value column, so a piece with fewer substats (a shorter panel) reads the same way.
SINGLE_HEADER_REGION = (588, 182, 140, 52)
SINGLE_NAME_X, SINGLE_NAME_WIDTH = 490, 195
SINGLE_VALUE_X, SINGLE_VALUE_WIDTH = 690, 92
SINGLE_ROWS_BAND = (295, 470)
SINGLE_SET_REGION = (526, 515, 240, 36)
SINGLE_ROW_INK = 3
SINGLE_ROW_GAP = 40
SINGLE_ROW_MARGIN = 5


def single_rows(frame: np.ndarray) -> list[tuple[int, int]]:
    """Return the (top, bottom) of the stat rows of the single piece's panel: main stat first, then the substats."""
    top, bottom = SINGLE_ROWS_BAND
    column = frame[top:bottom, SINGLE_VALUE_X : SINGLE_VALUE_X + SINGLE_VALUE_WIDTH]
    bright = (column.min(axis=2) > VALUE_WHITE_LEVEL).sum(axis=1) >= SINGLE_ROW_INK
    rows: list[tuple[int, int]] = []
    start = None
    for y, lit in enumerate(bright):
        if lit and start is None:
            start = y
        elif not lit and start is not None:
            if rows and start - rows[-1][1] > SINGLE_ROW_GAP:
                break
            rows.append((start + top, y + top))
            start = None
    return rows


def read_single(frame: np.ndarray, ocr: Ocr) -> PieceReading | None:
    """Read the single crafted piece's panel, or None if it cannot be read in full. It is a new piece: +0."""
    header = ocr.read(frame, SINGLE_HEADER_REGION, single_line=False)
    grade, slot = find_word(header, GRADES), find_word(header, SLOTS)
    rows = single_rows(frame)
    if not grade or not slot or len(rows) < 2:
        return None
    lines = []
    for row_top, row_bottom in rows:
        y, height = row_top - SINGLE_ROW_MARGIN, row_bottom - row_top + 2 * SINGLE_ROW_MARGIN
        name, value = _row(frame, ocr, y, SINGLE_NAME_X, SINGLE_NAME_WIDTH, SINGLE_VALUE_X, SINGLE_VALUE_WIDTH, height)
        key, number = stat_key(name, value), parse_value(value)
        if key is None or number is None:
            return None
        lines.append((key, number))
    main_stat, substats = lines[0][0], dict(lines[1:])
    if len(substats) != len(lines) - 1:
        return None
    set_match = re.match(r"([A-Za-z ]+?)\s*Set\b", ocr.read(frame, SINGLE_SET_REGION))
    return PieceReading(grade, slot, 0, main_stat, substats, set_match.group(1).strip().casefold() if set_match else "")

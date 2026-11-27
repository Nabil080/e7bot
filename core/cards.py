"""Reading the small cards of the Inventory grid (Display Format "Full") without clicking them.

A card shows the piece's grade (the icon's color), a +N badge once enhanced, a padlock when locked, the set badge,
and five lines: the main stat then the substats, each an icon and a value. Icons, set badges, grades and slots
(from the artwork, among the slots the main stat allows) are matched against templates built from cards whose detail
panel was read (tools/build_card_models.py). Anything not clearly recognised makes the card unsure: the bot then
clicks it and reads the detail panel. The card reader never guesses.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from core.enhance import ROLL_MAX
from core.gear import REFORGED_LEVEL, Piece, estimate_rolls, reforge_gain, roll_range
from core.vision import Ocr

# A card around its center (the point the bot clicks).
CROP_LEFT, CROP_TOP, CROP_WIDTH, CROP_HEIGHT = -50, -50, 180, 120
# Stat lines: icon then value, every 21 pixels.
ICON_X0, ICON_X1 = 101, 123
LINE_CENTERS = (18, 41, 62, 82, 103)
ICON_HALF = 9
# The value stops before the card's right edge, where a selected card shows its frame.
VALUE_X0, VALUE_X1 = 124, 170
VALUE_HALF = 10
# The card can sit a few pixels off the counted scroll: the main stat's icon is looked for this far up and down.
ALIGN_SEARCH = 8
LINE_SEARCH = 2
# Trusted matches: good enough, and clearly ahead of the next candidate. Crit chance and crit damage look alike
# the most: measured on 300 icons, the right one is always at least 0.19 ahead.
MIN_SCORE = 0.55
MIN_MARGIN = 0.03
CRIT_MARGIN = 0.12
# No template matches an empty line this well (0.49 at most, measured).
EMPTY_SCORE = 0.5
SET_MIN_SCORE = 0.6
SET_MIN_MARGIN = 0.05
SLOT_MIN_SCORE = 0.55
SLOT_MIN_MARGIN = 0.1
GRADE_MAX_RATIO = 0.7
# The +N badge (red or orange) and the padlock, in the card.
BADGE_BOX = (56, 0, 46, 19)
BADGE_PIXELS = 100
LOCK_BOX = (3, 0, 19, 17)
LOCK_PIXELS = 100
SET_BOX = (64, 55, 36, 33)
ART_BOX = (18, 22, 48, 58)
# A worn piece's hero portrait covers the artwork's lower left corner: a circle about (9, 49) in the art box, of radius
# 19 (22 with its rim). The level 88/90 examples are compared without it (measured: 0 wrong, 3 unread of 126 pieces).
ART_PORTRAIT = (9, 49, 22)
GRADE_BOX = (17, 12, 74, 72)
# An Otherworldly piece's card has a purple frame: right, top and bottom edges. Measured: 250 purple pixels or more
# on 19 such cards, under 20 on every other card.
OTHERWORLDLY_EDGES = ((88, 10, 6, 80), (10, 0, 76, 6), (10, 88, 76, 8))
OTHERWORLDLY_PIXELS = 150

STAT_ICON = {
    "attack": "attack", "attack_percent": "attack", "defense": "defense", "defense_percent": "defense",
    "health": "health", "health_percent": "health", "speed": "speed", "crit_chance": "crit_chance",
    "crit_damage": "crit_damage", "effectiveness": "effectiveness", "effect_resistance": "effect_resistance",
}
PERCENT_OR_FLAT = ("attack", "defense", "health")
# The slots a main stat can sit on.
SLOTS_OF_MAIN = {
    "attack": ("weapon", "necklace", "ring", "boots"), "health": ("helmet", "necklace", "ring", "boots"),
    "defense": ("armor", "necklace", "ring", "boots"), "attack_percent": ("necklace", "ring", "boots"),
    "health_percent": ("necklace", "ring", "boots"), "defense_percent": ("necklace", "ring", "boots"),
    "crit_chance": ("necklace",), "crit_damage": ("necklace",), "effectiveness": ("ring",),
    "effect_resistance": ("ring",), "speed": ("boots",),
}
# Values: characters of the game's font. A digit matches its template this well (0.75 at least, measured), and
# what follows a number (the "%") never matches a digit above 0.6.
GLYPH_SIZE = (10, 14)
GLYPH_MIN_HEIGHT, GLYPH_MAX_HEIGHT = 9, 16
DIGIT_SCORE = 0.72
DIGIT_MARGIN = 0.05
# A thin "1" after another digit can score a little less, but then far ahead of any other digit (0.5, measured).
LOOSE_DIGIT_SCORE = 0.62
LOOSE_DIGIT_MARGIN = 0.35
NOT_DIGIT_SCORE = 0.65
PERCENT_INK = 12
# Two digits of a number are 5 pixels apart at most (1,325 pairs measured); the Bulk screen puts its gain after a
# wider space.
GLYPH_MAX_GAP = 5
# The main stat's value, compared whole with the known ones.
MAIN_VALUE_WIDTH = 44
MAIN_MARGIN = 0.1
ALWAYS_PERCENT = ("crit_chance", "crit_damage", "effectiveness", "effect_resistance")
# The lowest first roll of a flat substat on level 70 to 85 gear: a smaller "flat" value is a "%" the reading missed.
FLAT_MIN = {"attack": 20, "defense": 15, "health": 80}
# An enhanced main stat: only its form matters. A "%" is a wide character (15 pixels, measured on 418 cards)
# after the digits (8 to 9 pixels); bits narrower than a digit at the edge are not characters.
PERCENT_GLYPH_WIDTH = 13
DIGIT_GLYPH_WIDTH = 10
MIN_GLYPH_WIDTH = 5
MAIN_GLYPH_MAX_HEIGHT = 18
MAIN_X1 = 180
# How many digits an enhanced main stat shows, from +1 to +15: it confirms the icon (a "12" is never flat health).
MAIN_DIGITS = {"health": (3, 4), "attack": (3, 3), "defense": (2, 3), "speed": (1, 2)}
PERCENT_MAIN_DIGITS = (2, 2)
# Substats a piece starts with; one more comes at each of these levels while it has fewer than four.
START_SUBSTATS = {"epic": 4, "heroic": 3, "rare": 2}
ADDED_AT = (12, 9)
MODELS_PATH = Path("templates/cards/models.npz")
BADGE_MODELS_PATH = Path("templates/cards/badges.npz")
LEVEL_MODELS_PATH = Path("templates/cards/levels.npz")
# The artworks of level 88 and 90 pieces, which differ from level 85's: built by tools/build_art_samples.py.
ART_SAMPLES_PATH = Path("templates/cards/art.npz")
# The item level, in yellow on the icon's top left corner (an epic card adds a yellow ornament before it). Only
# these levels are judged: 85, and 88 (rolls a little higher, never reforged).
LEVEL_BOX = (6, 4, 42, 24)
LEVEL_GLYPH_SIZE = (8, 12)
LEVEL_DIGIT_WIDTH = 8
LEVEL_SCORE = 0.6
LEVEL_MARGIN = 0.12
# A card's brightest level pixel is 250 or more; a dimmed card's about 127 (measured live on ticked and Bulk cards).
DIMMED_BRIGHTEST = 200
# A digit of the level: at most this tall, its top this low in the level's box (the ornament is taller, higher).
LEVEL_DIGIT_HEIGHT, LEVEL_DIGIT_TOP = 13, 9
JUDGED_ITEM_LEVELS = (85, 88)
# A locked card's padlock hides the first digit: the second one tells the levels judged, 90 being reforged gear.
LOCKED_SECOND_X = 19
LOCKED_LEVELS = {"5": 85, "8": 88, "0": 90}
# At level 88 a roll can be a little higher than at 85: one point more on a % or speed, a tenth more on a flat stat.
LEVEL_88_EXTRA = 1
# The portrait of the hero wearing a piece: a circle on the icon's lower left corner.
PORTRAIT_OFFSET = (-23, 21)
PORTRAIT_RADIUS = (16, 22)
PORTRAIT_SEARCH = 30
PORTRAIT_TOLERANCE = 5
PERCENT_MODEL_PATH = Path("templates/cards/percent.npy")
DIGIT_SAMPLES_PATH = Path("templates/cards/digits.npz")
# Digits matched against every labelled example: held out by collection, 5,987 digits read right, the right one
# scoring 0.77 at least and 0.08 ahead; no other character scored over 0.63.
SAMPLE_DIGIT_SCORE = 0.7
SAMPLE_DIGIT_MARGIN = 0.05
SAMPLE_NOT_DIGIT_SCORE = 0.68
# The "%" after a value's digits, as a picture this wide from the first ink after them.
PERCENT_WIDTH = 15
# A "%" starts at most 3 pixels after the last digit (3,272 measured: 0, 1 or 3); the Bulk screen's "(+N)" starts 3 or
# more after it.
PERCENT_GAP = 3
# Measured: a "%" scores 0.63 or more (2,474 signs), a digit 0.39 at most (797).
PERCENT_SCORE = 0.5
# The +N badge: red at +15, orange below. Its white characters ("+", then the digits) are matched one by one.
BADGE_RED_GREEN = 95
BADGE_GLYPH_SIZE = (8, 12)
BADGE_GLYPH_MIN_HEIGHT = 7
# Measured on the labelled badges: a digit scores 0.75 at least, 0.28 ahead of the next one.
BADGE_GLYPH_SCORE = 0.7
BADGE_GLYPH_MARGIN = 0.2
BADGE_PLUS_SCORE = 0.6
VALUE_PATTERN = re.compile(r"^(\d{1,4})(%?)$")


@dataclass(frozen=True)
class CardReading:
    """A piece as its card shows it."""

    grade: str
    slot: str
    main_stat: str
    substats: dict[str, float] = field(default_factory=dict)
    set_name: str = ""
    level: int = 0
    item_level: int = 85

    def piece(self) -> Piece:
        """Return the piece as the rules judge it: at +0 every roll count is 0; enhanced, they are estimated."""
        rolls = {name: 0 for name in self.substats} if self.level == 0 else estimate_rolls(self.grade, self.level, self.substats)
        return Piece(self.slot, self.main_stat, self.level, dict(self.substats), self.grade, self.set_name, rolls, self.item_level)

    def describe(self) -> str:
        """Return a short line for the log, as the panel reader writes it."""
        subs = ", ".join(f"{name} {value:g}" for name, value in self.substats.items())
        tier = "" if self.item_level == 85 else f" (level {self.item_level})"
        return f"{self.grade} {self.slot} +{self.level} {self.set_name or '?'} set{tier}, main {self.main_stat}: {subs}"


def card_key(reading: CardReading) -> tuple:
    """Return what tells a piece from the others in the bot's eyes: its slot, stats, set and level."""
    return (reading.slot, reading.main_stat, tuple(sorted(reading.substats.items())), reading.set_name, reading.level)


@dataclass(frozen=True)
class CardResult:
    """What the bot can tell from a card: "new" or "enhanced" with its reading, "locked", "equipped", or "unsure"."""

    status: str
    reading: CardReading | None = None
    why: str = ""
    # How far the card sat from the given center: the caller corrects its scroll count with it.
    shift: int = 0


@dataclass
class CardModels:
    """The templates the card reader matches against."""

    stats: dict[str, np.ndarray]
    digits: dict[str, np.ndarray]
    sets: dict[str, np.ndarray]
    grades: dict[str, np.ndarray]
    art_vectors: np.ndarray
    art_slots: np.ndarray
    main_vectors: np.ndarray
    main_keys: np.ndarray
    badge_glyphs: dict[str, np.ndarray] = field(default_factory=dict)
    percent: np.ndarray | None = None
    digit_samples: np.ndarray | None = None
    digit_labels: np.ndarray | None = None
    digit_classes: list[str] = field(default_factory=list)
    level_glyphs: dict[str, np.ndarray] = field(default_factory=dict)
    digit_starts: np.ndarray | None = None
    art_extra_vectors: np.ndarray | None = None
    art_extra_slots: np.ndarray | None = None

    def __post_init__(self) -> None:
        # Which known main values are a %: worked out once, not for every card.
        self.main_percent = np.array([key.endswith("percent") or key in ALWAYS_PERCENT for key in self.main_keys.astype(str)])
        if self.digit_labels is not None:
            self.digit_classes, starts = np.unique(self.digit_labels, return_index=True)
            self.digit_classes = [str(label) for label in self.digit_classes]
            self.digit_starts = starts

    def rank_digit(self, glyph: np.ndarray) -> list[tuple[str, float]]:
        """Return the digits by how well a character matches them: their closest example, or their template."""
        if self.digit_samples is None:
            return ranked(glyph, self.digits)
        # The examples are sorted by digit at load: one pass gives each digit's best match.
        best = np.maximum.reduceat(self.digit_samples @ glyph.astype(np.float32), self.digit_starts)
        order = np.argsort(-best)
        return [(self.digit_classes[i], float(best[i])) for i in order]

    @classmethod
    def load(cls, path: Path = MODELS_PATH, logger: logging.Logger | None = None) -> CardModels | None:
        """Load the templates, or None (with a warning) when they were never built: the bot then reads the panel."""
        if not path.is_file():
            if logger is not None:
                logger.warning("card templates missing (%s): pieces are read from the detail panel", path)
            return None
        data = np.load(path)

        def group(prefix: str) -> dict[str, np.ndarray]:
            return {key.removeprefix(prefix): data[key] for key in data.files if key.startswith(prefix)}

        badges: dict[str, np.ndarray] = {}
        badge_path = path.parent / BADGE_MODELS_PATH.name
        if badge_path.is_file():
            badge_data = np.load(badge_path)
            badges = {key: badge_data[key] for key in badge_data.files}
        elif logger is not None:
            logger.warning("badge templates missing (%s): enhanced cards below +15 are left unread", badge_path)
        percent_path = path.parent / PERCENT_MODEL_PATH.name
        percent = np.load(percent_path) if percent_path.is_file() else None
        samples = labels = None
        samples_path = path.parent / DIGIT_SAMPLES_PATH.name
        if samples_path.is_file():
            digit_data = np.load(samples_path)
            labels = digit_data["labels"].astype(str)
            order = np.argsort(labels, kind="stable")
            samples, labels = digit_data["features"].astype(np.float32)[order], labels[order]
        levels: dict[str, np.ndarray] = {}
        level_path = path.parent / LEVEL_MODELS_PATH.name
        if level_path.is_file():
            level_data = np.load(level_path)
            levels = {key: level_data[key] for key in level_data.files}
        art_extra_vectors = art_extra_slots = None
        art_path = path.parent / ART_SAMPLES_PATH.name
        if art_path.is_file():
            art_data = np.load(art_path)
            art_extra_vectors, art_extra_slots = art_data["vectors"].astype(np.float32), art_data["slots"].astype(str)
        elif logger is not None:
            logger.warning("artwork samples missing (%s): level 88/90 pieces may be left unread", art_path)
        art_vectors, art_slots = distinct(data["art_vectors"], data["art_slots"])
        main_vectors, main_keys = distinct(data["main_vectors"], data["main_keys"])
        return cls(
            group("stat_"), group("digit_"), group("set_"), group("grade_"),
            art_vectors, art_slots, main_vectors, main_keys, badges, percent, samples, labels, level_glyphs=levels,
            art_extra_vectors=art_extra_vectors, art_extra_slots=art_extra_slots,
        )


# Two known examples this alike are the same picture: one is enough, and reading is faster.
DUPLICATE = 0.99


def distinct(vectors: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the examples without near copies of one another under the same label."""
    vectors = vectors.astype(np.float32)
    kept: list[int] = []
    for label in np.unique(labels):
        same: list[int] = []
        for index in np.nonzero(labels == label)[0]:
            if not same or float((vectors[same] @ vectors[index]).max()) < DUPLICATE:
                same.append(int(index))
        kept += same
    return vectors[kept], labels[kept]


def normalized(values: np.ndarray) -> np.ndarray:
    """Return values with zero mean and unit norm, so brightness does not matter (the main stat is whiter)."""
    values = values.astype(np.float32)
    values = values - values.mean()
    norm = float(np.linalg.norm(values))
    return values / norm if norm else values


def box(image: np.ndarray, area: tuple[int, int, int, int]) -> np.ndarray:
    """Return a box (x, y, width, height) of an image."""
    x, y, width, height = area
    return image[y : y + height, x : x + width]


def icon_patch(gray: np.ndarray, center_y: int) -> np.ndarray:
    """Return the stat icon of a line, normalised."""
    return normalized(gray[center_y - ICON_HALF : center_y + ICON_HALF + 1, ICON_X0:ICON_X1])


def badge_patch(gray: np.ndarray) -> np.ndarray:
    return normalized(box(gray, SET_BOX))


def grade_features(card: np.ndarray) -> np.ndarray:
    """Return the hue histogram of the icon's vivid pixels: red for epic, purple for heroic, blue for rare."""
    hsv = cv2.cvtColor(box(card, GRADE_BOX), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    vivid = hsv[(hsv[:, 1] > 120) & (hsv[:, 2] > 60)]
    histogram = np.bincount((vivid[:, 0] // 10).astype(int), minlength=18).astype(np.float32)
    return histogram / (histogram.sum() or 1)


def art_features(card: np.ndarray) -> np.ndarray:
    """Return the outline of the piece's artwork, small and normalised: it tells the slot."""
    gray = cv2.cvtColor(box(card, ART_BOX), cv2.COLOR_BGR2GRAY).astype(np.float32)
    edges = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    return normalized(cv2.resize(edges, (16, 20))).ravel()


def art_without_portrait(art: np.ndarray) -> np.ndarray:
    """Return the outline of an artwork (the card's art box) without its lower left corner, where a worn piece shows
    its hero's portrait: two cards of the same piece match whoever wears them."""
    gray = cv2.cvtColor(art, cv2.COLOR_BGR2GRAY).astype(np.float32)
    edges = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    x, y, radius = ART_PORTRAIT
    rows, columns = np.mgrid[0 : edges.shape[0], 0 : edges.shape[1]]
    edges[(columns - x) ** 2 + (rows - y) ** 2 <= radius * radius] = 0
    return normalized(cv2.resize(edges, (16, 20))).ravel()


def crop_card(frame: np.ndarray, center: tuple[int, int], shift: int = 0) -> np.ndarray | None:
    """Return the card around its center, moved down by `shift`, or None when part of it is off screen."""
    x, y = center
    top, left = y + CROP_TOP + shift, x + CROP_LEFT
    if top < 0 or left < 0 or top + CROP_HEIGHT > frame.shape[0] or left + CROP_WIDTH > frame.shape[1]:
        return None
    return frame[top : top + CROP_HEIGHT, left : left + CROP_WIDTH]


def ranked(patch: np.ndarray, templates: dict[str, np.ndarray]) -> list[tuple[str, float]]:
    """Return the templates by how well they match a patch, best first."""
    return sorted(((name, float((patch * template).sum())) for name, template in templates.items()), key=lambda item: -item[1])


def best_icon(gray: np.ndarray, center_y: int, search: int, templates: dict[str, np.ndarray]) -> tuple[list[tuple[str, float]], int]:
    """Return the ranked matches of the best position near a line, and that position's shift."""
    best: list[tuple[str, float]] = []
    best_shift = 0
    for shift in range(-search, search + 1):
        y = center_y + shift
        if y - ICON_HALF < 0 or y + ICON_HALF + 1 > gray.shape[0]:
            continue
        scores = ranked(icon_patch(gray, y), templates)
        if not best or scores[0][1] > best[0][1]:
            best, best_shift = scores, shift
    return best, best_shift


def trusted(scores: list[tuple[str, float]]) -> bool:
    """Return whether the best icon match is clear enough to act on."""
    (name, score), (second, second_score) = scores[0], scores[1]
    if score < MIN_SCORE or score - second_score < MIN_MARGIN:
        return False
    if name in ("crit_chance", "crit_damage"):
        other = "crit_damage" if name == "crit_chance" else "crit_chance"
        return score - dict(scores)[other] >= CRIT_MARGIN
    return True


# Orange's red is mostly not blue: (red - blue) above this share of red. Measured: orange text 0.48 and up, a
# warm-tinted white 0.36.
ORANGE_SHARE = 0.42


def value_groups(region: np.ndarray) -> tuple[np.ndarray, np.ndarray | None, list[list[int]]]:
    """Return a value's gray image, its ink, and its characters: ink parts whose columns overlap, left to right."""
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # The Bulk screen writes each roll's gain after a value in orange ("8% (+4%) (1)"): it is not the value's ink. Orange
    # has almost no blue for its red; a white tinted warm by a colour filter on the user's screen (red 254, blue 162)
    # has more, and must stay ink.
    colors = region.astype(int)
    red_over_blue = colors[:, :, 2] - colors[:, :, 0]
    gray[(red_over_blue > 70) & (red_over_blue > ORANGE_SHARE * colors[:, :, 2])] = float(np.percentile(gray, 10))
    low, high = float(np.percentile(gray, 10)), float(gray.max())
    if high - low < 40:
        return gray, None, []
    ink = (gray > low + 0.45 * (high - low)).astype(np.uint8)
    _, _, boxes, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    parts = sorted(([x, x + w, y, y + h] for x, y, w, h, _ in boxes[1:]), key=lambda part: part[0])
    groups: list[list[int]] = []
    for x0, x1, y0, y1 in parts:
        if groups and x0 < groups[-1][1]:
            last = groups[-1]
            groups[-1] = [last[0], max(last[1], x1), min(last[2], y0), max(last[3], y1)]
        else:
            groups.append([x0, x1, y0, y1])
    return gray, ink, groups


def glyph_features(glyph: np.ndarray) -> np.ndarray:
    """Return a character, small and normalised."""
    return normalized(cv2.resize(glyph, GLYPH_SIZE, interpolation=cv2.INTER_AREA)).ravel()


def value_region(card: np.ndarray, center_y: int, x1: int = VALUE_X1) -> np.ndarray:
    return card[center_y - VALUE_HALF : center_y + VALUE_HALF + 1, VALUE_X0:x1]


def after_digits(gray: np.ndarray, ink: np.ndarray, end: int, dx: int = 0) -> np.ndarray | None:
    """Return what follows a value's digits, from its first ink (moved by dx), as a normalised picture; None when
    nothing does."""
    columns = np.nonzero(ink[:, end + 1 :].sum(axis=0))[0]
    if not len(columns):
        return None
    start = max(0, end + 1 + int(columns[0]) + dx)
    patch = gray[:, start : start + PERCENT_WIDTH]
    if patch.shape[1] < PERCENT_WIDTH:
        return None
    return normalized(patch).ravel()


def read_value(card: np.ndarray, center_y: int, models: CardModels, max_gap: int = GLYPH_MAX_GAP, right: int = VALUE_X1) -> tuple[float, bool] | None:
    """Return the number of a line and whether a "%" follows it, or None when a character is not clearly a digit.

    Digits are matched against templates of the game's font; the "%" is the ink right after the last digit. On
    3,439 values of cards the templates had not seen, this read every one right.
    """
    gray, ink, groups = value_groups(value_region(card, center_y, right))
    if ink is None:
        return None
    digits, end = "", 0
    for x0, x1, y0, y1 in groups:
        height = y1 - y0
        if height > GLYPH_MAX_HEIGHT:
            break
        if height < GLYPH_MIN_HEIGHT:
            if digits and y0 > gray.shape[0] // 2 and x0 - end <= max_gap:
                # A thousands comma, low in the line ("1,188"): the number goes on after it.
                end = x1
                continue
            if digits:
                break
            continue
        if digits and x0 - end > max_gap:
            break
        scores = models.rank_digit(glyph_features(gray[y0:y1, x0:x1]))
        lead = scores[0][1] - scores[1][1]
        if models.digit_samples is not None:
            accepted, not_digit = scores[0][1] >= SAMPLE_DIGIT_SCORE and lead >= SAMPLE_DIGIT_MARGIN, scores[0][1] < SAMPLE_NOT_DIGIT_SCORE
        else:
            accepted = (scores[0][1] >= DIGIT_SCORE and lead >= DIGIT_MARGIN) or (scores[0][1] >= LOOSE_DIGIT_SCORE and lead >= LOOSE_DIGIT_MARGIN)
            not_digit = scores[0][1] < NOT_DIGIT_SCORE
        if accepted:
            digits += scores[0][0]
            end = x1
            continue
        if digits and not_digit:
            break
        return None
    if not digits:
        return None
    if int(ink[:, end + 1 : end + 15].sum()) < PERCENT_INK:
        return float(digits), False
    after = np.nonzero(ink[:, end + 1 :].sum(axis=0))[0]
    if len(after) and after[0] > PERCENT_GAP:
        # A "%" starts at most PERCENT_GAP pixels after its digits; what comes later is the Bulk screen's gain, "(+190)".
        return float(digits), False
    # Something follows the digits: it must be a "%", not a digit the templates could not tell. The "%" can sit a
    # pixel or two off the line's center (the Bulk screen's cards).
    best = -1.0
    for dy in (-2, -1, 0, 1, 2):
        if center_y + dy - VALUE_HALF < 0 or center_y + dy + VALUE_HALF + 1 > card.shape[0]:
            continue
        shifted_gray, shifted_ink, _ = value_groups(value_region(card, center_y + dy, right))
        for dx in (-1, 0, 1):
            patch = None if shifted_ink is None else after_digits(shifted_gray, shifted_ink, end, dx)
            if patch is not None and models.percent is not None:
                best = max(best, float(patch @ models.percent))
    if best < PERCENT_SCORE:
        # Not a "%". After a space it is the Bulk screen's gain, "(+4)": the number is flat. (A "%" read as flat is
        # always refused later: too small for a flat stat, or on an icon that only shows a %.)
        if len(after) and after[0] >= PERCENT_GAP:
            return float(digits), False
        return None
    return float(digits), True


def main_features(card: np.ndarray, center_y: int) -> np.ndarray:
    """Return the main stat's value as a picture: at +0 it is one of a few fixed values ("100", "12%"...)."""
    return normalized(cv2.cvtColor(value_region(card, center_y), cv2.COLOR_BGR2GRAY)[:, :MAIN_VALUE_WIDTH]).ravel()


def read_main(card: np.ndarray, center_y: int, icon: str, models: CardModels) -> str | None:
    """Return the main stat of a +0 card: its value is matched against the main values of known cards.

    The nearest known value must belong to a stat with the same icon, and tell flat from % clearly (on 904 cards
    the templates had not seen, the right answer always led by 0.17 at least).
    """
    best = None
    for shift in (-2, -1, 0, 1, 2):
        scores = models.main_vectors @ main_features(card, center_y + shift)
        if best is None or scores.max() > best.max():
            best = scores
    nearest = str(models.main_keys[int(best.argmax())])
    percent = models.main_percent
    mine = nearest.endswith("percent") or nearest in ALWAYS_PERCENT
    if best[percent == mine].max() - best[percent != mine].max(initial=-1.0) < MAIN_MARGIN:
        return None
    key = stat_key(icon, bool(mine))
    if key is None:
        return None
    # Every % main stat shows the same values; a flat one or speed has its own: it must be the nearest's.
    if not mine and nearest != key:
        return None
    return key


def main_form(card: np.ndarray, center_y: int, digit_width: int = DIGIT_GLYPH_WIDTH) -> tuple[bool, int] | None:
    """Return whether an enhanced card's main value ends with "%", and its number of digits; None when unclear."""
    _, ink, groups = value_groups(value_region(card, center_y, MAIN_X1))
    if ink is None:
        return None
    widths = [x1 - x0 for x0, x1, y0, y1 in groups if GLYPH_MIN_HEIGHT <= y1 - y0 <= MAIN_GLYPH_MAX_HEIGHT and x1 - x0 >= MIN_GLYPH_WIDTH]
    if not widths:
        return None
    if widths[-1] >= PERCENT_GLYPH_WIDTH:
        return True, len(widths) - 1
    if widths[-1] <= digit_width:
        return False, len(widths)
    return None


def enhanced_main(card: np.ndarray, center_y: int, icon: str, digit_width: int = DIGIT_GLYPH_WIDTH) -> str | None:
    """Return an enhanced card's main stat: its icon and the form of its value, which must fit it.

    `digit_width`: the widest a digit is drawn. The Bulk screen draws main values a little bigger (a "4" is 11 pixels).
    """
    form = main_form(card, center_y, digit_width)
    if form is None:
        return None
    percent, digits = form
    key = stat_key(icon, percent)
    if key is None:
        return None
    low, high = PERCENT_MAIN_DIGITS if percent else MAIN_DIGITS[key]
    return key if low <= digits <= high else None


def badge_glyphs(badge: np.ndarray) -> list[np.ndarray]:
    """Return the white characters of a +N badge, left to right, each small and normalised."""
    white = ((badge.min(axis=2) > 190) & (badge.max(axis=2).astype(int) - badge.min(axis=2) < 60)).astype(np.uint8)
    _, _, boxes, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    parts = sorted((list(b[:4]) for b in boxes[1:] if b[4] >= 4), key=lambda part: part[0])
    groups: list[list[int]] = []
    for x, y, w, h in parts:
        if groups and x < groups[-1][0] + groups[-1][2]:
            gx, gy, gw, gh = groups[-1]
            left, top = min(gx, x), min(gy, y)
            groups[-1] = [left, top, max(gx + gw, x + w) - left, max(gy + gh, y + h) - top]
        else:
            groups.append([x, y, w, h])
    gray = cv2.cvtColor(badge, cv2.COLOR_BGR2GRAY)
    return [
        normalized(cv2.resize(gray[y : y + h, x : x + w], BADGE_GLYPH_SIZE, interpolation=cv2.INTER_AREA)).ravel()
        for x, y, w, h in groups if h >= BADGE_GLYPH_MIN_HEIGHT
    ]


def level_yellow(card: np.ndarray) -> np.ndarray:
    """Return the item level's yellow pixels in the card's corner. A dimmed card (ticked, or chosen in the Bulk
    screen) is the same card at about half brightness: its corner is brought back to full brightness first."""
    hsv = cv2.cvtColor(box(card, LEVEL_BOX), cv2.COLOR_BGR2HSV)
    hue = (hsv[:, :, 0] > 12) & (hsv[:, :, 0] < 40) & (hsv[:, :, 1] > 70)
    value = hsv[:, :, 2].astype(np.float32)
    if hue.any():
        brightest = float(np.percentile(value[hue], 98))
        if 0 < brightest < DIMMED_BRIGHTEST:
            value = value * (255.0 / brightest)
    return (hue & (value > 160)).astype(np.uint8)


def level_glyphs(card: np.ndarray, by_place: bool = False) -> list[np.ndarray] | None:
    """Return the two digits of a card's item level, each small and normalised, or None when not two are found.

    The digits are the last two yellow parts (an epic card's ornament comes first). `by_place` finds them by shape
    instead, for a digit that touches the frame's ornament: the first digit is the leftmost part shaped like one, the
    second whatever yellow sits right of it, on its rows.
    """
    yellow = level_yellow(card)
    _, _, boxes, _ = cv2.connectedComponentsWithStats(yellow, connectivity=8)
    parts = sorted((b for b in boxes[1:] if b[3] >= 9 and b[4] >= 15), key=lambda b: b[0])
    if by_place:
        digits = [b for b in parts if b[3] <= LEVEL_DIGIT_HEIGHT and b[2] <= LEVEL_DIGIT_WIDTH + 1 and b[1] >= LEVEL_DIGIT_TOP]
        if not digits:
            return None
        x0, y0, w0, h0, _ = digits[0]
        right = yellow[y0 : y0 + h0, x0 + w0 + 1 : x0 + w0 + 1 + LEVEL_DIGIT_WIDTH]
        used = np.flatnonzero(right.any(axis=0))
        if used.size == 0:
            return None
        cuts = [yellow[y0 : y0 + h0, x0 : x0 + w0], right[:, used[0] : used[-1] + 1]]
    else:
        if len(parts) < 2:
            return None
        # The second digit can touch the artwork's yellow: it is cut to the first digit's rows and to a digit's width.
        (x0, y0, w0, h0, _), (x1, _, w1, _, _) = parts[-2:]
        cuts = [yellow[y0 : y0 + h0, x : x + w] for x, w in ((x0, w0), (x1, min(w1, LEVEL_DIGIT_WIDTH)))]
    return [normalized(cv2.resize(cut.astype(np.float32), LEVEL_GLYPH_SIZE, interpolation=cv2.INTER_AREA)).ravel() for cut in cuts]


def read_item_level(card: np.ndarray, models: CardModels) -> int | None:
    """Return a card's item level (70, 78, 85, 88, 90...), or None when its digits are unclear."""
    if not models.level_glyphs:
        return None
    for by_place in (False, True):
        found = level_glyphs(card, by_place)
        if found is None:
            continue
        text = ""
        for position, glyph in enumerate(found):
            scores = ranked(glyph, {key[1]: value for key, value in models.level_glyphs.items() if key[0] == str(position)})
            if len(scores) < 2 or scores[0][1] < LEVEL_SCORE or scores[0][1] - scores[1][1] < LEVEL_MARGIN:
                break
            text += scores[0][0]
        else:
            return int(text)
    return None


def read_locked_level(card: np.ndarray, models: CardModels) -> int | None:
    """Return a locked card's item level from its second digit (5, 8 or 0: 85, 88 or 90), or None when unclear.

    The padlock hides the first digit. The second one sits right of it, from LOCKED_SECOND_X in the level's box: on
    87 unlocked cards it read the same as the full reader, 0.37 ahead of the next digit at least.
    """
    if not models.level_glyphs:
        return None
    yellow = level_yellow(card)
    _, _, boxes, _ = cv2.connectedComponentsWithStats(yellow, connectivity=8)
    parts = sorted((b for b in boxes[1:] if b[3] >= 9 and b[4] >= 15 and b[0] >= LOCKED_SECOND_X), key=lambda b: b[0])
    if not parts:
        return None
    x, y, w, h, _ = parts[-1]
    glyph = yellow[y : y + h, x : x + min(w, LEVEL_DIGIT_WIDTH)].astype(np.float32)
    vector = normalized(cv2.resize(glyph, LEVEL_GLYPH_SIZE, interpolation=cv2.INTER_AREA)).ravel()
    scores = ranked(vector, {key[1]: value for key, value in models.level_glyphs.items() if key[0] == "1"})
    if len(scores) < 2 or scores[0][1] < LEVEL_SCORE or scores[0][1] - scores[1][1] < LEVEL_MARGIN:
        return None
    return LOCKED_LEVELS.get(scores[0][0])


def read_badge(card: np.ndarray, models: CardModels) -> int | None:
    """Return the level on a card's +N badge: 15 when red, else its characters read; None when unclear."""
    part = box(card, BADGE_BOX).astype(int)
    pill = (part[:, :, 2] > 170) & (part[:, :, 0] < 110) & (part[:, :, 2] - part[:, :, 1] > 50)
    if float(part[:, :, 1][pill].mean()) < BADGE_RED_GREEN:
        return 15
    if not models.badge_glyphs:
        return None
    text = ""
    for index, glyph in enumerate(badge_glyphs(box(card, BADGE_BOX))):
        scores = ranked(glyph, models.badge_glyphs)
        # The "+" only starts the number: it may be clipped a little.
        least = BADGE_PLUS_SCORE if index == 0 else BADGE_GLYPH_SCORE
        if scores[0][1] < least or scores[0][1] - scores[1][1] < BADGE_GLYPH_MARGIN:
            return None
        text += scores[0][0]
    match = re.fullmatch(r"\+(\d{1,2})", text)
    if match is None or not 1 <= int(match.group(1)) <= 14:
        return None
    return int(match.group(1))


def roll_most(key: str, item_level: int) -> float:
    """Return the highest one roll can give a substat, a little more on level 88 gear."""
    if item_level < 88:
        return ROLL_MAX.get(key, 0)
    return ROLL_MAX.get(key, 0) * 1.1 if key in FLAT_MIN else ROLL_MAX.get(key, 0) + LEVEL_88_EXTRA


def substats_at(grade: str, level: int) -> int:
    """Return how many substats a piece of this grade has at this level."""
    start = START_SUBSTATS[grade]
    return min(4, start + sum(1 for tier in ADDED_AT[: 4 - start] if level >= tier))


def stat_key(icon: str, percent: bool) -> str | None:
    """Return the stat of an icon and its value's form, or None for a pair that cannot exist."""
    if icon in PERCENT_OR_FLAT:
        return f"{icon}_percent" if percent else icon
    if icon == "speed":
        return None if percent else icon
    return icon if percent else None


def pixels(card: np.ndarray, area: tuple[int, int, int, int], kind: str) -> int:
    """Count the badge's red or orange pixels, or the padlock's white ones."""
    part = box(card, area).astype(int)
    if kind == "badge":
        red = part[:, :, 2]
        return int(((red > 170) & (part[:, :, 0] < 110) & (red - part[:, :, 1] > 50)).sum())
    return int(((part.min(axis=2) > 170) & (part.max(axis=2) - part.min(axis=2) < 40)).sum())


def equipped(frame: np.ndarray, center: tuple[int, int]) -> bool:
    """Return whether the card around `center` shows the round portrait of the hero wearing it.

    The portrait covers the icon's lower left corner: a circle of radius 19 about (-23, +21) from the card's center.
    Selections (Bulk Enhancement) list worn pieces even when the Inventory hides them.
    """
    x, y = center
    cx, cy = x + PORTRAIT_OFFSET[0], y + PORTRAIT_OFFSET[1]
    half = PORTRAIT_SEARCH
    if cy - half < 0 or cx - half < 0 or cy + half + 1 > frame.shape[0] or cx + half + 1 > frame.shape[1]:
        return False
    gray = cv2.GaussianBlur(cv2.cvtColor(frame[cy - half : cy + half + 1, cx - half : cx + half + 1], cv2.COLOR_BGR2GRAY), (3, 3), 0)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=20, param1=80, param2=15, minRadius=PORTRAIT_RADIUS[0], maxRadius=PORTRAIT_RADIUS[1])
    if circles is None:
        return False
    return any(abs(a - half) <= PORTRAIT_TOLERANCE and abs(b - half) <= PORTRAIT_TOLERANCE for a, b, _ in circles[0])


def read_card(
    frame: np.ndarray, center: tuple[int, int], ocr: Ocr, models: CardModels, level: int | None = None, main_by_form: bool = False,
    slot: str | None = None, read_kept: bool = False, otherworldly_epic: bool = False, reforged: bool = False,
) -> CardResult:
    """Read the card around `center`: "new" or "enhanced" with its reading, "locked", or "unsure" on any doubt.

    An enhanced piece's level is read on its badge, unless the caller knows it (the Bulk screen enhances to an
    exact level). A locked or worn piece is not read, unless `read_kept` asks for it (the advisor judges every
    piece): its status stays "locked" or "equipped", with the reading when there is one. `otherworldly_epic` takes a
    card in the Otherworldly purple frame whose grade the colours leave open for epic (the advisor only). `reforged`
    also reads +15 pieces of level 90 (the account evaluation), their values as the card shows them.
    """
    rough = crop_card(frame, center, 0)
    if rough is None:
        return CardResult("unsure", why="off screen")
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x, y = center
    # Align on the main stat's icon, which every card has.
    scores, shift = best_icon(gray_frame[:, x + CROP_LEFT : x + CROP_LEFT + CROP_WIDTH], y + CROP_TOP + LINE_CENTERS[0], ALIGN_SEARCH, models.stats)
    card = crop_card(frame, center, shift)
    if card is None or not scores or scores[0][1] < MIN_SCORE:
        return CardResult("unsure", why="no card here", shift=shift)
    kept = ""
    if pixels(card, LOCK_BOX, "lock") >= LOCK_PIXELS:
        kept = "locked"
    elif equipped(frame, (x, y + shift)):
        kept = "equipped"
    if kept and not read_kept:
        return CardResult(kept, shift=shift)
    if pixels(card, BADGE_BOX, "badge") >= BADGE_PIXELS:
        if level is None:
            level = read_badge(card, models)
            if not level:
                return CardResult("unsure", why="level badge unread", shift=shift)
    else:
        level = 0
    if not models.level_glyphs:
        item_level = 85
    elif kept == "locked":
        # The padlock hides the first digit: a full read could take the second for it (an "88" read 85).
        item_level = read_locked_level(card, models)
    else:
        item_level = read_item_level(card, models)
    if item_level not in JUDGED_ITEM_LEVELS and not (reforged and level == 15 and item_level == REFORGED_LEVEL):
        return CardResult("unsure", why=f"item level {item_level}: not judged", shift=shift)
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    lines: list[tuple[str, float]] = []
    for index, line_y in enumerate(LINE_CENTERS):
        scores, line_shift = best_icon(gray, line_y, LINE_SEARCH, models.stats)
        if not scores:
            return CardResult("unsure", why=f"line {index + 1} cut", shift=shift)
        if index >= 2 and scores[0][1] < EMPTY_SCORE:
            break
        if not trusted(scores):
            return CardResult("unsure", why=f"line {index + 1}: {scores[0][0]} {scores[0][1]:.2f} vs {scores[1][0]} {scores[1][1]:.2f}", shift=shift)
        if index == 0 and (level > 0 or main_by_form):
            main = enhanced_main(card, line_y + line_shift, scores[0][0])
            if main is None:
                return CardResult("unsure", why=f"main stat: {scores[0][0]} value unclear", shift=shift)
            lines.append((main, 0.0))
            continue
        if index == 0:
            main = read_main(card, line_y + line_shift, scores[0][0], models)
            if main is None:
                return CardResult("unsure", why=f"main stat: {scores[0][0]} not confirmed by its value", shift=shift)
            lines.append((main, 0.0))
            continue
        value = read_value(card, line_y + line_shift, models)
        if value is None:
            return CardResult("unsure", why=f"line {index + 1}: value unread", shift=shift)
        key = stat_key(scores[0][0], value[1])
        if key is None or any(key == name for name, _ in lines):
            return CardResult("unsure", why=f"line {index + 1}: {scores[0][0]} with {value}", shift=shift)
        lowest = FLAT_MIN.get(key) or roll_range(key, "heroic")[0]
        if not lowest <= value[0] <= roll_most(key, item_level) * (1 + level // 3) + (reforge_gain(key, 5) if item_level == REFORGED_LEVEL else 0):
            # A substat is one roll, and one more per enhancement tier at most: more is a misread.
            return CardResult("unsure", why=f"line {index + 1}: {key} {value[0]:g} out of range", shift=shift)
        lines.append((key, value[0]))
    if len(lines) < 3:
        return CardResult("unsure", why="fewer than two substats", shift=shift)
    main, subs = lines[0][0], dict(lines[1:])
    grade = read_grade(card, models, len(subs), level)
    if grade is None and otherworldly_epic and otherworldly(card):
        grade = "epic"
    if grade is None and reforged and level == 15:
        # The account evaluation: a finished piece's efficiency does not depend on its grade (an Otherworldly frame
        # can hide it at +15, where epic and heroic pieces both have four substats).
        grade = "epic"
    if grade is None:
        return CardResult("unsure", why="grade", shift=shift)
    expected = substats_at(grade, level)
    if len(subs) != expected:
        return CardResult("unsure", why=f"{len(subs)} substats on a {grade} piece", shift=shift)
    set_name = read_set(gray, models)
    if set_name is None:
        return CardResult("unsure", why="set", shift=shift)
    if slot is None:
        slot = read_slot(card, main, models, item_level)
    elif slot not in SLOTS_OF_MAIN.get(main, ()):
        return CardResult("unsure", why=f"main stat {main} cannot be on a {slot}", shift=shift)
    if slot is None:
        return CardResult("unsure", why="slot", shift=shift)
    return CardResult(kept or ("new" if level == 0 else "enhanced"), CardReading(grade, slot, main, subs, set_name, level, item_level), shift=shift)


# The Bulk screen draws its cards a little differently: the substat lines sit up to this far from the grid's.
BULK_LINE_SEARCH = 4


def read_bulk_card(frame: np.ndarray, center: tuple[int, int], models: CardModels, known: CardReading, level: int) -> CardReading | None:
    """Return a piece on the Bulk screen after an enhancement, or None on any doubt.

    Its main stat, set, grade and slot are the ones read on its grid card before (enhancing never changes them);
    only the substats are read here, and they must be the known ones (plus the one a +9 or +12 tier adds).
    """
    card = crop_card(frame, center)
    if card is None:
        return None
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    subs: dict[str, float] = {}
    expected = substats_at(known.grade, level)
    # Only the lines the piece has: a heroic piece shows a padlock where its fourth substat will come.
    for line_y in LINE_CENTERS[1 : 1 + expected]:
        scores, line_shift = best_icon(gray, line_y, BULK_LINE_SEARCH, models.stats)
        if not scores or not trusted(scores):
            return None
        value = read_value(card, line_y + line_shift, models)
        if value is None:
            return None
        key = stat_key(scores[0][0], value[1])
        if key is None or key in subs:
            return None
        lowest = FLAT_MIN.get(key) or roll_range(key, "heroic")[0]
        if not lowest <= value[0] <= roll_most(key, known.item_level) * (1 + level // 3):
            return None
        subs[key] = value[0]
    added = set(subs) - set(known.substats)
    if len(subs) != expected or not set(known.substats) <= set(subs) or len(added) != expected - len(known.substats):
        return None
    # A value never goes down with an enhancement.
    if any(subs[name] < value for name, value in known.substats.items()):
        return None
    if read_set(gray, models) not in (known.set_name, None):
        return None
    return CardReading(known.grade, known.slot, known.main_stat, subs, known.set_name, level, known.item_level)


def otherworldly(card: np.ndarray) -> bool:
    """Return whether a card has the Otherworldly frame: purple on its right, top and bottom edges (none on others)."""
    total = 0
    for area in OTHERWORLDLY_EDGES:
        hsv = cv2.cvtColor(box(card, area), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        purple = (hsv[:, 0] >= 125) & (hsv[:, 0] < 165) & (hsv[:, 1] > 90) & (hsv[:, 2] > 90)
        total += int(purple.sum())
    return total >= OTHERWORLDLY_PIXELS


def read_grade(card: np.ndarray, models: CardModels, substats: int | None = None, level: int = 0) -> str | None:
    """Return the grade from the icon's color, or None when it is not clearly one.

    Some cards glow purple around their icon, which blurs the color between two grades: then the number of
    substats decides, when only one of the two grades has that many at this level.
    """
    features = grade_features(card)
    distances = sorted((float(np.linalg.norm(features - mean)), name) for name, mean in models.grades.items())
    if len(distances) < 2:
        return None
    if distances[0][0] <= GRADE_MAX_RATIO * distances[1][0]:
        return distances[0][1]
    if substats is None:
        return None
    fitting = [name for _, name in distances[:2] if substats_at(name, level) == substats]
    return fitting[0] if len(fitting) == 1 else None


def read_set(gray: np.ndarray, models: CardModels) -> str | None:
    """Return the set from its badge, or None when it is not clearly one the templates know."""
    x, y, width, height = SET_BOX
    best: list[tuple[str, float]] = []
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            patch = normalized(gray[y + dy : y + dy + height, x + dx : x + dx + width])
            scores = ranked(patch, models.sets)
            if not best or scores[0][1] > best[0][1]:
                best = scores
    if len(best) < 2 or best[0][1] < SET_MIN_SCORE or best[0][1] - best[1][1] < SET_MIN_MARGIN:
        return None
    return best[0][0]


def read_slot(card: np.ndarray, main: str, models: CardModels, item_level: int = 85) -> str | None:
    """Return the slot: the main stat's only slot, or the artwork's nearest match among the main stat's slots.

    The main stat alone never settles a flat one: necklaces, rings and boots have flat attack, health and defense
    too, with the weapon's, helmet's and armor's values. A level 88/90 piece is also compared with that level's
    artworks, without the portrait (only then: a level 85 card nearly like one would lose its margin).
    """
    allowed = SLOTS_OF_MAIN.get(main, ())
    if len(allowed) == 1:
        return allowed[0]
    features = art_features(card)
    scores: dict[str, float] = {}
    for vector, slot in zip(models.art_vectors, models.art_slots):
        slot = str(slot)
        if slot in allowed:
            scores[slot] = max(scores.get(slot, -2.0), float(vector @ features))
    if item_level >= 88 and models.art_extra_vectors is not None and len(models.art_extra_vectors):
        uncovered = art_without_portrait(box(card, ART_BOX))
        for slot, score in zip(models.art_extra_slots, models.art_extra_vectors @ uncovered):
            slot = str(slot)
            if slot in allowed:
                scores[slot] = max(scores.get(slot, -2.0), float(score))
    best = sorted(scores.items(), key=lambda item: -item[1])
    if len(best) < 2 or best[0][1] < SLOT_MIN_SCORE or best[0][1] - best[1][1] < SLOT_MIN_MARGIN:
        return None
    return best[0][0]


# The Steel Workshop's result screen: each crafted piece is an icon with its stats to the right, the main stat on
# top and the substats on two lines of two. Its lines are copied into a grid card's layout and read the same way.
RESULT_ICON_SIZE = 84
RESULT_MAIN = (109, 14)
RESULT_SUBS = ((110, 45), (184, 44), (109, 69), (184, 69))
# The icon is drawn bigger than on the grid: shrunk by this much, its set badge matches the grid's templates
# (measured on 10 pieces: every set right, 0.8 or more). The area kept around it, and where it goes on the card.
RESULT_ICON_SCALE = 0.92
RESULT_ICON_AREA = (-10, -10, 120, 120)
RESULT_ICON_PASTE = (3, -2)
# The stat lines: shrunk by this much, the stat icons match the grid's (0.85 on average over 10 pieces).
RESULT_LINES_SCALE = 0.86
RESULT_LINES_AREA = (60, -20, 260, 120)
RESULT_LINE_SEARCH = 3
RESULT_ICON_MIN_PIXELS = 3000


def result_icons(frame: np.ndarray) -> list[tuple[int, int]]:
    """Return the top left corners of the crafted pieces' icons, in reading order: squares of grade color."""
    hsv = cv2.cvtColor(frame[150:560, 100:1000], cv2.COLOR_BGR2HSV)
    vivid = ((hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 40)).astype(np.uint8)
    count, _, boxes, _ = cv2.connectedComponentsWithStats(vivid, connectivity=8)
    corners = []
    for x, y, w, h, area in boxes[1:]:
        # The set badge sticks out of the icon's lower right corner: the box is a little wider and taller.
        if RESULT_ICON_SIZE <= w <= RESULT_ICON_SIZE + 14 and RESULT_ICON_SIZE <= h <= RESULT_ICON_SIZE + 8 and area >= RESULT_ICON_MIN_PIXELS:
            corners.append((int(x) + 100, int(y) + 150))
    return sorted(corners, key=lambda corner: (corner[1] // 40, corner[0]))


def result_as_card(frame: np.ndarray, corner: tuple[int, int], models: CardModels | None = None) -> np.ndarray:
    """Return a crafted piece of the result screen laid out as a grid card (icon, then one stat per line).

    The screen draws its pieces bigger than the grid: the icon is shrunk to match the set badges, the stat lines to
    match the stat icons, each line placed on its icon's best match.
    """
    left, top = corner
    card = np.zeros((CROP_HEIGHT, CROP_WIDTH, 3), np.uint8)
    ax, ay, aw, ah = RESULT_ICON_AREA
    icon = cv2.resize(frame[top + ay : top + ay + ah, left + ax : left + ax + aw], None, fx=RESULT_ICON_SCALE, fy=RESULT_ICON_SCALE, interpolation=cv2.INTER_AREA)
    px, py = RESULT_ICON_PASTE
    icon = icon[max(0, -py) :, max(0, -px) :]
    height, width = min(icon.shape[0], CROP_HEIGHT - max(0, py)), min(icon.shape[1], ICON_X0 - max(0, px))
    card[max(0, py) : max(0, py) + height, max(0, px) : max(0, px) + width] = icon[:height, :width]
    rx, ry, rw, rh = RESULT_LINES_AREA
    lines = cv2.resize(frame[top + ry : top + ry + rh, left + rx : left + rx + rw], None, fx=RESULT_LINES_SCALE, fy=RESULT_LINES_SCALE, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(lines, cv2.COLOR_BGR2GRAY)
    width = CROP_WIDTH - ICON_X0
    for line_y, (dx, dy) in zip(LINE_CENTERS, (RESULT_MAIN, *RESULT_SUBS)):
        x, y = round((dx - rx) * RESULT_LINES_SCALE), round((dy - ry) * RESULT_LINES_SCALE)
        if models is not None:
            best = -2.0
            for ox in range(-RESULT_LINE_SEARCH, RESULT_LINE_SEARCH + 1):
                for oy in range(-RESULT_LINE_SEARCH, RESULT_LINE_SEARCH + 1):
                    patch = gray[y + oy - ICON_HALF : y + oy + ICON_HALF + 1, x + ox - 11 : x + ox + 11]
                    if patch.shape != (2 * ICON_HALF + 1, 22):
                        continue
                    score = ranked(normalized(patch), models.stats)[0][1]
                    if score > best:
                        best, best_x, best_y = score, x + ox, y + oy
            x, y = best_x, best_y
        strip = lines[y - VALUE_HALF : y + VALUE_HALF + 1, x - 11 : x - 11 + width]
        card[line_y - VALUE_HALF : line_y - VALUE_HALF + strip.shape[0], ICON_X0 : ICON_X0 + strip.shape[1]] = strip
    return card


def read_result_card(frame: np.ndarray, corner: tuple[int, int], ocr: Ocr, models: CardModels, slot: str | None = None) -> CardResult:
    """Read a crafted piece of the result screen: a +0 piece, read as its grid card would be. Its slot is the one
    crafted when given."""
    card = result_as_card(frame, corner, models)
    padded = cv2.copyMakeBorder(card, 20, 20, 20, 20, cv2.BORDER_CONSTANT)
    # Its main value is read by its form (flat or %, and its digits): the screen's background differs from the grid's.
    return read_card(padded, (20 - CROP_LEFT, 20 - CROP_TOP), ocr, models, level=0, main_by_form=True, slot=slot)

"""Gear advisor: reads the gear on the game's screen and says what the gear rules would do with it. It never clicks.

The player plays by hand. Each frame, the advisor finds which screen shows (Inventory, Arrange or Bulk selection,
Enhance Equipment, Bulk Enhancement, a craft's result), reads every piece it can with the gear modes' own readers
(the small cards, the detail panel, the Enhance screen's rows, the craft result) and judges each one with
`core.gear.judge` and the user's Settings > Gear. A piece the readers are unsure of is shown as unread, never
guessed. Readings are cached by the pixels they come from: a screen that does not change is not read twice.
"""

from __future__ import annotations

import base64
import hashlib
import re
import logging
import threading
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
import pywintypes
import win32gui
import win32ui

from core.cards import (
    CROP_HEIGHT,
    CROP_LEFT,
    CROP_TOP,
    CROP_WIDTH,
    ICON_HALF,
    ICON_X0,
    ICON_X1,
    LINE_CENTERS,
    BADGE_BOX,
    BADGE_PIXELS,
    GRADE_BOX,
    LOCK_BOX,
    LOCK_PIXELS,
    JUDGED_ITEM_LEVELS,
    CardModels,
    CardReading,
    CardResult,
    best_icon,
    box,
    crop_card,
    enhanced_main,
    equipped,
    pixels,
    read_badge,
    read_bulk_card,
    read_card,
    read_grade,
    read_item_level,
    read_set,
    read_slot,
    read_result_card,
    result_icons,
)
from core.enhance import read_enhance
from core.explain import ROLE_WORDS, explain, explain_unread, role_lines, summary
from core.account import as_level_85
from core.gear import GearPolicy, good_main, judged_outlook, plan_view, Piece, gear_score, judge, speed_potential
from core.inventory import GRID_COLUMNS_X, scroll_shift, UNREAD_LEVEL, badge_level, read_piece, read_single
from core.capture import is_blank, print_window
from states.gear import card_ticked
from core.gear_tags import GearTags, Tag
from core.settings import gear_policy
from core.vision import Ocr
from core.window import GameWindow, WindowError, client_size, list_windows, select_game_window

GAME_SIZE = (1280, 720)
# The screen's name, top left; a craft's "Success" banner.
TITLE_REGION = (60, 14, 300, 40)
CRAFT_RESULT_REGION = (540, 58, 200, 80)
# Manage turns the Inventory into a selection: its Manage button becomes Cancel, Arrange shows Auto Select.
SELECT_CANCEL_REGION = (1128, 90, 120, 36)
ARRANGE_MARK_REGION = (140, 86, 140, 38)
# The Inventory's detail panel, read again only when it changes.
PANEL_REGION = (872, 160, 360, 440)
# The Enhance screen's left column, where the piece is.
ENHANCE_REGION = (30, 80, 345, 400)
SINGLE_REGION = (480, 170, 330, 390)
# Cards whose center is in this band are wholly in view: under the tabs, above the bottom edge.
GRID_READ_Y = (188, 650)
GRID_TOP = 140
ROW_PITCH = 129
# A column's main stat icons match a stat template this well where a card is (0.55 is the reader's own floor).
ROW_ICON_SCORE = 0.55
# The Bulk Enhancement screen: 16 slots, 4 by 4, a card centered this far from each slot's top left corner.
BULK_SLOT_LEFT, BULK_SLOT_TOP, BULK_SLOT_WIDTH, BULK_SLOT_PITCH = 16, 128, 244, 129
BULK_CARD_CENTER = (43, 53)
BULK_SLOTS = 16
# A badge sits a pixel or two off where its card's center puts it (higher on the Bulk screen): these heights are tried.
BADGE_DY = (-1, -2, 0, -3, 1)
BULK_LINE_SEARCH = 4
# The main stat's icon is whiter on the Bulk screen: a lower score is enough, still clearly ahead.
BULK_MAIN_SCORE = 0.5
BULK_MAIN_MARGIN = 0.05
BULK_GRADES = ("epic", "heroic", "rare")
# The Bulk screen draws main values bigger: a digit is up to 12 pixels wide there, a "%" 15 or more.
BULK_DIGIT_WIDTH = 12
# A slot's lock button, from the card's center, turns white when the piece is locked.
BULK_LOCK_FROM_CENTER = (171, -33)
LOCK_WHITE_PIXELS = 120
# The Inventory's slot tabs: the open one is lit blue over about a third of this area (0.34 to 0.39 measured, the
# others 0), so its slot is known without reading the artwork.
SLOT_TABS = {"weapon": (260, 118), "helmet": (340, 118), "armor": (418, 118), "necklace": (497, 118), "ring": (577, 118), "boots": (652, 118)}
TAB_LIT_SHARE = 0.2
# A piece's icon is mostly grade color (red, purple, blue); an empty slot has none.
ICON_VIVID_SHARE = 0.3
# A ticked (dimmed) card: 0.16 to 0.24 vivid, 0.52 to 0.68 once dark colors count; empty slots 0.18 and 0.40 at most.
DIM_VIVID_SHARE = (0.15, 0.5)
# A card's stat lines, right of its icon: the same piece, ticked (dimmed) or not, matched them at 1.00; another
# piece at 0.64 at most (measured on live Bulk and Inventory captures).
STATS_FROM_LEFT, SAME_PIECE_SCORE = 95, 0.8
# What the rules would do, as the advisor says it.
ACTION_LABELS = {
    "enhance": "Enhance",
    "speed": "Speed",
    "keep": "Keep",
    "lock": "Lock",
    "sell": "Sell",
    "extract": "Extract",
    "": "Unread",
}
SCREEN_NAMES = {
    "inventory": "Inventory",
    "arrange": "Inventory · Arrange",
    "bulk_select": "Inventory · Bulk selection",
    "enhance": "Enhance Equipment",
    "bulk": "Bulk Enhancement",
    "craft": "Craft result",
    "other": "",
}
# The selected card's frame and its card's center agree this closely.
SELECTED_TOLERANCE = 20
# The window's grid: four cards a row, each with its icon cut from the card (inset from the card's box) and shrunk.
GRID_WIDTH = 4
# A card's icon frame, without the number band under it, cropped near its size on screen: the window never blows it up.
THUMB_INSET, CARD_THUMB, RESULT_THUMB, THUMB_SIZE = 6, 82, 84, 80
# A ticked card is the one read before when it sits this close to where the scroll puts it.
TICK_TOLERANCE = 4
SELECTED_BLUE, SELECTED_GREEN, SELECTED_RED = 140, 180, 200
SELECTED_BAND = (-45, 170)
SELECTED_PIXELS = 120
SELECTED_BOTTOM_PIXELS = 60
SELECTED_TO_CENTER = 50
SELECTED_HEIGHT = (100, 125)
NO_GEAR_HINT = "No piece of gear read here (an artifact is not judged)."
BADGE_UNREAD = "its +N badge is unreadable: open Enhance to judge it"
NO_CARD_HINT = "No card read: set the Inventory's Display Format to Full (filter button) so the cards show their stats."


@dataclass(frozen=True)
class Advice:
    """One piece on screen: where it is, what was read, and what the rules would do with it.

    `box` is the card's area in the 1280x720 game space (None for a piece shown on its own panel). `action` is ""
    when the piece could not be read; `reason` then says why.
    """

    where: str
    box: tuple[int, int, int, int] | None
    action: str
    reason: str
    grade: str = ""
    slot: str = ""
    level: int = 0
    set_name: str = ""
    main_stat: str = ""
    substats: dict[str, float] = field(default_factory=dict)
    rolls: dict[str, int] | None = None
    item_level: int = 85
    locked: bool = False
    equipped: bool = False
    score: float = 0.0
    speed_reach: float = 0.0
    focus: bool = False
    # Read before the player ticked it: the tick hides what the reader needs.
    ticked: bool = False
    # The role it is judged for, its dead stats and the stone in that role, whether its main counts as a good stat.
    role: str = ""
    dead: tuple[str, ...] = ()
    swap: tuple[str, str, float] | None = None
    main_good: bool = False
    # Its efficiency in that role, in % of a perfect piece.
    efficiency: float = 0.0
    # The reason in a few words, and every role tried.
    summary: str = ""
    roles: tuple = ()
    # The card's picture, a PNG data URL, and its place in the grid the window draws (row, column).
    thumb: str = ""
    cell: tuple[int, int] | None = None
    # The judge's own short reason, as the gear modes log it; `reason` says it in plain words.
    rule: str = ""

    @property
    def read(self) -> bool:
        """Return whether the piece was read and judged."""
        return bool(self.action)


@dataclass(frozen=True)
class ScreenAdvice:
    """What the advisor shows for one frame: the screen, the piece looked at (if any) and the cards."""

    screen: str
    focus: Advice | None = None
    cards: tuple[Advice, ...] = ()
    hint: str = ""

    @property
    def name(self) -> str:
        """Return the screen's name as the window shows it."""
        return SCREEN_NAMES.get(self.screen, "")


def verdict(piece: Piece, policy: GearPolicy) -> tuple[str, str]:
    """Return what the gear modes would do with a piece, and why. Only a finished piece is locked, as they do."""
    decision = judge(piece, policy)
    action = decision.action
    if action == "lock" and piece.level < 15:
        action = "keep"
    if decision.for_speed:
        # Still enhanced by the gear modes; the word says it is for its speed alone.
        action = "speed"
    return action, decision.reason


def advice_for(
    piece: Piece, policy: GearPolicy, where: str, box: tuple[int, int, int, int] | None, locked: bool = False, worn: bool = False,
    known_rolls: bool = False,
) -> Advice:
    """Judge a piece read in full. A reforged (level 90) piece is judged on its level 85 values, the scale of the
    rules: its reforge is already done, so none is counted again. The window shows the values the card shows."""
    shown = piece
    piece = as_level_85(piece)
    action, rule = verdict(piece, policy)
    passing = plan_view(piece, policy)
    view = passing[0] if passing is not None else judged_outlook(piece, policy)
    return Advice(
        where=where, box=box, action=action, reason=explain(piece, policy, rule), rule=rule, grade=piece.grade, slot=piece.slot, level=piece.level,
        set_name=piece.set_name, main_stat=piece.main_stat, substats=dict(shown.substats),
        rolls=dict(piece.rolls) if known_rolls and piece.rolls is not None else None, item_level=shown.item_level,
        locked=locked, equipped=worn, score=gear_score(shown.substats), speed_reach=speed_potential(piece),
        role=ROLE_WORDS.get(view.role, view.role), dead=tuple(view.dead), swap=view.swap, main_good=good_main(piece, view.role), efficiency=view.efficiency,
        summary=summary(piece, policy, rule), roles=role_lines(piece, policy),
    )


def unread(where: str, box: tuple[int, int, int, int] | None, why: str, locked: bool = False, worn: bool = False) -> Advice:
    """Return a piece the readers are not sure of."""
    reason = explain_unread(why)
    if locked or worn:
        # The player already keeps it: that is what matters, the reading failing comes second.
        reason = f"{'Locked' if locked else 'Worn by a hero'}: you already keep it. {reason}"
    return Advice(where=where, box=box, action="", reason=reason, rule=why, locked=locked, equipped=worn)


def same_piece(first: Advice, second: Advice) -> bool:
    """Return whether two readings are of the same piece: same slot, main stat and substats."""
    return bool(first.substats) and (first.slot, first.main_stat, first.substats) == (second.slot, second.main_stat, second.substats)


def linked(focus: Advice, cards: list[Advice], selected: tuple[int, int] | None = None) -> tuple[Advice, list[Advice]]:
    """Return the detail panel's piece and the cards, the panel's card marked as such.

    The panel's piece is then counted once, and when the panel's small badge could not be read, its card's level
    (read from the card's own badge) judges it. A selected card the reader could not read (its yellow frame can hide
    its stat icons) takes the panel's piece, found by its place in the grid: `selected` is (column, center y).
    """
    found = next((index for index, card in enumerate(cards) if card.read and same_piece(focus, card)), None)
    if found is None and selected is not None and focus.read:
        column, y = selected
        x = GRID_COLUMNS_X[column]
        place = next(
            (index for index, card in enumerate(cards) if not card.read and card.box is not None
             and card.box[0] - CROP_LEFT == x and abs(card.box[1] - CROP_TOP - y) <= SELECTED_TOLERANCE),
            None,
        )
        if place is not None:
            card = cards[place]
            cards = cards[:place] + [replace(focus, where=card.where, box=card.box, focus=True)] + cards[place + 1 :]
            return replace(focus, focus=True, box=card.box), cards
    if found is None:
        return replace(focus, focus=True), cards
    card = cards[found]
    cards = cards[:found] + [replace(card, focus=True)] + cards[found + 1 :]
    if not focus.read:
        focus = replace(card, where=focus.where, locked=focus.locked, equipped=focus.equipped)
    return replace(focus, focus=True, box=card.box), cards


def selected_frame(frame: np.ndarray) -> tuple[int, int] | None:
    """Return (column, card center y) of the grid card framed as selected, or None when none is in view.

    The frame's top and bottom edges are pale lines across the card: rows of a column with that many pale pixels.
    """
    area = frame[GRID_TOP:]
    pale = (area[:, :, 0] > SELECTED_BLUE) & (area[:, :, 1] > SELECTED_GREEN) & (area[:, :, 2] > SELECTED_RED)
    for column, x in enumerate(GRID_COLUMNS_X):
        left = x + SELECTED_BAND[0]
        counts = pale[:, left : left + SELECTED_BAND[1]].sum(axis=1)
        bottoms = np.nonzero(counts >= SELECTED_BOTTOM_PIXELS)[0]
        for top in np.nonzero(counts >= SELECTED_PIXELS)[0]:
            # A top edge has the bottom edge under it (shorter, cut by the badges): a bottom edge alone, its top
            # scrolled away, is no frame.
            if any(SELECTED_HEIGHT[0] <= other - top <= SELECTED_HEIGHT[1] for other in bottoms):
                return column, int(top) + GRID_TOP + SELECTED_TO_CENTER
    return None


def card_box(center: tuple[int, int]) -> tuple[int, int, int, int]:
    """Return a grid card's area around its center."""
    return center[0] + CROP_LEFT, center[1] + CROP_TOP, CROP_WIDTH, CROP_HEIGHT


def column_scores(gray: np.ndarray, x: int, models: CardModels) -> np.ndarray:
    """Return, for each y, how well the column's stat icon at that height matches its best stat template."""
    left = x + CROP_LEFT + ICON_X0
    strip = gray[:, left : left + (ICON_X1 - ICON_X0)].astype(np.float32)
    best = np.full(gray.shape[0], -1.0, np.float32)
    for template in models.stats.values():
        scores = cv2.matchTemplate(strip, template.astype(np.float32), cv2.TM_CCOEFF_NORMED)[:, 0]
        best[ICON_HALF : ICON_HALF + len(scores)] = np.maximum(best[ICON_HALF : ICON_HALF + len(scores)], scores)
    return best


def grid_rows(frame: np.ndarray, models: CardModels) -> list[int]:
    """Return the center y of the grid's card rows wholly in view, wherever the player scrolled it.

    A card has a stat icon on each of its first three lines, at fixed heights from its center: the rows are where
    the columns' icons line up with that pattern best, one row every ROW_PITCH pixels.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    signal = np.max([column_scores(gray, x, models) for x in GRID_COLUMNS_X], axis=0)
    offsets = [line + CROP_TOP for line in LINE_CENTERS[:3]]
    height = gray.shape[0]
    fit = np.full(height, -1.0, np.float32)
    for center in range(GRID_TOP - CROP_TOP, height):
        lines = [center + offset for offset in offsets]
        if lines[0] < 0 or lines[-1] >= height:
            continue
        fit[center] = min(signal[y] for y in lines)
    best_phase, best_total = None, 0.0
    for phase in range(ROW_PITCH):
        hits = [fit[y] for y in range(phase, height, ROW_PITCH) if fit[y] >= ROW_ICON_SCORE]
        total = float(sum(hits))
        if total > best_total:
            best_phase, best_total = phase, total
    if best_phase is None:
        return []
    return [y for y in range(best_phase, height, ROW_PITCH) if GRID_READ_Y[0] <= y <= GRID_READ_Y[1] and fit[y] >= ROW_ICON_SCORE]


def read_bulk_slot(frame: np.ndarray, center: tuple[int, int], models: CardModels, level: int) -> CardResult:
    """Read a card of the Bulk Enhancement screen, its level known from its badge.

    The icon side is read as on the grid (item level, grade, set, slot) and the main stat by its icon and form; the
    substats with the gear modes' own Bulk reader, which allows for the gains the screen writes after the values.
    """
    card = crop_card(frame, center)
    if card is None:
        return CardResult("unsure", why="off screen")
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    item_level = read_item_level(card, models)
    if item_level not in JUDGED_ITEM_LEVELS:
        return CardResult("unsure", why=f"item level {item_level}: not judged")
    scores, shift = best_icon(gray, LINE_CENTERS[0], BULK_LINE_SEARCH, models.stats)
    if not scores or scores[0][1] < BULK_MAIN_SCORE or scores[0][1] - scores[1][1] < BULK_MAIN_MARGIN:
        return CardResult("unsure", why="main stat icon unclear")
    main = enhanced_main(card, LINE_CENTERS[0] + shift, scores[0][0], BULK_DIGIT_WIDTH)
    if main is None:
        return CardResult("unsure", why=f"main stat: {scores[0][0]} value unclear")
    set_name = read_set(gray, models)
    slot = read_slot(card, main, models, item_level)
    if set_name is None or slot is None:
        return CardResult("unsure", why="set" if set_name is None else "slot")
    for grade in BULK_GRADES:
        known = CardReading(grade, slot, main, {}, set_name, level, item_level)
        reading = read_bulk_card(frame, center, models, known, level)
        if reading is not None and read_grade(card, models, len(reading.substats), level) == grade:
            return CardResult("new" if level == 0 else "enhanced", reading)
    return CardResult("unsure", why="substats unread")


def slot_locked(frame: np.ndarray, center: tuple[int, int]) -> bool:
    """Return whether a Bulk screen slot's lock button is white, as locked."""
    x, y = center[0] + BULK_LOCK_FROM_CENTER[0], center[1] + BULK_LOCK_FROM_CENTER[1]
    button = frame[y - 12 : y + 12, x - 18 : x + 18].astype(int)
    return int((button.min(axis=2) > 200).sum()) >= LOCK_WHITE_PIXELS


def same_card(before: np.ndarray, box_before: tuple[int, int, int, int] | None, now: np.ndarray, box_now: tuple[int, int, int, int] | None) -> bool:
    """Return whether two cards show the same piece: their stat lines match, however dimmed one is by a tick (the
    match ignores brightness). Another piece at the same place, after a screen change, does not match."""
    if box_before is None or box_now is None:
        return False
    parts = []
    for frame, (x, y, w, h) in ((before, box_before), (now, box_now)):
        area = frame[y : y + h, x + STATS_FROM_LEFT : x + w]
        if area.shape[0] != h or area.shape[1] != w - STATS_FROM_LEFT:
            return False
        parts.append(cv2.cvtColor(area, cv2.COLOR_BGR2GRAY).astype(np.float32))
    return float(cv2.matchTemplate(parts[0], parts[1], cv2.TM_CCOEFF_NORMED)[0, 0]) >= SAME_PIECE_SCORE


def open_slot_tab(frame: np.ndarray) -> str | None:
    """Return the slot whose Inventory tab is open (lit blue), or None on the All tab or another screen."""
    lit = []
    for slot, (x, y) in SLOT_TABS.items():
        patch = frame[y - 18 : y + 18, x - 30 : x + 30].astype(int)
        if patch.size and float(((patch[:, :, 0] - patch[:, :, 2] > 60) & (patch[:, :, 0] > 120)).mean()) >= TAB_LIT_SHARE:
            lit.append(slot)
    return lit[0] if len(lit) == 1 else None


def has_icon(card: np.ndarray) -> bool:
    """Return whether a card has a piece's icon: a square of grade color. An empty slot is grey."""
    hsv = cv2.cvtColor(box(card, GRADE_BOX), cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 60)
    if float(vivid.mean()) >= ICON_VIVID_SHARE:
        return True
    # A ticked card is dimmed to about half: its colors are still there, darker. An empty slot's art is not vivid.
    dim = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 30)
    return float(vivid.mean()) >= DIM_VIVID_SHARE[0] and float(dim.mean()) >= DIM_VIVID_SHARE[1]


def kept_flags(frame: np.ndarray, center: tuple[int, int], shift: int) -> tuple[bool, bool]:
    """Return whether a card shows the padlock, and whether a hero's portrait sits on it."""
    card = crop_card(frame, center, shift)
    if card is None:
        return False, False
    locked = pixels(card, LOCK_BOX, "lock") >= LOCK_PIXELS
    return locked, not locked and equipped(frame, (center[0], center[1] + shift))


def judge_card(result: CardResult, policy: GearPolicy, where: str, center: tuple[int, int], frame: np.ndarray) -> Advice:
    """Return the advice for a card the reader went through."""
    box = card_box((center[0], center[1] + result.shift))
    locked, worn = (result.status == "locked", result.status == "equipped")
    if result.reading is None:
        if not (locked or worn):
            locked, worn = kept_flags(frame, center, result.shift)
        return unread(where, box, result.why or "unread", locked, worn)
    return advice_for(result.reading.piece(), policy, where, box, locked, worn)


class Advisor:
    """Reads frames into advice. Keeps the readings of the pixels it saw last, so a still screen costs little."""

    def __init__(self, ocr: Ocr, models: CardModels | None, policy: GearPolicy) -> None:
        self.ocr = ocr
        self.models = models
        self.policy = policy
        self.cache: dict[str, Advice] = {}
        # The last grid read and its frame: a card ticked since keeps its verdict (the tick hides its item level).
        self.grid_frame: np.ndarray | None = None
        self.grid_cards: list[Advice] = []
        self.bulk_frame: np.ndarray | None = None
        self.bulk_cards: list[Advice] = []
        self.next_cache: dict[str, Advice] = {}

    def look(self, frame: np.ndarray) -> ScreenAdvice:
        self.next_cache = {}
        try:
            advice = self._look(frame)
        finally:
            self.cache = self.next_cache
        return with_pictures(frame, advice)

    def _cached(self, key: str, read) -> Advice:
        """Return the advice kept for these pixels, or read it now."""
        advice = self.cache.get(key)
        if advice is None:
            advice = read()
        self.next_cache[key] = advice
        return advice

    def _look(self, frame: np.ndarray) -> ScreenAdvice:
        title = self.ocr.read(frame, TITLE_REGION).casefold()
        if "success" in self.ocr.read(frame, CRAFT_RESULT_REGION).casefold():
            return self.craft(frame)
        if "enhance equipment" in title:
            return self.enhance(frame)
        if "bulk" in title:
            return self.bulk(frame)
        if "inventory" in title:
            return self.inventory(frame)
        return ScreenAdvice("other")

    def inventory(self, frame: np.ndarray) -> ScreenAdvice:
        """The Inventory, plain or in a selection: the cards in view, and the piece of the detail panel."""
        screen = "inventory"
        if "cancel" in self.ocr.read(frame, SELECT_CANCEL_REGION).casefold():
            screen = "arrange" if "auto select" in self.ocr.read(frame, ARRANGE_MARK_REGION).casefold() else "bulk_select"
        cards = self.grid(frame)
        focus = self._cached("panel:" + digest(frame, PANEL_REGION), lambda: self.panel(frame))
        if not focus.read and focus.rule == "no piece":
            focus = None
        if focus is not None:
            # In Arrange and the bulk selection every ticked card has the selected card's yellow frame: there the
            # frame does not tell the panel's card.
            focus, cards = linked(focus, cards, selected_frame(frame) if screen == "inventory" else None)
        hint = NO_CARD_HINT if not cards else ""
        return ScreenAdvice(screen, focus, tuple(cards), hint)

    def panel(self, frame: np.ndarray) -> Advice:
        """Read the Inventory's detail panel."""
        reading = read_piece(frame, self.ocr)
        if reading is None:
            return unread("Detail panel", None, "no piece")
        if reading.level == UNREAD_LEVEL:
            # Its card in the grid may tell the level: the stats are kept to find it.
            return replace(
                unread("Detail panel", None, BADGE_UNREAD, reading.locked, reading.equipped),
                grade=reading.grade, slot=reading.slot, main_stat=reading.main_stat, substats=dict(reading.substats), set_name=reading.set_name,
            )
        return advice_for(reading.piece(), self.policy, "Detail panel", None, reading.locked, reading.equipped)

    def grid(self, frame: np.ndarray) -> list[Advice]:
        """Read every card wholly in view."""
        if self.models is None:
            return []
        cards = []
        slot = open_slot_tab(frame)
        for row, y in enumerate(grid_rows(frame, self.models)):
            for column, x in enumerate(GRID_COLUMNS_X):
                card = crop_card(frame, (x, y))
                if card is None or float(cv2.cvtColor(card, cv2.COLOR_BGR2GRAY).std()) < 25.0:
                    continue
                where = f"Row {row + 1}, card {column + 1}"
                key = f"card:{slot}:{hashlib.md5(card.tobytes()).hexdigest()}"
                advice = self._cached(key, lambda x=x, y=y, where=where: self.card(frame, (x, y), where, slot))
                advice = replace(advice, where=where)
                if not advice.read and card_ticked(frame, (x, y)):
                    advice = self.before_tick(frame, advice)
                cards.append(advice)
        self.grid_frame, self.grid_cards = frame, cards
        return cards

    def before_tick(self, frame: np.ndarray, advice: Advice) -> Advice:
        """Return a ticked card's verdict from before its tick, when the last grid read had it, or the advice as is.

        The grid may have scrolled since: its shift is measured, and the card must sit exactly where the shift puts it.
        """
        if self.grid_frame is None or advice.box is None:
            return advice
        shift = scroll_shift(self.grid_frame, frame)
        x, y = advice.box[0], advice.box[1] + shift
        before = next((card for card in self.grid_cards if card.box is not None and card.box[0] == x and abs(card.box[1] - y) <= TICK_TOLERANCE), None)
        if before is None or not before.read or not same_card(self.grid_frame, before.box, frame, advice.box):
            return advice
        return replace(before, where=advice.where, box=advice.box, ticked=True, focus=False, locked=advice.locked, equipped=advice.equipped)

    def card(self, frame: np.ndarray, center: tuple[int, int], where: str, slot: str | None = None) -> Advice:
        """Read and judge one grid card. A badge the card reader could not read is tried again more patiently. `slot`
        is the Inventory tab's, when one slot's tab is open: the artwork then need not tell it (a level 90 piece's)."""
        result = read_card(frame, center, self.ocr, self.models, slot=slot, read_kept=True, otherworldly_epic=True, reforged=True)
        if result.status == "unsure" and result.why == "level badge unread":
            level = self.badge(frame, (center[0], center[1] + result.shift))
            if level:
                result = read_card(frame, center, self.ocr, self.models, level=level, slot=slot, read_kept=True, otherworldly_epic=True, reforged=True)
        if result.status == "unsure" and result.why == "no card here":
            return unread(where, card_box(center), "no card here")
        return judge_card(result, self.policy, where, center, frame)

    def enhance(self, frame: np.ndarray) -> ScreenAdvice:
        """The Enhance Equipment screen: one piece, with its roll counts."""
        focus = self._cached("enhance:" + digest(frame, ENHANCE_REGION), lambda: self.enhance_piece(frame))
        if not focus.read:
            # An artifact's Enhance screen, or rows still sliding in.
            return ScreenAdvice("enhance", hint=NO_GEAR_HINT)
        return ScreenAdvice("enhance", replace(focus, focus=True))

    def enhance_piece(self, frame: np.ndarray) -> Advice:
        reading = read_enhance(frame, self.ocr)
        if reading is None:
            return unread("Enhance screen", None, "the piece's rows could not be read")
        return advice_for(reading.piece(), self.policy, "Enhance screen", None, reading.locked, known_rolls=True)

    def bulk(self, frame: np.ndarray) -> ScreenAdvice:
        """The Bulk Enhancement screen: up to 16 cards in slots."""
        if self.models is None:
            return ScreenAdvice("bulk")
        cards = []
        for slot in range(BULK_SLOTS):
            left = BULK_SLOT_LEFT + (slot % 4) * BULK_SLOT_WIDTH
            top = BULK_SLOT_TOP + (slot // 4) * BULK_SLOT_PITCH
            center = (left + BULK_CARD_CENTER[0], top + BULK_CARD_CENTER[1])
            card = crop_card(frame, center)
            if card is None or not has_icon(card):
                continue
            key = f"bulk:{hashlib.md5(card.tobytes()).hexdigest()}"
            advice = self._cached(key, lambda center=center, slot=slot: self.bulk_card(frame, center, f"Slot {slot + 1}"))
            advice = replace(advice, where=f"Slot {slot + 1}")
            if not advice.read:
                # A card ticked in Arrange is dimmed: the reading from before the tick stands, as on the grid.
                before = next((card for card in self.bulk_cards if card.where == advice.where and card.read), None)
                if before is not None and self.bulk_frame is not None and same_card(self.bulk_frame, before.box, frame, advice.box):
                    advice = replace(before, ticked=True, focus=False, locked=advice.locked, equipped=advice.equipped)
            cards.append(advice)
        self.bulk_frame, self.bulk_cards = frame, cards
        return ScreenAdvice("bulk", None, tuple(cards))

    def badge(self, frame: np.ndarray, center: tuple[int, int]) -> int | None:
        """Return the level on a card's +N badge, 0 without one, None when unread.

        The badge can sit a pixel or two off the card's center: its characters are matched to the templates at a few
        heights, then read by OCR (a "8", missing from the templates, is read that way).
        """
        x, y = center
        card = crop_card(frame, center)
        if card is None:
            return None
        if pixels(card, BADGE_BOX, "badge") < BADGE_PIXELS:
            return 0
        for dy in BADGE_DY:
            moved = crop_card(frame, (x, y + dy))
            if moved is not None and (level := read_badge(moved, self.models)) is not None:
                return level
        for dy in BADGE_DY:
            region = (x + CROP_LEFT + BADGE_BOX[0], y + CROP_TOP + BADGE_BOX[1] + dy, BADGE_BOX[2], BADGE_BOX[3])
            level = badge_level(frame, self.ocr, region)
            if level:
                return level
        return None

    def bulk_card(self, frame: np.ndarray, center: tuple[int, int], where: str) -> Advice:
        level = self.badge(frame, center)
        if level is None:
            return unread(where, card_box(center), "its +N badge is unreadable", *kept_flags(frame, center, 0))
        result = read_bulk_slot(frame, center, self.models, level)
        locked = slot_locked(frame, center)
        if result.reading is None:
            return unread(where, card_box(center), result.why, locked)
        return advice_for(result.reading.piece(), self.policy, where, card_box(center), locked)

    def craft(self, frame: np.ndarray) -> ScreenAdvice:
        """A craft's result: one piece on its own panel, or up to ten small ones."""
        single = self._cached("single:" + digest(frame, SINGLE_REGION), lambda: self.single(frame))
        if single.read:
            return ScreenAdvice("craft", replace(single, focus=True))
        if self.models is None:
            return ScreenAdvice("craft")
        cards = []
        for index, corner in enumerate(result_icons(frame)):
            where = f"Crafted piece {index + 1}"
            area = frame[corner[1] : corner[1] + CROP_HEIGHT, corner[0] : corner[0] + 260]
            key = f"result:{corner}:{hashlib.md5(area.tobytes()).hexdigest()}"
            cards.append(replace(self._cached(key, lambda corner=corner, where=where: self.result_card(frame, corner, where)), where=where))
        return ScreenAdvice("craft", None, tuple(cards))

    def single(self, frame: np.ndarray) -> Advice:
        reading = read_single(frame, self.ocr)
        if reading is None:
            return unread("Crafted piece", None, "no single piece")
        return advice_for(reading.piece(), self.policy, "Crafted piece", None)

    def result_card(self, frame: np.ndarray, corner: tuple[int, int], where: str) -> Advice:
        result = read_result_card(frame, corner, self.ocr, self.models)
        box = (corner[0], corner[1], 260, CROP_HEIGHT)
        if result.reading is None:
            return unread(where, box, result.why or "unread")
        return advice_for(result.reading.piece(), self.policy, where, box)


def cell_of(where: str) -> tuple[int, int] | None:
    """Return a card's (row, column) in the window's grid, from its place on screen: four cards a row, as in the game."""
    found = re.match(r"Row (\d+), card (\d+)", where)
    if found:
        return int(found.group(1)) - 1, int(found.group(2)) - 1
    found = re.match(r"(?:Slot|Crafted piece) (\d+)", where)
    if found:
        return divmod(int(found.group(1)) - 1, GRID_WIDTH)
    return None


def thumbnail(frame: np.ndarray, box: tuple[int, int, int, int], screen: str) -> str:
    """Return the picture of a card's icon as a small PNG data URL. A craft result's icon sits at its box's corner."""
    x, y = box[0], box[1]
    left, top, size = (0, 0, RESULT_THUMB) if screen == "craft" else (THUMB_INSET, THUMB_INSET, CARD_THUMB)
    patch = frame[max(0, y + top) : y + top + size, max(0, x + left) : x + left + size]
    if patch.size == 0:
        return ""
    small = cv2.resize(patch, (THUMB_SIZE, THUMB_SIZE), interpolation=cv2.INTER_AREA)
    return "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", small)[1]).decode()


def with_pictures(frame: np.ndarray, advice: ScreenAdvice) -> ScreenAdvice:
    """Return the advice with each card's picture and place in the window's grid."""
    cards = tuple(
        replace(card, thumb=thumbnail(frame, card.box, advice.screen) if card.box is not None else "", cell=cell_of(card.where))
        for card in advice.cards
    )
    focus = advice.focus
    if focus is not None and focus.box is not None:
        focus = replace(focus, thumb=thumbnail(frame, focus.box, advice.screen))
    return replace(advice, focus=focus, cards=cards)


def digest(frame: np.ndarray, region: tuple[int, int, int, int]) -> str:
    """Return a fingerprint of a region's pixels."""
    x, y, width, height = region
    return hashlib.md5(np.ascontiguousarray(frame[y : y + height, x : x + width]).tobytes()).hexdigest()


def to_game_size(frame: np.ndarray) -> np.ndarray:
    """Return the frame at 1280x720, the size every reader expects. A game window of another size is scaled."""
    if (frame.shape[1], frame.shape[0]) == GAME_SIZE:
        return frame
    return cv2.resize(frame, GAME_SIZE, interpolation=cv2.INTER_AREA)


class AdviceBoard:
    """The latest advice, written by the advisor's thread and read by the window's."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._advice: ScreenAdvice | None = None
        self._note = ""

    def publish(self, advice: ScreenAdvice | None, note: str = "") -> None:
        with self._lock:
            self._advice, self._note = advice, note

    def latest(self) -> tuple[ScreenAdvice | None, str]:
        with self._lock:
            return self._advice, self._note


# The loop: look at the game a few times a second. It sends no input; like every mode, it keeps the game at 1280x720.

LOOK_SECONDS = 0.35
WAIT_GAME_SECONDS = 2.0
# Two frames this alike (mean difference of small copies) are the same still screen; more is the player moving.
STILL_DIFFERENCE = 2.0
STILL_SIZE = (160, 90)
# A change on at most this share of the small copy's pixels (each by more than this) leaves the cards in place:
# a tick changes about 5 %, a scroll or a new screen most of it.
LOCAL_PIXEL_DIFFERENCE = 25
LOCAL_SHARE = 0.2
NOTE_NO_GAME = "Waiting for the game window."
NOTE_MINIMIZED = "The game is minimized."
NOTE_SCALED = "The game window is {width}x{height}: the advisor reads it best at 1280x720."
TAG_WORDS = {"enhance": "ENHANCE", "speed": "SPEED", "keep": "KEEP", "lock": "LOCK", "sell": "SELL", "extract": "EXTRACT", "": "?"}


def tag_for(advice: Advice) -> Tag | None:
    """Return the tag drawn on a card, or None for a piece without a card on screen."""
    if advice.box is None:
        return None
    if not advice.read and (advice.locked or advice.equipped):
        return Tag(advice.box, "", "LOCKED" if advice.locked else "WORN")
    word = TAG_WORDS.get(advice.action, "?")
    if advice.read and advice.locked and advice.action in ("keep", "lock"):
        # Already locked and worth it: nothing to do, so no call to lock it.
        return Tag(advice.box, "", "KEPT")
    if advice.read and (advice.locked or advice.equipped) and advice.action in ("sell", "extract"):
        # The gear modes never touch it: the word only says what the rules think of it.
        return Tag(advice.box, "", f"could {word.lower()}")
    return Tag(advice.box, advice.action, word)


def small_copy(frame: np.ndarray) -> np.ndarray:
    """Return a tiny grey copy of a frame, to tell a still screen from a moving one."""
    return cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), STILL_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16)


def alike(first: np.ndarray, second: np.ndarray) -> bool:
    """Return whether two small copies show the same still screen."""
    return float(np.abs(first - second).mean()) <= STILL_DIFFERENCE


def local_change(first: np.ndarray, second: np.ndarray) -> bool:
    """Return whether two small copies differ only in a few places (a tick, a button, the detail panel), not a scroll
    or another screen: the cards are where they were."""
    return float((np.abs(first - second) > LOCAL_PIXEL_DIFFERENCE).mean()) <= LOCAL_SHARE


def grab(hwnd: int) -> np.ndarray | None:
    """Return the game's client area as a 1280x720 frame, read from its own window, or None if it cannot be."""
    try:
        width, height = client_size(hwnd)
        if width <= 0 or height <= 0:
            return None
        frame = print_window(hwnd, width, height)
    except (WindowError, OSError, ValueError, pywintypes.error, win32ui.error):
        # A window closing under the capture (win32ui's DeleteDC failing) is a frame missed, not a crash.
        return None
    if is_blank(frame):
        return None
    return to_game_size(frame)


def describe_piece(advice: Advice) -> str:
    """Return a log line for a judged piece."""
    subs = ", ".join(f"{name} {value:g}" for name, value in advice.substats.items())
    return f"{advice.grade} {advice.slot} +{advice.level} {advice.set_name or '?'} set, main {advice.main_stat}: {subs}"


def keep_size(game: GameWindow, logger: logging.Logger) -> None:
    """Force the game's client area to 1280x720 when it is not. A failure is only logged: frames are then scaled."""
    try:
        game.verify()
    except WindowError as error:
        logger.warning("advisor: could not resize the game: %s", error)


def run_advisor(config, logger: logging.Logger, stop_event: threading.Event, board: AdviceBoard, show_tags: bool = True) -> str:
    """Advise on the gear the player looks at until stopped. Returns why it stopped.

    It sends no input at all. Like the other modes it keeps the game's client area at 1280x720, the size the readers
    know: forced when the game is found and again whenever it drifts (a frame of another size is still read, scaled).
    """
    ocr = Ocr(config.vision)
    models = CardModels.load(logger=logger)
    if models is None:
        logger.warning("advisor: the card templates are missing (templates/cards/models.npz): only single pieces are read")
    advisor = Advisor(ocr, models, gear_policy(config.gear))
    logger.info("advisor: watching the game, read only")
    tags: GearTags | None = None
    hwnd = 0
    last: np.ndarray | None = None
    # The small copy of the frame the tags on screen were read from.
    drawn: np.ndarray | None = None
    told = ""
    try:
        while not stop_event.is_set():
            info = select_game_window(list_windows(), config.window)
            if info is None:
                board.publish(None, NOTE_NO_GAME)
                if tags is not None:
                    tags.show(())
                stop_event.wait(WAIT_GAME_SECONDS)
                continue
            if info.hwnd != hwnd:
                if tags is not None:
                    tags.close()
                hwnd, last = info.hwnd, None
                tags = GearTags(hwnd, logger) if show_tags else None
            if not win32gui.IsIconic(hwnd):
                keep_size(GameWindow(info, config.window, logger), logger)
            if win32gui.IsIconic(hwnd):
                board.publish(None, NOTE_MINIMIZED)
                if tags is not None:
                    tags.show(())
                stop_event.wait(WAIT_GAME_SECONDS)
                continue
            frame = grab(hwnd)
            if frame is None:
                stop_event.wait(LOOK_SECONDS)
                continue
            small = small_copy(frame)
            still = last is not None and alike(small, last)
            last = small
            if not still:
                # A tick, a button or the panel changing leaves the cards where they are: the tags stay until the next
                # reading. A scroll or another screen moves everything: the tags would land on the wrong cards.
                if tags is not None and (drawn is None or not local_change(drawn, small)):
                    tags.show(())
                    drawn = None
                stop_event.wait(LOOK_SECONDS / 2)
                continue
            advice = advisor.look(frame)
            try:
                width, height = client_size(hwnd)
            except WindowError:
                hwnd = 0
                continue
            note = "" if (width, height) == GAME_SIZE else NOTE_SCALED.format(width=width, height=height)
            board.publish(advice, note)
            after = grab(hwnd)
            moved = after is None or not local_change(small, small_copy(after))
            if tags is not None:
                tags.show(() if moved else tuple(tag for tag in map(tag_for, advice.cards) if tag is not None))
                drawn = None if moved else small
            focus = advice.focus
            if focus is not None and focus.read:
                line = f"{describe_piece(focus)}: {ACTION_LABELS[focus.action]} ({focus.rule})"
                if line != told:
                    logger.info("advisor: %s", line)
                    told = line
            stop_event.wait(LOOK_SECONDS)
    finally:
        if tags is not None:
            tags.close()
        board.publish(None, "")
    return "stopped by the user"

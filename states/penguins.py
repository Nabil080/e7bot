"""Penguins on a kept account: Sanctuary, Forest of Souls, Growth Altar, then buy the Penguin Nest 50 at a time.

It stops after a set number of purchases or once a set amount of stigma is spent, whichever the settings ask, or when
the stigma runs out. Every purchase is checked in its popup first: the Penguin Nest, 50 of 50, for 5,100 stigma.

Spending is counted three ways, so that a missed screen can never let it buy past the limit: each purchase counts when
it is confirmed; after a confirm nothing else is clicked until its result has shown and closed; and the stigma the
Growth Altar shows is compared with the stigma at the start, the stop following whichever says more was spent.
"""

from __future__ import annotations

import re
import time

from itertools import product

import cv2
import numpy as np
import pytesseract

from core.config import State
from core.state import Context
from states.story import click_once, seen

# The lobby's left column holds the Sanctuary's tower; the Sanctuary's menu lists the Forest of Souls on the right.
LOBBY_SHORTCUTS_REGION = (0, 120, 140, 120)
SANCTUARY_FOREST_REGION = (1060, 150, 210, 50)
SANCTUARY_FOREST_POINT = (1160, 175)
# The Forest of Souls: its title in the header, the Growth Altar button at the bottom right.
HEADER_REGION = (64, 12, 320, 46)
GROWTH_ALTAR_POINT = (1083, 648)
# The Growth Altar popup: its title, the stigma it shows, the Penguin Nest's Buy button.
ALTAR_TITLE_REGION = (75, 140, 220, 45)
ALTAR_STIGMA_REGION = (1098, 145, 110, 35)
PENGUIN_BUY_POINT = (200, 536)
# The purchase popup: the product's name, the count "1/50", the price on its Buy button.
BUY_NAME_REGION = (645, 298, 240, 32)
BUY_COUNT_REGION = (560, 412, 160, 48)
BUY_PRICE_REGION = (640, 575, 120, 30)
BUY_MAX_POINT = (908, 472)
BUY_CONFIRM_POINT = (835, 589)
BUY_CANCEL_POINT = (469, 589)
# "A Penguin has fully grown!" after a purchase, closed by a tap.
RESULT_REGION = (330, 140, 620, 60)
RESULT_CLOSE_POINT = (640, 530)
# The result's penguins: one round portrait per kind, side by side in this strip, its count under it (none for 1).
PORTRAIT_ROW = (380, 325, 520, 80)
PORTRAIT_MIN_WIDTH = 50
COUNT_BELOW = (-20, 393, 40, 23)
# Tesseract reads these small white counts one way or another: each is read a few ways, and the readings that add up
# to the 50 penguins of a purchase are kept. (page mode, enlargement)
COUNT_READS = ((7, 3), (8, 5), (13, 5))
# A kind is told by its portrait: the newbie wears bright green leaves; the experienced and the newbie stand on a dark
# green background, the epic on a red one. Measured on one result of each (shares 0.188 / 0.001 / 0.0 of bright
# green, 0.146 / 0.098 / 0.024 of dark green): to check on more results.
NEWBIE_GREEN = 0.08
EPIC_DARK_GREEN = 0.05
KINDS = ("newbie", "experienced", "epic")
# 50 penguins a purchase, 102 stigma each.
PER_PURCHASE = 50
PRICE = 102 * PER_PURCHASE
CLICK_WAIT_SECONDS = 2.0
TRANSITION_SECONDS = 6.0
# After a confirm, the result popup fades in over the Altar: no click until it has shown, or for this long at most.
RESULT_WAIT_SECONDS = 8.0


def penguin_data(ctx: Context) -> dict:
    """Return the run's purchases and stigma spent. The first call logs the plan, which the window shows."""
    if "penguins" not in ctx.route_data:
        limit = ctx.config.penguins
        unit = "purchases" if limit.kind == "purchases" else "stigma"
        ctx.logger.info("penguins: %d %s planned", limit.amount, unit)
    return ctx.route_data.setdefault("penguins", {"purchases": 0, "stigma": 0, "confirmed_at": None, "start_stigma": None, "seen_stigma": None})


def click_after_wait(ctx: Context, key: str, point: tuple[int, int]) -> State:
    """Click a point, at most once per CLICK_WAIT_SECONDS for the same key."""
    data = penguin_data(ctx)
    now = time.monotonic()
    if now - data.get(f"{key}_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data[f"{key}_at"] = now
    data["acted_at"] = now
    return click_once(ctx, *point) or State.ROUTE


def read_number(ctx: Context, region: tuple[int, int, int, int]) -> int | None:
    """Read a number such as "2,750,129", or None."""
    digits = re.sub(r"\D", "", ctx.ocr.read(ctx.frame, region, whitelist="0123456789,."))
    return int(digits) if digits else None


def done_message(data: dict, why: str) -> str:
    """Return the line the bot stops with, from the stigma really spent."""
    purchases = spent(data) // PRICE
    return f"Penguins: {why}, {purchases} purchases ({purchases * PER_PURCHASE} penguins) for {spent(data):,} stigma"


def spent(data: dict) -> int:
    """Return the stigma spent in this run: the purchases confirmed, or what the Altar's count lost if that is more."""
    shown = 0
    if data["start_stigma"] is not None and data["seen_stigma"] is not None:
        shown = max(0, data["start_stigma"] - data["seen_stigma"])
    return max(data["stigma"], shown)


def limit_reached(ctx: Context, data: dict) -> str:
    """Return why the set limit stops the run, or ''. A stigma budget stops before a purchase that would go over it."""
    limit = ctx.config.penguins
    if limit.kind == "purchases" and spent(data) // PRICE >= limit.amount:
        return "purchases done"
    if limit.kind == "stigma" and spent(data) + PRICE > limit.amount:
        return "stigma budget spent"
    return ""


def waiting_for_result(data: dict) -> bool:
    """Return whether a confirmed purchase's result has not shown yet: nothing may be clicked meanwhile."""
    return data["confirmed_at"] is not None and time.monotonic() - data["confirmed_at"] < RESULT_WAIT_SECONDS


def portrait_centers(frame: np.ndarray) -> list[int]:
    """Return the x of each penguin portrait's centre on the result, left to right."""
    x, y, width, height = PORTRAIT_ROW
    gray = cv2.cvtColor(frame[y : y + height, x : x + width], cv2.COLOR_BGR2GRAY)
    columns = np.flatnonzero((gray > 90).sum(axis=0) > 20)
    centers, start, previous = [], None, None
    for column in columns:
        if start is None or column - previous > 12:
            if start is not None and previous - start >= PORTRAIT_MIN_WIDTH:
                centers.append(x + (start + previous) // 2)
            start = column
        previous = column
    if start is not None and previous - start >= PORTRAIT_MIN_WIDTH:
        centers.append(x + (start + previous) // 2)
    return centers


def penguin_kind(frame: np.ndarray, center: int) -> str:
    """Return the kind of the portrait centred at x: newbie, experienced or epic."""
    hsv = cv2.cvtColor(frame[332:398, center - 33 : center + 33], cv2.COLOR_BGR2HSV).astype(int)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    green = (hue >= 35) & (hue <= 85)
    if (green & (saturation > 80) & (value > 80)).mean() >= NEWBIE_GREEN:
        return "newbie"
    if (green & (saturation > 60) & (value <= 80)).mean() < EPIC_DARK_GREEN:
        return "epic"
    return "experienced"


def count_readings(frame: np.ndarray, center: int) -> set[int]:
    """Return the counts the white number under a portrait may read as; {1} when there is none (a single penguin)."""
    dx, y, width, height = COUNT_BELOW
    area = frame[y : y + height, center + dx : center + dx + width]
    hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV)
    white = (hsv[:, :, 1] < 60) & (hsv[:, :, 2] > 190)
    if white.sum() < 20:
        return {1}
    image = np.full(white.shape, 255, np.uint8)
    image[white] = 0
    image = cv2.copyMakeBorder(image, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    readings = set()
    for mode, scale in COUNT_READS:
        big = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        text = pytesseract.image_to_string(big, config=f"--psm {mode} -c tessedit_char_whitelist=0123456789").strip()
        if text.isdigit() and 1 <= int(text) <= PER_PURCHASE:
            readings.add(int(text))
    return readings


def read_kinds(ctx: Context) -> dict[str, int] | None:
    """Return how many of each kind the result shows, or None when no readings add up to the 50 of a purchase."""
    centers = portrait_centers(ctx.frame)
    kinds = [penguin_kind(ctx.frame, center) for center in centers]
    if not centers or len(set(kinds)) != len(kinds):
        return None
    options = [sorted(count_readings(ctx.frame, center)) for center in centers]
    fits = [choice for choice in product(*options) if sum(choice) == PER_PURCHASE]
    if len(fits) != 1:
        return None
    counts = dict.fromkeys(KINDS, 0)
    counts.update(zip(kinds, fits[0]))
    return counts


def result(ctx: Context) -> State:
    """The purchase's result: count each kind of penguin once, then close it."""
    data = penguin_data(ctx)
    seen(ctx, "penguin result")
    if data["confirmed_at"] is not None:
        kinds = read_kinds(ctx)
        if kinds is None:
            ctx.logger.info("penguins: the kinds of this purchase could not be read")
        else:
            ctx.logger.info("penguins: got %d newbie, %d experienced, %d epic", kinds["newbie"], kinds["experienced"], kinds["epic"])
            if ctx.on_penguin_kinds is not None:
                ctx.on_penguin_kinds(kinds)
    data["confirmed_at"] = None
    return click_after_wait(ctx, "close", RESULT_CLOSE_POINT)


def buy_popup(ctx: Context) -> State:
    """The purchase popup: only the Penguin Nest, set to 50 of 50 for 5,100 stigma, is bought; anything else is cancelled."""
    data = penguin_data(ctx)
    seen(ctx, "penguin purchase popup")
    if "penguin" not in ctx.ocr.read(ctx.frame, BUY_NAME_REGION).casefold():
        ctx.logger.info("penguins: the purchase popup is not the Penguin Nest, cancelling")
        return click_after_wait(ctx, "cancel", BUY_CANCEL_POINT)
    count = re.sub(r"\s", "", ctx.ocr.read(ctx.frame, BUY_COUNT_REGION, whitelist="0123456789/"))
    if count != f"{PER_PURCHASE}/{PER_PURCHASE}":
        return click_after_wait(ctx, "max", BUY_MAX_POINT)
    price = read_number(ctx, BUY_PRICE_REGION)
    if price != PRICE:
        ctx.logger.info("penguins: the price reads %s, not %s: cancelling", price, f"{PRICE:,}")
        return click_after_wait(ctx, "cancel", BUY_CANCEL_POINT)
    why = limit_reached(ctx, data)
    if why:
        return ctx.halt(done_message(data, why))
    if waiting_for_result(data) or time.monotonic() - data.get("confirm_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    # Counted as it is confirmed: a purchase can never go uncounted.
    data["confirmed_at"] = time.monotonic()
    data["purchases"] += 1
    data["stigma"] += PRICE
    ctx.progressed = True
    ctx.logger.info("penguins: bought %d penguins for %s stigma, %d purchases in this run", PER_PURCHASE, f"{PRICE:,}", data["purchases"])
    if ctx.on_penguin_buy is not None:
        ctx.on_penguin_buy(PRICE)
    return click_after_wait(ctx, "confirm", BUY_CONFIRM_POINT)


def altar(ctx: Context) -> State:
    """The Growth Altar: stop at the limit or without the stigma for 50, else open the Penguin Nest's purchase."""
    data = penguin_data(ctx)
    seen(ctx, "growth altar")
    if waiting_for_result(data):
        # The result popup is fading in over the Altar: a click now would close it unseen.
        return State.ROUTE
    stigma = read_number(ctx, ALTAR_STIGMA_REGION)
    if stigma is not None:
        if data["start_stigma"] is None:
            data["start_stigma"] = stigma
        # The count only goes down in a run: a higher reading is the OCR's mistake, not a refund.
        elif stigma <= data["start_stigma"]:
            data["seen_stigma"] = stigma
    why = limit_reached(ctx, data)
    if why:
        return ctx.halt(done_message(data, why))
    if stigma is not None and stigma < PRICE:
        return ctx.halt(done_message(data, f"not enough stigma ({stigma:,})"))
    if stigma is None and data["start_stigma"] is None:
        # Without the count at the start, the spending could not be checked: never buy blind.
        return ctx.halt("Penguins: the Growth Altar's stigma cannot be read, nothing bought")
    return click_after_wait(ctx, "buy", PENGUIN_BUY_POINT)


def penguins(ctx: Context, unknown, at_lobby) -> State:
    """One tick of the penguin purchases. `unknown` handles stray screens, `at_lobby` tells the lobby."""
    data = penguin_data(ctx)
    if "fully grown" in ctx.ocr.read(ctx.frame, RESULT_REGION).casefold():
        return result(ctx)
    if "penguin nest" in ctx.ocr.read(ctx.frame, BUY_NAME_REGION).casefold() or "/50" in ctx.ocr.read(ctx.frame, BUY_COUNT_REGION):
        return buy_popup(ctx)
    if "growth altar" in ctx.ocr.read(ctx.frame, ALTAR_TITLE_REGION).casefold():
        return altar(ctx)
    if "forest" in ctx.ocr.read(ctx.frame, HEADER_REGION).casefold():
        seen(ctx, "forest of souls")
        return click_after_wait(ctx, "altar", GROWTH_ALTAR_POINT)
    if "forest of souls" in ctx.ocr.read(ctx.frame, SANCTUARY_FOREST_REGION).casefold():
        seen(ctx, "sanctuary")
        return click_after_wait(ctx, "forest", SANCTUARY_FOREST_POINT)
    if at_lobby(ctx):
        seen(ctx, "lobby")
        shortcut = ctx.find("lobby_sanctuary", LOBBY_SHORTCUTS_REGION)
        if shortcut is None:
            return ctx.halt("Penguins: the Sanctuary is not at the top of the lobby's left column")
        return click_after_wait(ctx, "sanctuary", shortcut.center)
    if time.monotonic() - data.get("acted_at", 0.0) < TRANSITION_SECONDS:
        # The next screen is on its way: an unknown screen is waited for, never escaped.
        ctx.route_data["unknown_since"] = time.monotonic()
    return unknown(ctx)

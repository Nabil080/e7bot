"""Gear modes on the player's account: Sort sells and extracts, Roll enhances by 16, Craft uses the Steel Workshop.

Everything is read from the small cards of the grid (core/cards.py), never from a piece's own screen. A mode is a
list of sweeps. A sweep opens Manage > Arrange (to sell or to extract) or Manage > Bulk Enhancement at the top of the
grid, reads every card on screen, ticks the ones its action wants, keeps a screenshot, scrolls to the next cards and
goes on; at the end it sells or extracts what it ticked, or opens the Bulk screen with up to 16 pieces to roll.
On the Bulk screen the pieces are enhanced a tier at a time (+3, +6... +15): after each one the cards are read
again, the pieces that failed are unselected and sold from the screen's own Arrange, the finished ones locked.
Nothing equipped (hidden by the filter) or locked is ever touched, and a card the bot cannot read is left alone.
"""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime

import cv2
import numpy as np

from core.cards import CardModels, CardResult, card_key, equipped, read_bulk_card, read_result_card, result_icons
from core.cards import read_card as read_small_card
from core.config import State
from core.enhance import GOLD_REGION, read_amount
from core.account import evaluate, level, role_values, save_result, stats
from core.gear import ALL_ROLES, LEVELS, judge
from core.inventory import (
    FIRST_ROW_Y,
    read_single,
    GRID_COLUMNS_X,
    ROW_PITCH,
    SCROLL_FROM,
    SCROLL_TO,
    has_card,
    inventory_count,
    scroll_shift,
)
from core.settings import HUNTS, gear_policy
from core.state import Context
from states.story import click_once, seen

# The account evaluation reads the +15 pieces, worn ones too, and changes nothing.
ACCOUNT_GOAL = "gear_account"
# The card reader's status of a piece the player keeps, as the evaluation names it.
KEPT_KINDS = {"locked": "locked", "equipped": "worn"}
GEAR_GOALS = ("gear_roll", "gear_sort", "gear_craft", ACCOUNT_GOAL)
TITLE_REGION = (60, 14, 300, 40)
# A popup's title: higher up when the popup is tall (an extraction of many sets lists one core per set).
POPUP_TITLE_REGIONS = ((380, 100, 520, 52), (380, 156, 520, 52))
# The Cancel button glows orange under it: its row tells where the popup's buttons are, the popup's size aside.
CANCEL_GLOW_BAND = (440, 420, 190, 270)
GLOW_TO_BUTTON = 27
BACK_POINT = (37, 33)
# The Inventory's left rail: its first tab lists the heroes with their gear, the second the equipment. The open tab is
# lit blue (blue mean measured 143 and 138, against 28 and 22 for the other one).
EQUIPMENT_TAB_POINT = (52, 190)
EQUIPMENT_TAB_REGION = (0, 160, 108, 60)
TAB_LIT_BLUE = 80
# The lobby's bag opens the Inventory; its top-right menu leads to the Steel Workshop.
BAG_POINT = (1066, 33)
MENU_POINT = (1240, 33)
MENU_WORKSHOP_REGION = (1150, 385, 110, 50)
MENU_WORKSHOP_POINT = (1202, 380)
# The Inventory: its sort and filter panel, the grid and the detail panel.
SORT_BUTTON_POINT = (912, 107)
SORT_LABEL_REGION = (880, 94, 100, 28)
SORT_PANEL_REGION = (284, 158, 160, 28)
HIDE_EQUIPPED_POINT = (308, 624)
HIDE_MAX_POINT = (308, 670)
# A ticked filter box shows a green check mark. The panel slides in: its boxes are read once it is still.
CHECK_GREEN_PIXELS = 20
PANEL_SETTLE_SECONDS = 1.2
# The grid is sorted by enhancement level, highest first ("Max Enhance"): the enhanced pieces come in blocks of one
# level, before every +0 piece.
MAX_ENHANCE_POINT = (784, 175)
CLOSE_PANEL_POINT = (1150, 400)
# Switching the grid's tab, to Weapon then back to All, brings the plain grid back to its top at once: a long
# inventory takes a hundred drags otherwise. It is only done when one drag shows the grid is not at its top.
WEAPON_TAB_POINT = (260, 118)
# The grid's cards slide in after a tab switch: the next look waits this long.
TAB_SECONDS = 2.5
TOP_DRAGS_BEFORE_TABS = 6
ALL_TAB_POINT = (182, 118)
# One tab per slot, right of All: the account evaluation reads them one by one.
SLOT_TAB_POINTS = {"weapon": (260, 118), "helmet": (340, 118), "armor": (418, 118), "necklace": (497, 118), "ring": (577, 118), "boots": (652, 118)}
# Manage opens Arrange (sell or extract several pieces) and Bulk Enhancement. Both turn the grid into a selection
# where each clicked card is ticked; Manage becomes Cancel.
MANAGE_POINT = (1188, 107)
MANAGE_ARRANGE_REGION = (1097, 150, 150, 40)
MANAGE_ARRANGE_POINT = (1172, 170)
MANAGE_BULK_REGION = (1097, 205, 150, 45)
MANAGE_BULK_POINT = (1170, 227)
SELECT_CANCEL_REGION = (1128, 90, 120, 36)
SELECT_CANCEL_POINT = (1188, 108)
# Arrange: Auto Select on the left, then Sell, Extract and the number of ticked cards.
ARRANGE_MARK_REGION = (140, 86, 140, 38)
ARRANGE_ACTION_POINTS = {"sell": (424, 104), "extract": (575, 104)}
ARRANGE_COUNT_REGION = (712, 90, 80, 28)
# Bulk Enhancement's selection: the button that opens the Bulk screen shows the count.
SELECT_COUNT_REGION = (1010, 655, 210, 38)
SELECT_COUNT = re.compile(r"\((\d+)")
SELECT_EMPTY_POINT = (938, 673)
SELECT_OPEN_POINT = (1112, 673)
# A ticked card shows a green check mark on its icon.
TICK_BOX = (-48, -42, 100, 80)
TICK_GREEN_PIXELS = 25
# The cards read on a screen: wholly in view, under the tabs and above the bottom edge.
READ_Y_RANGE = (195, 640)
# A sweep scrolls so that its next row comes up here.
NEXT_ROW_Y = 205
# Arrange takes this many ticked cards at once (128 were tried in the game, no limit met).
ARRANGE_MAX = 100
# A review ticks every piece it would sell or extract, as many as the game takes.
REVIEW_MAX = 1200
# The Bulk Enhancement screen: 16 slots, a button per target level, the gold cost, Enhance. It enhances to the
# exact level at once, without a popup.
BULK_SIZE = 16
BULK_SLOT_LEFT, BULK_SLOT_TOP, BULK_SLOT_WIDTH, BULK_SLOT_PITCH = 16, 128, 244, 129
# A slot's card is read as a grid card centered here, from the slot's top left corner.
BULK_CARD_CENTER = (43, 53)
BULK_TOGGLE = (120, 60)
BULK_LOCK = (214, 20)
BULK_TIER_POINTS = {3: (106, 672), 6: (304, 672), 9: (501, 672), 12: (698, 672), 15: (895, 672)}
BULK_COST_REGION = (1046, 648, 104, 32)
# The Bulk screen's own Arrange: Sell and Extract there act on the slots ticked, then the screen comes back.
BULK_ARRANGE_POINT = (922, 97)
BULK_ARRANGE_MARK_REGION = (14, 76, 150, 36)
BULK_ARRANGE_SELL_POINT = (300, 93)
BULK_ARRANGE_COUNT_REGION = (735, 78, 60, 30)
BULK_ARRANGE_CANCEL_POINT = (922, 97)
# A selected slot is framed in light blue, its top edge a bright line (about 200 pixels; 0 when not selected).
SELECTED_BLUE_PIXELS = 150
# A locked piece's lock button turns white.
LOCK_WHITE_PIXELS = 120
# The popup that refunds excess EXP as Enhancement Powder, closed by a tap.
TAP_TO_CLOSE_REGION = (520, 510, 240, 36)
TAP_TO_CLOSE_POINT = (640, 528)
# The Bulk screen's pieces are read on this many frames at most before an unread one is left alone.
BULK_READS = 6
# Clicks on the Bulk screen, sent in a row this far apart; the screen is looked at again this long after the last.
BULK_CLICK_SECONDS = 0.35
BULK_SETTLE_AFTER_CLICKS = 0.6
# The Bulk screen opens with an animation: the level is picked once it has been there this long.
BULK_SETTLE_SECONDS = 1.5
BULK_TIER_TRIES = 3
# A picked level's button turns white: 214 against 26 for the others. Clicking it again would unpick it.
TIER_LIT_MEAN = 120
TIER_BUTTON_HALF = (65, 11)
BULK_ENHANCE_POINT = (1208, 665)
# Picking a target level fills the ingredients at once: no cost after this means nothing to enhance with.
TIER_COST_SECONDS = 4.0
CONFIRM_POINT = (746, 528)
CANCEL_POINT = (532, 528)
# Under the title, a line the game adds when it took pieces out of an extraction by itself.
POPUP_NOTE_REGION = (300, 170, 680, 80)
# After a sale, the count of pieces can be hidden a while (another player's chat bubble): this many reads at most.
COUNT_READ_TRIES = 120
# Sell or Extract opens a popup to confirm: without it after this long, the button is clicked again.
ACTION_POPUP_SECONDS = 5.0
ACTION_RETRIES = 2
# A popup the game shows after a sale or an extraction, with a single button in its middle.
RESULT_CONFIRM_POINT = (640, 580)
# The Steel Workshop.
HUNT_TAB_POINTS = ((198, 110), (300, 110), (400, 110), (500, 110), (600, 110))
WORKSHOP_TITLE_REGION = (918, 86, 340, 42)
SLOT_POINTS = {
    "weapon": (508, 218), "helmet": (355, 308), "necklace": (662, 308),
    "armor": (355, 478), "ring": (662, 478), "boots": (508, 568),
}
COUNT_LABEL_REGION = (922, 480, 130, 30)
COUNT_POINTS = {1: (1188, 497), 10: (1240, 497)}
MATERIAL_REGION = (1030, 588, 140, 28)
CRAFT_COST_REGION = (985, 655, 105, 30)
CRAFT_POINT = (1165, 669)
CRAFT_CONFIRM_POINT = (816, 532)
# "Success": lower over ten pieces, higher over a single one.
CRAFT_RESULT_REGION = (540, 58, 200, 80)
CRAFT_RESULT_CONFIRM_POINT = (484, 654)
# The result's own Arrange: tick the crafted pieces, then Sell or Extract at the bottom. In it, Confirm moves and
# Arrange becomes Cancel; the pieces sold or extracted leave the screen.
RESULT_ARRANGE_POINT = (1100, 571)
RESULT_SELECTED_REGION = (520, 560, 240, 34)
RESULT_SELECTED = re.compile(r"(\d+)\s*selected")
RESULT_ACTION_POINTS = {"sell": (403, 663), "extract": (617, 663)}
RESULT_ARRANGE_CONFIRM_POINT = (811, 663)
# A crafted piece is ticked by its icon's center.
RESULT_ICON_CENTER = 42
RESULT_READS = 4
RESULT_ACTION_SECONDS = 10.0
# A craft's result shows this long after its confirmation at most.
CRAFT_RESULT_SECONDS = 20.0
# Ticks on the result screen: this far apart, and the whole Arrange tried again once when the count is wrong.
RESULT_TICK_SECONDS = 0.6
# A single crafted piece shows its own panel with Lock, Sell and Extract; a tap outside closes it, and after a sale
# or an extraction the game goes back to the workshop.
SINGLE_EXTRACT_REGION = (850, 598, 160, 36)
SINGLE_ACTION_POINTS = {"sell": (912, 559), "extract": (912, 616)}
SINGLE_CLOSE_POINT = (200, 640)
# One click per button in this time: the next screen can take a moment.
CLICK_WAIT_SECONDS = 1.6
# After a click, the next screen can take seconds to open: an unknown screen is waited for meanwhile.
TRANSITION_SECONDS = 6.0
# An enhancement plays an animation: the screen is used again once it stays still this long.
ENHANCE_MIN_SECONDS = 2.5
ENHANCE_STILL_SECONDS = 1.0
ENHANCE_MAX_SECONDS = 20.0
STILL_DIFFERENCE = 2.0
# Ticks show after a short animation.
TICK_SETTLE_SECONDS = 0.6
MAX_SCROLL_TRIES = 400
# After a drag the grid stands still at once, except at an end of the list where it bounces back a while: the bot
# looks again every SETTLE_STEP_SECONDS until two frames are alike.
AFTER_DRAG_SECONDS = 0.15
SETTLE_STEP_SECONDS = 0.1
SETTLE_MAX_SECONDS = 1.2
# Screens of cards read in one tick, between two checks of what the game shows.
SCREENS_PER_TICK = 5
# Where a drag of the grid starts and ends: 440 pixels, about three and a half rows.
GRID_DRAG_RANGE = (200, 640)
MIN_SCROLL_PIXELS = 10
# An empty slot ends the sweep only if it is still empty this long after.
EMPTY_CONFIRM_SECONDS = 1.5
# A drag that does not move the grid may come too early: the end of the inventory needs this many in a row.
END_DRAGS = 2
# The selection must look gone this long before the sweep starts over.
SELECTION_GONE_SECONDS = 2.5
# The pieces the game refused to extract, kept between runs.
UNEXTRACTABLE_FILE = "unextractable.json"
# Popups the bot did not ask for are cancelled; this many in a run stop it.
MAX_UNEXPECTED_POPUPS = 3


def gear_data(ctx: Context) -> dict:
    """Return the gear run's progress: the sweeps still to run (`plan`), the one running, and the run's counts."""
    return ctx.route_data.setdefault("gear", {
        "phase": "craft" if ctx.goal == "gear_craft" else "pieces",
        "plan": None, "sweep": None, "cycle": None,
        "changed": 0, "gold": 0, "crafted": 0, "judged": 0, "tally": {}, "done_keys": set(),
        "unextractable": set(), "unexpected": 0, "bulks": 0, "arranges": 0, "card_reads": 0, "unread": 0,
    })


def unextractable(ctx: Context) -> set:
    """Return the pieces the game refused to extract (some event gear), kept between runs in logs/gear."""
    data = gear_data(ctx)
    if "unextractable_loaded" not in data:
        data["unextractable_loaded"] = True
        path = ctx.config.logging.log_dir / "gear" / UNEXTRACTABLE_FILE
        try:
            data["unextractable"] |= {as_key(item) for item in json.loads(path.read_text(encoding="utf-8"))}
        except (OSError, ValueError, TypeError):
            pass
    return data["unextractable"]


def as_key(item) -> tuple:
    """Return a piece's key read back from JSON, where tuples became lists."""
    slot, main, subs, set_name, level = item
    return (slot, main, tuple((name, value) for name, value in subs), set_name, level)


def save_unextractable(ctx: Context) -> None:
    """Write the pieces the game refused to extract, for the next runs."""
    folder = ctx.config.logging.log_dir / "gear"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / UNEXTRACTABLE_FILE).write_text(json.dumps(sorted(unextractable(ctx), key=str)), encoding="utf-8")


def card_models(ctx: Context) -> CardModels | None:
    """Return the small-card templates, loaded once."""
    if "card_models" not in ctx.route_data:
        ctx.route_data["card_models"] = CardModels.load(logger=ctx.logger)
    return ctx.route_data["card_models"]


def act_once(ctx: Context, key: str, point: tuple[int, int], wait: float = CLICK_WAIT_SECONDS) -> State:
    """Click a point, at most once per `wait` seconds for the same key."""
    data = gear_data(ctx)
    now = time.monotonic()
    if now - data.get(f"{key}_at", 0.0) < wait:
        return State.ROUTE
    data[f"{key}_at"] = now
    data["acted_at"] = now
    click_once(ctx, *point)
    return State.ROUTE


def title(ctx: Context) -> str:
    """Return the screen's title, lowercased."""
    return ctx.ocr.read(ctx.frame, TITLE_REGION).casefold()


def mode(ctx: Context) -> str:
    """Return what is done to the pieces now: roll or sort. Crafted pieces are only sorted: they are +0."""
    return "roll" if ctx.goal == "gear_roll" else "sort"


def summary(ctx: Context, why: str) -> str:
    """Return the line the bot stops with: what it did in this run."""
    data = gear_data(ctx)
    tally = ", ".join(f"{count} {action}" for action, count in sorted(data["tally"].items())) or "nothing done"
    crafted = f"{data['crafted']} crafted, " if ctx.goal == "gear_craft" else ""
    batches = []
    if data["bulks"]:
        batches.append(f"{data['bulks']} bulk enhancements")
    if data["arranges"]:
        batches.append(f"{data['arranges']} sales or extractions")
    extra = f" ({', '.join(batches)})" if batches else ""
    if data["unread"]:
        extra += f"; {data['unread']} cards unread, left as they are"
    if data.get("unsold"):
        extra += f"; {data['unsold']} pieces to sell left unsold (Sell did not answer, see logs/failures)"
    if data.get("refused_pieces"):
        extra += f"; {data['refused_pieces']} fresh pieces the game would not extract, sold instead"
    dry = " (dry run)" if ctx.config.gear.dry_run else ""
    return f"Gear{dry}: {why}. {crafted}{data['judged']} decisions: {tally}{extra}; {data['gold']:,} gold spent"


def gold_left(ctx: Context) -> int | None:
    """Return the gold the run may still spend, or None without a limit."""
    limit = ctx.config.gear.max_gold_millions
    return None if not limit else max(0, int(limit * 1_000_000) - gear_data(ctx)["gold"])


def pieces_left(ctx: Context) -> int | None:
    """Return the pieces the run may still change, the ticked ones included, or None without a limit."""
    limit = ctx.config.gear.max_pieces
    if not limit:
        return None
    data = gear_data(ctx)
    sweep = data.get("sweep") or {}
    return max(0, limit - data["changed"] - len(sweep.get("ticked", [])))


def screens_folder(ctx: Context):
    """Return today's folder of gear screenshots, created."""
    folder = ctx.config.logging.log_dir / "gear" / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_screen(ctx: Context, name: str, frame: np.ndarray) -> str:
    """Save a screenshot when the setting asks; return its file name ("" when none)."""
    if not ctx.config.gear.screenshots:
        return ""
    image = screens_folder(ctx) / f"{datetime.now():%H%M%S}_{name}.jpg"
    cv2.imwrite(str(image), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return image.name


def record(ctx: Context, reading, action: str, reason: str, source: str) -> None:
    """Log a decision, count it, and add a line to decisions.csv when screenshots are on."""
    data = gear_data(ctx)
    data["judged"] += 1
    data["tally"][action] = data["tally"].get(action, 0) + 1
    dry = "would " if ctx.config.gear.dry_run else ""
    ctx.logger.info("gear: %s -> %s%s: %s [%s]", reading.describe(), dry, action, reason, source)
    ctx.progressed = True
    if not ctx.config.gear.dry_run:
        count(ctx, "judged")
    if not ctx.config.gear.screenshots:
        return
    log = screens_folder(ctx) / "decisions.csv"
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if new:
            writer.writerow(["time", "screen", "piece", "decision", "dry_run", "read_from", "reason"])
        writer.writerow([f"{datetime.now():%H:%M:%S}", data.get("screen_name", ""), reading.describe(), action, ctx.config.gear.dry_run, source, reason])


def count(ctx: Context, event: str, amount: int = 1) -> None:
    """Pass a gear event to the statistics."""
    if ctx.on_gear is not None:
        ctx.on_gear(event, amount)


def decide(ctx: Context, reading) -> tuple[str, str]:
    """Return what the rules do with a piece in this mode, and why."""
    decision = judge(reading.piece(), gear_policy(ctx.config.gear))
    action, reason = decision.action, decision.reason
    if action == "enhance" and mode(ctx) == "sort":
        return "keep", f"worth rolling ({reason}), left for the Roll mode"
    if action == "lock" and reading.level < 15:
        # Only a finished piece is locked.
        action = "keep"
    return action, reason


def finish(ctx: Context, why: str) -> State:
    """Stop the run with its summary."""
    data = gear_data(ctx)
    data["phase"] = "done"
    data["done_why"] = why
    ctx.logger.info("gear: %d cards read, %d left unread", data["card_reads"], data["unread"])
    return ctx.halt(summary(ctx, why))


# The plan: the sweeps a mode runs, in order.


def build_plan(ctx: Context) -> list[dict]:
    """Return the sweeps of the mode: extract then sell the pieces to get rid of, then roll by 16 (Roll only)."""
    gear = ctx.config.gear
    if gear.review and mode(ctx) == "sort":
        # One Arrange with every piece the bot would sell or extract ticked, left open for the player.
        return [{"kind": "arrange", "action": "review"}]
    plan = []
    if gear.new_bad_action == "sell" or (gear.sell_failed and not gear.only_new):
        plan.append({"kind": "arrange", "action": "sell"})
    if gear.new_bad_action == "extract":
        plan.append({"kind": "arrange", "action": "extract"})
    if mode(ctx) == "roll":
        plan.append({"kind": "bulk"})
    return plan


def next_sweep(ctx: Context) -> State:
    """Start the next sweep of the plan, or end the run when none is left."""
    data = gear_data(ctx)
    if data.get("stop_why"):
        return finish(ctx, data.pop("stop_why"))
    if not data["plan"]:
        return finish(ctx, "every sweep is done")
    spec = data["plan"].pop(0)
    data["sweep"] = dict(spec, stage="open", ticked=[], next=0, offset=0, screens=0, levels=set())
    what = "roll" if spec["kind"] == "bulk" else spec["action"]
    ctx.logger.info("gear: sweep to %s%s", what, f" ({len(spec['only'])} pieces)" if spec.get("only") else "")
    return State.ROUTE


# The Inventory.


def box_ticked(frame: np.ndarray, point: tuple[int, int]) -> bool:
    """Return whether a filter box shows its green check mark."""
    x, y = point
    box = frame[y - 10 : y + 10, x - 10 : x + 10].astype(int)
    green = (box[:, :, 1] > 150) & (box[:, :, 2] < 130) & (box[:, :, 0] < 130)
    return int(green.sum()) >= CHECK_GREEN_PIXELS


def card_ticked(frame: np.ndarray, center: tuple[int, int]) -> bool:
    """Return whether the card around `center` shows the green check of a ticked card."""
    x, y = center
    dx, dy, w, h = TICK_BOX
    box = frame[max(0, y + dy) : y + dy + h, max(0, x + dx) : x + dx + w].astype(int)
    green = (box[:, :, 1] > 160) & (box[:, :, 1] - box[:, :, 2] > 60) & (box[:, :, 1] - box[:, :, 0] > 60)
    return int(green.sum()) >= TICK_GREEN_PIXELS


def filters_ok(ctx: Context) -> State | None:
    """With the sort panel open: tick both filters (untick them for the account evaluation, which reads the worn and
    +15 pieces) and sort by enhancement level, highest first. None once done.

    A box is clicked only when the panel has been open a while and two readings in a row find it the wrong way: a
    click on a box already right would undo it.
    """
    wanted = ctx.goal != ACCOUNT_GOAL
    data = gear_data(ctx)
    now = time.monotonic()
    opened = data.setdefault("panel_seen_at", now)
    if now - opened < PANEL_SETTLE_SECONDS:
        return State.ROUTE
    for point in (HIDE_EQUIPPED_POINT, HIDE_MAX_POINT):
        key = f"wrong_{point[0]}_{point[1]}"
        if box_ticked(ctx.frame, point) == wanted:
            data.pop(key, None)
            continue
        seen_at = data.get(key)
        if seen_at is None or now - seen_at < 0.5:
            data.setdefault(key, now)
            return State.ROUTE
        data.pop(key, None)
        data["panel_seen_at"] = now
        ctx.logger.info("gear: %s the filter at %s", "ticking" if wanted else "unticking", point)
        return act_once(ctx, f"tick_{point[0]}_{point[1]}", point)
    data["filters"] = True
    data.pop("panel_seen_at", None)
    if "max enhance" not in ctx.ocr.read(ctx.frame, SORT_LABEL_REGION).casefold():
        # Picking the order closes the panel.
        return act_once(ctx, "max_enhance", MAX_ENHANCE_POINT)
    return act_once(ctx, "close_panel", CLOSE_PANEL_POINT)


def drag_grid(ctx: Context, distance: int) -> int:
    """Drag the grid: a positive distance shows the cards below. Return how far it moved, up being positive."""
    # A fresh frame: the tick's own can be from before an animation that ended since.
    before = ctx.session.capture.grab()
    x, (low, high) = SCROLL_FROM[0], GRID_DRAG_RANGE
    distance = max(-(high - low), min(high - low, distance))
    if distance >= 0:
        ctx.session.input.drag(x, high, x, high - distance, quick=True)
    else:
        ctx.session.input.drag(x, low, x, low - distance, quick=True)
    ctx.frame = settled_frame(ctx)
    ctx.progressed = True
    moved = scroll_shift(before, ctx.frame) if distance >= 0 else -scroll_shift(ctx.frame, before)
    ctx.logger.debug("gear: drag of %d moved the grid %d", distance, moved)
    return moved


def settled_frame(ctx: Context) -> np.ndarray:
    """Return a frame once the grid stopped moving: two frames in a row alike, or after SETTLE_MAX_SECONDS."""
    ctx.wait(AFTER_DRAG_SECONDS)
    frame = ctx.session.capture.grab()
    waited = AFTER_DRAG_SECONDS
    while waited < SETTLE_MAX_SECONDS:
        ctx.wait(SETTLE_STEP_SECONDS)
        waited += SETTLE_STEP_SECONDS
        latest = ctx.session.capture.grab()
        if scroll_shift(frame, latest) == 0 and scroll_shift(latest, frame) == 0:
            return latest
        frame = latest
    return frame


def plain_grid_at_top(ctx: Context) -> bool:
    """Bring the plain Inventory's grid to its top: drag it down until it no longer moves. True once there.

    A long way up (more than TOP_DRAGS_BEFORE_TABS drags) is cut short once by switching tabs, Weapon then All,
    which shows the grid from its top.
    """
    data = gear_data(ctx)
    step = data.get("top_step", 0)
    if step == 1:
        if time.monotonic() - data.get("tab_weapon_at", 0.0) < TAB_SECONDS:
            return False
        data["top_step"] = 2
        act_once(ctx, "tab_all", ALL_TAB_POINT, wait=TAB_SECONDS)
        return False
    if step == 2:
        if time.monotonic() - data.get("tab_all_at", 0.0) < TAB_SECONDS:
            return False
        data["top_step"] = 0
    if drag_grid(ctx, -10_000) > -MIN_SCROLL_PIXELS:
        data.pop("top_drags", None)
        return True
    data["top_drags"] = data.get("top_drags", 0) + 1
    if data["top_drags"] == TOP_DRAGS_BEFORE_TABS:
        data["top_step"] = 1
        act_once(ctx, "tab_weapon", WEAPON_TAB_POINT, wait=TAB_SECONDS)
    elif data["top_drags"] > MAX_SCROLL_TRIES:
        data.pop("top_drags", None)
        return True
    return False


def overlay(ctx: Context) -> str:
    """Return which selection the Inventory is in: "arrange", "bulk", or "" for the plain grid."""
    if "cancel" not in ctx.ocr.read(ctx.frame, SELECT_CANCEL_REGION).casefold():
        return ""
    return "arrange" if "auto select" in ctx.ocr.read(ctx.frame, ARRANGE_MARK_REGION).casefold() else "bulk"


def selection_count(ctx: Context, kind: str) -> int | None:
    """Return how many cards the selection shows ticked, or None if it cannot be read."""
    if kind == "arrange":
        text = ctx.ocr.read(ctx.frame, ARRANGE_COUNT_REGION, whitelist="0123456789[]")
        digits = re.search(r"(\d+)", text)
        return int(digits.group(1)) if digits else None
    found = SELECT_COUNT.search(ctx.ocr.read(ctx.frame, SELECT_COUNT_REGION))
    return int(found.group(1)) if found else 0


def inventory(ctx: Context) -> State:
    """The Inventory: set the filters once, then run the sweeps."""
    data = gear_data(ctx)
    seen(ctx, "gear inventory")
    sweep = data.get("sweep")
    shown = overlay(ctx)
    if ctx.ocr.contains(ctx.frame, "display format", SORT_PANEL_REGION):
        return filters_ok(ctx) or State.ROUTE
    if sweep is None or sweep["stage"] == "open":
        if shown:
            # A selection left over, or the end of one: leave it, ticks and all.
            if shown == "bulk" and selection_count(ctx, "bulk"):
                return act_once(ctx, "select_empty", SELECT_EMPTY_POINT)
            return act_once(ctx, "select_cancel", SELECT_CANCEL_POINT)
        if ctx.ocr.contains(ctx.frame, "bulk enhancement", MANAGE_BULK_REGION) and sweep is None:
            # The Manage menu is open for nothing: close it.
            return act_once(ctx, "manage_close", MANAGE_POINT)
    if not data.get("filters"):
        account_step(ctx, "setting the inventory filters: worn pieces shown, highest level first")
        return act_once(ctx, "sort_panel", SORT_BUTTON_POINT)
    if data["phase"] == "done":
        return ctx.halt(data.get("done_line") or summary(ctx, data.get("done_why", "done")))
    if ctx.goal == ACCOUNT_GOAL:
        return account_scan(ctx)
    if data["plan"] is None:
        data["plan"] = build_plan(ctx)
    if sweep is None:
        return next_sweep(ctx)
    if card_models(ctx) is None:
        return finish(ctx, "the card templates are missing (templates/cards/models.npz)")
    return run_sweep(ctx, sweep, shown)


def run_sweep(ctx: Context, sweep: dict, shown: str) -> State:
    """One step of a sweep: open its selection at the top, read and tick screen by screen, then act."""
    data = gear_data(ctx)
    kind = sweep["kind"]
    if sweep["stage"] == "open":
        if not sweep.get("grid_at_top"):
            if plain_grid_at_top(ctx):
                sweep["grid_at_top"] = True
            return State.ROUTE
        region, point = (MANAGE_ARRANGE_REGION, MANAGE_ARRANGE_POINT) if kind == "arrange" else (MANAGE_BULK_REGION, MANAGE_BULK_POINT)
        if ctx.ocr.contains(ctx.frame, "arrange" if kind == "arrange" else "bulk enhancement", region):
            sweep["stage"] = "opening"
            return act_once(ctx, f"manage_{kind}", point, wait=2.5)
        return act_once(ctx, "manage", MANAGE_POINT, wait=2.5)
    if sweep["stage"] == "acted":
        return after_action(ctx, sweep, shown)
    if sweep["stage"] == "verify":
        return check_removed(ctx, sweep)
    if sweep["stage"] == "leave":
        if shown:
            if shown == "bulk" and selection_count(ctx, "bulk"):
                return act_once(ctx, "select_empty", SELECT_EMPTY_POINT)
            return act_once(ctx, "select_cancel", SELECT_CANCEL_POINT)
        return end_sweep(ctx)
    if shown != kind:
        if sweep["stage"] == "opening" and time.monotonic() - data.get(f"manage_{kind}_at", 0.0) < 4.0:
            return State.ROUTE
        # One frame can hide the selection's Cancel button (a chat bubble, a transition): it must stay gone a while.
        missing = sweep.setdefault("missing_since", time.monotonic())
        if time.monotonic() - missing < SELECTION_GONE_SECONDS:
            return State.ROUTE
        # Thrown out of the selection: start the sweep over.
        ctx.logger.warning("gear: out of the %s selection, starting the sweep again", kind)
        restart_sweep(sweep)
        return State.ROUTE
    sweep.pop("missing_since", None)
    if sweep["stage"] == "opening":
        if selection_count(ctx, kind):
            # Ticks left over: leave the selection and come back clean.
            sweep["stage"] = "open"
            sweep["grid_at_top"] = False
            return act_once(ctx, "select_cancel", SELECT_CANCEL_POINT)
        sweep["stage"] = "read"
        return State.ROUTE
    if sweep["stage"] == "read":
        # Several screens in a row: the screen checks of each tick (popups, title, selection) cost more than a
        # screen of cards. The selection is checked again every SCREENS_PER_TICK screens.
        for _ in range(SCREENS_PER_TICK):
            sweep.pop("scrolled", None)
            read_screen(ctx, sweep)
            if sweep["stage"] != "read" or not sweep.get("scrolled"):
                break
        return State.ROUTE
    if sweep["stage"] == "act":
        return act_on_sweep(ctx, sweep)
    if sweep["stage"] == "bulk":
        cycle = data.get("cycle")
        if cycle is not None and cycle.get("seen_at") is not None:
            # Back from the Bulk screen: the game leaves it on its own once all its pieces are sold.
            data["cycle"] = None
            sweep["stage"] = "leave"
            return State.ROUTE
        # The Bulk screen did not open yet.
        return act_once(ctx, "select_open", SELECT_OPEN_POINT, wait=4.0)
    return State.ROUTE


def restart_sweep(sweep: dict) -> None:
    """Start a sweep over from the top, its ticks forgotten (the game drops them when it leaves the selection)."""
    sweep.update(stage="open", ticked=[], next=0, offset=0, grid_at_top=False, levels=set(), level=None)
    sweep.pop("empty_since", None)
    sweep.pop("still_drags", None)
    sweep.pop("missing_since", None)


def wants(ctx: Context, sweep: dict, reading, action: str) -> bool:
    """Return whether the sweep ticks a piece it judged."""
    if sweep["kind"] == "arrange":
        if action == "extract" and card_key(reading) in unextractable(ctx):
            # Known not to extract: sold by a sale of its own once this sweep is over.
            if sweep["action"] == "extract":
                sweep.setdefault("sell_after", set()).add(card_key(reading))
            return False
        if sweep["action"] == "review":
            return action in ("sell", "extract")
        if action != sweep["action"]:
            return False
        only = sweep.get("only")
        return only is None or card_key(reading) in only
    if action != "enhance" or card_key(reading) in gear_data(ctx)["done_keys"]:
        return False
    # Any level: each step of a batch raises only the pieces under its target level.
    return reading.level < ctx.config.gear.max_level


def read_screen(ctx: Context, sweep: dict) -> State:
    """Read every card on screen not read yet, tick the wanted ones, keep a screenshot, then scroll on."""
    data = gear_data(ctx)
    models = card_models(ctx)
    review = sweep["kind"] == "arrange" and sweep["action"] == "review"
    full = BULK_SIZE if sweep["kind"] == "bulk" else REVIEW_MAX if review else ARRANGE_MAX
    clicked: list[tuple[int, int]] = []
    while True:
        index = sweep["next"]
        if len(sweep["ticked"]) >= full:
            return end_of_cards(ctx, sweep, "full", clicked)
        if sweep.get("only") is not None and len(sweep["ticked"]) >= len(sweep["only"]):
            # Every piece this sweep looks for is ticked.
            return end_of_cards(ctx, sweep, "all found", clicked)
        x = GRID_COLUMNS_X[index % 4]
        y = FIRST_ROW_Y + (index // 4) * ROW_PITCH - sweep["offset"]
        if y > READ_Y_RANGE[1] or y < READ_Y_RANGE[0]:
            break
        if not has_card(ctx.frame, x, y):
            if clicked:
                break
            # One frame can catch the screen between two pictures: the slot must still be empty a moment later.
            first = sweep.setdefault("empty_since", time.monotonic())
            if time.monotonic() - first < EMPTY_CONFIRM_SECONDS:
                return State.ROUTE
            return end_of_cards(ctx, sweep, "the end of the inventory is reached")
        sweep.pop("empty_since", None)
        result = read_small_card(ctx.frame, (x, y), ctx.ocr, models)
        if result.status != "unsure" and result.shift:
            # The card sat lower or higher than counted: the count is corrected for the next ones.
            sweep["offset"] -= result.shift
            y += result.shift
        sweep["next"] += 1
        if past_its_cards(ctx, sweep, result):
            return end_of_cards(ctx, sweep, "its pieces are all read", clicked)
        if not judge_card(ctx, sweep, result):
            continue
        if ctx.config.gear.dry_run and not review:
            continue
        if pieces_left(ctx) == 0 and not review:
            data["stop_why"] = f"{ctx.config.gear.max_pieces} pieces changed, the limit"
            return end_of_cards(ctx, sweep, "the limit", clicked)
        ctx.session.input.click(x, y)
        clicked.append((x, y))
        sweep["ticked"].append({"index": index, "key": card_key(result.reading), "level": result.reading.level, "point": (x, y), "reading": result.reading})
    if clicked:
        ctx.wait(TICK_SETTLE_SECONDS)
        ctx.frame = ctx.session.capture.grab()
        if not ticks_shown(ctx, sweep, clicked):
            return State.ROUTE
    sweep["screens"] += 1
    data["screen_name"] = save_screen(ctx, f"{sweep_name(sweep)}_{sweep['screens']:02d}", ctx.frame)
    return scroll_on(ctx, sweep)


def past_its_cards(ctx: Context, sweep: dict, result: CardResult) -> bool:
    """Return whether the grid, sorted by level, is past the cards this sweep is for.

    A sale of the failed enhanced pieces ends at the first +0 piece (unless fresh pieces are sold too); a roll ends
    where its level's block ends.
    """
    if result.reading is None:
        return False
    level = result.reading.level
    if sweep["kind"] == "arrange":
        sells_new = ctx.config.gear.new_bad_action == "sell"
        return sweep["action"] == "sell" and not sells_new and sweep.get("only") is None and level == 0
    return False


def sweep_name(sweep: dict) -> str:
    """Return a short name for the sweep's screenshots."""
    return "roll" if sweep["kind"] == "bulk" else sweep["action"]


def judge_card(ctx: Context, sweep: dict, result: CardResult) -> bool:
    """Judge a card the sweep read; return whether the sweep ticks it."""
    data = gear_data(ctx)
    if result.status in ("locked", "equipped"):
        return False
    if result.status == "unsure" or result.reading is None:
        data["unread"] += 1
        ctx.logger.info("gear: card %d unread (%s), left as it is", sweep["next"], result.why)
        return False
    reading = result.reading
    if reading.level > 0 and ctx.config.gear.only_new:
        return False
    data["card_reads"] += 1
    action, reason = decide(ctx, reading)
    tick = wants(ctx, sweep, reading, action)
    # Each piece's decision is logged once in the run: by the first sweep, or by the one that acts on it.
    key = card_key(reading)
    logged = data.setdefault("logged_keys", set())
    if tick or key not in logged:
        logged.add(key)
        if action == "enhance":
            reason = f"to +{next_tier(ctx, reading.level)}: {reason}"
        record(ctx, reading, action, reason, "card")
    return tick


def next_tier(ctx: Context, level: int) -> int:
    return min(next((tier for tier in LEVELS if tier > level), 15), ctx.config.gear.max_level)


def ticks_shown(ctx: Context, sweep: dict, clicked: list[tuple[int, int]]) -> bool:
    """Check that every card clicked on this screen shows its tick; a missing one stops the sweep's action."""
    missing = [point for point in clicked if not card_ticked(ctx.frame, point)]
    if missing:
        # A tick can show a moment late: look again, then click once more a card still without its tick.
        ctx.wait(TICK_SETTLE_SECONDS)
        ctx.frame = ctx.session.capture.grab()
        missing = [point for point in missing if not card_ticked(ctx.frame, point)]
        for point in missing:
            ctx.session.input.click(*point)
            ctx.wait(0.3)
        if missing:
            ctx.wait(TICK_SETTLE_SECONDS)
            ctx.frame = ctx.session.capture.grab()
            missing = [point for point in missing if not card_ticked(ctx.frame, point)]
    if not missing:
        return True
    if sweep["kind"] == "arrange" and sweep["action"] == "review":
        # The game takes no more ticks: the review shows what it took.
        shown = len(sweep["ticked"]) - len(missing)
        finish(ctx, f"review: {shown} pieces ticked in Arrange (the game took no more), nothing done")
        return False
    ctx.logger.warning("gear: %d cards clicked but not shown ticked: the sweep is started again", len(missing))
    sweep["stage"] = "leave"
    sweep["ticked"] = []
    sweep["dropped"] = True
    return False


def scroll_on(ctx: Context, sweep: dict) -> State:
    """Bring the next unread row up; two drags that move nothing mean the end of the inventory."""
    row_y = FIRST_ROW_Y + (sweep["next"] // 4) * ROW_PITCH - sweep["offset"]
    moved = drag_grid(ctx, row_y - NEXT_ROW_Y)
    if abs(moved) < MIN_SCROLL_PIXELS:
        sweep["still_drags"] = sweep.get("still_drags", 0) + 1
        if sweep["still_drags"] >= END_DRAGS:
            return end_of_cards(ctx, sweep, "the end of the inventory is reached")
        return State.ROUTE
    sweep["still_drags"] = 0
    sweep["offset"] += moved
    sweep["scrolled"] = True
    return State.ROUTE


def end_of_cards(ctx: Context, sweep: dict, why: str, clicked: list[tuple[int, int]] | None = None) -> State:
    """No more cards for this sweep: act on the ticked ones, or leave."""
    if clicked:
        ctx.wait(TICK_SETTLE_SECONDS)
        ctx.frame = ctx.session.capture.grab()
        if not ticks_shown(ctx, sweep, clicked):
            return State.ROUTE
        sweep["screens"] += 1
        gear_data(ctx)["screen_name"] = save_screen(ctx, f"{sweep_name(sweep)}_{sweep['screens']:02d}", ctx.frame)
    sweep["why"] = why
    if why in ("full",) and sweep["kind"] == "arrange":
        # The same sweep goes on after this batch, from the top: the cards acted on are gone by then.
        gear_data(ctx)["plan"].insert(0, {key: sweep[key] for key in ("kind", "action", "only") if key in sweep})
    if not sweep["ticked"]:
        sweep["stage"] = "leave"
        return State.ROUTE
    if sweep["kind"] == "arrange" and sweep["action"] == "review":
        # Nothing is sold or extracted: the selection stays on screen for the player to look at.
        return finish(ctx, f"review: {len(sweep['ticked'])} pieces ticked in Arrange (would be sold or extracted, see the log), nothing done")
    sweep["stage"] = "act"
    return State.ROUTE


def act_on_sweep(ctx: Context, sweep: dict) -> State:
    """Every card is read: check the count shown, then sell, extract, or open the Bulk screen."""
    data = gear_data(ctx)
    ticked = len(sweep["ticked"])
    shown = selection_count(ctx, sweep["kind"])
    if shown != ticked:
        sweep["count_misses"] = sweep.get("count_misses", 0) + 1
        if sweep["count_misses"] < 4:
            return State.ROUTE
        ctx.logger.warning("gear: %s cards shown ticked instead of %d: nothing done, the sweep is started again", shown, ticked)
        sweep["stage"] = "leave"
        sweep["ticked"] = []
        sweep["dropped"] = True
        return State.ROUTE
    if sweep["kind"] == "bulk":
        data["cycle"] = {
            "slots": [dict(entry, slot=i) for i, entry in enumerate(sorted(sweep["ticked"], key=lambda e: e["index"]))],
            "stage": "enhance", "sold": 0,
        }
        sweep["stage"] = "bulk"
        return act_once(ctx, "select_open", SELECT_OPEN_POINT, wait=2.5)
    counted = inventory_count(ctx.frame, ctx.ocr)
    if counted is None:
        sweep["count_tries"] = sweep.get("count_tries", 0) + 1
        if sweep["count_tries"] > COUNT_READ_TRIES:
            ctx.logger.warning("gear: the number of pieces cannot be read before the %s: nothing done", sweep["action"])
            sweep["stage"] = "leave"
            sweep["ticked"] = []
        return State.ROUTE
    # The count of pieces before the sale or extraction tells, after it, whether it went through.
    sweep["count_before"] = counted[0]
    sweep["pending"] = sweep["action"]
    sweep["stage"] = "acted"
    sweep["confirmed"] = time.monotonic()
    return act_once(ctx, f"arrange_{sweep['action']}", ARRANGE_ACTION_POINTS[sweep["action"]], wait=3.0)


def after_action(ctx: Context, sweep: dict, shown: str) -> State:
    """After Sell or Extract: the game leaves Arrange on its own once it is done; a result popup may come first."""
    if shown:
        waited = time.monotonic() - sweep.get("confirmed", 0.0)
        if sweep.get("pending") and waited > ACTION_POPUP_SECONDS:
            # The button was clicked but no popup came: click it again, a few times at most.
            tries = sweep.get("action_tries", 1)
            if tries <= ACTION_RETRIES:
                sweep["action_tries"] = tries + 1
                sweep["confirmed"] = time.monotonic()
                ctx.logger.info("gear: no popup after %s, clicking it again", sweep["action"])
                return act_once(ctx, f"arrange_{sweep['action']}", ARRANGE_ACTION_POINTS[sweep["action"]], wait=3.0)
            ctx.logger.warning("gear: %s never opened its popup: nothing done, the pieces stay", sweep["action"])
            sweep["stage"] = "leave"
            sweep["ticked"] = []
        elif waited > 8.0:
            sweep["stage"] = "leave"
        return State.ROUTE
    return check_removed(ctx, sweep)


def check_removed(ctx: Context, sweep: dict) -> State:
    """Out of Arrange: the count of pieces says whether the sale or extraction went through."""
    data = gear_data(ctx)
    counted = inventory_count(ctx.frame, ctx.ocr)
    if counted is None:
        sweep["stage"] = "verify"
        sweep["verify_tries"] = sweep.get("verify_tries", 0) + 1
        if sweep["verify_tries"] > COUNT_READ_TRIES:
            return finish(ctx, "the number of pieces cannot be read after a sale or extraction")
        return State.ROUTE
    ticked = len(sweep["ticked"])
    before = sweep.get("count_before")
    if before is not None and counted[0] == before - ticked:
        done = "sold" if sweep["action"] == "sell" else "extracted"
        data["arranges"] += 1
        data["changed"] += ticked
        count(ctx, done, ticked)
        ctx.logger.info("gear: %s %d pieces", done, ticked)
    elif before is not None and counted[0] == before:
        ctx.logger.warning("gear: the %s did not go through", "sale" if sweep["action"] == "sell" else "extraction")
    else:
        # Not what the bot asked for: it stops rather than count the cards wrong.
        return finish(ctx, f"{before} pieces before the {sweep['action']}, {counted[0]} after, {ticked} ticked: stopped to be safe")
    return end_sweep(ctx)


def end_sweep(ctx: Context) -> State:
    """The sweep is over: queue what follows from it, then go on with the plan."""
    data = gear_data(ctx)
    sweep = data.pop("sweep")
    data["sweep"] = None
    if sweep.get("refused") or sweep.get("dropped"):
        # Nothing was done: the pieces limit was not reached by this sweep.
        if "limit" in data.get("stop_why", ""):
            data.pop("stop_why")
    if sweep.get("sell_after"):
        data["plan"].insert(0, {"kind": "arrange", "action": "sell", "only": set(sweep["sell_after"])})
    if sweep.get("refused"):
        data["plan"][:0] = split_refused(ctx, sweep)
    elif sweep.get("dropped"):
        retries = data.setdefault("dropped_sweeps", 0)
        if retries < 2:
            data["dropped_sweeps"] = retries + 1
            data["plan"].insert(0, {key: sweep[key] for key in ("kind", "action", "only") if key in sweep})
        else:
            ctx.logger.warning("gear: the %s sweep failed three times, given up", sweep_name(sweep))
    elif sweep["kind"] == "bulk" and not ctx.config.gear.dry_run and sweep["ticked"]:
        # Pieces worth rolling may be left, at this level or another: another sweep looks for them.
        data["plan"].insert(0, {"kind": "bulk"})
    return next_sweep(ctx)


def split_refused(ctx: Context, sweep: dict) -> list[dict]:
    """Return a refused extraction cut in two sweeps; a single piece refused is left as it is.

    The game does not say which pieces it refused: the halves find them. Fresh pieces are never sold instead.
    """
    keys = [entry["key"] for entry in sweep["ticked"]]
    if len(keys) == 1:
        unextractable(ctx).add(keys[0])
        save_unextractable(ctx)
        gear_data(ctx)["refused_pieces"] = gear_data(ctx).get("refused_pieces", 0) + 1
        # It cannot be extracted: it is sold.
        ctx.logger.warning("gear: a piece cannot be extracted, it is sold instead: %s", keys[0])
        return [{"kind": "arrange", "action": "sell", "only": {keys[0]}}]
    half = len(keys) // 2
    return [{"kind": "arrange", "action": "extract", "only": set(part)} for part in (keys[:half], keys[half:])]


# The Bulk Enhancement screen: the cycle enhances its pieces tier by tier, judging them after each one.


def slot_left_top(slot: int) -> tuple[int, int]:
    """Return the top left corner of a Bulk screen slot."""
    return BULK_SLOT_LEFT + (slot % 4) * BULK_SLOT_WIDTH, BULK_SLOT_TOP + (slot // 4) * BULK_SLOT_PITCH


def slot_point(slot: int, offset: tuple[int, int]) -> tuple[int, int]:
    """Return a point of a Bulk screen slot, from its top left corner."""
    left, top = slot_left_top(slot)
    return left + offset[0], top + offset[1]


def slot_selected(frame: np.ndarray, slot: int) -> bool:
    """Return whether a Bulk screen slot is framed in light blue, as selected."""
    left, top = slot_left_top(slot)
    edge = frame[top - 2 : top + 3, left + 20 : left + 220].astype(int)
    bright = (edge[:, :, 0] > 200) & (edge[:, :, 1] > 200) & (edge[:, :, 2] > 170)
    return int(bright.sum()) >= SELECTED_BLUE_PIXELS


def slot_locked(frame: np.ndarray, slot: int) -> bool:
    """Return whether a Bulk screen slot's lock button is white, as locked."""
    x, y = slot_point(slot, BULK_LOCK)
    button = frame[y - 12 : y + 12, x - 18 : x + 18].astype(int)
    return int((button.min(axis=2) > 200).sum()) >= LOCK_WHITE_PIXELS


def bulk_screen(ctx: Context) -> State:
    """The Bulk screen: enhance the cycle's pieces a tier, judge them, sell the failed ones, lock the finished ones."""
    data = gear_data(ctx)
    seen(ctx, "gear bulk")
    cycle = data.get("cycle")
    if cycle is None:
        return act_once(ctx, "back", BACK_POINT, wait=2.5)
    if "tap to close" in ctx.ocr.read(ctx.frame, TAP_TO_CLOSE_REGION).casefold():
        # Enhancing past what a level needs refunds the excess as Enhancement Powder, in a popup over the cards.
        return act_once(ctx, "close_reward", TAP_TO_CLOSE_POINT, wait=2.0)
    if "auto select" in ctx.ocr.read(ctx.frame, BULK_ARRANGE_MARK_REGION).casefold():
        return bulk_arrange(ctx, cycle)
    stage = cycle["stage"]
    if stage == "enhance":
        return bulk_enhance(ctx, cycle)
    if stage == "enhancing":
        if screen_still(ctx, "bulk_since"):
            # The pieces under the target reached it; the others did not move.
            for entry in cycle["slots"]:
                entry["raised"] = entry.get("state", "roll") == "roll" and entry["level"] < cycle["target"]
                if entry["raised"]:
                    entry["level"] = cycle["target"]
            cycle["stage"] = "judge"
        return State.ROUTE
    if stage == "judge":
        return bulk_judge(ctx, cycle)
    if stage == "apply":
        return bulk_apply(ctx, cycle)
    if stage == "sold":
        return bulk_after_sale(ctx, cycle)
    if stage == "arrange":
        # Arrange was clicked and its view is not shown yet: click it again after a while.
        return act_once(ctx, "bulk_arrange", BULK_ARRANGE_POINT, wait=4.0)
    if stage == "selling":
        # Arrange closed without the sale: check every slot again before anything else. A reopened Arrange ticks the
        # selected pieces on its own, so its ticks are always set again from the slots' states.
        cycle["stage"] = "apply"
        return State.ROUTE
    return leave_cycle(ctx, cycle)


def bulk_enhance(ctx: Context, cycle: dict) -> State:
    """Pick the next level for the selected pieces, check the cost against the limits, and enhance."""
    data = gear_data(ctx)
    arrived = cycle.setdefault("seen_at", time.monotonic())
    if time.monotonic() - arrived < BULK_SETTLE_SECONDS:
        return State.ROUTE
    if not cycle.get("worn_checked"):
        # Before the first enhancement: a piece a hero wears is unselected (selections list worn pieces too).
        cycle["worn_checked"] = True
        for entry in cycle["slots"]:
            if equipped(ctx.frame, slot_point(entry["slot"], BULK_CARD_CENTER)):
                ctx.logger.warning("gear: bulk slot %d is worn by a hero: unselected, left as it is", entry["slot"] + 1)
                entry["state"] = "keep"
        if any(entry.get("state") == "keep" for entry in cycle["slots"]):
            cycle["stage"] = "apply"
            return State.ROUTE
    rolling = [entry for entry in cycle["slots"] if entry.get("state", "roll") == "roll"]
    lowest = min((entry["level"] for entry in rolling), default=ctx.config.gear.max_level)
    # The next tier of the lowest piece: the pieces at it or above are not enhanced by this step.
    target = next_tier(ctx, lowest)
    cycle["target"] = target
    selected = [entry for entry in cycle["slots"] if entry.get("state", "roll") == "roll"]
    if not selected or target <= lowest:
        cycle["stage"] = "leave"
        return State.ROUTE
    cost = read_amount(ctx.frame, ctx.ocr, BULK_COST_REGION)
    if not cost:
        picked_at = cycle.get("tier_picked_at")
        tries = cycle.get("tier_tries", 0)
        x, y = BULK_TIER_POINTS[target]
        half_w, half_h = TIER_BUTTON_HALF
        lit = float(ctx.frame[y - half_h : y + half_h, x - half_w : x + half_w].mean()) > TIER_LIT_MEAN
        if not lit and tries < BULK_TIER_TRIES and (picked_at is None or time.monotonic() - picked_at >= 2.0):
            cycle["tier_picked_at"] = time.monotonic()
            cycle["tier_tries"] = tries + 1
            return act_once(ctx, "bulk_tier", BULK_TIER_POINTS[target])
        if picked_at is not None and time.monotonic() - picked_at < TIER_COST_SECONDS:
            return State.ROUTE
        save_failure(ctx, "bulk_no_cost")
        return stop_cycle(ctx, cycle, f"Bulk Enhancement shows no cost for +{target} after {tries} tries (screenshot in logs/failures)")
    left = gold_left(ctx)
    if left is not None and cost > left:
        return stop_cycle(ctx, cycle, f"enhancing to +{target} costs {cost:,} gold, over what the limit leaves ({left:,})")
    gold = read_amount(ctx.frame, ctx.ocr, GOLD_REGION)
    if gold is not None and cost > gold:
        return stop_cycle(ctx, cycle, f"not enough gold to enhance to +{target} ({cost:,})")
    if not cycle.get("counted"):
        cycle["counted"] = True
        data["changed"] += len(selected)
    data["gold"] += cost
    data["bulks"] += 1
    count(ctx, "enhanced", len(selected))
    count(ctx, "gold", cost)
    ctx.logger.info("gear: bulk enhanced %d pieces to +%d for %s gold", len(selected), target, f"{cost:,}")
    cycle.pop("tier_picked_at", None)
    cycle.pop("tier_tries", None)
    cycle["stage"] = "enhancing"
    data["bulk_since"] = time.monotonic()
    click_once(ctx, *BULK_ENHANCE_POINT)
    return State.ROUTE


def bulk_judge(ctx: Context, cycle: dict) -> State:
    """Read each piece just enhanced and decide: roll on, lock, keep, or sell.

    A piece is read knowing what it was before this tier (its grid card, or the Bulk screen after the last tier):
    the slot must show that same piece. Every slot is read before any decision; one still unread after a few
    frames is unselected and left as it is.
    """
    data = gear_data(ctx)
    models = card_models(ctx)
    if not cycle.get("reads"):
        data["screen_name"] = save_screen(ctx, f"bulk_plus{cycle['target']}", ctx.frame)
    rolling = [entry for entry in cycle["slots"] if entry.get("state", "roll") == "roll" and entry.get("raised")]
    readings = {}
    for entry in rolling:
        center = slot_point(entry["slot"], BULK_CARD_CENTER)
        readings[entry["slot"]] = read_bulk_card(ctx.frame, center, models, entry["reading"], entry["level"])
    if any(reading is None for reading in readings.values()):
        cycle["reads"] = cycle.get("reads", 0) + 1
        if cycle["reads"] < BULK_READS:
            return State.ROUTE
    cycle.pop("reads", None)
    for entry in rolling:
        if entry["slot"] not in readings:
            continue
        reading = readings[entry["slot"]]
        if reading is None:
            data["unread"] += 1
            ctx.logger.warning("gear: bulk slot %d unread: unselected, left as it is", entry["slot"] + 1)
            entry["state"] = "keep"
            continue
        entry["reading"] = reading
        data["done_keys"].add(card_key(reading))
        action, reason = decide(ctx, reading)
        if action == "enhance" and next_tier(ctx, reading.level) <= reading.level:
            action, reason = "keep", f"at +{ctx.config.gear.max_level}, the level the settings stop at"
        if action == "enhance":
            reason = f"to +{next_tier(ctx, reading.level)}: {reason}"
        record(ctx, reading, action, reason, f"bulk +{entry['level']}")
        entry["state"] = {"enhance": "roll", "lock": "lock", "keep": "keep"}.get(action, "sell")
    cycle["stage"] = "apply"
    return State.ROUTE


def bulk_apply(ctx: Context, cycle: dict) -> State:
    """Lock the finished pieces, unselect those that stop here, then sell the failed ones from Arrange.

    Every click a step needs is sent at once; the next frame checks them all.
    """
    clicks = []
    for entry in cycle["slots"]:
        state = entry.get("state")
        if state in ("gone", None):
            continue
        if state == "lock" and not slot_locked(ctx.frame, entry["slot"]):
            if entry.get("lock_clicks", 0) >= 3:
                ctx.logger.warning("gear: bulk slot %d does not lock", entry["slot"] + 1)
                entry["state"] = "keep"
            else:
                entry["lock_clicks"] = entry.get("lock_clicks", 0) + 1
                clicks.append(slot_point(entry["slot"], BULK_LOCK))
            continue
        if state == "lock" and not entry.get("lock_counted"):
            entry["lock_counted"] = True
            count(ctx, "locked")
        want = state == "roll"
        if slot_selected(ctx.frame, entry["slot"]) != want:
            if entry.get("toggles", 0) >= 3:
                return stop_cycle(ctx, cycle, f"bulk slot {entry['slot'] + 1} does not change its selection")
            entry["toggles"] = entry.get("toggles", 0) + 1
            clicks.append(slot_point(entry["slot"], BULK_TOGGLE))
            continue
        entry.pop("toggles", None)
    if clicks:
        click_all(ctx, clicks)
        return State.ROUTE
    if any(entry.get("state") == "sell" for entry in cycle["slots"]):
        cycle["stage"] = "arrange"
        return act_once(ctx, "bulk_arrange", BULK_ARRANGE_POINT, wait=2.5)
    cycle["stage"] = "enhance"
    cycle.pop("seen_at", None)
    return State.ROUTE


def bulk_arrange(ctx: Context, cycle: dict) -> State:
    """The Bulk screen's Arrange: tick exactly the pieces to sell, check the count, sell."""
    if cycle["stage"] == "sold":
        # The sale is confirmed: the Bulk screen comes back on its own.
        return State.ROUTE
    if cycle["stage"] not in ("arrange", "selling"):
        # Opened for nothing: leave it.
        return act_once(ctx, "bulk_arrange_cancel", BULK_ARRANGE_CANCEL_POINT, wait=2.0)
    if cycle["stage"] == "selling":
        if time.monotonic() - cycle.get("sell_at", 0.0) > ACTION_POPUP_SECONDS:
            tries = cycle.get("sell_tries", 1)
            if tries > ACTION_RETRIES:
                # Seen once at +15, not understood yet: the pieces stay unsold and the run goes on.
                save_failure(ctx, "bulk_sell_no_popup")
                for entry in cycle["slots"]:
                    if entry["slot"] in cycle.get("selling", []):
                        entry["state"] = "keep"
                data = gear_data(ctx)
                data["unsold"] = data.get("unsold", 0) + len(cycle.get("selling", []))
                ctx.logger.warning("gear: Sell never opened its popup on the Bulk screen: %d pieces left unsold (screenshot in logs/failures)", len(cycle.get("selling", [])))
                cycle.pop("selling", None)
                cycle.pop("sell_tries", None)
                cycle["stage"] = "apply"
                return act_once(ctx, "bulk_arrange_cancel", BULK_ARRANGE_CANCEL_POINT, wait=2.0)
            cycle["sell_tries"] = tries + 1
            cycle["sell_at"] = time.monotonic()
            click_once(ctx, *BULK_ARRANGE_SELL_POINT)
        return State.ROUTE
    sells, clicks = [], []
    for entry in cycle["slots"]:
        if entry.get("state") == "gone":
            continue
        want = entry.get("state") == "sell"
        center = slot_point(entry["slot"], BULK_CARD_CENTER)
        if card_ticked(ctx.frame, center) != want:
            if entry.get("ticks", 0) >= 3:
                return stop_cycle(ctx, cycle, f"bulk slot {entry['slot'] + 1} cannot be ticked right in Arrange")
            entry["ticks"] = entry.get("ticks", 0) + 1
            clicks.append(slot_point(entry["slot"], BULK_TOGGLE))
        elif want:
            sells.append(entry)
    if clicks:
        # Arrange opens with every selected piece ticked: all the changes at once, checked on the next frame.
        click_all(ctx, clicks)
        return State.ROUTE
    text = ctx.ocr.read(ctx.frame, BULK_ARRANGE_COUNT_REGION, whitelist="0123456789[]")
    shown = re.search(r"(\d+)", text)
    if shown is None or int(shown.group(1)) != len(sells):
        cycle["count_misses"] = cycle.get("count_misses", 0) + 1
        if cycle["count_misses"] < 6:
            return State.ROUTE
        return stop_cycle(ctx, cycle, f"Arrange shows {text!r} ticked, not {len(sells)}")
    cycle.pop("count_misses", None)
    for entry in cycle["slots"]:
        entry.pop("ticks", None)
    cycle["selling"] = [entry["slot"] for entry in sells]
    cycle["stage"] = "selling"
    cycle["sell_at"] = time.monotonic()
    data = gear_data(ctx)
    data["sweep"] = data.get("sweep") or {}
    data["sweep"]["pending"] = "sell"
    click_once(ctx, *BULK_ARRANGE_SELL_POINT)
    return State.ROUTE


def bulk_after_sale(ctx: Context, cycle: dict) -> State:
    """Back on the Bulk screen after a sale: the sold slots are gone."""
    data = gear_data(ctx)
    sold = [entry for entry in cycle["slots"] if entry["slot"] in cycle.get("selling", [])]
    for entry in sold:
        entry["state"] = "gone"
    data["arranges"] += 1
    count(ctx, "sold", len(sold))
    ctx.logger.info("gear: sold %d pieces", len(sold))
    cycle.pop("selling", None)
    cycle["stage"] = "apply"
    return State.ROUTE


def click_all(ctx: Context, points: list[tuple[int, int]]) -> None:
    """Click several points of the Bulk screen in a row, then let the last change show."""
    for point in points:
        ctx.session.input.click(*point)
        ctx.wait(BULK_CLICK_SECONDS)
    ctx.wait(BULK_SETTLE_AFTER_CLICKS)
    ctx.progressed = True


def stop_cycle(ctx: Context, cycle: dict, why: str) -> State:
    """Give up the cycle: leave the screens, then stop the run."""
    data = gear_data(ctx)
    data["stop_why"] = why
    data["plan"] = []
    cycle["stage"] = "leave"
    return State.ROUTE


def leave_cycle(ctx: Context, cycle: dict) -> State:
    """The cycle is over: back to the Inventory, where the sweep ends."""
    data = gear_data(ctx)
    sweep = data.get("sweep")
    if sweep is not None:
        sweep["stage"] = "leave"
    data["cycle"] = None
    return act_once(ctx, "back", BACK_POINT, wait=2.5)


def save_failure(ctx: Context, name: str) -> None:
    """Save the screen in logs/failures."""
    failure = ctx.config.logging.log_dir / "failures"
    failure.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(failure / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.png"), ctx.frame)


def screen_still(ctx: Context, since_key: str) -> bool:
    """Return True once an animation started at data[since_key] is over: the screen stays still, or it took too long."""
    data = gear_data(ctx)
    now = time.monotonic()
    elapsed = now - data[since_key]
    small = cv2.resize(ctx.frame, (160, 90)).astype(np.int16)
    last = data.get("last_small")
    data["last_small"] = small
    if elapsed < ENHANCE_MIN_SECONDS:
        return False
    if last is not None and float(np.abs(small - last).mean()) < STILL_DIFFERENCE:
        data["still_since"] = data.get("still_since") or now
    else:
        data["still_since"] = None
    if (data["still_since"] is not None and now - data["still_since"] >= ENHANCE_STILL_SECONDS) or elapsed > ENHANCE_MAX_SECONDS:
        data.pop(since_key, None)
        data.pop("still_since", None)
        data.pop("last_small", None)
        return True
    return False


# Popups.


def popup_buttons_y(frame: np.ndarray) -> int | None:
    """Return the height of a popup's buttons, from the orange glow under its Cancel button."""
    x, y, w, h = CANCEL_GLOW_BAND
    band = frame[y : y + h, x : x + w].astype(int)
    glow = (band[:, :, 2] > 190) & (band[:, :, 1] > 110) & (band[:, :, 1] < 215) & (band[:, :, 0] < 110)
    rows = np.nonzero(glow.sum(axis=1) > 40)[0]
    return int(rows.max()) + y - GLOW_TO_BUTTON if len(rows) else None


def popup(ctx: Context, text: str) -> State:
    """Confirm the sale, extraction or craft the bot asked for; cancel any other popup of these screens."""
    data = gear_data(ctx)
    sweep = data.get("sweep") or {}
    if not sweep.get("pending") and (data.get("craft_arrange") or {}).get("pending"):
        sweep = data["craft_arrange"]
    pending = sweep.get("pending")
    if pending == "sell" and "sell" in text or pending == "extract" and "extraction" in text:
        seen(ctx, f"gear {pending} popup")
        row = popup_buttons_y(ctx.frame)
        if row is None:
            return State.ROUTE
        if "automatically" in ctx.ocr.read(ctx.frame, POPUP_NOTE_REGION, single_line=False).casefold():
            # The game took some pieces out of the extraction on its own, without showing which: they cannot be
            # extracted. Nothing is done here; the sweep is run again in halves to find them.
            ctx.logger.warning("gear: the game left some pieces out of the %s; trying them in smaller batches", pending)
            save_failure(ctx, "extraction_refused")
            sweep["pending"] = None
            sweep["stage"] = "leave"
            sweep["refused"] = True
            return act_once(ctx, "cancel", (CANCEL_POINT[0], row), wait=3.0)
        sweep["pending"] = None
        sweep["confirmed"] = time.monotonic()
        cycle = data.get("cycle")
        if cycle is not None and cycle.get("stage") == "selling":
            cycle["stage"] = "sold"
        return act_once(ctx, "confirm", (CONFIRM_POINT[0], row), wait=3.0)
    if "craft" in text and data.get("craft_pending"):
        seen(ctx, "gear craft popup")
        data.pop("craft_pending", None)
        # Counted as soon as it is confirmed, so that the limits always hold; its result is waited for.
        batch = data.get("batch_size", 10)
        cost = data.pop("craft_cost", 0)
        data["crafted"] += batch
        data["gold"] += cost
        count(ctx, "crafted", batch)
        count(ctx, "gold", cost)
        ctx.logger.info("gear: crafted %d %s %s for %s gold, %d in this run", batch, ctx.config.gear.craft_hunt, ctx.config.gear.craft_slot, f"{cost:,}", data["crafted"])
        data["crafting"] = time.monotonic()
        return act_once(ctx, "craft_confirm", CRAFT_CONFIRM_POINT, wait=3.0)
    now = time.monotonic()
    if now - data.get("cancel_at", 0.0) >= 3.0:
        data["unexpected"] += 1
        ctx.logger.warning("gear: unexpected popup '%s', cancelled", text)
        if data["unexpected"] > MAX_UNEXPECTED_POPUPS:
            click_once(ctx, *CANCEL_POINT)
            return finish(ctx, f"popups the bot did not ask for kept coming ('{text}')")
    row = popup_buttons_y(ctx.frame)
    return act_once(ctx, "cancel", (CANCEL_POINT[0], row if row is not None else CANCEL_POINT[1]), wait=3.0)


# The Steel Workshop.


def workshop(ctx: Context) -> State:
    """Craft the pieces the settings ask for, by 10 then by 1, while materials and gold allow."""
    data = gear_data(ctx)
    seen(ctx, "gear workshop")
    single_done(ctx)
    if data.get("crafting") is not None:
        # A craft was confirmed: its result comes over the workshop after an animation. Nothing is clicked meanwhile
        # (a click there would close a single piece's result and craft again).
        if time.monotonic() - data["crafting"] < CRAFT_RESULT_SECONDS:
            return State.ROUTE
        return finish(ctx, "a craft's result never showed: stopped to be safe")
    gear = ctx.config.gear
    left = gear.craft_pieces - data["crafted"]
    if left <= 0 or data["phase"] != "craft":
        return to_pieces(ctx)
    hunt_title = ctx.ocr.read(ctx.frame, WORKSHOP_TITLE_REGION).casefold()
    if gear.craft_hunt not in hunt_title:
        tries = data.get("tab_tries", 0)
        if tries >= len(HUNT_TAB_POINTS) * 2:
            return finish(ctx, f"the {gear.craft_hunt} tab of the Steel Workshop cannot be found")
        data["tab_tries"] = tries + 1
        guess = HUNT_TAB_POINTS[(HUNTS.index(gear.craft_hunt) + tries) % len(HUNT_TAB_POINTS)]
        return act_once(ctx, "hunt_tab", guess, wait=2.5)
    data["tab_tries"] = 0
    if "craft count" not in ctx.ocr.read(ctx.frame, COUNT_LABEL_REGION).casefold():
        return act_once(ctx, "slot", SLOT_POINTS[gear.craft_slot], wait=2.0)
    batch = 10 if left >= 10 else 1
    if data.get("batch_size") != batch:
        data["batch_size"] = batch
        return act_once(ctx, "count", COUNT_POINTS[batch])
    material = ctx.ocr.read(ctx.frame, MATERIAL_REGION, whitelist="0123456789,/")
    have, _, need = material.replace(",", "").partition("/")
    if have.isdigit() and need.isdigit() and int(have) < int(need):
        if data["crafted"]:
            ctx.logger.info("gear: not enough %s materials for more (%s/%s)", gear.craft_hunt, have, need)
            return to_pieces(ctx)
        return finish(ctx, f"not enough {gear.craft_hunt} materials ({have}/{need})")
    cost = read_amount(ctx.frame, ctx.ocr, CRAFT_COST_REGION)
    if not cost:
        return State.ROUTE
    limit = gold_left(ctx)
    if limit is not None and cost > limit:
        if data["crafted"]:
            return to_pieces(ctx)
        return finish(ctx, f"crafting costs {cost:,} gold, over what the limit leaves ({limit:,})")
    if ctx.config.gear.dry_run:
        ctx.logger.info("gear: would craft %d %s %s for %s gold (dry run)", batch, gear.craft_hunt, gear.craft_slot, f"{cost:,}")
        data["crafted"] += batch
        return State.ROUTE
    data["craft_pending"] = True
    data["craft_cost"] = cost
    return act_once(ctx, "craft", CRAFT_POINT, wait=3.0)


def craft_result(ctx: Context) -> State:
    """Count the crafted pieces once, then close the result."""
    data = gear_data(ctx)
    seen(ctx, "gear craft result")
    if data.pop("crafting", None) is not None:
        if ctx.config.gear.craft_then == "sort":
            data["result"] = {"stage": "read", "reads": 0}
    selected = RESULT_SELECTED.search(ctx.ocr.read(ctx.frame, RESULT_SELECTED_REGION).casefold())
    result = data.get("result")
    if result is None or result["stage"] == "done":
        data.pop("result", None)
        if selected is not None:
            return act_once(ctx, "craft_result", RESULT_ARRANGE_CONFIRM_POINT, wait=2.5)
        return act_once(ctx, "craft_result", CRAFT_RESULT_CONFIRM_POINT, wait=2.5)
    if result["stage"] == "read" and "extract" in ctx.ocr.read(ctx.frame, SINGLE_EXTRACT_REGION).casefold():
        return judge_single(ctx, result)
    if result["stage"] == "single":
        # The sale or extraction was asked: the game goes back to the workshop once it is done.
        holder = data.get("craft_arrange") or {}
        if holder.get("refused") or time.monotonic() - result["since"] > RESULT_ACTION_SECONDS:
            ctx.logger.warning("gear: the crafted piece's %s did not happen: left as it is", ctx.config.gear.new_bad_action)
            data.pop("craft_arrange", None)
            result["stage"] = "done"
            return act_once(ctx, "single_close", SINGLE_CLOSE_POINT, wait=2.5)
        return State.ROUTE
    if result["stage"] == "read":
        return judge_result(ctx, result)
    if result["stage"] == "tick":
        return tick_result(ctx, result, selected)
    return result_acted(ctx, result)


def judge_single(ctx: Context, result: dict) -> State:
    """Judge a single crafted piece on its panel: extract or sell it there, or close the panel and keep it."""
    data = gear_data(ctx)
    reading = read_single(ctx.frame, ctx.ocr)
    if reading is None and result["reads"] < RESULT_READS:
        result["reads"] += 1
        return State.ROUTE
    data["screen_name"] = save_screen(ctx, "craft_result", ctx.frame)
    if reading is None:
        data["unread"] += 1
        ctx.logger.info("gear: crafted piece unread, left as it is")
        result["stage"] = "done"
        return act_once(ctx, "single_close", SINGLE_CLOSE_POINT, wait=2.5)
    decision, reason = decide(ctx, reading)
    record(ctx, reading, decision, reason, "craft result")
    action = ctx.config.gear.new_bad_action
    if decision != action or ctx.config.gear.dry_run:
        result["stage"] = "done"
        return act_once(ctx, "single_close", SINGLE_CLOSE_POINT, wait=2.5)
    data["craft_arrange"] = {"pending": action}
    result.update(stage="single", since=time.monotonic())
    return act_once(ctx, f"single_{action}", SINGLE_ACTION_POINTS[action], wait=3.0)


def single_done(ctx: Context) -> None:
    """Back at the workshop after a single piece's sale or extraction was confirmed: count it."""
    data = gear_data(ctx)
    result = data.pop("result", None)
    holder = data.pop("craft_arrange", None) or {}
    if result is None or result.get("stage") != "single":
        return
    action = ctx.config.gear.new_bad_action
    if holder.get("pending") or holder.get("refused"):
        ctx.logger.warning("gear: the crafted piece's %s was not confirmed: left as it is", action)
        return
    done = "sold" if action == "sell" else "extracted"
    data["arranges"] += 1
    data["changed"] += 1
    count(ctx, done, 1)
    ctx.logger.info("gear: %s 1 pieces", done)


def judge_result(ctx: Context, result: dict) -> State:
    """Read the crafted pieces on the result screen and pick the ones to extract or sell."""
    data = gear_data(ctx)
    models = card_models(ctx)
    action = ctx.config.gear.new_bad_action
    corners = result_icons(ctx.frame)
    readings = [read_result_card(ctx.frame, corner, ctx.ocr, models, slot=ctx.config.gear.craft_slot) for corner in corners]
    if (not corners or any(read.reading is None for read in readings)) and result["reads"] < RESULT_READS:
        result["reads"] += 1
        return State.ROUTE
    data["screen_name"] = save_screen(ctx, "craft_result", ctx.frame)
    losers = []
    for corner, read in zip(corners, readings):
        if read.reading is None:
            data["unread"] += 1
            ctx.logger.info("gear: crafted piece unread (%s), left as it is", read.why)
            continue
        data["card_reads"] += 1
        decision, reason = decide(ctx, read.reading)
        record(ctx, read.reading, decision, reason, "craft result")
        if decision == action and not ctx.config.gear.dry_run:
            losers.append(corner)
    result.update(losers=losers, shown=len(corners), stage="tick" if losers else "done")
    return State.ROUTE


def tick_result(ctx: Context, result: dict, selected) -> State:
    """Open the result's Arrange, tick the pieces to get rid of, check the count, then sell or extract."""
    if selected is None:
        return act_once(ctx, "result_arrange", RESULT_ARRANGE_POINT, wait=2.5)
    if not result.get("ticked"):
        for left, top in result["losers"]:
            ctx.session.input.click(left + RESULT_ICON_CENTER, top + RESULT_ICON_CENTER)
            ctx.wait(RESULT_TICK_SECONDS)
        result["ticked"] = True
        ctx.wait(TICK_SETTLE_SECONDS)
        return State.ROUTE
    if int(selected.group(1)) != len(result["losers"]):
        result["misses"] = result.get("misses", 0) + 1
        if result["misses"] < 4:
            return State.ROUTE
        if not result.get("retried"):
            # A tick that did not take: leave Arrange (its ticks go) and tick them all again.
            result.update(retried=True, ticked=False, misses=0)
            return act_once(ctx, "result_arrange_cancel", RESULT_ARRANGE_POINT, wait=2.5)
        ctx.logger.warning("gear: %s crafted pieces shown ticked instead of %d: nothing done", selected.group(1), len(result["losers"]))
        result["stage"] = "done"
        return act_once(ctx, "result_arrange_cancel", RESULT_ARRANGE_POINT, wait=2.5)
    action = ctx.config.gear.new_bad_action
    gear_data(ctx)["craft_arrange"] = {"pending": action}
    result.update(stage="acted", since=time.monotonic())
    return act_once(ctx, f"result_{action}", RESULT_ACTION_POINTS[action], wait=3.0)


def result_acted(ctx: Context, result: dict) -> State:
    """After the popup: the pieces sold or extracted leave the screen. Count them, or give up after a while."""
    data = gear_data(ctx)
    holder = data.get("craft_arrange") or {}
    action = ctx.config.gear.new_bad_action
    done = "sold" if action == "sell" else "extracted"
    if holder.get("refused"):
        ctx.logger.warning("gear: the game would not %s some crafted pieces: all left as they are", action)
        data["refused_pieces"] = data.get("refused_pieces", 0) + len(result["losers"])
        result["stage"] = "done"
        return State.ROUTE
    left = len(result_icons(ctx.frame))
    if not holder.get("pending") and left == result["shown"] - len(result["losers"]):
        data["arranges"] += 1
        data["changed"] += len(result["losers"])
        count(ctx, done, len(result["losers"]))
        ctx.logger.info("gear: %s %d pieces", done, len(result["losers"]))
        result["stage"] = "done"
        return State.ROUTE
    if time.monotonic() - result["since"] > RESULT_ACTION_SECONDS:
        ctx.logger.warning("gear: the crafted pieces did not leave the screen after the %s: count not sure", action)
        result["stage"] = "done"
    return State.ROUTE


# The account evaluation: the grid sorted by level, every +15 card read from the top, worn or locked ones too.


def account_step(ctx: Context, step: str) -> None:
    """Log a step of the account evaluation once, for the window's live progress. Other modes log nothing here."""
    if ctx.goal != ACCOUNT_GOAL:
        return
    done = gear_data(ctx).setdefault("account_steps", [])
    if step not in done:
        done.append(step)
        ctx.logger.info("account: %s", step)


def account_scan(ctx: Context) -> State:
    """One step of the account evaluation: open each slot's tab (the grid shows it from its top, and the slot is then
    known: the artwork alone left 17 of 152 cards unread), read screen after screen until the cards go under +15 or
    the tab ends, and come back to the All tab, the one the other gear modes sweep."""
    data = gear_data(ctx)
    scan = data.setdefault("scan", {
        "stage": "tab", "tabs": list(SLOT_TAB_POINTS), "slot": None, "next": 0, "offset": 0,
        "pieces": [], "unread": 0, "other": 0, "screens": 0, "ends": [],
    })
    if card_models(ctx) is None:
        return account_done(ctx, "the card templates are missing (templates/cards/models.npz)")
    if scan["stage"] == "tab":
        if not scan["tabs"]:
            scan["stage"] = "all"
            return act_once(ctx, "tab_all", ALL_TAB_POINT, wait=TAB_SECONDS)
        scan.update(stage="wait", slot=scan["tabs"].pop(0), next=0, offset=0, still_drags=0, tab_first=len(scan["pieces"]))
        scan.pop("empty_since", None)
        ctx.logger.info("account: %s tab", scan["slot"])
        return act_once(ctx, f"tab_{scan['slot']}", SLOT_TAB_POINTS[scan["slot"]], wait=TAB_SECONDS)
    if scan["stage"] == "wait":
        # The tab's cards slide in.
        if time.monotonic() - gear_data(ctx).get(f"tab_{scan['slot']}_at", 0.0) >= TAB_SECONDS:
            scan["stage"] = "read"
        return State.ROUTE
    if scan["stage"] == "all":
        if time.monotonic() - gear_data(ctx).get("tab_all_at", 0.0) < TAB_SECONDS:
            return State.ROUTE
        ends = ", ".join(scan["ends"])
        return account_done(ctx, f"every tab is read ({ends})")
    for _ in range(SCREENS_PER_TICK):
        why = account_screen(ctx, scan)
        if not why:
            moved = drag_grid(ctx, FIRST_ROW_Y + (scan["next"] // 4) * ROW_PITCH - scan["offset"] - NEXT_ROW_Y)
            if abs(moved) >= MIN_SCROLL_PIXELS:
                scan["still_drags"] = 0
                scan["offset"] += moved
                continue
            scan["still_drags"] = scan.get("still_drags", 0) + 1
            if scan["still_drags"] < END_DRAGS:
                return State.ROUTE
            why = "end of the tab"
        scan["ends"].append(f"{scan['slot']}: {why}")
        found = len(scan["pieces"]) - scan.get("tab_first", 0)
        ctx.logger.info("account: %s tab done: %d +15 piece%s (%s)", scan["slot"], found, "" if found == 1 else "s", why)
        scan["stage"] = "tab"
        return State.ROUTE
    return State.ROUTE


def account_screen(ctx: Context, scan: dict) -> str:
    """Read the cards on screen not read yet. Returns why the scan ends, or "" to scroll on."""
    models = card_models(ctx)
    while True:
        index = scan["next"]
        x = GRID_COLUMNS_X[index % 4]
        y = FIRST_ROW_Y + (index // 4) * ROW_PITCH - scan["offset"]
        if y > READ_Y_RANGE[1] or y < READ_Y_RANGE[0]:
            break
        if not has_card(ctx.frame, x, y):
            first = scan.setdefault("empty_since", time.monotonic())
            if time.monotonic() - first < EMPTY_CONFIRM_SECONDS:
                ctx.wait(SETTLE_STEP_SECONDS)
                ctx.frame = ctx.session.capture.grab()
                continue
            return "end of the tab"
        scan.pop("empty_since", None)
        result = read_small_card(ctx.frame, (x, y), ctx.ocr, models, slot=scan.get("slot"), read_kept=True, otherworldly_epic=True, reforged=True)
        if result.status != "unsure" and result.shift:
            scan["offset"] -= result.shift
        scan["next"] += 1
        reading = result.reading
        if reading is None:
            if result.why.startswith("item level"):
                scan["other"] += 1
                ctx.logger.info("account: card %d skipped (%s)", index + 1, result.why)
            else:
                scan["unread"] += 1
                ctx.logger.info("account: card %d unread (%s)", index + 1, result.why)
            continue
        if reading.level < 15:
            # Sorted by level: every +15 piece came before.
            return "under +15"
        scan["pieces"].append(reading)
        kind = KEPT_KINDS.get(result.status)
        if kind:
            # Read in full and judged again on the page: is it still worth keeping with the targets of now?
            scan.setdefault("kept", {})[len(scan["pieces"]) - 1] = kind
            ctx.logger.info("account: %s (%s)", reading.describe(), kind)
        else:
            ctx.logger.info("account: %s", reading.describe())
        count(ctx, "judged")
        ctx.progressed = True
    scan["screens"] += 1
    save_screen(ctx, f"account_{scan['screens']:02d}", ctx.frame)
    return ""


def account_done(ctx: Context, why: str) -> State:
    """Keep each piece's values, save the result for the window, and stop with the account's level."""
    data = gear_data(ctx)
    scan = data.get("scan") or {"pieces": [], "unread": 0, "other": 0}
    ctx.logger.info("account: computing the scores")
    result = evaluate([reading.piece() for reading in scan["pieces"]], scan["unread"], scan.get("kept"))
    save_result(result, ctx.config.logging.log_dir / "gear")
    name, median = level(result)
    for role in ALL_ROLES:
        found = stats(role_values(result, role))
        if found.count:
            ctx.logger.info("account: %s, %d pieces, median %g%%, best %g%%, worst %g%%", role, found.count, found.median, found.best, found.worst)
    extra = f", {scan['other']} of other item levels skipped" if scan["other"] else ""
    level_text = f"level {name} (median piece {median:g}%)" if median is not None else "no finished piece suits a role"
    line = f"Account: {why}. {result.read} +15 pieces read ({result.reforged} reforged), {result.unread} unread{extra}; {level_text}. See the Account page for the targets."
    data["phase"] = "done"
    data["done_line"] = line
    return ctx.halt(line)


def to_pieces(ctx: Context) -> State:
    """After crafting: the bad pieces were already dealt with on each result screen."""
    return finish(ctx, "crafting done")


def lose_place(data: dict) -> None:
    """Away from the Inventory the game forgets the grid's place and the selection: the sweep starts over."""
    sweep = data.get("sweep")
    if sweep is not None and sweep["stage"] in ("opening", "read"):
        restart_sweep(sweep)
    data.pop("top_step", None)


def equipment_tab_open(frame: np.ndarray) -> bool:
    """Return whether the Inventory shows its Equipment tab: its icon in the left rail is lit blue."""
    x, y, width, height = EQUIPMENT_TAB_REGION
    return float(frame[y : y + height, x : x + width, 0].mean()) > TAB_LIT_BLUE


def gear(ctx: Context, unknown, at_lobby) -> State:
    """One tick of a gear mode. `unknown` handles stray screens, `at_lobby` tells the lobby."""
    data = gear_data(ctx)
    popup_text = " / ".join(ctx.ocr.read(ctx.frame, region).casefold() for region in POPUP_TITLE_REGIONS)
    # A popup has its Cancel button's glow: without it, words like "Sell" are Arrange's buttons, not a popup's title.
    if any(word in popup_text for word in ("sell", "extraction", "craft")) and "workshop" not in popup_text and popup_buttons_y(ctx.frame) is not None:
        return popup(ctx, popup_text)
    if (data["phase"] == "craft" or data.get("crafting") is not None) and "success" in ctx.ocr.read(ctx.frame, CRAFT_RESULT_REGION).casefold():
        return craft_result(ctx)
    screen = title(ctx)
    if "enhance equipment" in screen:
        # Not used by the gear modes: back to the Inventory.
        return act_once(ctx, "back", BACK_POINT)
    if "bulk" in screen:
        return bulk_screen(ctx)
    if "inventory" in screen:
        if data["phase"] == "craft":
            return act_once(ctx, "back", BACK_POINT, wait=2.5)
        if not equipment_tab_open(ctx.frame):
            # The Inventory reopens on the tab it was left on: its first one lists the heroes with their gear.
            seen(ctx, "inventory heroes tab")
            return act_once(ctx, "equipment_tab", EQUIPMENT_TAB_POINT, wait=2.0)
        return inventory(ctx)
    if "steel workshop" in screen:
        lose_place(data)
        if data["phase"] != "craft":
            return act_once(ctx, "back", BACK_POINT, wait=2.5)
        return workshop(ctx)
    if data["phase"] == "craft" and ctx.ocr.contains(ctx.frame, "workshop", MENU_WORKSHOP_REGION):
        seen(ctx, "menu")
        return act_once(ctx, "menu_workshop", MENU_WORKSHOP_POINT, wait=2.5)
    if at_lobby(ctx):
        lose_place(data)
        seen(ctx, "lobby")
        if data["phase"] == "craft":
            return act_once(ctx, "menu", MENU_POINT, wait=2.5)
        account_step(ctx, "opening the inventory")
        return act_once(ctx, "bag", BAG_POINT, wait=2.5)
    sweep = data.get("sweep")
    if sweep is not None and sweep.get("stage") == "acted" and time.monotonic() - sweep.get("confirmed", 0.0) < 10.0:
        # The sale or extraction's result: close it.
        return act_once(ctx, "result_confirm", RESULT_CONFIRM_POINT, wait=2.0)
    if time.monotonic() - data.get("acted_at", 0.0) < TRANSITION_SECONDS:
        # Between two screens: wait for the next one.
        ctx.route_data["unknown_since"] = time.monotonic()
    else:
        lose_place(data)
    return unknown(ctx)

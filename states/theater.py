"""Moonlight Theater on a kept account: watch every act in order, story by story and season by season."""

from __future__ import annotations

import re
import time

import numpy as np

from core.config import State
from core.state import Context
from states.story import SKILL_ANIMATION_SECONDS, click_once, seen, skip_video

# Lobby > Side Story > Moonlight Theater.
SIDE_STORY_POINT = (1180, 260)
THEATER_TILE_REGION = (1048, 330, 230, 74)
# The carousel of stories, one dot per story under it.
PROLOGUE_REGION = (10, 604, 192, 84)
ENTER_POINT = (1140, 644)
NEXT_STORY_POINT = (975, 380)
PREVIOUS_STORY_POINT = (360, 380)
STORY_DOTS_X = tuple(640 + 17 * index for index in range(7))
STORY_DOTS_Y = 674
DOT_WHITE_LEVEL = 200
CAROUSEL_SETTLE_SECONDS = 1.0
# A story's page, where its acts are listed by season.
CAST_BOARD_REGION = (28, 620, 304, 94)
WATCH_ICON_REGION = (628, 628, 82, 74)
WATCH_POINT = (776, 664)
RECEIVE_REWARDS_POINT = (884, 588)
SEASON_POINTS = ((1200, 105), (1200, 183))
# The season shown has a blue bar on the left of its tab. It is not always drawn: no bar, no answer.
SEASON_BAR_X = (1118, 1134)
SEASON_BAR_HALF = 39
SEASON_BAR_PIXELS = 40
BACK_POINT = (37, 33)
TICKETS_REGION = (680, 18, 34, 30)
GOLD_REGION = (742, 18, 116, 30)
TICKET_PLUS_POINT = (667, 25)
# Popups.
NO_SKIP_REGION = (316, 314, 652, 92)
NO_SKIP_CONFIRM_POINT = (748, 495)
USE_TICKET_REGION = (485, 114, 310, 82)
USE_TICKET_CONFIRM_POINT = (742, 528)
PURCHASE_REGION = (456, 86, 370, 84)
PURCHASE_GOLD_REGION = (576, 550, 80, 80)
PURCHASE_BUY_POINT = (835, 589)
PURCHASE_CANCEL_POINT = (469, 589)
BUY_TICKET_REGION = (626, 346, 180, 72)
BUY_GOLD_REGION = (574, 492, 80, 80)
BUY_CONFIRM_POINT = (830, 531)
BUY_CANCEL_POINT = (469, 531)
REWARD_REGION = (506, 128, 270, 82)
REWARD_CLOSE_POINT = (640, 528)
# While an act plays, its dialogue cannot be skipped: a tap in the middle moves it on.
DIALOGUE_POINT = (640, 400)
DIALOGUE_TAP_SECONDS = 0.05
DIALOGUE_TAPS_PER_TICK = 3
DARK_LEVEL = 25.0
# The dialogue has a pause button like a fight's: its story log icon tells them apart.
DIALOGUE_LOG_REGION = (1148, 0, 84, 76)
# A video inside an act, unlike its dialogue, is skipped as everywhere else: Esc held.
# A screen without the dialogue's log icon for this long is taken for a video.
VIDEO_WAIT_SECONDS = 1.0
VIDEO_RETRY_SECONDS = 8.0
# An act lasts a few minutes. Past this, the bot stops tapping and lets the watchdog judge.
MAX_ACT_SECONDS = 20 * 60
# Watch clicked this many times without the act starting: nothing more to watch in this season.
MAX_WATCH_CLICKS = 3
# An act starts this long after its popup is confirmed, at most.
START_WAIT_SECONDS = 15.0
NO_WATCH_SECONDS = 3.0
WATCH_WAIT_SECONDS = 4.0
STORY_COUNT = len(STORY_DOTS_X)
# The selected act of the list wears a yellow frame; its status is written inside.
ACT_LIST_X = (940, 952)
ACT_LIST_Y = (100, 720)
ACT_TEXT_X, ACT_TEXT_WIDTH = 950, 160
# Statuses of an act that still has to be watched: free now, or in some days and with a ticket now.
WATCHABLE = ("available", "left")
WATCHED = ("complete", "rewards", "unavailable")
# Each act's status line has its own color: green when complete, orange when it can be watched
# (Available, or "6d left"), gray with a lock when it is not open yet.
ACT_ROW_X = 1028
STATUS_X = (950, 1110)
STATUS_MIN_PIXELS = 4
STATUS_MIN_HEIGHT = 12
# A click on a row that does not select it: the act cannot be picked, the season is left.
MAX_PICKS = 3
# The list shows about seven acts: a season has up to 14 or more.
ACT_LIST_DRAG_FROM = (1028, 600)
ACT_LIST_DRAG_TO = (1028, 300)
MAX_LIST_SCROLLS = 6
# Status lines lower than this are cut by the bottom of the screen.
LOWEST_ACT_Y_AT_END = 712
ACT_LIST_REGION = (940, 140, 180, 580)
LIST_STILL_LEVEL = 3.0
AFTER_DRAG_SECONDS = 1.0


def selected_act(ctx: Context) -> tuple[str, int] | None:
    """Return the text of the act selected in the list, such as "Act 3 6d left", and the bottom of its frame."""
    x0, x1 = ACT_LIST_X
    y0, y1 = ACT_LIST_Y
    band = ctx.frame[y0:y1, x0:x1].astype(int)
    yellow = (band[:, :, 2] > 180) & (band[:, :, 1] > 150) & (band[:, :, 0] < 130)
    rows = np.where(yellow.any(axis=1))[0]
    if len(rows) < 2 or rows.max() - rows.min() < 40:
        return None
    top, bottom = y0 + int(rows.min()), y0 + int(rows.max())
    return ctx.ocr.read(ctx.frame, (ACT_TEXT_X, top + 4, ACT_TEXT_WIDTH, bottom - top - 8), single_line=False), bottom


def act_rows(frame: np.ndarray) -> list[tuple[int, str]]:
    """Return the (center y, status) of every act row in view, top to bottom: complete, watchable or locked."""
    x0, x1 = STATUS_X
    y0, y1 = ACT_LIST_Y[0] + 40, LOWEST_ACT_Y_AT_END
    area = frame[y0:y1, x0:x1].astype(int)
    blue, green, red = area[:, :, 0], area[:, :, 1], area[:, :, 2]
    spread = area.max(axis=2) - area.min(axis=2)
    masks = {
        "complete": (green > 140) & (green - red > 50) & (green - blue > 60),
        "watchable": (red > 150) & (red - blue > 60) & (red - green > 20) & (red - green < 110),
        "locked": (spread < 25) & (area.max(axis=2) > 120) & (area.max(axis=2) < 205),
    }
    rows = []
    for status, mask in masks.items():
        start = None
        for index, count in enumerate([*mask.sum(axis=1), 0]):
            if count >= STATUS_MIN_PIXELS and start is None:
                start = index
            elif count < STATUS_MIN_PIXELS and start is not None:
                if index - start >= STATUS_MIN_HEIGHT:
                    rows.append((y0 + (start + index) // 2, status))
                start = None
    return sorted(rows)


def selected_act_text(ctx: Context) -> str | None:
    """Return the text of the act selected in the list, or None if none is."""
    selected = selected_act(ctx)
    return selected[0] if selected is not None else None


def act_list_image(ctx: Context) -> np.ndarray:
    """Return a small gray picture of the act list, to tell whether a scroll moved it."""
    x, y, width, height = ACT_LIST_REGION
    return ctx.frame[y : y + height, x : x + width].mean(axis=2)[::4, ::4]


def list_at_end(ctx: Context) -> bool:
    """Return whether the last scroll left the act list where it was: its last act is in view."""
    data = theater_data(ctx)
    if data.get("list_end"):
        return True
    before = data.get("list_before")
    if before is None:
        return False
    # Once seen, it stays known: the frame moving down the list changes the picture too.
    data["list_end"] = float(np.abs(act_list_image(ctx) - before).mean()) < LIST_STILL_LEVEL
    data.pop("list_before", None)
    return data["list_end"]


def scroll_act_list(ctx: Context) -> State:
    """Drag the list of acts up, to show the acts below."""
    data = theater_data(ctx)
    data["list_scrolls"] = data.get("list_scrolls", 0) + 1
    data.pop("no_selection_since", None)
    data["list_before"] = act_list_image(ctx)
    seen(ctx, "theater act list")
    ctx.session.input.drag(*ACT_LIST_DRAG_FROM, *ACT_LIST_DRAG_TO)
    ctx.wait(AFTER_DRAG_SECONDS)
    return State.ROUTE


def act_to_watch(text: str | None) -> bool:
    """Return whether the selected act's status says it is still to be watched."""
    if text is None:
        return False
    folded = " ".join(text.casefold().split())
    if any(word in folded for word in WATCHED):
        return False
    return any(word in folded for word in WATCHABLE)


def read_number(ctx: Context, region: tuple[int, int, int, int]) -> int | None:
    """Read a number such as 5 or 47,746,877 from the top bar, or None."""
    text = ctx.ocr.read(ctx.frame, region, whitelist="0123456789,", white_level=170)
    digits = text.replace(",", "")
    return int(digits) if re.fullmatch(r"\d+", digits) else None


def story_on_carousel(frame: np.ndarray) -> int | None:
    """Return the index of the story in front on the carousel, from the white dot under it."""
    levels = [int(frame[STORY_DOTS_Y - 2 : STORY_DOTS_Y + 3, x - 2 : x + 3].min(axis=2).mean()) for x in STORY_DOTS_X]
    best = max(range(len(levels)), key=levels.__getitem__)
    return best if levels[best] >= DOT_WHITE_LEVEL else None


def season_shown(frame: np.ndarray) -> int | None:
    """Return the index of the season whose tab is lit on a story's page, or None if no tab is."""
    x0, x1 = SEASON_BAR_X
    for index, (_, y) in enumerate(SEASON_POINTS):
        area = frame[y - SEASON_BAR_HALF : y + SEASON_BAR_HALF, x0:x1].astype(int)
        blue = (area[:, :, 0] > 200) & (area[:, :, 0] - area[:, :, 2] > 100)
        if int(blue.sum()) >= SEASON_BAR_PIXELS:
            return index
    return None


def theater_data(ctx: Context) -> dict:
    """Return the theater's progress in this run: which story and season, what was watched and bought."""
    return ctx.route_data.setdefault("theater", {"story": 0, "season": 0, "watched": 0, "bought": 0, "free_only": False})


def next_season(ctx: Context, why: str) -> State:
    """Nothing left to watch in this season: try the next one, then the next story."""
    data = theater_data(ctx)
    data["watch_clicks"] = 0
    data["picks"] = 0
    data["list_scrolls"] = 0
    data.pop("list_before", None)
    data["list_end"] = False
    if data["season"] + 1 < len(SEASON_POINTS):
        data["season"] += 1
        ctx.logger.info("theater: story %d season %d has nothing more to watch (%s), next season", data["story"] + 1, data["season"], why)
        click_once(ctx, *SEASON_POINTS[data["season"]])
        return State.ROUTE
    data["season"] = 0
    data["story"] += 1
    ctx.logger.info("theater: story %d has nothing more to watch (%s), next story", data["story"], why)
    if data["story"] >= STORY_COUNT:
        left = f"; acts that need a ticket were left ({data.get('free_reason', 'no ticket')})" if data["free_only"] else ""
        return ctx.halt(f"Moonlight Theater: nothing more to watch, {data['watched']} acts watched and {data['bought']} tickets bought in this run{left}")
    click_once(ctx, *BACK_POINT)
    return State.ROUTE


def buy_allowed(ctx: Context) -> str | None:
    """Return why the bot may not buy a ticket now, or None if it may."""
    data = theater_data(ctx)
    if not ctx.config.theater.buy_tickets:
        return "buying tickets is turned off"
    # The top bar is dimmed under a popup: the gold is the one read last on the story's page.
    gold = data.get("gold")
    if gold is None:
        return "the gold could not be read"
    floor = round(ctx.config.theater.min_gold_millions * 1_000_000)
    if gold < floor:
        return f"gold under {floor:,}"
    return None


def popups(ctx: Context) -> State | None:
    """Answer the theater's popups. Tickets are only ever bought with gold."""
    data = theater_data(ctx)
    if ctx.find("theater_reward", REWARD_REGION) is not None:
        seen(ctx, "theater reward")
        return click_once(ctx, *REWARD_CLOSE_POINT) or State.ROUTE
    if ctx.find("theater_no_skip", NO_SKIP_REGION) is not None:
        seen(ctx, "theater no skip notice")
        start_act(ctx)
        return click_once(ctx, *NO_SKIP_CONFIRM_POINT) or State.ROUTE
    if ctx.find("theater_use_ticket", USE_TICKET_REGION) is not None:
        seen(ctx, "theater use ticket")
        start_act(ctx)
        return click_once(ctx, *USE_TICKET_CONFIRM_POINT) or State.ROUTE
    if ctx.find("theater_purchase", PURCHASE_REGION) is not None:
        seen(ctx, "theater ticket purchase")
        refused = buy_allowed(ctx)
        if refused is None and ctx.find("theater_gold_buy", PURCHASE_GOLD_REGION) is None:
            refused = "the price is not in gold"
        if refused is not None:
            ctx.logger.info("theater: no ticket bought (%s), only free acts from now on", refused)
            data["free_only"] = True
            data["free_reason"] = refused
            return click_once(ctx, *PURCHASE_CANCEL_POINT) or State.ROUTE
        # The popup buys one ticket at a time: it counts on Buy, as a confirmation does not always follow.
        data["bought"] += 1
        data["plus_at"] = time.monotonic()
        ctx.logger.info("theater: one ticket bought with 500,000 gold, %d in this run", data["bought"])
        if ctx.on_theater_ticket is not None:
            ctx.on_theater_ticket()
        return click_once(ctx, *PURCHASE_BUY_POINT) or State.ROUTE
    if ctx.find("theater_buy_ticket", BUY_TICKET_REGION) is not None:
        seen(ctx, "theater ticket confirmation")
        if ctx.find("theater_gold_confirm", BUY_GOLD_REGION) is None or buy_allowed(ctx) is not None:
            data["free_only"] = True
            data["free_reason"] = buy_allowed(ctx) or "the price is not in gold"
            return click_once(ctx, *BUY_CANCEL_POINT) or State.ROUTE
        return click_once(ctx, *BUY_CONFIRM_POINT) or State.ROUTE
    return None


def start_act(ctx: Context) -> None:
    """Remember that an act is starting: from now on, unknown screens are its dialogue."""
    data = theater_data(ctx)
    if not data.get("playing_since"):
        data["playing_since"] = time.monotonic()
        data["act_seen"] = False
        data["watch_clicks"] = 0
        data["picks"] = 0
        data["list_scrolls"] = 0
        data["list_end"] = False
        data.pop("list_before", None)
        ctx.logger.info("theater: act started, story %d season %d", data["story"] + 1, data["season"] + 1)


def story_page(ctx: Context) -> State:
    """On a story's page: claim rewards after an act, then watch the selected act, with a ticket if needed."""
    data = theater_data(ctx)
    if data.get("playing_since") and not data.get("act_seen"):
        if time.monotonic() - data["playing_since"] < START_WAIT_SECONDS:
            # The popup that starts the act is still fading over the page.
            return State.ROUTE
        # The act never showed: it did not start.
        data["playing_since"] = 0
    if data.get("playing_since"):
        data["playing_since"] = 0
        data["act_seen"] = False
        data["watched"] += 1
        ctx.progressed = True
        ctx.logger.info("theater: act watched, %d in this run", data["watched"])
        if ctx.on_theater_act is not None:
            ctx.on_theater_act()
        data["claim"] = True
    if data.pop("claim", False):
        seen(ctx, "theater story page")
        return click_once(ctx, *RECEIVE_REWARDS_POINT) or State.ROUTE
    target = ctx.config.theater.target_acts
    if target and data["watched"] >= target:
        return ctx.halt(f"Moonlight Theater: target reached, {data['watched']} acts watched and {data['bought']} tickets bought in this run")
    if data.get("entered_story") != data["story"]:
        # A page the bot did not open itself, at its start: the carousel tells which story it is.
        shown = season_shown(ctx.frame)
        if not data.get("located") and shown is not None:
            # Its season is kept: the bot goes on from there.
            data["season"] = shown
        seen(ctx, "theater story page")
        return click_once(ctx, *BACK_POINT) or State.ROUTE
    selected = selected_act(ctx)
    if selected is None:
        # The list redraws after a purchase or a reward: no act is framed for a moment.
        since = data.setdefault("no_selection_since", time.monotonic())
        if time.monotonic() - since < NO_WATCH_SECONDS:
            return State.ROUTE
        # The framed act can also sit below the bottom of the screen: scroll the list to it.
        if data.get("list_scrolls", 0) < MAX_LIST_SCROLLS:
            return scroll_act_list(ctx)
    data.pop("no_selection_since", None)
    text = " ".join(selected[0].split()) if selected is not None else "none"
    if "rewards" in text.casefold() and data.get("claimed_on") != (data["story"], data["season"]):
        # Acts watched without their rewards claimed: one click claims them all.
        data["claimed_on"] = (data["story"], data["season"])
        seen(ctx, "theater story page")
        return click_once(ctx, *RECEIVE_REWARDS_POINT) or State.ROUTE
    if not act_to_watch(text if selected is not None else None):
        if data.get("season_clicked") != (data["story"], data["season"]):
            # Show the season the bot is on before judging it: the page may open on another one.
            data["season_clicked"] = (data["story"], data["season"])
            seen(ctx, "theater story page")
            return click_once(ctx, *SEASON_POINTS[data["season"]]) or State.ROUTE
        # Acts open in order: the first one in view that is not complete is the one to watch, or a lock.
        rows = act_rows(ctx.frame)
        first = next((row for row in rows if row[1] != "complete"), None)
        if first is None:
            if not list_at_end(ctx) and data.get("list_scrolls", 0) < MAX_LIST_SCROLLS:
                return scroll_act_list(ctx)
            return next_season(ctx, "every act is complete")
        if first[1] == "locked":
            return next_season(ctx, "the next act is locked")
        data["picks"] = data.get("picks", 0) + 1
        if data["picks"] > MAX_PICKS:
            return next_season(ctx, "the act to watch cannot be picked")
        seen(ctx, "theater story page")
        return click_once(ctx, ACT_ROW_X, first[0]) or State.ROUTE
    gold = read_number(ctx, GOLD_REGION)
    if gold is not None:
        data["gold"] = gold
    needs_ticket = ctx.find("theater_ticket_icon", WATCH_ICON_REGION) is not None
    free = ctx.find("theater_film_icon", WATCH_ICON_REGION) is not None
    if not needs_ticket and not free:
        # The button can be hidden a moment by a popup closing: the season is done only if it stays hidden.
        since = data.setdefault("no_watch_since", time.monotonic())
        if time.monotonic() - since < NO_WATCH_SECONDS:
            return State.ROUTE
        data.pop("no_watch_since", None)
        return next_season(ctx, "no act to watch")
    data.pop("no_watch_since", None)
    if needs_ticket:
        tickets = read_number(ctx, TICKETS_REGION)
        if data["free_only"] and not tickets:
            # Rewards can hand out tickets: the count is read again on every page, never assumed empty.
            return next_season(ctx, "the next act needs a ticket")
        if tickets == 0:
            refused = buy_allowed(ctx)
            if refused is not None:
                ctx.logger.info("theater: no ticket left and none bought (%s), only free acts from now on", refused)
                data["free_only"] = True
                data["free_reason"] = refused
                return next_season(ctx, "the next act needs a ticket")
            seen(ctx, "theater no ticket")
            now = time.monotonic()
            if now - data.get("plus_at", 0.0) < WATCH_WAIT_SECONDS:
                # The purchase popup of the last click, or the ticket just bought, is on its way.
                return State.ROUTE
            data["plus_at"] = now
            return click_once(ctx, *TICKET_PLUS_POINT) or State.ROUTE
    seen(ctx, "theater story page")
    now = time.monotonic()
    if now - data.get("watch_at", 0.0) < WATCH_WAIT_SECONDS:
        # The popup of the last click can take a moment: one click per wait.
        return State.ROUTE
    data["watch_at"] = now
    data["watch_clicks"] = data.get("watch_clicks", 0) + 1
    if data["watch_clicks"] > MAX_WATCH_CLICKS:
        return next_season(ctx, "Watch does nothing")
    return click_once(ctx, *WATCH_POINT) or State.ROUTE


def carousel(ctx: Context) -> State:
    """On the carousel: bring the story the bot is on to the front, then enter it."""
    data = theater_data(ctx)
    front = story_on_carousel(ctx.frame)
    seen(ctx, "theater carousel")
    if not data.get("located") and front is not None:
        # Started inside the theater: go on from the story the game is on, not from the first one.
        # The carousel slides in when the bot comes back to it: the dot is trusted once it stays put.
        now = time.monotonic()
        first = data.get("front_seen")
        if first is None or first[0] != front:
            data["front_seen"] = (front, now)
            return State.ROUTE
        if now - first[1] < CAROUSEL_SETTLE_SECONDS:
            return State.ROUTE
        data.pop("front_seen", None)
        data["located"] = True
        data["story"] = front
        ctx.logger.info("theater: going on from story %d", front + 1)
    if front is None or front == data["story"]:
        data["entered_story"] = data["story"]
        data["season_clicked"] = None
        data["list_end"] = False
        data["list_scrolls"] = 0
        data.pop("list_before", None)
        return click_once(ctx, *ENTER_POINT) or State.ROUTE
    return click_once(ctx, *(NEXT_STORY_POINT if front < data["story"] else PREVIOUS_STORY_POINT)) or State.ROUTE


def try_video_skip(ctx: Context, data: dict) -> bool:
    """Skip a video playing inside an act, and return whether a skip was tried this tick.

    A screen is taken for a video once the dialogue's log icon has been gone for a moment.
    A failed try is not repeated for a while: the dialogue taps go on meanwhile.
    """
    now = time.monotonic()
    since = data.setdefault("video_since", now)
    if now - since < VIDEO_WAIT_SECONDS or now - data.get("video_tried_at", 0.0) < VIDEO_RETRY_SECONDS:
        return False
    data["video_tried_at"] = now
    if skip_video(ctx):
        ctx.logger.info("theater: video skipped")
        data.pop("video_since", None)
    else:
        ctx.logger.info("theater: no video to skip here")
    return True


def theater(ctx: Context, unknown, at_lobby) -> State:
    """One tick of the theater. `unknown` handles fights and stray screens, `at_lobby` tells the lobby."""
    data = theater_data(ctx)
    state = popups(ctx)
    if state is not None:
        return state
    if ctx.find("theater_cast_board", CAST_BOARD_REGION) is not None:
        return story_page(ctx)
    if ctx.find("theater_prologue", PROLOGUE_REGION) is not None:
        return carousel(ctx)
    dialogue = ctx.find("theater_dialogue_log", DIALOGUE_LOG_REGION) is not None
    # A black screen is a fade inside an act (an Esc there would leave the act, and maybe its ticket), or a loading
    # screen between two menus (the lobby to Side Story): it starts no act by itself, only the dialogue does.
    dark = float(ctx.frame.mean()) < DARK_LEVEL
    if dialogue:
        # An act already playing when the bot started: it is watched to its end.
        start_act(ctx)
    if data.get("playing_since") and not dialogue and (at_lobby(ctx) or ctx.find("theater_tile", THEATER_TILE_REGION) is not None):
        # The lobby or Side Story in view: no act is playing, whatever the bot thought (once a loading screen
        # taken for an act's fade had it tap the Side Story menu for ten minutes).
        ctx.logger.info("theater: no act is playing after all, back to the menus")
        data["playing_since"] = 0
    playing = data.get("playing_since")
    if dark and not playing:
        # A loading screen: wait for what comes after it, without a tap or an Esc.
        return State.ROUTE
    if playing and time.monotonic() - playing < MAX_ACT_SECONDS:
        data["act_seen"] = True
        fight = ctx.find("pause_hud", (1228, 14, 44, 50)) is not None or ctx.find("hourglass", (12, 55, 42, 44)) is not None
        if fight and not dialogue:
            # A fight inside the act: the route's fight handling keeps the game's auto on.
            return unknown(ctx)
        if not dialogue and time.monotonic() - ctx.route_data.get("fight_at", -SKILL_ANIMATION_SECONDS) < SKILL_ANIMATION_SECONDS:
            # A skill's animation hides the fight's buttons: neither a video to skip nor a dialogue to tap.
            data.pop("video_since", None)
            return State.ROUTE
        ctx.progressed = True
        if dialogue or dark:
            data.pop("video_since", None)
        elif try_video_skip(ctx, data):
            return State.ROUTE
        # Every tick, without the pause that keeps other screens from a double click: the lines go by fast.
        for _ in range(DIALOGUE_TAPS_PER_TICK):
            ctx.session.input.tap(*DIALOGUE_POINT)
            ctx.wait(DIALOGUE_TAP_SECONDS)
        return State.ROUTE
    if ctx.find("theater_tile", THEATER_TILE_REGION) is not None:
        # Started outside the theater: every story is checked from the first one.
        data["located"] = True
        seen(ctx, "side story")
        return click_once(ctx, 1160, 320) or State.ROUTE
    if at_lobby(ctx):
        data["located"] = True
        seen(ctx, "lobby")
        return click_once(ctx, *SIDE_STORY_POINT) or State.ROUTE
    return unknown(ctx)

"""ROUTE: the long reroll, a fixed list of steps done one after the other on the same account.

Each step is a function that looks at the screen, does one action and returns the next state,
or DONE once its goal is reached. The step number is saved, so a restart carries on from it.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2

from core.config import State
from core.heroes import UNRANKED, chest_rank, load_ranking
from core.state import Context
from core.vision import Region
from states.story import (
    HOURGLASS_REGION,
    LOBBY_REGION,
    PAUSE_MENU_REGION,
    PAUSE_REGION,
    POPUP_TAPS,
    RESULTS_REGION,
    SUMMON_HEADER_REGION,
    QUIT_CANCEL_POINT,
    QUIT_REGION,
    RETURN_TO_GAME_POINT,
    SKILL_ANIMATION_SECONDS,
    TAPS,
    AUTO_POINT,
    _auto_is_off,
    click_once,
    do_tap,
    seen,
)

from states.arena import arena
from states.shop import shop
from states.huche import huche
from states.penguins import penguins
from states.gear import GEAR_GOALS, gear
from states.theater import theater

DONE = "done"
ESC = "esc"
AFTER_KEY_SECONDS = 0.6

Step = Callable[[Context], "State | str"]
Point = tuple[int, int]


def stage_order(stage: str) -> tuple[int, int] | None:
    """Return (chapter, stage) of '6th World Episode 3-3', or None if it cannot be read."""
    found = re.search(r"(\d+)-(\d+)$", stage)
    return (int(found.group(1)), int(found.group(2))) if found else None


def story_reached(current: str | None, target: str) -> bool:
    """Return whether the story stands at the target stage or past it."""
    if not current:
        return False
    now, goal = stage_order(current), stage_order(target)
    return now is not None and goal is not None and now >= goal


def story_to(target: str) -> Step:
    """A step that plays the story until the stage list shows the target stage."""

    def step(ctx: Context) -> State | str:
        if story_reached(ctx.story_reached, target):
            return DONE
        ctx.story_target = target
        return State.STORY

    step.__name__ = f"story to {target}"
    return step


def at_lobby(ctx: Context) -> bool:
    return ctx.find("episode", LOBBY_REGION) is not None


def go_lobby(ctx: Context) -> State:
    """Leave whatever menu is open with Esc, one press per tick, cancelling the quit popup it ends on."""
    if ctx.find("quit_popup", QUIT_REGION) is not None:
        seen(ctx, "quit popup")
        click_once(ctx, *QUIT_CANCEL_POINT)
        return State.ROUTE
    ctx.session.input.press(ESC)
    ctx.wait(AFTER_KEY_SECONDS)
    return State.ROUTE


@dataclass(frozen=True)
class Screen:
    """A screen of a step, recognized by a template in a region, and passed with a click."""

    name: str
    template: str
    region: Region
    point: Point | None = None


UNKNOWN_WAIT_SECONDS = 1.5
# A fight on the game's auto counts as progress for this long; past it, the watchdog takes over.
MAX_FIGHT_SECONDS = 20 * 60
# Fight screens closer than this belong to the same fight.
FIGHT_GAP_SECONDS = 30.0
MAX_UNKNOWN_ESC = 4


def screens_step(name: str, done: Screen, screens: tuple[Screen, ...], start: Point) -> Step:
    """A step made of screens to click through, from the lobby, until the done screen shows.

    The screens are checked from the last to the first, so a popup over a page wins. Nothing
    recognized: wait, then Esc back to the lobby, where the step starts again from `start`.
    """

    def step(ctx: Context) -> State | str:
        if ctx.find(done.template, done.region) is not None:
            seen(ctx, done.name)
            return DONE
        for screen in reversed(screens):
            match = ctx.find(screen.template, screen.region)
            if match is not None:
                seen(ctx, screen.name)
                ctx.route_data.pop("unknown_since", None)
                click_once(ctx, *(screen.point or match.center))
                return State.ROUTE
        if at_lobby(ctx):
            seen(ctx, "lobby")
            click_once(ctx, *start)
            return State.ROUTE
        return unknown(ctx)

    step.__name__ = name
    return step


def unknown(ctx: Context) -> State:
    """Nothing known in view: wait out a fight, pass a common popup, otherwise Esc back toward the lobby."""
    if ctx.find("abyss_next", (1110, 620, 150, 82)) is not None:
        # An Abyss floor's CLEAR, left over from the climb: Back leaves it.
        return act(ctx, "abyss clear", ABYSS_BACK_POINT)
    if ctx.find("fight_clear", (500, 250, 290, 80)) is not None:
        # The pause icon still shows on a won fight's CLEAR: a tap goes on to the results.
        return act(ctx, "fight clear", CENTER_POINT)
    if ctx.find("pause_menu", PAUSE_MENU_REGION) is not None:
        # Opened by an Esc during a fight: back to the fight, never Yield.
        return act(ctx, "pause menu", RETURN_TO_GAME_POINT)
    if ctx.find("pause_hud", PAUSE_REGION) is not None or ctx.find("hourglass", HOURGLASS_REGION) is not None:
        seen(ctx, "fight")
        now = time.monotonic()
        if "fight_since" not in ctx.route_data or now - ctx.route_data.get("fight_at", -FIGHT_GAP_SECONDS) >= FIGHT_GAP_SECONDS:
            ctx.route_data["fight_since"] = now
        ctx.route_data["fight_at"] = now
        ctx.route_data.pop("unknown_since", None)
        # A boss fight on auto can last more than the watchdog's minutes: it counts as progress, up to a limit.
        if now - ctx.route_data["fight_since"] < MAX_FIGHT_SECONDS:
            ctx.progressed = True
        # A kept account's climb relies on the game's auto: put it back if it is off.
        # Huche's mode expects a fight only after its teleport: one before is not its own, Auto is left alone.
        huche_fight = ctx.goal == "huche" and ctx.route_data.get("huche", {}).get("teleported", False)
        if (ctx.goal in ("climb", "theater", "arena") or huche_fight) and _auto_is_off(ctx):
            ctx.logger.info("%s: auto battle was off, turning it on", "abyss" if ctx.goal == "climb" else ctx.goal)
            click_once(ctx, *AUTO_POINT)
        return State.ROUTE
    # A skill's animation hides the fight's buttons: no Esc then, it would open the pause menu.
    if time.monotonic() - ctx.route_data.get("fight_at", -SKILL_ANIMATION_SECONDS) < SKILL_ANIMATION_SECONDS:
        return State.ROUTE
    if do_tap(ctx, POPUP_TAPS + TAPS) is not None:
        ctx.route_data.pop("unknown_since", None)
        return State.ROUTE
    now = time.monotonic()
    since = ctx.route_data.setdefault("unknown_since", now)
    # Each Esc of the step waits longer: on a slow PC a page can still be loading after the first wait,
    # and an Esc then cancels it, which looped forever on the Exchange of a friend's PC.
    if now - since < UNKNOWN_WAIT_SECONDS * (1 + ctx.route_data.get("escapes", 0)):
        return State.ROUTE
    ctx.route_data["unknown_since"] = now
    ctx.route_data["escapes"] = ctx.route_data.get("escapes", 0) + 1
    if ctx.route_data["escapes"] > MAX_UNKNOWN_ESC:
        ctx.route_data["escapes"] = 0
        ctx.logger.info("route: unknown screen, tapping the centre")
        click_once(ctx, 640, 400)
        return State.ROUTE
    ctx.logger.info("route: unknown screen, pressing Esc")
    return go_lobby(ctx)


def around(x: int, y: int, w: int, h: int, pad: int = 20) -> Region:
    """Return a region with some room around a template's place."""
    return (max(0, x - pad), max(0, y - pad), w + 2 * pad, h + 2 * pad)


MENU_POINT = (1240, 33)
SKIP_TUTORIAL = Screen("skip tutorial", "skip_tutorial", around(1092, 18, 140, 32))

# The hero list opens on Arbiter Vildred, and Select Hero is only clicked while his name shows.
RECRUIT_VILDRED = screens_step(
    "recruit Arbiter Vildred",
    done=Screen("blessing taken", "blessing_done", around(1000, 104, 220, 30)),
    screens=(
        Screen("menu", "menu_recruit", around(1160, 278, 90, 30), (1203, 265)),
        Screen("recruit page", "blessing_card", around(535, 120, 220, 30), (644, 340)),
        Screen("moonlight's blessing", "blessing_vildred", around(30, 128, 190, 36), (775, 662)),
        Screen("blessing confirm", "blessing_confirm", around(500, 138, 272, 34), (746, 554)),
        SKIP_TUTORIAL,
    ),
    start=MENU_POINT,
)

HERO_POINT = (984, 650)
HERO_SEARCH_POINT = (966, 92)
SEARCH_FIELD_POINT = (740, 150)
SEARCH_RESULT_POINT = (860, 269)
MAIN_TAB_POINT = (42, 97)
LEVEL_UP_POINT = (626, 640)
AUTO_SELECT_POINT = (1095, 662)
LEVEL_CONFIRM_POINT = (748, 662)
TAP_TO_CLOSE_POINT = (640, 693)
AWAKEN_PAGE_POINT = (838, 640)
AWAKEN_POINT = (1108, 662)
EXCHANGE_CONFIRM_POINT = (740, 594)
NAME_REGION = around(104, 114, 250, 38, pad=12)


def find(ctx: Context, template: str, x: int, y: int, w: int, h: int, pad: int = 20):
    """Look for a template around its usual place."""
    return ctx.find(template, around(x, y, w, h, pad))


def act(ctx: Context, screen: str, point: Point) -> State:
    """Note the screen, click once, and stay on the route."""
    seen(ctx, screen)
    ctx.route_data.pop("unknown_since", None)
    # A click on a screen other than a fight: the next fight is a new one, with its own time limit.
    ctx.route_data.pop("fight_since", None)
    click_once(ctx, *point)
    return State.ROUTE


def close_then_done(ctx: Context, screen: str, point: Point) -> State:
    """Close the popup that ends a step. The step is done once it has gone, or the next step would see it."""
    ctx.route_data["closing"] = True
    return act(ctx, screen, point)


def open_hero(ctx: Context, name_template: str, search_text: str) -> State | None:
    """Get to a hero's page through the hero search. Returns None once it is in view."""
    # A tutorial blocks every other button until it is skipped.
    if find(ctx, "skip_tutorial", 1092, 18, 140, 32) is not None:
        return act_and_settle(ctx, "skip tutorial", (1162, 34))
    if not settle(ctx):
        return State.ROUTE
    if ctx.find(name_template, NAME_REGION) is not None:
        return None
    if find(ctx, "hero_search_results", 655, 206, 130, 26) is not None:
        return act(ctx, "hero search results", SEARCH_RESULT_POINT)
    if find(ctx, "hero_search_go", 990, 135, 90, 30) is not None:
        if ctx.route_data.get("typed") != search_text:
            act(ctx, "hero search", SEARCH_FIELD_POINT)
            ctx.session.input.type_text(search_text)
            ctx.route_data["typed"] = search_text
            ctx.wait(AFTER_KEY_SECONDS)
            return State.ROUTE
        return act(ctx, "hero search", (1022, 150))
    if find(ctx, "hero_search", 925, 78, 100, 28) is not None:
        ctx.route_data.pop("typed", None)
        return act(ctx, "hero page", HERO_SEARCH_POINT)
    if find(ctx, "skip_tutorial", 1092, 18, 140, 32) is not None:
        return act(ctx, "skip tutorial", (1162, 34))
    if at_lobby(ctx):
        return act(ctx, "lobby", HERO_POINT)
    return unknown(ctx)


def level_up(name_template: str, search_text: str) -> Step:
    """A step that takes a hero to level 50 with the penguins the game picks by itself."""

    def step(ctx: Context) -> State | str:
        if find(ctx, "level_success", 530, 155, 190, 36) is not None:
            return close_then_done(ctx, "level up success", TAP_TO_CLOSE_POINT)
        if ctx.route_data.get("closing"):
            return DONE
        if find(ctx, "level_auto_select", 1040, 648, 110, 26) is not None:
            # Auto Select, then Level Up; if the page is still there, pick the penguins again.
            if not settle(ctx):
                return State.ROUTE
            if not ctx.route_data.get("auto_selected"):
                ctx.route_data["auto_selected"] = True
                return act_and_settle(ctx, "level up page", AUTO_SELECT_POINT)
            ctx.route_data["auto_selected"] = False
            return act_and_settle(ctx, "level up page", LEVEL_CONFIRM_POINT)
        state = open_hero(ctx, name_template, search_text)
        if state is not None:
            return state
        if find(ctx, "hero_level_max", 104, 192, 100, 30) is not None:
            return DONE
        if find(ctx, "hero_level_up", 596, 670, 62, 24) is not None:
            return act(ctx, "hero page", LEVEL_UP_POINT)
        return act(ctx, "hero page", MAIN_TAB_POINT)

    step.__name__ = f"level up {search_text}"
    return step


def awaken_once(number: int) -> Step:
    """A step that awakens Arbiter Vildred once more. Four of them make him 4 stars, and no more."""

    def step(ctx: Context) -> State | str:
        if find(ctx, "awaken_locked", 520, 200, 240, 40) is not None:
            return ctx.halt("route: awakening is still locked, chapter 3 is not cleared")
        if find(ctx, "awakening_reward", 416, 152, 250, 36) is not None:
            return close_then_done(ctx, "awakening reward", TAP_TO_CLOSE_POINT)
        if ctx.route_data.get("closing"):
            return DONE
        if find(ctx, "item_exchange", 290, 88, 150, 32) is not None:
            return act(ctx, "item exchange", EXCHANGE_CONFIRM_POINT)
        if find(ctx, "skip_tutorial", 1092, 18, 140, 32) is not None:
            return act(ctx, "skip tutorial", (1162, 34))
        if (find(ctx, "awaken_button", 1050, 648, 116, 28) is not None
                or find(ctx, "awaken_use_chest", 1050, 648, 116, 28) is not None):
            return act(ctx, "awakening page", AWAKEN_POINT)
        state = open_hero(ctx, "hero_name_vildred", "Arbiter Vildred")
        if state is not None:
            return state
        if find(ctx, "hero_awaken", 808, 670, 60, 24) is not None:
            return act(ctx, "hero page", AWAKEN_PAGE_POINT)
        return act(ctx, "hero page", MAIN_TAB_POINT)

    step.__name__ = f"awaken Arbiter Vildred ({number} of 4)"
    return step


# A hero other than Vildred may lack the runes: an awakening that does not come after this many
# clicks on Awaken is given up, and the route goes on.
MAX_AWAKEN_TRIES = 8


def awaken_hero(name_template: str, search_text: str, times: int) -> Step:
    """A step that awakens a hero up to `times` times, as far as the runes go."""

    def step(ctx: Context) -> State | str:
        if find(ctx, "awaken_locked", 520, 200, 240, 40) is not None:
            return ctx.halt("route: awakening is still locked, chapter 3 is not cleared")
        if not settle(ctx):
            return State.ROUTE
        done = ctx.route_data.get("awakened", 0)
        if find(ctx, "awakening_reward", 416, 152, 250, 36) is not None:
            # The popup can outlast a click on it: one awakening per popup, however long it stays.
            if not ctx.route_data.get("reward_open"):
                ctx.route_data["reward_open"] = True
                ctx.route_data["awakened"] = done + 1
                ctx.route_data["tries"] = 0
                ctx.logger.info("%s awakened %d of %d", search_text, done + 1, times)
            return act_and_settle(ctx, "awakening reward", TAP_TO_CLOSE_POINT)
        ctx.route_data["reward_open"] = False
        if done >= times:
            return DONE
        if find(ctx, "item_exchange", 290, 88, 150, 32) is not None:
            return act_and_settle(ctx, "item exchange", EXCHANGE_CONFIRM_POINT)
        if find(ctx, "skip_tutorial", 1092, 18, 140, 32) is not None:
            return act_and_settle(ctx, "skip tutorial", (1162, 34))
        if (find(ctx, "awaken_button", 1050, 648, 116, 28) is not None
                or find(ctx, "awaken_use_chest", 1050, 648, 116, 28) is not None):
            tries = ctx.route_data.get("tries", 0) + 1
            ctx.route_data["tries"] = tries
            if tries > MAX_AWAKEN_TRIES:
                ctx.logger.info("%s: awakening %d does not come, not enough runes: going on", search_text, done + 1)
                return DONE
            return act_and_settle(ctx, "awakening page", AWAKEN_POINT)
        state = open_hero(ctx, name_template, search_text)
        if state is not None:
            return state
        if find(ctx, "hero_awaken", 808, 670, 60, 24) is not None:
            return act_and_settle(ctx, "hero page", AWAKEN_PAGE_POINT)
        return act_and_settle(ctx, "hero page", MAIN_TAB_POINT)

    step.__name__ = f"awaken {search_text} up to {times} times"
    return step


def then(first: Step, second: Step) -> Step:
    """One step made of two: the second starts with fresh scratch data once the first is done."""

    def step(ctx: Context) -> State | str:
        if not ctx.route_data.get("first_done"):
            result = first(ctx)
            if result != DONE:
                return result
            ctx.route_data = {"first_done": True}
            return State.ROUTE
        return second(ctx)

    step.__name__ = f"{first.__name__}, then {second.__name__}"
    return step


BATTLE_POINT = (1203, 325)
SPIRIT_ALTAR_POINT = (167, 430)
DARK_EASY_POINT = (880, 182)
START_POINT = (1129, 657)
QUICK_POINT = (1065, 657)
QUICK_CLOSE_POINT = (850, 558)
QUICK_BUY_POINT = (1234, 658)
BUY_POINT = (748, 509)
MVP_CONFIRM_POINT = (1168, 660)
LEAVE_POINT = (126, 660)


def to_dark_altar(ctx: Context) -> State | None:
    """Get to the team screen of Dark Spirit Altar - Easy. Returns None once it is in view."""
    if ctx.find("auto_assign", (540, 190, 200, 60)) is not None:
        # Here the best CP heroes are the right team.
        return act(ctx, "auto assign", AUTO_ASSIGN_CONFIRM_POINT)
    if find(ctx, "ready_dark_altar", 925, 78, 150, 22) is not None:
        return None
    if find(ctx, "altar_dark_easy", 790, 170, 180, 24) is not None:
        return act(ctx, "altar list", DARK_EASY_POINT)
    if find(ctx, "battle_spirit_altar", 75, 188, 110, 26) is not None:
        return act(ctx, "battle page", SPIRIT_ALTAR_POINT)
    if at_lobby(ctx):
        return act(ctx, "lobby", BATTLE_POINT)
    return unknown(ctx)


def after_fight(ctx: Context) -> State | str | None:
    """Pass the screens that follow a fight. DONE once it is left, None if none of them shows."""
    if find(ctx, "fight_mvp_confirm", 1125, 648, 90, 26) is not None:
        return act(ctx, "fight results", MVP_CONFIRM_POINT)
    if find(ctx, "fight_leave", 90, 646, 75, 28) is not None:
        act(ctx, "after the fight", LEAVE_POINT)
        return DONE
    return None


def altar_run(name: str, skip_if_quick: bool) -> Step:
    """A step that fights Dark Spirit Altar - Easy once, on the game's auto."""

    def step(ctx: Context) -> State | str:
        state = after_fight(ctx)
        if state is not None:
            return state if ctx.route_data.get("started") else (State.ROUTE if state == DONE else state)
        state = to_dark_altar(ctx)
        if state is not None:
            return state
        if skip_if_quick and find(ctx, "ready_quick", 1095, 640, 85, 36) is not None:
            return DONE
        if find(ctx, "ready_start", 1090, 644, 70, 28) is not None:
            ctx.route_data["started"] = True
            return act(ctx, "dark altar", START_POINT)
        return unknown(ctx)

    step.__name__ = name
    return step


def altar_quick(name: str) -> Step:
    """A step that runs one Quick Battling x10 on Dark Spirit Altar - Easy."""

    def step(ctx: Context) -> State | str:
        if find(ctx, "quick_done", 585, 148, 235, 22) is not None:
            return close_then_done(ctx, "quick battling done", QUICK_CLOSE_POINT)
        if ctx.route_data.get("closing"):
            return DONE
        if find(ctx, "quick_progress", 630, 150, 200, 22) is not None:
            seen(ctx, "quick battling")
            return State.ROUTE
        state = to_dark_altar(ctx)
        if state is not None:
            return state
        if find(ctx, "ready_quick", 1095, 640, 85, 36) is not None:
            ctx.route_data["start_seen"] = 0
            return act(ctx, "dark altar", QUICK_POINT)
        if find(ctx, "ready_start", 1090, 644, 70, 28) is not None:
            # Start in place of Quick Battling: these ten runs were used, say before a restart.
            seen_start = ctx.route_data.get("start_seen", 0) + 1
            ctx.route_data["start_seen"] = seen_start
            if seen_start >= QUICK_USED_SIGHTINGS:
                ctx.logger.info("route: Quick Battling already used, going on")
                return DONE
            return State.ROUTE
        return unknown(ctx)

    step.__name__ = name
    return step


QUICK_USED_SIGHTINGS = 3


def altar_buy(ctx: Context) -> State | str:
    """Buy ten more Quick Battling chances, only at 50 skystones."""
    if find(ctx, "quick_buy_title", 540, 145, 200, 26) is not None:
        if find(ctx, "quick_buy_price", 630, 495, 250, 30) is None:
            return ctx.halt("route: Quick Battling chances are not priced 50 skystones, nothing bought")
        ctx.route_data["bought"] = True
        return act(ctx, "buy quick battling", BUY_POINT)
    state = to_dark_altar(ctx)
    if state is not None:
        return state
    if find(ctx, "ready_quick", 1095, 640, 85, 36) is not None:
        return DONE
    if find(ctx, "ready_start", 1090, 644, 70, 28) is not None:
        return act(ctx, "dark altar", QUICK_BUY_POINT)
    return unknown(ctx)


altar_buy.__name__ = "buy Quick Battling x10 for 50 skystones"


MILESTONE_POINT = (57, 185)
MILE_RECEIVE_POINT = (870, 613)
MILE_MIDDLE_HERO_POINT = (640, 300)
MILE_PICK_RECEIVE_POINT = (1118, 653)
MILE_SELECTOR_CONFIRM_POINT = (747, 512)
MILE_OBTAINED_POINT = (155, 660)
NEXT_POINT = (1133, 662)
CENTER_POINT = (640, 400)
EXCHANGE_POINT = (1215, 612)
SPEED_CRIT_POINT = (603, 330)
BUY_SET_POINT = (800, 509)
REWARD_CLOSE_POINT = (640, 528)
EQUIPMENT_TAB_POINT = (42, 175)
EQUIP_ALL_POINT = (631, 640)
EQUIP_CHANGE_POINT = (640, 626)
CHANGE_CONFIRM_POINT = (768, 528)
# A tier claim shows its popups within this time; a page still idle after it means not enough essence.
CLAIM_SETTLE_SECONDS = 4.0


def to_milestone(ctx: Context) -> State | None:
    """Get to the Milestone of Radiance page. Returns None once it is in view."""
    if find(ctx, "mile_title", 66, 20, 250, 28) is not None:
        return None
    if find(ctx, "mile_splash", 520, 558, 240, 24) is not None:
        return act(ctx, "milestone splash", TAP_TO_CLOSE_POINT)
    if at_lobby(ctx):
        return act(ctx, "lobby", MILESTONE_POINT)
    return unknown(ctx)


# The essence sits right of its icon, which moves with the width of the numbers.
MILE_ESSENCE_ICON_REGION = (700, 10, 300, 50)
MILE_ESSENCE_OFFSET = (20, 18, 50, 32)
MILE_COST_REGION = (650, 600, 80, 30)
# Landy's tier, and what she and her set (the next tier, 130) cost together.
LANDY_TIER_COST = 160
LANDY_AND_SET_COST = 290
MAX_MILESTONE_CLAIMS = 20
MAX_UNREAD = 5
COINS_REGION = (928, 18, 45, 32)
SET_PRICE = 60
COINS_WHITE_LEVEL = 180


def read_essence(ctx: Context) -> int | None:
    """Read the Radiance essence in the Milestone top bar, or None."""
    icon = ctx.find("mile_essence_icon", MILE_ESSENCE_ICON_REGION)
    if icon is None:
        return None
    dx, y, w, h = MILE_ESSENCE_OFFSET
    return read_number(ctx, (icon.x + dx, y, w, h))


def read_number(ctx: Context, region: Region) -> int | None:
    text = ctx.ocr.read(ctx.frame, region, whitelist="0123456789").strip()
    return int(text) if text.isdigit() else None


def popup_over_page(ctx: Context) -> bool:
    """Return whether the Milestone page is dimmed by something over it: its Receive button then looks darker."""
    x, y = MILE_RECEIVE_POINT
    return float(ctx.frame[y - 8:y + 8, x - 40:x + 40].mean()) < DIMMED_LEVEL


DIMMED_LEVEL = 120


def read_coins(ctx: Context) -> int | None:
    """Read the Radiance coins shown on the Milestone page, or None."""
    text = ctx.ocr.read(ctx.frame, COINS_REGION, whitelist="0123456789", white_level=COINS_WHITE_LEVEL).strip()
    return int(text) if text.isdigit() else None


# The three heroes of a chest: where to click, and where their stars and element show.
CHEST_COLUMNS = (
    ((235, 300), (40, 410, 200, 55)),
    (MILE_MIDDLE_HERO_POINT, (445, 410, 200, 55)),
    ((1045, 300), (845, 410, 200, 55)),
)
# A chest card's hero name sits under its stars, in short form: Charles for Closer Charles.
CHEST_NAME_DY = 42
CHEST_NAME_WIDTH = 300
CHEST_NAME_HEIGHT = 40
OBTAINED_ELEMENT_REGION = (58, 26, 90, 28)
# Wider than a summon's: Moonlight names such as Sylvan Sage Vivian run long.
OBTAINED_NAME_REGION = (24, 62, 430, 56)
MOONLIGHT_ELEMENTS = ("light", "dark")


def pick_chest_hero(ctx: Context) -> tuple[Point, dict]:
    """Choose a hero in a chest: the best ranked 5★ Moonlight if an upgraded chest shows any, otherwise the middle one."""
    ranking = load_ranking()
    candidates = []
    for point, region in CHEST_COLUMNS:
        stars = len(ctx.library.find_every(ctx.frame, "star_card", region))
        dark = ctx.find("element_moon", region) is not None
        light = ctx.find("element_sun", region) is not None
        moonlight = dark or light
        details = {"stars": stars, "moonlight": moonlight}
        if moonlight and stars == 5:
            name_region = (region[0], region[1] + CHEST_NAME_DY, CHEST_NAME_WIDTH, CHEST_NAME_HEIGHT)
            details["name"] = " ".join(ctx.ocr.read(ctx.frame, name_region).split())
            element = "dark" if dark and not light else "light" if light and not dark else None
            details["rank"] = chest_rank(details["name"], ranking, element)
        candidates.append((point, details))
    # Among the 5★ Moonlight, the best ranked. One not recognized ranks 0, above them all. The middle first on a tie.
    in_order = (candidates[1], candidates[0], candidates[2])
    moonlights = [c for c in in_order if c[1]["moonlight"] and c[1]["stars"] == 5]
    best = min(moonlights, key=lambda c: UNRANKED if c[1].get("rank") is None else c[1]["rank"]) if moonlights else candidates[1]
    ctx.logger.info("chest heroes: %s, taking %s", [c[1] for c in candidates], best[1])
    if any(c[1]["moonlight"] for c in candidates) and not ctx.route_data.get("chest_shot"):
        # Keep a screenshot of every upgraded chest that offers a Moonlight hero.
        ctx.route_data["chest_shot"] = True
        ctx.logger.warning("upgraded chest with a Moonlight hero: %s", save_shot(ctx, "chest_pick"))
    return best


def save_shot(ctx: Context, label: str) -> Path:
    """Keep a picture of the game, next to the summon results."""
    folder = ctx.config.logging.log_dir / "results"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d_%H%M%S}_{label}.png"
    cv2.imwrite(str(path), ctx.frame)
    return path


def note_chest_hero(ctx: Context, picked: dict | None) -> None:
    """Count a 5★ Moonlight from a chest like one from a pull: the keep decision covers both."""
    element = (ctx.ocr.read(ctx.frame, OBTAINED_ELEMENT_REGION).split() or [""])[0].lower()
    stars = picked["stars"] if picked else None
    moonlight = (picked or {}).get("moonlight") or element in MOONLIGHT_ELEMENTS
    if not moonlight:
        return
    summon = sys.modules["states.summon"]
    name = summon._read_name(ctx, OBTAINED_NAME_REGION)
    ctx.logger.warning("Moonlight from a Milestone chest: %s, %s stars read, see %s", name, stars,
                       save_shot(ctx, "chest_obtained"))
    ctx.route_data.pop("chest_shot", None)
    if stars != 5:
        # A sparkle can hide a star, and a restart loses them: unsure, which keeps the account.
        ctx.summon.unsure.append(f"{name} ML from a chest")
    else:
        ctx.summon.moonlight_5star.append(name)
    ctx.save_route_pulls()


def claim_milestone(name: str, landy_with_set: bool = False) -> Step:
    """A step that claims Milestone of Radiance tiers while the essence read covers the cost shown.

    A hero chest shows three heroes: the middle one is taken. With `landy_with_set`, Landy is only
    claimed when the essence also pays for her set: she is no use without it.
    """

    def step(ctx: Context) -> State | str:
        if find(ctx, "mile_selector", 560, 184, 160, 28) is not None:
            return act(ctx, "use selector", MILE_SELECTOR_CONFIRM_POINT)
        if find(ctx, "mile_pick", 700, 642, 180, 24) is not None:
            point, candidate = pick_chest_hero(ctx)
            ctx.route_data["picked"] = candidate
            act(ctx, "pick a hero", point)
            ctx.wait(AFTER_KEY_SECONDS)
            return act(ctx, "pick a hero", MILE_PICK_RECEIVE_POINT)
        if find(ctx, "mile_obtained", 60, 640, 190, 42) is not None:
            note_chest_hero(ctx, ctx.route_data.pop("picked", None))
            return act(ctx, "hero obtained", MILE_OBTAINED_POINT)
        if find(ctx, "mile_title", 66, 20, 250, 28) is not None and not popup_over_page(ctx):
            claimed_at = ctx.route_data.get("claimed_at")
            now = time.monotonic()
            if claimed_at is not None and now - claimed_at < CLAIM_SETTLE_SECONDS:
                return State.ROUTE
            seen_at = ctx.route_data.setdefault("page_seen_at", now)
            if now - seen_at < PAGE_SETTLE_SECONDS:
                # The page slides in: numbers read now belong to the screen before.
                return State.ROUTE
            essence, cost = read_essence(ctx), read_number(ctx, MILE_COST_REGION)
            claims = ctx.route_data.get("claims", 0)
            unread = ctx.route_data.get("unread", 0)
            if (essence is None or cost is None) and unread < MAX_UNREAD:
                # The numbers can still be drawing in: read again before giving up.
                ctx.route_data["unread"] = unread + 1
                return State.ROUTE
            ctx.route_data["unread"] = 0
            # Only before a claim: right after Landy's, her tier still shows while the essence is spent.
            if (landy_with_set and claims == 0 and cost == LANDY_TIER_COST
                    and (essence is None or essence < LANDY_AND_SET_COST)):
                return ctx.halt(f"route: {essence} Radiance essence read, Landy and her set need "
                                f"{LANDY_AND_SET_COST}: nothing claimed")
            if essence is None or cost is None or essence < cost or claims >= MAX_MILESTONE_CLAIMS:
                ctx.logger.info("milestone: %s essence for a tier at %s, done", essence, cost)
                return DONE
            ctx.route_data["claims"] = claims + 1
            ctx.route_data["claimed_at"] = now
            return act(ctx, "milestone", MILE_RECEIVE_POINT)
        if ctx.route_data.get("claimed_at") is not None:
            # A claim opened chests and popups: tap through them, Next on a hero's reveal.
            if find(ctx, "mile_splash", 520, 558, 240, 24) is not None:
                return act(ctx, "milestone splash", TAP_TO_CLOSE_POINT)
            seen(ctx, "milestone rewards")
            click_once(ctx, *CENTER_POINT)
            ctx.wait(AFTER_KEY_SECONDS)
            click_once(ctx, *NEXT_POINT)
            return State.ROUTE
        state = to_milestone(ctx)
        return state if state is not None else State.ROUTE

    step.__name__ = name
    return step


def buy_gear_set(ctx: Context) -> State | str:
    """Buy one Speed/Critical equipment set with Radiance coins, only at its 60 coin price."""
    if find(ctx, "received_reward", 540, 152, 200, 30) is not None:
        return close_then_done(ctx, "set received", REWARD_CLOSE_POINT)
    if ctx.route_data.get("closing"):
        return DONE
    if find(ctx, "exchange_buy_set", 640, 364, 280, 22) is not None:
        if find(ctx, "exchange_buy_price", 610, 494, 290, 30) is None:
            return ctx.halt("route: the equipment set is not priced 60 coins, nothing bought")
        return act(ctx, "buy the set", BUY_SET_POINT)
    if find(ctx, "exchange_speed_crit", 512, 164, 180, 22) is not None:
        return act(ctx, "exchange", SPEED_CRIT_POINT)
    state = to_milestone(ctx)
    if state is not None:
        return state
    coins = read_coins(ctx)
    if coins is None or coins < SET_PRICE:
        return ctx.halt(f"route: {coins} Radiance coins read, the set costs {SET_PRICE}: nothing bought")
    return act(ctx, "milestone", EXCHANGE_POINT)


buy_gear_set.__name__ = "buy the Speed/Critical equipment set"


def equip_all(name_template: str, search_text: str) -> Step:
    """A step that puts the best equipment on a hero with Equip All."""

    def step(ctx: Context) -> State | str:
        if find(ctx, "equip_change", 595, 166, 90, 30) is not None:
            return act(ctx, "change equipment", CHANGE_CONFIRM_POINT)
        if find(ctx, "equip_all_popup", 585, 44, 110, 30) is not None:
            return act(ctx, "equip all", EQUIP_CHANGE_POINT)
        state = open_hero(ctx, name_template, search_text)
        if state is not None:
            return state
        # The Speed Set shows on the first or the second set line, depending on the other set.
        if ctx.find("hero_speed_set", (100, 600, 220, 90)) is not None or find(ctx, "no_better_equipment", 520, 658, 240, 26) is not None:
            return DONE
        if find(ctx, "equip_all", 600, 672, 66, 20) is not None:
            return act(ctx, "equipment tab", EQUIP_ALL_POINT)
        return act(ctx, "hero page", EQUIPMENT_TAB_POINT)

    step.__name__ = f"equip {search_text}"
    return step


ABYSS_CARD_POINT = (498, 400)
ABYSS_FLOOR_POINT = (1141, 240)
ABYSS_SELECT_TEAM_POINT = (1131, 661)
ABYSS_START_POINT = (1151, 658)
NEXT_FLOOR_POINT = (1185, 661)
ABYSS_BACK_POINT = (147, 660)
TRY_AGAIN_POINT = (1180, 661)
AUTO_ASSIGN_CANCEL_POINT = (532, 495)
AUTO_ASSIGN_CONFIRM_POINT = (747, 495)
EMPTY_SLOT_POINT = (575, 437)
# The back place, free while Vildred stands alone at the front.
FREE_SLOT_POINT = (425, 365)
BATTLE_DRAG = ((1100, 430), (200, 430))
LIST_REGION = (1030, 150, 250, 520)
TEAM_REGION = (150, 180, 600, 420)
PLUS_REGION = (900, 150, 120, 520)
CLEAR_FLOOR_REGION = (560, 425, 160, 45)
MAX_FAILS_IN_A_ROW = 10
# Climbing the Abyss of a kept account: a floor lost this often needs a stronger team, not more tries.
MAX_KEPT_FAILS_IN_A_ROW = 3
# The Lobby button of a lost fight's screen, the same in the story and the Abyss.
FAILED_LOBBY_POINT = (268, 660)


STILL_SECONDS = 0.5
STILL_DIFFERENCE = 2.0


def still(ctx: Context) -> bool:
    """Return whether the screen has not moved since a read at least STILL_SECONDS ago."""
    now = time.monotonic()
    small = cv2.resize(ctx.frame, (160, 90)).astype(int)
    previous = ctx.route_data.get("still_frame")
    if previous is None or now - ctx.route_data.get("still_at", 0.0) < STILL_SECONDS:
        if previous is None:
            ctx.route_data["still_frame"], ctx.route_data["still_at"] = small, now
        return False
    moved = float(abs(previous - small).mean()) >= STILL_DIFFERENCE
    ctx.route_data["still_frame"], ctx.route_data["still_at"] = small, now
    if moved:
        return False
    ctx.route_data.pop("still_frame", None)
    return True


# How long the Abyss floor list takes to scroll to the current floor.
FLOOR_LIST_SECONDS = 2.5
# A page seen again after this gap counts as newly opened.
SETTLE_GAP_SECONDS = 1.0


def settled_on(ctx: Context, key: str, seconds: float) -> bool:
    """Return whether a page has been in view for `seconds`, counted again each time it reopens."""
    now = time.monotonic()
    first, last = ctx.route_data.get(key, (now, now))
    if now - last > SETTLE_GAP_SECONDS:
        first = now
    ctx.route_data[key] = (first, now)
    return now - first >= seconds


# The row of card titles on the Battle page.
BATTLE_CARD_TITLES = (0, 160, 1280, 80)


def to_abyss(ctx: Context) -> State | None:
    """Get to the Abyss team screen. Returns None once it is in view."""
    if ctx.find("auto_assign", (540, 190, 200, 60)) is not None:
        # The Abyss team is made by hand: Vildred alone, then Landy.
        return act(ctx, "auto assign", AUTO_ASSIGN_CANCEL_POINT)
    if find(ctx, "ready_abyss", 940, 78, 60, 22) is not None or find(ctx, "team_list", 1195, 84, 60, 22) is not None:
        return None
    # These pages never stand still (fire, glowing monster): a fixed wait instead of a still screen.
    if find(ctx, "abyss_select_team", 1060, 646, 125, 30) is not None:
        # The floor list scrolls to the current floor when it opens: click once it is there.
        if not settled_on(ctx, "abyss_floors", FLOOR_LIST_SECONDS):
            return State.ROUTE
        return act(ctx, "abyss floors", ABYSS_SELECT_TEAM_POINT)
    if find(ctx, "abyss_statistics", 580, 638, 150, 48) is not None:
        # Still scrolling, past floors that show Replay instead of Select Team.
        seen(ctx, "abyss floors scrolling")
        return State.ROUTE
    if find(ctx, "abyss_progress", 1100, 92, 80, 22) is not None:
        if not settled_on(ctx, "abyss_page", PAGE_SETTLE_SECONDS):
            return State.ROUTE
        return act(ctx, "abyss page", ABYSS_FLOOR_POINT)
    # The cards before the Abyss differ from one account to the next: look along the whole row.
    match = ctx.find("battle_abyss", BATTLE_CARD_TITLES)
    if match is not None:
        return act(ctx, "battle page", (match.center[0] + 50, 400))
    if find(ctx, "battle_spirit_altar", 75, 188, 110, 26, pad=200) is not None:
        seen(ctx, "battle page")
        (x1, y1), (x2, y2) = BATTLE_DRAG
        ctx.session.input.drag(x1, y1, x2, y2)
        ctx.wait(AFTER_KEY_SECONDS)
        return State.ROUTE
    if at_lobby(ctx):
        return act(ctx, "lobby", BATTLE_POINT)
    return unknown(ctx)


def abyss_team(name: str, row_templates: tuple[str, ...], only_if_empty: bool) -> Step:
    """A step that puts one hero in the Abyss team: its row in the list, the plus beside it, then a free place."""

    def step(ctx: Context) -> State | str:
        state = to_abyss(ctx)
        if state is not None:
            return state
        if not settle(ctx):
            return State.ROUTE
        if ctx.route_data.get("placed"):
            return DONE
        if ctx.route_data.get("plus_clicked"):
            places = ctx.library.find_every(ctx.frame, "place_plus", TEAM_REGION)
            if places:
                ctx.route_data["placed"] = True
                return act_and_settle(ctx, "team place", min(places, key=lambda m: m.center[0]).center)
            ctx.route_data["plus_clicked"] = False
        if only_if_empty and find(ctx, "team_empty", 250, 632, 110, 60) is None:
            return DONE
        row = next((m for m in (ctx.find(n, LIST_REGION) for n in row_templates) if m is not None), None)
        if row is None:
            if find(ctx, "team_empty", 250, 632, 110, 60) is not None:
                # An empty team shows no list until a place is picked.
                return act_and_settle(ctx, "abyss team", EMPTY_SLOT_POINT)
            if find(ctx, "ready_abyss", 940, 78, 60, 22) is not None:
                # The floor's details hide the list: a free place brings it back.
                return act_and_settle(ctx, "abyss team", FREE_SLOT_POINT)
            return unknown(ctx)
        plus = ctx.find("list_plus", (PLUS_REGION[0], row.center[1] - 50, PLUS_REGION[2], 100))
        if plus is not None:
            ctx.route_data["plus_clicked"] = True
            return act_and_settle(ctx, "hero list", plus.center)
        return act_and_settle(ctx, "hero list", row.center)

    step.__name__ = name
    return step


def read_clear_floor(ctx: Context) -> int | None:
    """Read the floor number of an Abyss CLEAR screen, or None."""
    found = re.search(r"(\d+)", ctx.ocr.read(ctx.frame, CLEAR_FLOOR_REGION))
    return int(found.group(1)) if found else None


def abyss_climb(until_floor: int | None, keep_account: bool = False) -> Step:
    """A step that climbs the Abyss until a floor is cleared, or with no floor for as long as it wins.

    Lost fights are tried again. On the reroll route, after MAX_FAILS_IN_A_ROW in a row the account
    is reset. On a kept account the bot stops after MAX_KEPT_FAILS_IN_A_ROW instead.
    """

    def step(ctx: Context) -> State | str:
        # Floors past 50 cost energy: the same leaf, never skystones.
        refilled = sys.modules["states.story"].refill_energy(ctx)
        if refilled is not None:
            return State.ROUTE if refilled is State.STORY else refilled
        if find(ctx, "abyss_failed", 740, 142, 170, 32) is not None:
            if keep_account and not ctx.route_data.get("abyss_started"):
                # A lost fight the climb did not start, such as a story stage: never Try again, go to the lobby.
                return act(ctx, "lost fight of another mode", FAILED_LOBBY_POINT)
            if not ctx.route_data.get("counted_fail"):
                ctx.route_data["counted_fail"] = True
                ctx.route_data["fails"] = ctx.route_data.get("fails", 0) + 1
                ctx.logger.info("abyss: lost fight, %d in a row", ctx.route_data["fails"])
                if keep_account and ctx.on_floor_lost is not None:
                    ctx.on_floor_lost()
            if keep_account and ctx.route_data["fails"] >= MAX_KEPT_FAILS_IN_A_ROW:
                cleared = ctx.route_data.get("logged_floor")
                where = f"abyss floor {cleared + 1}" if cleared is not None else "the abyss"
                return ctx.halt(f"{ctx.route_data['fails']} lost fights in a row on {where}: the team needs to get stronger")
            if ctx.route_data["fails"] >= MAX_FAILS_IN_A_ROW:
                ctx.logger.warning("abyss: %d lost fights in a row, starting a new account", MAX_FAILS_IN_A_ROW)
                ctx.reset_pending = True
                return State.RESET_ACCOUNT
            return act(ctx, "abyss failed", TRY_AGAIN_POINT)
        ctx.route_data["counted_fail"] = False
        if find(ctx, "abyss_next", 1130, 640, 110, 42) is not None:
            ctx.route_data["fails"] = 0
            floor = read_clear_floor(ctx)
            if floor is not None and floor != ctx.route_data.get("logged_floor"):
                ctx.route_data["logged_floor"] = floor
                ctx.logger.info("abyss: floor %d cleared", floor)
                if keep_account and ctx.on_floor_won is not None:
                    ctx.on_floor_won()
            if floor is not None and until_floor is not None and floor >= until_floor:
                return DONE
            ctx.route_data["abyss_started"] = True
            return act(ctx, "abyss clear", NEXT_FLOOR_POINT)
        state = to_abyss(ctx)
        if state is not None:
            return state
        if find(ctx, "ready_start", 1090, 644, 90, 28) is not None:
            if keep_account and find(ctx, "team_empty", 250, 632, 110, 60) is not None:
                # The bot never picks the heroes of a kept account.
                return ctx.halt("the abyss team is empty: set it in the game, then start again")
            ctx.route_data["abyss_started"] = True
            return act(ctx, "abyss ready", ABYSS_START_POINT)
        return unknown(ctx)

    step.__name__ = f"climb the Abyss to floor {until_floor}" if until_floor is not None else "climb the Abyss as far as it wins"
    return step


REPUTATION_POINT = (952, 495)
REPUTATION_TABS = ((60, 110), (60, 190), (60, 272))
RECEIVE_ALL_POINT = (1127, 662)
REP_POPUP_CLOSE_POINT = (640, 558)
EPIC_PASS_POINT = (57, 262)
PASS_TAB_MEMORY = (102, 160)
PASS_TAB_FIRST_STEPS = (102, 222)
PASS_MISSIONS_POINT = (519, 116)
PASS_REWARDS_POINT = (325, 116)
PASS_MISSION_RECEIVE_POINT = (829, 280)
PASS_RANK_POINT = (398, 345)
PASS_UPGRADE_POINT = (1100, 578)
PASS_RANK_CLOSE_POINT = (640, 484)
PASS_UNLOCKED_CONFIRM_POINT = (640, 508)
PASS_CLAIM_POINT = (588, 345)
ORBIS_POINT = (896, 660)
ORBIS_HEIR_TAB = (164, 216)
ORBIS_HEIR_RECEIVE = (1110, 361)
ORBIS_DIARY_TAB = (170, 140)
ORBIS_DIARY_CHEST = (502, 98)
SKYSTONE_REGION = (890, 15, 90, 38)
SKYSTONE_WHITE_LEVEL = 200
PASS_PRICE = 1900
PASS_BUTTON_REGION = (990, 562, 220, 34)
SETTLE_SECONDS = 2.5
BOTTOM_CLOSE_POINT = (640, 697)
# Nothing on the route stays still this long: fights and menus move, waits are short.
FROZEN_SECONDS = 20
# Some pages never stop moving (animated heroes), so a fixed wait after their title shows.
PAGE_SETTLE_SECONDS = 1.5


def settle(ctx: Context) -> bool:
    """Return whether the last action has had time to show its result."""
    at = ctx.route_data.get("acted_at")
    return at is None or time.monotonic() - at >= SETTLE_SECONDS


def act_and_settle(ctx: Context, screen: str, point: Point) -> State:
    """Click, and give the game time to answer before the next decision."""
    ctx.route_data["acted_at"] = time.monotonic()
    return act(ctx, screen, point)


def sequence(name: str, page_template: tuple[str, Region], to_page: Callable[[Context], State | None],
             actions: tuple[Point, ...], popups: tuple[tuple[str, Region, Point], ...]) -> Step:
    """A step that opens a page, closes popups as they come, and clicks a fixed list of points on it once it has settled."""

    def step(ctx: Context) -> State | str:
        for template, region, point in popups:
            if ctx.find(template, region) is not None:
                return act_and_settle(ctx, f"{name} popup", point)
        if not settle(ctx):
            return State.ROUTE
        if ctx.find(*page_template) is None:
            state = to_page(ctx)
            return state if state is not None else State.ROUTE
        done = ctx.route_data.get("done", 0)
        if done >= len(actions):
            return DONE
        seen_at = ctx.route_data.setdefault("page_seen_at", time.monotonic())
        if time.monotonic() - seen_at < PAGE_SETTLE_SECONDS:
            # The page slides in: a click during the slide lands on the page before.
            return State.ROUTE
        ctx.route_data["done"] = done + 1
        return act_and_settle(ctx, name, actions[done])

    step.__name__ = name
    return step


def from_lobby(point: Point) -> Callable[[Context], State | None]:
    """Reach a page by one click from the lobby."""

    def go(ctx: Context) -> State | None:
        if at_lobby(ctx):
            return act(ctx, "lobby", point)
        return unknown(ctx)

    return go


def to_reputation(ctx: Context) -> State | None:
    """Open Reputation from the main menu."""
    if find(ctx, "menu_reputation", 905, 512, 95, 26) is not None:
        return act(ctx, "menu", REPUTATION_POINT)
    if at_lobby(ctx):
        return act(ctx, "lobby", MENU_POINT)
    return unknown(ctx)


REWARD_POPUPS = (
    ("received_reward", around(540, 152, 200, 30), REWARD_CLOSE_POINT),
    ("rep_popup", around(520, 128, 240, 30), REP_POPUP_CLOSE_POINT),
    ("diary_popup", around(450, 150, 380, 34), REWARD_CLOSE_POINT),
)

# Receive All only claims one level of each achievement. Only the first tab has any on a new
# account: three Receive All there, and nothing on the others.
CLAIM_REPUTATION = sequence(
    "claim Reputation rewards",
    ("reputation_title", around(66, 20, 130, 30)),
    to_reputation,
    (REPUTATION_TABS[0], RECEIVE_ALL_POINT, RECEIVE_ALL_POINT, RECEIVE_ALL_POINT),
    REWARD_POPUPS,
)

CLAIM_FIRST_STEPS = sequence(
    "claim An Heir's First Steps",
    ("epic_pass_title", around(66, 20, 110, 30)),
    from_lobby(EPIC_PASS_POINT),
    (PASS_TAB_FIRST_STEPS, PASS_MISSIONS_POINT, PASS_MISSION_RECEIVE_POINT, PASS_REWARDS_POINT, PASS_RANK_POINT),
    REWARD_POPUPS,
)

# A popup the route does not know, after the missions raise the rank, can close the page before
# the rank cells are clicked: they get a second go, which does nothing once they are claimed.
CLAIM_FIRST_STEPS_RANKS = sequence(
    "claim An Heir's First Steps ranks",
    ("epic_pass_title", around(66, 20, 110, 30)),
    from_lobby(EPIC_PASS_POINT),
    (PASS_TAB_FIRST_STEPS, PASS_REWARDS_POINT, PASS_RANK_POINT),
    REWARD_POPUPS,
)

CLAIM_ORBIS = sequence(
    "claim Path of Heir and the Three Sisters' Diary",
    ("orbis_title", around(66, 66, 150, 34)),
    from_lobby(ORBIS_POINT),
    (ORBIS_HEIR_TAB, ORBIS_HEIR_RECEIVE, ORBIS_DIARY_TAB, ORBIS_DIARY_CHEST),
    REWARD_POPUPS,
)


def read_skystones(ctx: Context) -> int | None:
    """Read the skystones in the top bar, or None."""
    text = ctx.ocr.read(ctx.frame, SKYSTONE_REGION, whitelist="0123456789,", white_level=SKYSTONE_WHITE_LEVEL)
    digits = text.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def buy_pass(ctx: Context, strict: bool = True) -> State | str:
    """Buy the Premium Epic Pass with 1,900 skystones, never anything else.

    Not strict, too few skystones leave the purchase to a second go after the free summons.
    """
    if find(ctx, "pass_unlocked", 560, 178, 160, 30) is not None:
        return act_and_settle(ctx, "content unlocked", PASS_UNLOCKED_CONFIRM_POINT)
    if find(ctx, "pass_rank_complete", 520, 196, 250, 30) is not None:
        return act_and_settle(ctx, "season rank bought", PASS_RANK_CLOSE_POINT)
    if find(ctx, "pass_buy_name", 640, 338, 180, 26) is not None:
        if find(ctx, "pass_buy_price", 610, 495, 290, 30) is None:
            return ctx.halt("route: the Premium Epic Pass is not priced 1,900 skystones, nothing bought")
        ctx.route_data["bought"] = True
        return act_and_settle(ctx, "buy the pass", BUY_POINT)
    if not settle(ctx):
        return State.ROUTE
    if find(ctx, "epic_pass_title", 66, 20, 110, 30) is None:
        state = from_lobby(EPIC_PASS_POINT)(ctx)
        return state if state is not None else State.ROUTE
    # Read, not matched: the button is see-through and the artwork behind it moves.
    if "purchase" in ctx.ocr.read(ctx.frame, PASS_BUTTON_REGION).lower():
        return DONE
    if not ctx.route_data.get("on_memory"):
        ctx.route_data["on_memory"] = True
        return act_and_settle(ctx, "epic pass", PASS_TAB_MEMORY)
    skystones = read_skystones(ctx)
    if ctx.route_data.get("bought") and (skystones is None or skystones < PASS_PRICE):
        # Its button can still be fading to "Purchase complete": the skystones spent tell enough.
        ctx.logger.info("route: the pass was bought, %s skystones left", skystones)
        return DONE
    if skystones is None or skystones < PASS_PRICE:
        if not strict:
            ctx.logger.info("route: %s skystones, short of %d: the free summons first, then Orbis again", skystones, PASS_PRICE)
            ctx.route_flags["pass_short"] = True
            return DONE
        return ctx.halt(f"route: {skystones} skystones read, the pass needs {PASS_PRICE}: nothing bought")
    return act_and_settle(ctx, "epic pass", PASS_UPGRADE_POINT)


buy_pass.__name__ = "buy the Premium Epic Pass"


def buy_pass_if_enough(ctx: Context) -> State | str:
    """Buy the pass now, or note it is short and leave it to a second go."""
    return buy_pass(ctx, strict=False)


buy_pass_if_enough.__name__ = "buy the Premium Epic Pass if the skystones are there"


def only_if_pass_short(step: Step) -> Step:
    """A step that only runs when the first try at the pass found too few skystones."""

    def wrapped(ctx: Context) -> State | str:
        return step(ctx) if ctx.route_flags.get("pass_short") else DONE

    wrapped.__name__ = f"{step.__name__}, if the pass is still unbought"
    return wrapped

CLAIM_PASS = sequence(
    "claim the Heir's Memory bookmarks",
    ("epic_pass_title", around(66, 20, 110, 30)),
    from_lobby(EPIC_PASS_POINT),
    (PASS_TAB_MEMORY, PASS_CLAIM_POINT),
    REWARD_POPUPS,
)


SUMMON_ENTRY_POINT = (1196, 455)
COVENANT_TAB_POINT = (108, 640)
SUMMON_TEN_POINT = (1137, 662)
SUMMON_AGAIN_POINT = (1157, 662)
SUMMON_CONFIRM_POINT = (748, 542)
SUMMON_CANCEL_POINT = (532, 542)
REVEAL_SKIP_POINT = (1215, 38)
# Without an animation seen, how long after a pull the results screen can only be the new one.
NEW_RESULT_SECONDS = 8.0


SUMMON_ONE_POINT = (880, 662)
RESULT_BACK_POINT = (155, 660)


def prepare_covenant(ctx: Context) -> State | str:
    """Fill the empty wish list with the game's recommended one and do the free x1.

    The game refuses Covenant Summon x10 until both have been done once on the account.
    """
    summon = sys.modules["states.summon"]
    if ctx.find("wish_confirm", summon.WISH_CONFIRM_REGION) is not None:
        return act_and_settle(ctx, "apply recommended wish list", summon.WISH_CONFIRM_POINT)
    if not settle(ctx):
        return State.ROUTE
    apply = ctx.find("wish_apply", summon.WISH_APPLY_REGION)
    if apply is not None:
        if ctx.find("wish_slot_empty", summon.WISH_SLOT_REGION) is None:
            return act_and_settle(ctx, "wish list", summon.WISH_CLOSE_POINT)
        return act_and_settle(ctx, "wish list", apply.center)
    if ctx.find("result_back", summon.RESULT_BACK_REGION) is not None:
        ctx.route_data["pulled_one"] = True
        return act_and_settle(ctx, "free summon result", RESULT_BACK_POINT)
    if find(ctx, "covenant_wish", 650, 545, 85, 26) is not None:
        if ctx.find("wish_empty", summon.WISH_EMPTY_REGION) is not None:
            return act_and_settle(ctx, "covenant summon", summon.WISH_LIST_POINT)
        if find(ctx, "summon_free", 808, 648, 48, 28) is not None and not ctx.route_data.get("pulled_one"):
            # Free: no popup, no bookmark spent.
            return act_and_settle(ctx, "free summon x1", SUMMON_ONE_POINT)
        return DONE
    if ctx.find("reveal_skip", summon.REVEAL_SKIP_REGION) is not None:
        return act(ctx, "summon animation", REVEAL_SKIP_POINT)
    if ctx.find("summon_header", SUMMON_HEADER_REGION) is not None:
        return act_and_settle(ctx, "summon page", COVENANT_TAB_POINT)
    if at_lobby(ctx):
        return act_and_settle(ctx, "lobby", SUMMON_ENTRY_POINT)
    return unknown(ctx)


prepare_covenant.__name__ = "set the Covenant wish list and do the free x1"


def event_summons(ctx: Context) -> State | str:
    """Claim the current event's free summons and pull them, if the settings ask for it.

    The summon state does it as for the short reroll, pulling only on a Free label, then hands back.
    """
    if not ctx.config.summon.event_summons or ctx.summon.event_done:
        return DONE
    summon = sys.modules["states.summon"]
    # From the lobby only: the summon state reads any Back button as a pull's result, the Abyss
    # CLEAR screen's included.
    if (at_lobby(ctx) or ctx.find("summon_header", SUMMON_HEADER_REGION) is not None
            or ctx.find("event_header", summon.EVENT_HEADER_REGION) is not None):
        return State.SUMMON
    return unknown(ctx)


event_summons.__name__ = "claim and pull the event's free summons"


def pull_bookmarks(ctx: Context) -> State | str:
    """Pull Covenant Summon x10 with bookmarks, Chronicle then Covenant, reading every result.

    A pull is only confirmed when the popup is paid in bookmarks. Once they run out the game
    offers to buy more: that popup is closed and the step is done.
    """
    summon = sys.modules["states.summon"]
    progress = ctx.summon
    if ctx.find("buy_bookmarks", summon.BUY_BOOKMARKS_REGION) is not None:
        act(ctx, "out of bookmarks", summon.BUY_CLOSE_POINT)
        return DONE
    if find(ctx, "summon_x10_title", 540, 128, 200, 50) is not None:
        paid_in_bookmarks = (find(ctx, "bookmark_chronicle", 518, 322, 84, 84) is not None
                             or find(ctx, "bookmark_covenant", 518, 322, 84, 84) is not None)
        if not paid_in_bookmarks:
            act(ctx, "summon not paid in bookmarks", SUMMON_CANCEL_POINT)
            return DONE
        progress.result_done = False
        progress.current, progress.reads = [], 0
        ctx.route_data["pulled_at"] = time.monotonic()
        ctx.route_data["animation_seen"] = False
        return act(ctx, "summon x10", SUMMON_CONFIRM_POINT)
    if ctx.find("summon_results", RESULTS_REGION) is not None:
        seen(ctx, "summon results")
        waited = time.monotonic() - ctx.route_data.get("pulled_at", 0.0)
        if not progress.result_done and not ctx.route_data.get("animation_seen") and waited < NEW_RESULT_SECONDS:
            # The last result stays on screen while the popup fades: it is not the new one.
            return State.ROUTE
        if not progress.result_done:
            summon._merge(ctx, [
                summon.Card(
                    summon._stars(ctx, "star_card", summon._offset(slot, summon.CARD_HERO_STARS)),
                    summon._stars(ctx, "star_artifact", summon._offset(slot, summon.CARD_ARTIFACT_STARS)),
                    summon._is_moonlight(ctx, slot),
                )
                for slot in summon.CARD_SLOTS
            ])
            if progress.reads < summon.READS_PER_RESULT:
                return State.ROUTE
            names = [
                summon._read_name(ctx, summon._offset(slot, summon.CARD_HERO_NAME)) if card.hero_stars else ""
                for slot, card in zip(summon.CARD_SLOTS, progress.current)
            ]
            stop = summon._finish_reading(ctx, names, stay=State.ROUTE)
            if stop is not None:
                return stop
            ctx.save_route_pulls()
        return act(ctx, "summon again", SUMMON_AGAIN_POINT)
    next_button = ctx.find("reveal_next", summon.REVEAL_NEXT_REGION)
    if next_button is not None:
        ctx.route_data["animation_seen"] = True
        return act(ctx, "hero reveal", next_button.center)
    if ctx.find("reveal_skip", summon.REVEAL_SKIP_REGION) is not None:
        ctx.route_data["animation_seen"] = True
        return act(ctx, "summon animation", REVEAL_SKIP_POINT)
    if find(ctx, "covenant_wish", 650, 545, 85, 26) is not None:
        return act(ctx, "covenant summon", SUMMON_TEN_POINT)
    if ctx.find("summon_header", SUMMON_HEADER_REGION) is not None:
        return act(ctx, "summon page", COVENANT_TAB_POINT)
    if at_lobby(ctx):
        return act(ctx, "lobby", SUMMON_ENTRY_POINT)
    return unknown(ctx)


pull_bookmarks.__name__ = "pull Covenant Summon with the bookmarks"


def decide(ctx: Context) -> State | str:
    """Keep the account if the pulls hold what the rules ask for, otherwise start a new one from the lobby."""
    summon = sys.modules["states.summon"]
    if not ctx.route_data.get("decided"):
        reason = summon._decide(ctx)
        summon._count_run(ctx)
        if reason is not None:
            return ctx.halt(reason)
        ctx.logger.info("route finished, nothing to keep: going to the lobby to start a new account")
        ctx.route_data["decided"] = True
    # Nothing of a finished route fights. A fight right after the start means the saved route
    # belonged to an older account, and the user reset it by hand: play the new one's story.
    if ctx.route_steps_this_run == 0 and (
        ctx.find("pause_hud", PAUSE_REGION) is not None or ctx.find("hourglass", HOURGLASS_REGION) is not None
    ):
        ctx.logger.info("this account is new, the saved route was an older one's: starting the route over")
        ctx.start_new_account()
        return State.STORY
    if at_lobby(ctx):
        ctx.reset_pending = True
        ctx.reset_from_saved_route = ctx.route_steps_this_run == 0
        return State.RESET_ACCOUNT
    # The reset only starts from the lobby, and its clicks there would open a card on the results.
    if ctx.find("result_back", summon.RESULT_BACK_REGION) is not None:
        return act_and_settle(ctx, "summon results", RESULT_BACK_POINT)
    if not settle(ctx):
        return State.ROUTE
    return unknown(ctx)


decide.__name__ = "keep the account or start a new one"


MAIL_POINT = (1124, 33)
MAIL_RECEIVE_X = 1144
# The Receive button sits a little below the middle of the icon template, which leaves out the count.
MAIL_RECEIVE_DY = 14
MAIL_LETTER_POINT = (641, 550)
MAIL_ICON_REGION = (925, 80, 120, 560)
MAIL_DRAG = ((900, 600), (900, 230))
MAX_MAIL_SCROLLS = 6
# Only these are taken from the mail: no tickets, selectors or anything else.
WANTED_MAIL = (
    "mail_energy",
    "mail_penguin_10",
    "mail_penguin_60",
    "mail_chest_20",
    "mail_chest_40",
    "mail_chest_60",
    "mail_chest_75",
    "mail_chest_90",
    "mail_chest_180",
)


def claim_mail(ctx: Context) -> State | str:
    """Receive the mails holding penguins, energy or rune chests, one by one, and nothing else."""
    if do_tap(ctx, POPUP_TAPS) is not None:
        return State.ROUTE
    if find(ctx, "mail_letter", 503, 524, 277, 52) is not None:
        return act_and_settle(ctx, "mail letter", MAIL_LETTER_POINT)
    if not settle(ctx):
        return State.ROUTE
    if find(ctx, "mailbox_title", 60, 18, 110, 32) is None:
        state = from_lobby(MAIL_POINT)(ctx)
        return state if state is not None else State.ROUTE
    matches = [m for name in WANTED_MAIL for m in ctx.library.find_every(ctx.frame, name, MAIL_ICON_REGION)]
    if matches:
        top = min(matches, key=lambda m: m.center[1])
        ctx.route_data["scrolls"] = 0
        return act_and_settle(ctx, "mailbox", (MAIL_RECEIVE_X, top.center[1] + MAIL_RECEIVE_DY))
    icons = ctx.frame[80:640, 925:1045]
    previous = ctx.route_data.get("icons")
    scrolls = ctx.route_data.get("scrolls", 0)
    if (previous is not None and previous.shape == icons.shape and float(abs(previous.astype(int) - icons.astype(int)).mean()) < 2.0) or scrolls >= MAX_MAIL_SCROLLS:
        return DONE
    ctx.route_data["icons"] = icons.copy()
    ctx.route_data["scrolls"] = scrolls + 1
    (x1, y1), (x2, y2) = MAIL_DRAG
    seen(ctx, "mailbox")
    ctx.session.input.drag(x1, y1, x2, y2)
    ctx.route_data["acted_at"] = time.monotonic()
    return State.ROUTE


CLAIM_MAIL = claim_mail

STEPS: tuple[Step, ...] = (
    story_to("6th World Episode 3-3"),
    RECRUIT_VILDRED,
    CLAIM_MAIL,
    level_up("hero_name_vildred", "Arbiter Vildred"),
    # Awakening only opens once chapter 3 is cleared, Kayron included.
    story_to("6th World Episode 4-1"),
    awaken_once(1),
    awaken_once(2),
    awaken_once(3),
    awaken_once(4),
    altar_run("first Dark Spirit Altar fight", skip_if_quick=True),
    altar_quick("Quick Battling x10"),
    altar_buy,
    altar_quick("bought Quick Battling x10"),
    altar_run("one more Dark Spirit Altar fight", skip_if_quick=False),
    claim_milestone("claim Milestone of Radiance tiers"),
    buy_gear_set,
    equip_all("hero_name_vildred", "Arbiter Vildred"),
    abyss_team("Vildred alone in the Abyss team", ("row_vildred", "row_vildred_plain"), only_if_empty=True),
    # A new account always has the same essence by then: floor 29 pays for Landy and her set.
    abyss_climb(29),
    claim_milestone("claim Landy and her set in Milestone of Radiance", landy_with_set=True),
    # One step, so that the step numbers of an account in progress stay the same.
    then(level_up("hero_name_landy", "Landy"), awaken_hero("hero_name_landy", "Landy", times=4)),
    equip_all("hero_name_landy", "Landy"),
    abyss_team("Landy in the Abyss team", ("row_landy", "row_landy_face", "row_landy_plain"), only_if_empty=False),
    abyss_climb(45),
    CLAIM_REPUTATION,
    CLAIM_FIRST_STEPS,
    CLAIM_FIRST_STEPS_RANKS,
    CLAIM_ORBIS,
    buy_pass_if_enough,
    CLAIM_PASS,
    # All the summons in a row.
    event_summons,
    prepare_covenant,
    # When the game's day changed during the account, the diary lacks the points of its daily
    # Covenant Summon mission, and with them the last skystones: the summons above gave them.
    only_if_pass_short(CLAIM_ORBIS),
    only_if_pass_short(buy_pass),
    only_if_pass_short(CLAIM_PASS),
    pull_bookmarks,
    decide,
)


# Climbing the Abyss of a kept account, from its current floor, until the team loses too often.
CLIMB = abyss_climb(None, keep_account=True)

def frozen(ctx: Context) -> bool:
    """Return whether the screen has stayed the same for FROZEN_SECONDS. The count starts again after."""
    now = time.monotonic()
    small = cv2.resize(ctx.frame, (160, 90)).astype(int)
    previous = ctx.route_data.get("frozen_frame")
    if previous is None or float(abs(previous - small).mean()) >= STILL_DIFFERENCE:
        ctx.route_data["frozen_frame"], ctx.route_data["frozen_since"] = small, now
        return False
    if now - ctx.route_data["frozen_since"] < FROZEN_SECONDS:
        return False
    ctx.logger.warning("route: the screen has not changed for %d s, pressing Esc", FROZEN_SECONDS)
    ctx.route_data["frozen_since"] = now
    return True


def route(ctx: Context) -> State:
    """Run the current step, and move to the next one when it is done."""
    if not ctx.attach():
        return State.RECOVER
    if ctx.goal not in ("climb", "theater", "arena", "shop", "penguins", "huche", *GEAR_GOALS) and ctx.route_step >= len(STEPS):
        return ctx.halt(f"route step {ctx.route_step + 1} is not built yet")
    if ctx.find("quit_popup", QUIT_REGION) is not None or frozen(ctx):
        # A screen that does not move whatever the step clicks: something unknown lies over it.
        return go_lobby(ctx)
    # Popups over the lobby, such as the paid First Pack offer, leave the lobby buttons in view
    # but dead. Only ever their Tap to close: never the offer.
    if at_lobby(ctx) and find(ctx, "bottom_tap_to_close", 560, 675, 160, 44) is not None:
        return act(ctx, "popup over the lobby", BOTTOM_CLOSE_POINT)
    if ctx.goal == "climb":
        # A kept account: one step, the climb, which only ends by stopping the bot.
        return CLIMB(ctx)
    if ctx.goal == "theater":
        # A kept account: the Moonlight Theater, act after act, until nothing is left to watch.
        return theater(ctx, unknown, at_lobby)
    if ctx.goal == "arena":
        # A kept account: NPC Challenge fights until the flags run out.
        return arena(ctx, unknown, at_lobby)
    if ctx.goal == "penguins":
        # A kept account: buy penguins at the Growth Altar.
        return penguins(ctx, unknown, at_lobby)
    if ctx.goal == "huche":
        # A kept account: buy Huche's charms in the first labyrinth.
        return huche(ctx, unknown, at_lobby)
    if ctx.goal == "shop":
        # A kept account: the Secret Shop, bought from and refreshed as set.
        return shop(ctx, unknown, at_lobby)
    if ctx.goal in GEAR_GOALS:
        # A kept account: craft, sort or roll its gear.
        return gear(ctx, unknown, at_lobby)
    step = STEPS[ctx.route_step]
    result = step(ctx)
    if result == DONE:
        ctx.logger.info("route step %d done: %s", ctx.route_step + 1, step.__name__)
        ctx.route_steps_this_run += 1
        ctx.set_route_step(ctx.route_step + 1)
        ctx.progressed = True
        return State.ROUTE
    return result

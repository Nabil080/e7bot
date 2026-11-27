"""The bot window's logic: what each part of the window says, and what its buttons do. Drawing is in core/panel_window.py and ui/."""

from __future__ import annotations

import copy
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from core.account import (
    LEVELS, SLOTS, SPEED_SLOTS, AccountResult, Seating, advise, best_values, level, load_result, role_values, scan_piece, seat_pieces,
    stats,
)
from core.account import Advice as AccountAdvice
from core.advisor import ACTION_LABELS, Advice, AdviceBoard, ScreenAdvice
from core.clients import CLIENTS, state_path, window_config_for
from core.explain import ROLE_WORDS, STAT_LABELS, explain, role_name
from core.gear import FAMILIES, MAIN_ROLES, NICHE_EXTRA, ROLE_WEIGHTS, auto_target, judge, perfect_score
from core.config import WindowConfig
from core.heroes import load_moonlight_heroes, load_ranking, ranked_heroes
from core.logger import LOG_FILE_NAME
from core.progress import ACCOUNT_TABS, Progress, activity, read_progress, split_stage
from core.settings import CRAFT_SLOTS, HUNTS, SERVERS, Settings, save_settings, settings_policy
from core.stats import HUCHE_CHARMS, SHOP_REFRESH_PRICE, TICKET_PRICE, Stats, load_stats, load_total_rerolls, save_stats, save_total_rerolls
from core.status import BotStatus, LogFeed, StatusSnapshot

# The window's size in logical pixels, and its gap to the game.
PANEL_WIDTH = 400
PANEL_HEIGHT = 720
GAP = 12
STOP_WAIT_SECONDS = 5.0
# A first click on a button that cannot be undone wears off after this long.
ERASE_CONFIRM_SECONDS = 4.0
RESET_STATS_SECONDS = 5.0
ACTIVITY_LINES = 5
ROUTE_STEP_COUNT = 37
ABYSS_TOP_FLOOR = 45

RUNNING = "running"
IDLE = "idle"
KEPT = "kept"
ATTENTION = "attention"
KIND_NAMES = {IDLE: "Idle", KEPT: "Account kept", ATTENTION: "Needs a look"}

NOT_RESET = "The account was not erased."
DELETE_LABEL = "Erase this account and start over…"
DELETE_ARMED_LABEL = "Click again to erase this account"
PICK_MODE_LABEL = "Pick a mode to see what it does and start it."
RESET_STATS_TEXT = "Reset the stats"
RESET_STATS_NOTE = "Erases the stats and the reroll count of the chosen client. Accounts, settings and the route step stay."
RESET_SETTINGS_SECONDS = 5.0
# Settings a page's reset never touches: the client is chosen apart, on its own.
NEVER_RESET = ("client",)
INVALID_SETTING = "A setting is not valid: check the numbers in Settings."
ADMIN_NOTE = "{client} runs the game as administrator, so the bot must too. Restart it as administrator to start."

Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class Goal:
    """Something the bot can be asked to do, as the window offers it."""

    key: str
    label: str
    name: str
    description: str
    bot_goal: str
    # A mode shown greyed out, to come: it cannot be picked yet.
    available: bool = True
    # The window shows the gear modes apart, under their own heading.
    group: str = "main"
    # The game's own menu glyph beside the mode's name, in ui/glyphs.
    icon: str = ""
    # The rail's heading the mode sits under: Reroll, Progression, Gear or Tools.
    category: str = ""
    # Modes sharing a menu are one item of the rail, picked between on their page.
    menu: str = ""


GOALS = (
    Goal(
        "reroll_summons", "Summon reroll", "Summon reroll",
        "Plays a new account only up to the summons, pulls the free summons, then keeps the account or erases it. The quick reroll for a free summon event.", "summons", icon="recruit.png", category="Reroll",
    ),
    Goal(
        "reroll_abyss", "Abyss 45 reroll", "Abyss 45 reroll",
        "Story, Vildred, Abyss up to floor 45, pass, then the summons. Keeps the account if it pulls what you want.", "abyss", icon="abyss.png", category="Reroll",
    ),
    Goal(
        "story", "Push the story", "Story",
        "Pushes the story of the current account. Never erases it, and stops after 3 losses in a row.", "story", icon="journal.png", category="Progression",
    ),
    Goal(
        "climb", "Climb the Abyss", "Abyss",
        "Climbs the Abyss of the current account from its floor, with your team. Never erases it, and stops after 3 losses in a row.", "climb", icon="abyss.png", category="Progression",
    ),
    Goal(
        "theater", "Moonlight Theater", "Theater",
        "Watches the Moonlight Theater acts in order, story by story, up to the number of acts set below. Fights on auto. Buys tickets with gold if you allow it.", "theater", icon="theater.png", category="Progression",
    ),
    Goal(
        "arena", "Auto arena", "Arena",
        "Fights in the Arena against NPCs or players, with Quick Match when possible, up to the number of fights set below. Refills flags only as you allow.", "arena", icon="arena.png", category="Tools",
    ),
    Goal(
        "shop", "Shop refresh", "Shop refresh",
        "Opens the Secret Shop, buys its Covenant Bookmarks and Mystic Medals with gold, then refreshes it for 3 skystones, as many times as set below or until a target is reached.", "shop", icon="secret_shop.png", category="Tools",
    ),
    Goal(
        "penguins", "Buy penguins", "Penguins",
        "Goes to the Sanctuary, the Forest of Souls and its Growth Altar, then buys the Penguin Nest 50 at a time (5,100 stigma), as many times or for as much stigma as set below.", "penguins", icon="sanctuary.png", category="Tools",
    ),
    Goal(
        "huche", "Huche shop", "Huche shop",
        "Goes to the first labyrinth (Tirel Castle in Chaos, Area 1), teleports to the waypoint by Huche, walks to him and buys every charm he sells with gold, then yields the labyrinth.", "huche", icon="huche.png", category="Tools",
    ),
    Goal(
        "gear_craft", "Craft gear", "Craft gear",
        "Crafts the pieces set below at the Steel Workshop, then reads their cards and extracts or sells the bad ones, as set for new pieces. Stops when gold, materials or a limit run out.", "gear_craft", group="gear", icon="workshop.png", category="Gear", menu="gear_work",
    ),
    Goal(
        "gear_roll", "Enhance gear", "Enhance gear",
        "Does what Sell gear does, then enhances the promising pieces 16 at a time in Bulk Enhancement, a tier at a time: after each one it reads them again, sells the ones that lost their potential and locks the finished good ones.", "gear_roll", group="gear", icon="grace.png", category="Gear", menu="gear_work",
    ),
    Goal(
        "gear_sort", "Sell gear", "Sell gear",
        "Reads every card no hero wears and that is not locked, screen by screen in Arrange: extracts the fresh pieces not worth rolling, then sells the enhanced ones that failed. Enhances nothing.", "gear_sort", group="gear", icon="inventory.png", category="Gear", menu="gear_work",
    ),
    Goal(
        "gear_account", "Account", "Account evaluation",
        "Reads every +15 piece of the Inventory, worn and locked ones too: shows what your gear is worth and your account's level, then the targets per role and slot that keep only pieces improving one of the heroes you gear. Apply them for the gear modes and the Gear overlay. Sells and enhances nothing.", "gear_account", group="gear", icon="hero.png", category="Gear",
    ),
    Goal(
        "gear_advise", "Gear overlay", "Gear overlay",
        "You play, it watches and never clicks. It reads the pieces on screen (Inventory, Arrange, Bulk Enhancement, Enhance, craft results), judges each one with the Gear settings, and shows here what to do with it and why, with a tag on each card in the game.", "gear_advise", group="gear", icon="orbis.png", category="Gear",
    ),
)
GEAR_GOALS = ("gear_craft", "gear_sort", "gear_roll", "gear_account")
# The account evaluation: its result page and its button that applies the scores as the per-role targets.
ACCOUNT_GOAL = "gear_account"
# The gear advisor only looks: no run numbers of its own, only what is on screen.
ADVISOR_GOAL = "gear_advise"
# These modes play an account the user kept: the window never offers to erase it.
KEPT_GOALS = ("story", "climb", "theater", "arena", "shop", "penguins", "huche", *GEAR_GOALS, ADVISOR_GOAL)
# What Craft does with the pieces it made, as the window says it.
CRAFT_THEN_LABELS = {"sort": "Remove the bad ones", "none": "Nothing"}
GOAL_BY_KEY = {goal.key: goal for goal in GOALS}
GOAL_BY_BOT_GOAL = {goal.bot_goal: goal for goal in GOALS if goal.available}


@dataclass(frozen=True)
class Display:
    """What the top of the window says: how it looks, the headline, and one line of detail."""

    kind: str
    title: str
    detail: str


def format_stage(stage: str) -> str:
    """Show 6th World Episode 2-2 in words, and nothing before the first stage is known."""
    parts = split_stage(stage)
    if parts is None:
        return ""
    episode, numbers = parts
    return f"{episode} · Chapter {numbers.split('-')[0]} · stage {numbers}"


def clean_hero_name(reading: str) -> str:
    """Keep the words of a name read by OCR, which can have artwork noise after it."""
    words = [word for word in reading.split() if re.fullmatch(r"[A-Za-z][A-Za-z'\-]{2,}", word)]
    return " ".join(words) or reading


def _describe_kept(names: str) -> str:
    """Show 'Briar Witch Iseria ML 5*, Rose 5*' without the OCR noise."""
    shown = []
    unmatched = ", a name that could not be matched to a hero"
    if names.endswith(unmatched):
        return f"A 5★ Moonlight whose name was not recognized: {clean_hero_name(names.removesuffix(unmatched).strip().removesuffix(' ML 5*'))}"
    for card in names.split(","):
        name = card.strip().removesuffix("*").rsplit(" ", 1)[0]
        moonlight = name.endswith(" ML")
        shown.append(f"{clean_hero_name(name.removesuffix(' ML'))} 5★{' Moonlight' if moonlight else ''}")
    return ", ".join(shown)


def theater_ticket_reason(reason: str) -> str:
    """Return in plain words why the theater bought no more tickets."""
    gold = re.match(r"gold under ([\d,]+)", reason)
    if gold is not None:
        millions = int(gold.group(1).replace(",", "")) / 1_000_000
        return f"gold is under {millions:g}M, your threshold in Settings › Theater"
    if reason.startswith("buying tickets is turned off"):
        return "buying tickets is off for this mode"
    if "tickets bought" in reason:
        return "the limit of 20 tickets bought per session is reached"
    if "could not be read" in reason:
        return "the gold could not be read"
    return "the price was not in gold"


def describe_stop(result: str) -> Display:
    """Turn the reason the bot stopped into what the user needs to know and do."""
    if not result:
        return Display(IDLE, "Ready to start", "Pick a mode, then Start.")
    if result == "stopped by the user":
        return Display(IDLE, "Stopped", "Start picks this account up where it left off.")
    if result.startswith("account kept:"):
        return Display(KEPT, "Account kept", _describe_kept(result.split(":", 1)[1]))
    if result.startswith("the game offers a patch"):
        return Display(ATTENTION, "Game update", f"Accept it in the game, then restart the bot. {NOT_RESET}")
    if result.startswith("the day changed"):
        return Display(ATTENTION, "New day", f"The free pulls left have expired. {NOT_RESET}")
    if result.startswith("the game asks to buy"):
        return Display(ATTENTION, "No free pulls left", f"Nothing was bought. {NOT_RESET}")
    if result.startswith("could not read"):
        return Display(ATTENTION, "Unreadable result", f"Check the pulls before restarting. {NOT_RESET}")
    found = re.search(r"(\d+) of (\d+) results", result)
    if found:
        return Display(ATTENTION, "Incomplete pulls", f"{found.group(1)} of {found.group(2)} results read. {NOT_RESET}")
    if result.startswith("Summon Support"):
        return Display(ATTENTION, "Event not found", f"Summon Support is no longer in the list. {NOT_RESET}")
    if result.startswith("SUMMON still failing"):
        return Display(ATTENTION, "Summons stuck", f"Look at the game screen. {NOT_RESET}")
    if result.startswith("the game refused"):
        return Display(ATTENTION, "Nickname refused", f"The game accepted none of the nickname readings. {NOT_RESET}")
    floor = re.match(r"(\d+) lost fights in a row on (?:abyss floor (\d+)|the abyss):", result)
    if floor:
        where = f" on floor {floor.group(2)}" if floor.group(2) else ""
        return Display(ATTENTION, "Team too weak", f"{floor.group(1)} losses in a row in the Abyss{where}. Strengthen your team, then restart. {NOT_RESET}")
    full = re.match(r"cannot start: the (\w+) inventory is full", result)
    if full:
        what = {"artifacts": "artifact inventory full", "equipment": "equipment inventory full", "heroes": "hero inventory full"}
        return Display(ATTENTION, "Cannot start", f"{what.get(full.group(1), 'inventory full').capitalize()}. Make room in the game, then restart. {NOT_RESET}")
    if (arena := re.match(r"Arena: (no flag left|target reached), (\d+) fights \((\d+) won, (\d+) quick, (\d+) refills, (\d+) skystones\)", result)) is not None:
        done = f"{arena.group(2)} fights, {arena.group(3)} won, {arena.group(4)} quick matches, {arena.group(5)} refills, {arena.group(6)} skystones spent"
        if arena.group(1) == "target reached":
            return Display(ATTENTION, "Arena done", f"Fight count reached: {done}. {NOT_RESET}")
        return Display(ATTENTION, "Arena done", f"No flags left: {done}. Flags come back over time. {NOT_RESET}")
    if result.startswith("Arena: Challenge opens neither"):
        return Display(ATTENTION, "Arena stopped", f"Challenge opened neither the fight nor the flag refill: the bot stops rather than click on blind. {NOT_RESET}")
    if (shop := re.match(r"Secret Shop: (.*?), (\d+) refreshes \((\d+) skystones\), (\d+) bookmarks and (\d+) medals bought for ([\d,]+) gold", result)) is not None:
        done = f"{shop.group(2)} refreshes ({shop.group(3)} skystones), {shop.group(4)} bookmarks and {shop.group(5)} medals bought for {shop.group(6)} gold"
        why = shop.group(1)
        if why == "refreshes done":
            return Display(ATTENTION, "Shop refresh done", f"Refresh count reached: {done}. {NOT_RESET}")
        if why.endswith("target reached"):
            what = "Bookmark" if why.startswith("bookmark") else "Medal"
            return Display(ATTENTION, "Shop refresh done", f"{what} target reached: {done}. {NOT_RESET}")
        if why.startswith("not enough gold"):
            return Display(ATTENTION, "Out of gold", f"Not enough gold to buy what the shop offers: {done}. {NOT_RESET}")
        if why == "not enough skystones":
            return Display(ATTENTION, "Out of skystones", f"Not enough skystones for another refresh: {done}. {NOT_RESET}")
        return Display(ATTENTION, "Shop refresh stopped", f"{why.capitalize()}: {done}. {NOT_RESET}")
    if result.startswith("Secret Shop: its button is not in the lobby"):
        return Display(ATTENTION, "Secret Shop not found", f"Its button is not in the lobby's left column. Open the lobby, then restart. {NOT_RESET}")
    if (reached := re.match(r"Moonlight Theater: target reached, (\d+) acts watched and (\d+) tickets bought", result)) is not None:
        return Display(ATTENTION, "Theater done", f"Act count reached: {reached.group(1)} acts watched, {reached.group(2)} tickets bought. {NOT_RESET}")
    if (theater := re.match(r"Moonlight Theater: nothing more to watch, (\d+) acts watched and (\d+) tickets bought", result)) is not None:
        seen = f"{theater.group(1)} acts watched, {theater.group(2)} tickets bought"
        left = re.search(r"acts that need a ticket were left \((.*)\)", result)
        if left is not None:
            why = theater_ticket_reason(left.group(1))
            return Display(ATTENTION, "Theater paused", f"Paid acts are left, but no more tickets: {why}. {seen}. Restart when you have tickets or gold. {NOT_RESET}")
        return Display(ATTENTION, "Theater done", f"Nothing more to watch for now: {seen}. The next acts unlock over time. {NOT_RESET}")
    if result.startswith("out of energy"):
        return Display(ATTENTION, "Out of energy", f"No usable leaf left. No skystones were spent. {NOT_RESET}")
    if result.startswith("the abyss team is empty"):
        return Display(ATTENTION, "Abyss team empty", f"Set up your Abyss team in the game, then restart. {NOT_RESET}")
    lost = re.match(r"(\d+) lost fights in a row on (.*?):", result)
    if lost:
        parts = split_stage(lost.group(2))
        where = f" on {parts[1]}" if parts else ""
        return Display(ATTENTION, "Team too weak", f"{lost.group(1)} losses in a row{where}. Strengthen your team, then restart. {NOT_RESET}")
    if result.startswith("the selected stage stays locked"):
        return Display(ATTENTION, "Stage locked", f"Pick the stage to play in the game, then restart. {NOT_RESET}")
    if result.startswith("never resetting a kept account"):
        return Display(ATTENTION, "Reset refused", "The Story and Abyss modes never erase the account.")
    if " still failing after " in result:
        return Display(ATTENTION, "Screen stuck", f"Look at the game screen, then restart. {NOT_RESET}")
    if result.startswith("stopped by an error"):
        return Display(ATTENTION, "Error", "The details are in the Log tab.")
    return Display(ATTENTION, "Check this", result)


def describe_run(snapshot: StatusSnapshot) -> Display:
    """Say what a running bot is busy with."""
    if snapshot.state == "RECOVER":
        return Display(ATTENTION, "Screen stuck", "The bot is trying to get unstuck.")
    if snapshot.state == "SUMMON":
        if snapshot.pulls_expected:
            return Display(RUNNING, "Summons", f"{snapshot.pulls_read} of {snapshot.pulls_expected} pulls read")
        return Display(RUNNING, "Summons", "Claiming the free pulls")
    if snapshot.state == "RESET_ACCOUNT":
        return Display(RUNNING, "Account reset", "A new account is on its way.")
    if snapshot.state == "ROUTE":
        return Display(RUNNING, "Abyss route", "Heroes, altar, Abyss, pass")
    if snapshot.state == "STORY":
        parts = split_stage(snapshot.stage)
        if parts is None:
            return Display(RUNNING, "Story", "Videos and dialogues")
        episode, numbers = parts
        return Display(RUNNING, episode or "Story", f"Chapter {numbers.split('-')[0]} · stage {numbers}")
    return Display(RUNNING, "Logging in", "Entering the game")


def describe(snapshot: StatusSnapshot) -> Display:
    """Return what the top of the window says for this status."""
    return describe_run(snapshot) if snapshot.running else describe_stop(snapshot.last_result)


def panel_position(
    game: Rect, work: Rect, others: list[Rect], width: int = PANEL_WIDTH, height: int = PANEL_HEIGHT
) -> tuple[int, int]:
    """Return where to put the window, beside the game rather than over it.

    Tries the right of the game, then its left, then another monitor. Only if
    nothing fits does it fall back to the right.
    """
    game_left, game_top, game_right, _ = game
    work_left, work_top, work_right, work_bottom = work
    top = max(work_top, min(game_top, work_bottom - height))
    if game_right + GAP + width <= work_right:
        return game_right + GAP, top
    if game_left - GAP - width >= work_left:
        return game_left - GAP - width, top
    for other in others:
        if other[2] - other[0] >= width and other[3] - other[1] >= height:
            return other[0] + GAP, other[1] + GAP
    return game_right + GAP, top


def format_elapsed(seconds: float) -> str:
    """Format a duration as h:mm:ss."""
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def format_duration(seconds: float | None) -> str:
    """Format a duration for a glance: 59 min, 1 min 40, 45 s, or a dash when unknown."""
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 60:
        return f"{total} s"
    if total < 600:
        return f"{total // 60} min {total % 60:02d}"
    if total < 3600:
        return f"{round(total / 60)} min"
    return f"{total // 3600} h {total % 3600 // 60:02d}"


def percent(part: int, whole: int) -> str:
    """Return a share as "42%", or a dash when there is nothing to divide."""
    return f"{round(100 * part / whole)}%" if whole else "—"


def win_rate(won: int, lost: int) -> str:
    """Return the share of fights won, or a dash before the first fight."""
    return percent(won, won + lost)


def format_gold(amount: int) -> str:
    """Return gold as "8M", "1.5M" or "500k"."""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:g}M"
    if amount >= 1_000:
        return f"{amount // 1_000}k"
    return str(amount)


def stat_sections(stats: Stats, total_rerolls: int) -> list[tuple[str, list[tuple[str, str]]]]:
    """Return the statistics by section, as (value, caption) tiles, three to a row."""
    return [section for _, sections in stat_tabs(stats, total_rerolls) for section in sections]


def stat_tabs(stats: Stats, total_rerolls: int) -> list[tuple[str, list[tuple[str, list[tuple[str, str]]]]]]:
    """Return the statistics as one tab per mode, in the order of the home tab, each with its sections."""
    average = stats.average_run_seconds()
    return [
        ("Reroll", [
            ("Rerolls", [
                (str(total_rerolls), "accounts tried"),
                (format_elapsed(average) if average is not None else "—", "per account"),
                (str(stats.summons), "summons"),
            ]),
            ("Pulls", [
                (str(stats.moonlight_5), "5★ Moonlight"),
                (str(stats.heroes_5), "5★ in all"),
                (percent(stats.heroes_5, stats.summons), "5★ rate"),
                (str(stats.moonlight_4), "4★ Moonlight"),
                (str(stats.normal_4), "4★ normal"),
                (str(stats.artifacts_5), "5★ artifacts"),
            ]),
        ]),
        ("Story", [
            ("Push the story", [
                (str(stats.story_stages_won), "stages won"),
                (str(stats.story_fights_lost), "fights lost"),
                (win_rate(stats.story_stages_won, stats.story_fights_lost), "win rate"),
                (format_elapsed(stats.story_seconds), "time spent"),
                (format_duration(stats.average_stage_seconds()), "per stage"),
            ]),
            ("Spent", [
                (str(stats.leaves_used), "energy leaves"),
                (str(stats.story_revives), "skystone revives"),
            ]),
        ]),
        ("Abyss", [
            ("Climb the Abyss", [
                (str(stats.abyss_floors_won), "floors won"),
                (str(stats.abyss_fights_lost), "fights lost"),
                (win_rate(stats.abyss_floors_won, stats.abyss_fights_lost), "win rate"),
                (format_elapsed(stats.abyss_seconds), "time spent"),
                (format_duration(stats.average_floor_seconds()), "per floor"),
            ]),
        ]),
        ("Theater", [
            ("Moonlight Theater", [
                (str(stats.theater_acts), "acts watched"),
                (str(stats.theater_tickets), "tickets bought"),
                (format_gold(stats.theater_tickets * TICKET_PRICE), "gold on tickets"),
                (format_elapsed(stats.theater_seconds), "time spent"),
                (format_duration(stats.average_act_seconds()), "per act"),
            ]),
        ]),
        ("Arena", [
            ("Auto arena", [
                (str(stats.arena_fights), "fights"),
                (str(stats.arena_wins), "fights won"),
                (percent(stats.arena_wins, stats.arena_fights), "win rate"),
                (str(stats.arena_quick), "quick matches"),
                (str(stats.arena_skystones), "skystones on flags"),
                (format_duration(stats.average_arena_seconds()), "per fight"),
            ]),
        ]),
        ("Shop", [
            ("Shop refresh", [
                (str(stats.shop_refreshes), "refreshes"),
                (str(stats.shop_refreshes * SHOP_REFRESH_PRICE), "skystones spent"),
                (str(stats.shop_bookmarks), "bookmarks bought"),
                (str(stats.shop_medals), "medals bought"),
                (format_gold(stats.shop_gold), "gold spent"),
                (format_duration(stats.average_refresh_seconds()), "per refresh"),
            ]),
        ]),
        ("Penguins", [
            ("Buy penguins", [
                (str(stats.penguin_purchases), "purchases"),
                (str(stats.penguin_purchases * 50), "penguins"),
                (f"{stats.penguin_stigma:,}", "stigma spent"),
            ]),
            ("By kind", [
                (str(stats.penguin_newbie), "newbie penguins"),
                (str(stats.penguin_experienced), "experienced penguins"),
                (str(stats.penguin_epic), "epic penguins"),
            ]),
        ]),
        ("Huche", [
            ("Huche shop", [
                (str(stats.huche_purchases), "charms bought"),
                (format_gold(stats.huche_gold), "gold spent"),
            ]),
            *[
                (group, [(str(getattr(stats, f"huche_{kind}")), label) for kind, label in HUCHE_CHARMS if kind.startswith(prefixes)])
                for group, prefixes in (("Epic", ("epic",)), ("Greater", ("greater",)), ("Lesser and powders", ("lesser", "powder")))
            ],
        ]),
        ("Gear", [
            ("Pieces", [
                (str(stats.gear_judged), "judged"),
                (str(stats.gear_locked), "locked"),
                (str(stats.gear_crafted), "crafted"),
                (str(stats.gear_sold), "sold"),
                (str(stats.gear_extracted), "extracted"),
            ]),
            ("Enhancing and crafting", [
                (str(stats.gear_enhanced), "tiers enhanced"),
                (format_gold(stats.gear_gold), "gold spent"),
            ]),
        ]),
    ]


# The stats tab that matches each mode of the home tab.
STATS_TAB_OF_GOAL = {"summons": "Reroll", "abyss": "Reroll", "story": "Story", "climb": "Abyss", "theater": "Theater", "arena": "Arena", "shop": "Shop", "penguins": "Penguins", "huche": "Huche", **{goal: "Gear" for goal in GEAR_GOALS}, ADVISOR_GOAL: "Gear"}
# The settings, one tab each, and the tab that matches a mode of the home tab.
SETTINGS_TABS = ("Reroll", "Theater", "Arena", "Gear", "Advanced")
SETTINGS_TAB_OF_GOAL = {"theater": "Theater", "arena": "Arena", **{goal: "Gear" for goal in GEAR_GOALS}, ADVISOR_GOAL: "Gear"}
# The tabs of the window, Home first, and the pages of the Gear settings.
TABS = ("Home", "Stats", "Log", "Settings", "Help")
# The game's menu glyphs beside the pages of the rail, in ui/glyphs.
TAB_ICONS = {"Stats": "reputation.png", "Log": "board.png", "Settings": "settings.png", "Help": "orbis.png"}
# The Help page: a few plain steps per mode, in the rail's order. Only what the bot does today.
HELP = (
    ("Start here", (
        "Set the game to English (Settings > Language): the bot reads English screens.",
        "Leave the game window open, not minimized. The bot sizes it to 1280×720 itself.",
        "STOVE runs as administrator, so the bot asks for it too. Steam asks nothing.",
        "Pick a mode on the left, check its settings under This run, then press Start.",
        "Stop stops it at once. The bot never takes your mouse or keyboard.",
    )),
    ("Summon reroll", (
        "In Settings > Reroll: choose your server and what makes an account worth keeping.",
        "The reset erases the account on that server, for good.",
        "Plays a new account up to the summons, pulls the free ones, then keeps it or starts over.",
        "\"Erase this account and start over…\" asks for two clicks.",
    )),
    ("Abyss 45 reroll", (
        "Same settings and keep rules as Summon reroll.",
        "Plays the story, the Abyss up to floor 45 and the pass, then the summons.",
        "Much longer per account than Summon reroll.",
    )),
    ("Push the story", (
        "It picks up where your story stands, even if the game opens a cleared stage.",
        "Stops after 3 losses in a row on one stage: strengthen your team, then restart.",
        "Skystone revives per fight: 0 means it never pays.",
        "Never erases the account.",
    )),
    ("Climb the Abyss", (
        "Climbs from your current floor with your team.",
        "Stops after 3 losses in a row.",
        "Never erases the account.",
    )),
    ("Moonlight Theater", (
        "Watches the acts in order, story by story. Fights run on auto.",
        "Acts to watch: 0 watches everything left.",
        "Buys a ticket (500,000 gold) when an act needs one, unless turned off, never under the gold set in Settings > Theater.",
    )),
    ("Auto arena", (
        "Fights NPCs, or players if you turn it on (that changes your ranking).",
        "Uses Quick Match while the game allows it, then fights on auto.",
        "Fights to do: 0 fights until the flags run out.",
        "Flag refills (friendship, skystones) only as allowed in Settings > Arena.",
    )),
    ("Shop refresh", (
        "Start from the lobby.",
        "Buys every Covenant Bookmarks and Mystic Medals in the Secret Shop, then refreshes it for 3 skystones.",
        "Set the refreshes: the window shows their cost in skystones.",
        "Optional stops: a number of bookmarks or medals. It also stops when gold or skystones run out.",
    )),
    ("Buy penguins", (
        "Start from the lobby.",
        "Goes to Sanctuary > Forest of Souls > Growth Altar, and buys the Penguin Nest 50 at a time.",
        "One purchase: 50 penguins for 5,100 stigma.",
        "Stop after a number of purchases, or after an amount of stigma: choose under This run.",
        "It also stops when the stigma runs out.",
    )),
    ("Huche shop", (
        "Start from the lobby. Tirel Castle in Chaos, Area 1 must be explored, with a labyrinth team set.",
        "Teleports to the waypoint by Huche (3 morale), goes west, and fights on auto if a fight is on the way.",
        "Buys every charm and enhancement powder Huche sells, with gold. Skips his artifacts.",
        "Then yields the labyrinth: the next run meets no fight on the way.",
        "Huche's sold items come back once a day.",
    )),
    ("Craft gear", (
        "Choose the hunt, the piece and how many to craft.",
        "Crafts at the Steel Workshop, then reads each result and removes the bad ones if you ask.",
        "Stops when gold, materials or your limit run out.",
        "Dry run: judges and logs every piece, touches nothing.",
    )),
    ("Enhance gear", (
        "Does what Sell gear does, then enhances the promising pieces 16 at a time.",
        "After each tier it reads them again: sells the ones that failed, locks the finished good ones.",
        "Uses gold. Try a dry run first.",
    )),
    ("Sell gear", (
        "Reads every card no hero wears and that is not locked.",
        "Extracts fresh pieces not worth rolling, sells enhanced ones that failed. Enhances nothing.",
        "Try a dry run first, or Sort review: it ticks what it would remove, then stops so you can check.",
        "What counts as good is set in Settings > Gear.",
    )),
    ("Account", (
        "Start from the lobby. It reads every +15 piece, worn and locked ones too. Sells nothing.",
        "Shows your account's level and how good your gear is for each role.",
        "Set how many heroes you gear per role, then press Apply these targets: the gear modes and the Gear overlay use them.",
        "Lists the locked or worn pieces no longer worth keeping.",
    )),
    ("Gear overlay", (
        "You play, it watches. It never clicks.",
        "Open the Inventory, Arrange, Bulk Enhancement, Enhance or a craft result.",
        "Each card gets a tag in the game; the window says why.",
        "SPEED: enhance it for its speed alone. ENHANCE: it is good for a role.",
        "Set the Inventory's Display Format to Full.",
    )),
)
GEAR_PAGES = ("Rules", "Roles", "Run")
CHASE_WORDS = {"speed": "speed", "score": "gear score", "both": "speed and gear score"}
# The game's own icons in ui/icons, shown beside the numbers whose caption names them, rather than plain words.
CAPTION_ICONS = {
    "skystones spent": "skystone.png",
    "penguins": "penguin.png",
    "newbie penguins": "penguin_newbie.png",
    "experienced penguins": "penguin_experienced.png",
    "epic penguins": "penguin_epic.png",
    "stigma spent": "stigma.png",
    "charms bought": "charm_lesser_equipment.png",
    "charms": "charm_lesser_equipment.png",
    **{label: f"charm_{kind}.png" for kind, label in HUCHE_CHARMS},
    "bookmarks bought": "covenant_bookmark.png",
    "bookmarks": "covenant_bookmark.png",
    "medals bought": "mystic_medal.png",
    "medals": "mystic_medal.png",
    "gold spent": "gold.png",
    "5★ Moonlight": "moonlight.png",
    "5★ Moonlight pulled": "moonlight.png",
    "4★ Moonlight": "moonlight.png",
    "5★ in all": "star.png",
    "5★ artifacts": "star.png",
    "energy leaves": "energy_leaf.png",
    "skystone revives": "skystone.png",
    "acts watched": "film.png",
    "act watched": "film.png",
    "tickets bought": "ticket.png",
    "ticket bought": "ticket.png",
    "gold on tickets": "gold.png",
    "fights": "arena_flag.png",
    "fight": "arena_flag.png",
    "skystones on flags": "skystone.png",
}


def idle_metrics(snapshot: StatusSnapshot) -> list[tuple[str, str]]:
    """Return the three numbers shown at rest for the rerolls, as (value, caption)."""
    return [
        (str(snapshot.total_rerolls), "accounts tried"),
        (str(snapshot.stats.moonlight_5), "5★ Moonlight pulled"),
        (format_duration(snapshot.stats.average_run_seconds()), "per account"),
    ]


def story_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last story session, or of all of them before the first one in this window."""
    if not progress.first_stage:
        return "All sessions", [
            (str(stats.story_stages_won), "stages won"),
            (str(stats.story_fights_lost), "fights lost"),
            (format_duration(stats.average_stage_seconds()), "per stage"),
        ]
    start = split_stage(progress.first_stage)
    end = split_stage(progress.last_stage)
    line = "Last session"
    if start is not None:
        line += f": {start[0]}, {start[1]}"
        if end is not None and end[1] != start[1]:
            line += f" to {end[1]}"
    return line, [
        (str(progress.stages_won), "stages won" if progress.stages_won != 1 else "stage won"),
        (str(progress.fights_lost), "fights lost" if progress.fights_lost != 1 else "fight lost"),
        (format_duration(seconds), "time"),
    ]


def climb_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last Abyss climb, or of all of them before the first one in this window."""
    if progress.first_floor is None and not progress.fights_lost:
        return "All climbs", [
            (str(stats.abyss_floors_won), "floors won"),
            (str(stats.abyss_fights_lost), "fights lost"),
            (format_duration(stats.average_floor_seconds()), "per floor"),
        ]
    line = "Last climb"
    if progress.first_floor is not None:
        if progress.abyss_floor != progress.first_floor:
            line += f": floors {progress.first_floor} to {progress.abyss_floor}"
        else:
            line += f": floor {progress.first_floor}"
    return line, [
        (str(progress.floors_won), "floors won" if progress.floors_won != 1 else "floor won"),
        (str(progress.fights_lost), "fights lost" if progress.fights_lost != 1 else "fight lost"),
        (format_duration(seconds), "time"),
    ]


def theater_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last Moonlight Theater run."""
    return "Last session", [
        (str(progress.acts_watched), "acts watched" if progress.acts_watched != 1 else "act watched"),
        (str(progress.tickets_bought), "tickets bought" if progress.tickets_bought != 1 else "ticket bought"),
        (format_duration(seconds), "time"),
    ]


def arena_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last NPC Challenge run."""
    return "Last session", [
        (str(progress.arena_fights), "fights" if progress.arena_fights != 1 else "fight"),
        (str(progress.arena_wins), "wins" if progress.arena_wins != 1 else "win"),
        (format_duration(seconds), "time"),
    ]


def penguin_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last penguin run."""
    return "Last session", [
        (str(progress.penguin_purchases), "purchases" if progress.penguin_purchases != 1 else "purchase"),
        (str(progress.penguin_purchases * 50), "penguins"),
        (f"{progress.penguin_purchases * 5100:,}", "stigma spent"),
    ]


def huche_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last Huche shop run."""
    return "Last session", [
        (str(progress.huche_bought), "charms"),
        (format_gold(progress.huche_gold), "gold spent"),
    ]


def shop_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last Shop refresh."""
    return "Last session", [
        (str(progress.shop_refreshes), "refreshes" if progress.shop_refreshes != 1 else "refresh"),
        (str(progress.shop_bookmarks), "bookmarks"),
        (str(progress.shop_medals), "medals"),
    ]


def gear_summary(progress: Progress, seconds: float, stats: Stats) -> tuple[str, list[tuple[str, str]]]:
    """Return the line and the numbers of the last gear run."""
    return "Last session", [
        (str(progress.gear_judged), "pieces judged" if progress.gear_judged != 1 else "piece judged"),
        (str(progress.gear_removed), "sold or extracted"),
        (format_gold(progress.gear_gold), "gold spent"),
    ]


def advice_pieces(advice: ScreenAdvice | None) -> list[Advice]:
    """Return every piece the advisor shows: the one looked at (unless it is also a card), then the cards."""
    if advice is None:
        return []
    focus = [advice.focus] if advice.focus is not None and advice.focus.box is None else []
    return focus + list(advice.cards)


def advice_metrics(advice: ScreenAdvice | None) -> list[tuple[str, str]]:
    """Return the advisor's three numbers: the pieces on screen to enhance, to keep, and to get rid of."""
    pieces = advice_pieces(advice)

    def count(actions: tuple[str, ...]) -> str:
        return str(sum(1 for piece in pieces if piece.action in actions))

    return [(count(("enhance", "speed")), "to enhance"), (count(("keep", "lock")), "to keep"), (count(("sell", "extract")), "to sell or extract")]


PERCENT_STATS = ("attack_percent", "defense_percent", "health_percent", "crit_chance", "crit_damage", "effectiveness", "effect_resistance")


def verdict_word(piece: Advice) -> str:
    """Return the verdict as the window writes it: a locked piece worth keeping is "Kept", not to lock again, and a
    kept piece the rules would remove only "could" go."""
    word = ACTION_LABELS.get(piece.action, "")
    if piece.read and piece.locked and piece.action in ("keep", "lock"):
        return "Kept"
    if piece.read and (piece.locked or piece.equipped) and piece.action in ("sell", "extract"):
        return f"Could {word.lower()}"
    return word


def piece_view(piece: Advice) -> dict[str, Any]:
    """Return one piece as the page draws it: where it is, the verdict and why, and what was read."""
    flags = [word for word, on in (("Worn", piece.equipped), ("Locked", piece.locked), ("Ticked", piece.ticked)) if on]
    view: dict[str, Any] = {
        "where": piece.where, "action": piece.action, "label": verdict_word(piece), "reason": piece.reason,
        "flags": flags, "focus": piece.focus, "title": "", "main": "", "subs": [], "score": "", "speed_reach": "",
    }
    if not piece.read:
        return view
    tier = "" if piece.item_level == 85 else f" · level {piece.item_level}"
    set_name = f" · {piece.set_name.capitalize()} set" if piece.set_name else ""
    view["title"] = f"{piece.grade.capitalize()} {piece.slot} +{piece.level}{set_name}{tier}"
    view["main"] = STAT_LABELS.get(piece.main_stat, piece.main_stat)
    view["subs"] = [
        [
            STAT_LABELS.get(name, name),
            f"{value:g}{'%' if name in PERCENT_STATS else ''}",
            f"({piece.rolls[name]})" if piece.rolls and piece.rolls.get(name) else "",
        ]
        for name, value in piece.substats.items()
    ]
    view["score"] = f"{piece.score:g}"
    view["speed_reach"] = f"{piece.speed_reach:g}" if piece.speed_reach else ""
    view["summary"] = piece.summary
    view["role"] = piece.role.removeprefix("a ").removeprefix("an ")
    view["main_icon"] = icon_of(piece.main_stat)
    view["main_good"] = piece.main_good
    view["efficiency"] = f"{piece.efficiency:g}"
    view["stats"] = [stat_row(piece, name, value) for name, value in piece.substats.items()]
    view["roles"] = [{"role": line.role, "passes": line.passes, "text": line.text} for line in piece.roles]
    return view


def icon_of(name: str) -> str:
    """Return the file of a stat's icon in ui/icons: one icon for a stat, flat or %."""
    return name.removesuffix("_percent") + ".png"


def stat_row(piece: Advice, name: str, value: float) -> dict[str, Any]:
    """Return one substat as the details draw it: useful, useless, or replaced by the stone (with what it becomes)."""
    shown = f"{value:g}{'%' if name in PERCENT_STATS else ''}"
    # As the game writes it: the enhancement rolls a substat took, none shown when it took none.
    rolls = f"({piece.rolls[name]})" if piece.rolls and piece.rolls.get(name) else ""
    row: dict[str, Any] = {"name": STAT_LABELS.get(name, name), "value": shown, "rolls": rolls, "icon": icon_of(name), "state": "good"}
    if piece.swap is not None and name == piece.swap[0]:
        new, worth = piece.swap[1], piece.swap[2]
        row.update(state="swapped", into=STAT_LABELS.get(new, new), into_value=f"{worth:g}{'%' if new in PERCENT_STATS else ''}")
    elif name in piece.dead:
        row["state"] = "dead"
    return row


def tile_view(piece: Advice, selected: bool) -> dict[str, Any]:
    """Return one card of the window's grid: its picture, level and verdict, at its place."""
    word = verdict_word(piece) if piece.read else ("Locked" if piece.locked else "Worn" if piece.equipped else "?")
    return {
        "where": piece.where, "action": piece.action if piece.read else "", "word": word, "thumb": piece.thumb,
        "level": piece.level if piece.read else 0, "locked": piece.locked, "selected": selected,
        "row": piece.cell[0] if piece.cell else None, "column": piece.cell[1] if piece.cell else None,
        # Its best role and efficiency there, as a number and a bar on the card.
        "efficiency": round(piece.efficiency) if piece.read and piece.role else None,
        "role": ROLE_SHORT.get(ROLE_KEYS.get(piece.role, ""), piece.role.removeprefix("a ")) if piece.read else "",
    }


def selected_piece(advice: ScreenAdvice, picked: str) -> Advice | None:
    """Return the piece the details show: the card clicked in the window, else the one selected in the game (its detail
    panel, or the Enhance screen's piece), else the first card."""
    chosen = next((card for card in advice.cards if card.where == picked), None)
    if chosen is not None:
        return chosen
    if advice.focus is not None:
        return next((card for card in advice.cards if card.focus and card.read), advice.focus)
    return advice.cards[0] if advice.cards else None


def advice_view(advice: ScreenAdvice | None, note: str, picked: str = "") -> dict[str, Any]:
    """Return what the advisor shows: the screen, the cards in a grid like the game's, and one piece in detail."""
    if advice is None:
        return {"screen": "", "note": note or "Looking at the game…", "hint": "", "focus": None, "cards": [], "grid": [], "detail": None}
    if not note and advice.screen == "other":
        note = "Open the Inventory, Enhance, Bulk Enhancement or a craft's result."
    chosen = selected_piece(advice, picked)
    return {
        "screen": advice.name,
        "note": note,
        "hint": advice.hint,
        "focus": piece_view(advice.focus) if advice.focus is not None else None,
        "cards": [piece_view(card) for card in advice.cards],
        "grid": [tile_view(card, chosen is not None and card.where == chosen.where and card.box == chosen.box) for card in advice.cards],
        "detail": piece_view(chosen) if chosen is not None else None,
    }


def run_metrics(goal: str, snapshot: StatusSnapshot, progress: Progress) -> list[tuple[str, str]]:
    """Return the three numbers shown during a run of this goal, as (value, caption)."""
    if goal == ACCOUNT_GOAL:
        return [
            (str(progress.account_read), "+15 pieces read" if progress.account_read != 1 else "+15 piece read"),
            (str(progress.account_unread), "unread"),
            (str(progress.account_skipped), "other item levels"),
        ]
    if goal in GEAR_GOALS:
        return [
            (str(progress.gear_judged), "pieces judged" if progress.gear_judged != 1 else "piece judged"),
            (str(progress.gear_enhanced), "tiers enhanced" if progress.gear_enhanced != 1 else "tier enhanced"),
            (str(progress.gear_removed), "sold or extracted"),
        ]
    if goal == "arena":
        per_fight = snapshot.elapsed_seconds / progress.arena_fights if progress.arena_fights else None
        return [
            (str(progress.arena_fights), "fights" if progress.arena_fights != 1 else "fight"),
            (str(progress.arena_wins), "wins" if progress.arena_wins != 1 else "win"),
            (format_duration(per_fight), "per fight"),
        ]
    if goal == "shop":
        return [
            (str(progress.shop_refreshes), "refreshes" if progress.shop_refreshes != 1 else "refresh"),
            (str(progress.shop_bookmarks), "bookmarks"),
            (str(progress.shop_medals), "medals"),
        ]
    if goal == "penguins":
        return [
            (str(progress.penguin_purchases), "purchases" if progress.penguin_purchases != 1 else "purchase"),
            (str(progress.penguin_purchases * 50), "penguins"),
            (f"{progress.penguin_purchases * 5100:,}", "stigma spent"),
        ]
    if goal == "huche":
        return [
            (str(progress.huche_bought), "charms"),
            (format_gold(progress.huche_gold), "gold spent"),
        ]
    if goal == "theater":
        per_act = snapshot.elapsed_seconds / progress.acts_watched if progress.acts_watched else None
        return [
            (str(progress.acts_watched), "acts watched" if progress.acts_watched != 1 else "act watched"),
            (str(progress.tickets_bought), "tickets bought" if progress.tickets_bought != 1 else "ticket bought"),
            (format_duration(per_act), "per act"),
        ]
    if goal == "climb":
        per_floor = snapshot.elapsed_seconds / progress.floors_won if progress.floors_won else None
        return [
            (str(progress.floors_won), "floors won" if progress.floors_won != 1 else "floor won"),
            (str(progress.fails_in_a_row), "losses in a row" if progress.fails_in_a_row > 1 else "loss in a row"),
            (format_duration(per_floor), "per floor"),
        ]
    if goal == "story":
        per_stage = snapshot.elapsed_seconds / progress.stages_won if progress.stages_won else None
        return [
            (str(progress.stages_won), "stages won" if progress.stages_won != 1 else "stage won"),
            (str(progress.fails_in_a_row), "losses in a row" if progress.fails_in_a_row > 1 else "loss in a row"),
            (format_duration(per_stage), "per stage"),
        ]
    return [
        (str(snapshot.session_rerolls), "accounts this session"),
        (str(snapshot.stats.moonlight_5), "5★ Moonlight pulled"),
        (format_duration(snapshot.stats.average_run_seconds()), "per account"),
    ]


def route_line(progress: Progress, snapshot: StatusSnapshot) -> str:
    """Say where the Abyss route stands, and when the account should end if that is known."""
    done = progress.route_steps_done or 0
    text = f"Route: step {min(done + 1, ROUTE_STEP_COUNT)} of {ROUTE_STEP_COUNT}"
    average = snapshot.stats.average_run_seconds()
    if snapshot.account_started_at is not None and average is not None:
        text += f" · done around {datetime.fromtimestamp(snapshot.account_started_at + average):%H:%M}"
    return text


def export_log(log_dir: Path, destination: Path) -> None:
    """Write the whole log to one file, oldest part first, including what rotated out of bot.log."""
    current = log_dir / LOG_FILE_NAME
    numbered = []
    for path in log_dir.glob(f"{LOG_FILE_NAME}.*"):
        suffix = path.suffix[1:]
        if suffix.isdigit():
            numbered.append((int(suffix), path))
    parts = [path for _, path in sorted(numbered, reverse=True)] + [current]
    with destination.open("wb") as output:
        for part in parts:
            if part.is_file():
                output.write(part.read_bytes())



def shop_line(progress: Progress) -> tuple[str, dict[str, Any] | None]:
    """Say which refresh the Shop refresh is on, with a bar of the planned refreshes and the skystones spent."""
    done = progress.shop_refreshes
    planned = progress.shop_planned
    if not planned:
        return (f"Refresh {done}" if done else "Reading the first shop" if progress.doing else "On the way to the Secret Shop"), None
    detail = f"Refresh {min(done + 1, planned)} of {planned}"
    bar = {
        "text": f"{done} of {planned} refreshes · {done * SHOP_REFRESH_PRICE} of {planned * SHOP_REFRESH_PRICE} skystones",
        "fraction": min(1.0, done / planned),
    }
    return detail, bar


def penguin_line(progress: Progress) -> tuple[str, dict[str, Any] | None]:
    """Say which purchase the penguin mode is on, with a bar of the planned purchases or stigma."""
    done = progress.penguin_purchases
    if progress.penguin_kind is None:
        return (f"Purchase {done + 1}" if done else "On the way to the Growth Altar"), None
    if progress.penguin_kind == "purchases":
        planned = progress.penguin_amount
        spent = f"{done} of {planned} purchases · {done * 5100:,} stigma"
    else:
        planned = max(1, progress.penguin_amount // 5100)
        spent = f"{done * 5100:,} of {progress.penguin_amount:,} stigma · {done} purchases"
    detail = f"Purchase {min(done + 1, planned)} of {planned}" if done or progress.doing else "On the way to the Growth Altar"
    return detail, {"text": spent, "fraction": min(1.0, done / planned)}


def theater_line(progress: Progress) -> str:
    """Say which story, season and act the Theater is on."""
    if progress.theater_story is None:
        return "Watching acts, fights on auto" if progress.acts_watched else "On the way to the Moonlight Theater"
    return f"Story {progress.theater_story}, season {progress.theater_season} · act {progress.acts_watched + 1}"


def band(
    snapshot: StatusSnapshot, run_goal: str, progress: Progress, error: str = "", advice: ScreenAdvice | None = None, advice_note: str = "",
    mode: str = "",
    rest: bool = False,
) -> dict[str, Any]:
    """Return what the top of the home tab says: its look, headline, detail, current activity and the route bar.

    `mode` is the name of the mode open on Home when the band speaks of it: the band then names it (as its headline at
    rest, else at the start of the detail) and carries the way back to the modes, so the mode is not said twice.
    """
    # `rest`: the open mode is not the one that ran last; it is ready to start, whatever the last run said.
    display = Display(IDLE, "Ready to start", "") if rest else describe(snapshot)
    title, detail = display.title, error or display.detail
    route = None
    if snapshot.running and run_goal == "abyss" and snapshot.state == "ROUTE":
        if progress.abyss_floor is not None:
            detail = f"Abyss: floor {progress.abyss_floor} of {ABYSS_TOP_FLOOR}"
        route = {"text": route_line(progress, snapshot), "fraction": min(1.0, (progress.route_steps_done or 0) / ROUTE_STEP_COUNT)}
    if snapshot.running and display.kind == RUNNING:
        if run_goal == "climb":
            title = "Abyss"
            detail = f"Floor {progress.abyss_floor + 1} next" if progress.abyss_floor is not None else "On the way to the Abyss"
        elif run_goal == "arena":
            title = "Arena"
            detail = f"Fight {progress.arena_fights + 1}" if progress.arena_fights else "On the way to the Arena"
        elif run_goal == "shop":
            title = "Shop refresh"
            detail, route = shop_line(progress)
        elif run_goal == "penguins":
            title = "Penguins"
            detail, route = penguin_line(progress)
        elif run_goal == "huche":
            title = "Huche shop"
            bought = progress.huche_bought
            detail = f"{bought} {'charm' if bought == 1 else 'charms'} bought" if bought else "On the way to Huche"
        elif run_goal == "theater":
            title = "Theater"
            detail = theater_line(progress)
    doing = progress.doing if snapshot.running and snapshot.state in ("STORY", "ROUTE") and progress.doing else ""
    if snapshot.running and display.kind == RUNNING and run_goal in GEAR_GOALS:
        # The gear modes run in the ROUTE state: without this, they would read as the Abyss route.
        title = GOAL_BY_BOT_GOAL[run_goal].name
        detail, doing = doing or "On the way to the gear", ""
        if run_goal == ACCOUNT_GOAL:
            # The scan's own steps go on the big line, the six slot tabs on the bar.
            detail = "Reads every +15 piece, worn ones too, and changes nothing"
            doing = progress.doing if progress.account_doing else "On the way to the inventory"
            tabs = progress.account_tabs_done
            route = {"text": f"{tabs} of {ACCOUNT_TABS} slot tabs read", "fraction": tabs / ACCOUNT_TABS}
    if snapshot.running and display.kind == RUNNING and run_goal == ADVISOR_GOAL:
        title = "Gear overlay"
        detail = advice_note or (advice.name if advice is not None and advice.name else "Watching the game, never clicking")
        doing = ""
    if mode and display.kind == IDLE and not snapshot.running and title == "Ready to start":
        title, detail = mode, error or "Ready to start"
    elif mode and title != mode and not error:
        detail = f"{mode} · {detail}" if detail else mode
    if snapshot.running:
        kind_label = f"Running · {GOAL_BY_BOT_GOAL.get(run_goal, GOALS[0]).name}"
    else:
        kind_label = KIND_NAMES.get(display.kind, "")
    return {
        "kind": display.kind,
        "kind_label": kind_label,
        "timer": format_elapsed(snapshot.elapsed_seconds) if snapshot.running else "",
        "title": title,
        "detail": detail,
        "doing": doing,
        "route": route,
        "image": bool(snapshot.result_image) and not snapshot.running and display.kind in (KEPT, ATTENTION),
        "back": bool(mode),
    }


def mode_summary(bot_goal: str, settings: dict[str, Any]) -> str:
    """Return the rules a mode follows, in one line, for the Home tab. They are changed in Settings; '' when a mode has none there."""
    if bot_goal == "abyss":
        keep = f"Keeps the account with {settings['min_moonlight_5star']} or more 5★ Moonlight"
        if any(count > 0 for count, _ in settings["top_rules"]):
            keep += ", or top-ranked ones"
        if settings["stop_heroes"]:
            chosen = len(settings["stop_heroes"])
            keep += f", or one of {chosen} chosen hero{'es' if chosen != 1 else ''}"
        erase = f"otherwise erases it on {settings['server']}" if settings["reset_account"] else "never erases it"
        return f"{keep} · {erase}"
    if bot_goal == "theater":
        if not settings["theater_buy_tickets"]:
            return "Buys no tickets"
        return f"Buys tickets only above {settings['theater_min_gold_millions']:g}M gold"
    if bot_goal == "arena":
        refills = [name for name, on in (("friendship", settings["arena_friendship_refill"]), ("skystones", settings["arena_skystone_refill"])) if on]
        quick = "Quick Match when possible" if settings["arena_quick_match"] else "no Quick Match"
        return f"{quick} · {'refills with ' + ' and '.join(refills) if refills else 'no refills'}"
    if bot_goal == ADVISOR_GOAL:
        target = CHASE_WORDS.get(settings["gear_chase"], "speed and gear score")
        return f"Judges as the gear modes do: chases {target}, up to +{settings['gear_max_level']}, target speed {settings['gear_min_speed_speed_set']:g}, efficiency {settings['gear_efficiency']:g}%"
    if bot_goal == ACCOUNT_GOAL:
        judged = len(settings["gear_roles"])
        return f"Judges the {judged} role{'s' if judged != 1 else ''} ticked in Settings › Gear › Roles"
    if bot_goal in GEAR_GOALS:
        action = "sells" if settings["gear_new_bad_action"] == "sell" else "extracts"
        parts = [f"Chases {CHASE_WORDS.get(settings['gear_chase'], 'speed and gear score')}", f"up to +{settings['gear_max_level']}", f"{action} bad +0 pieces"]
        if settings["gear_only_new"]:
            parts.append("only +0 pieces")
        if settings["gear_max_pieces"]:
            parts.append(f"at most {settings['gear_max_pieces']} pieces")
        if settings["gear_max_gold_millions"]:
            parts.append(f"at most {settings['gear_max_gold_millions']:g}M gold")
        return " · ".join(parts)
    return ""


def account_advice(result: AccountResult, settings: dict[str, Any]) -> AccountAdvice:
    """Return the targets the evaluation recommends with the heroes geared set now."""
    return advise(
        result, settings["gear_efficiency"], settings["gear_role_heroes"], settings["gear_general_heroes"], settings["gear_openers"],
        settings["gear_min_speed_speed_set"], tuple(settings["gear_roles"]), settings["gear_role_efficiency"],
    )


def figure(value: float | None) -> str:
    """Return a % as the page shows it, a dash for none."""
    return "—" if value is None else f"{value:g}%"


def slot_line(targets: dict[str, float], unit: str = "") -> str:
    return " · ".join(f"{slot} {value:g}{unit}" for slot, value in targets.items())


# Role names short enough for a row of the targets' chart.
ROLE_SHORT = {
    "damage": "Damage", "damage_sure_crit": "Dmg sure crit", "damage_nocrit": "Dmg no crit", "damage_slow": "Dmg slow",
    "bruiser": "Crit bruiser", "bruiser_def": "Def bruiser", "bruiser_sure_crit": "Bruiser sure crit",
    "bruiser_nocrit": "Bruiser no crit", "support_res": "Support", "tank": "Tank", "support_eff": "Debuffer", "burner": "Burner",
}

# The role a piece's reason names ("a support"), back to its key.
ROLE_KEYS = {words: role for role, words in ROLE_WORDS.items()}


def target_cells(
    result: AccountResult, targets: dict[str, float], role: str, heroes: int, floor: float, seating: Seating | None = None,
) -> list[dict[str, Any]]:
    """Return one cell per slot for the targets' chart: the target, how many seats are filled (the pieces of the slot
    for the general row), and whether it is the user's own target (seats left empty, or the weakest piece under it)."""
    cells = []
    for slot in SLOTS:
        if role and seating is not None:
            count = len(seating.seated.get(role, {}).get(slot, []))
        else:
            count = len(best_values(result, slot))
        target = targets.get(slot, floor)
        cells.append({"slot": slot, "target": target, "count": count, "heroes": heroes, "own": target <= floor, "short": count < heroes})
    return cells


def unneeded_view(result: AccountResult, seating: Seating) -> list[dict[str, Any]]:
    """Return the pieces no seat takes, per slot, worst first: every hero geared has a better one."""
    rows = []
    for slot in SLOTS:
        pieces = sorted((result.pieces[index] for index in seating.unseated if result.pieces[index].slot == slot), key=lambda piece: piece.best or 0)
        if not pieces:
            continue
        rows.append({
            "slot": slot, "count": len(pieces),
            "pieces": [
                {
                    "set": piece.set_name or "unknown", "score": piece.score, "kept": piece.kept,
                    "role": role_name(max(piece.values, key=piece.values.get)), "value": max(piece.values.values()),
                }
                for piece in pieces
            ],
        })
    return rows


def account_view(result: AccountResult | None, settings: dict[str, Any]) -> dict[str, Any]:
    """Return the account evaluation's page: what the gear is worth now, the account's level, and the targets per slot
    that would improve it, from the heroes geared set on the page."""
    base = {
        "general_heroes": settings["gear_general_heroes"], "openers": settings["gear_openers"],
        "applied_any": bool(settings["gear_slot_targets"] or settings["gear_speed_targets"]),
        "slots": list(SLOTS),
    }
    if result is None:
        return {
            **base, "empty": True, "note": "No evaluation yet. Open the game's lobby, then press Start: the bot reads every +15 piece in under a minute.",
            "level": "", "figures": [], "roles": [], "speed": [], "general_cells": [], "can_apply": False, "applied": False,
        }
    advice = account_advice(result, settings)
    seating = seat_pieces(result, settings["gear_role_heroes"], tuple(settings["gear_roles"]))
    name, median = level(result)
    scores = stats([piece.score for piece in result.pieces])
    fastest = max((piece.speed for piece in result.pieces if piece.slot != "boots"), default=0)
    rows = []
    for role in settings["gear_roles"]:
        heroes = settings["gear_role_heroes"].get(role, 0)
        # A role's pieces are its seated ones: each piece counts in one role. A role with no seats shows all it suits.
        values = [value for slot in SLOTS for value in seating.seated[role][slot]] if role in seating.seated else role_values(result, role)
        found = stats(values)
        targets = advice.roles.get(role) or advice.general
        floor = settings["gear_role_efficiency"].get(role, auto_target(settings["gear_efficiency"], role)) if role in advice.roles else settings["gear_efficiency"]
        rows.append({
            "role": role, "name": role_name(role), "short": ROLE_SHORT.get(role, role), "niche": role not in MAIN_ROLES, "heroes": heroes,
            "count": found.count, "median": figure(found.median), "best": figure(found.best), "worst": figure(found.worst),
            "targets": slot_line(advice.roles[role]) if role in advice.roles else "follows the general targets",
            "values": sorted(round(value) for value in values), "median_value": found.median,
            "cells": target_cells(result, targets, role if role in advice.roles else "", heroes or settings["gear_general_heroes"], floor, seating),
        })
    speed = []
    for slot in SPEED_SLOTS:
        top = sorted((piece.speed for piece in result.pieces if piece.slot == slot), reverse=True)[:3]
        speed.append({
            "slot": slot, "fastest": " · ".join(f"{value:g}" for value in top) or "—", "values": top,
            "target": f"{advice.speed[slot]:g}" if slot in advice.speed else "—", "target_value": advice.speed.get(slot),
        })
    unread = f", {result.unread} unread" if result.unread else ""
    applied = settings["gear_slot_targets"] == {**advice.roles, "general": advice.general} and settings["gear_speed_targets"] == advice.speed
    return {
        **base,
        "note": f"{result.read} +15 pieces read on {result.when} ({result.reforged} reforged{unread}).",
        "level": name,
        "level_note": "" if median is None else f"Gear score: median {median:g} · best {scores.best:g} · worst {scores.worst:g} · fastest speed substat {fastest:g}",
        "empty": False,
        "figures": [] if median is None else [[f"{median:g}", "median gear score"], [f"{scores.best:g}", "best piece"], [f"{fastest:g}", "fastest speed"]],
        "score": {"median": median, "best": scores.best, "worst": scores.worst, "levels": [[lowest, label] for lowest, label in reversed(LEVELS)]},
        "general": slot_line(advice.general),
        "general_cells": target_cells(result, advice.general, "", settings["gear_general_heroes"], settings["gear_efficiency"]),
        "roles": rows, "speed": speed, "can_apply": True, "applied": applied,
        "unneeded": unneeded_view(result, seating), "unneeded_count": len(seating.unseated),
        **kept_view(result, settings),
    }


def kept_view(result: AccountResult, settings: dict[str, Any]) -> dict[str, Any]:
    """Return the locked or worn pieces the gear rules of now would sell or extract, the same judge as the advisor's
    "worn: sell" tags: better pieces came since they were kept. Judged with the settings as they are on the page, so
    applying new targets updates the list."""
    if not result.kept_checked:
        return {"kept_note": "This evaluation predates the locked and worn check: run it again to see them.", "outdated": []}
    try:
        policy = settings_policy(Settings.model_validate(settings))
    except ValueError:
        return {"kept_note": "A setting is not valid: fix it to judge the locked and worn pieces.", "outdated": []}
    rows = []
    for kept in result.kept:
        piece = kept.piece()
        decision = judge(piece, policy)
        if decision.action not in ("sell", "extract"):
            continue
        best = scan_piece(piece).best
        better = sum(1 for other in result.pieces if other.slot == piece.slot and other.best is not None and (best is None or other.best > best))
        rows.append({
            "kind": kept.kind, "action": decision.action,
            "title": f"{piece.grade.title()} {piece.slot}, {piece.set_name or 'unknown'} set",
            "stats": " · ".join([
                f"{STAT_LABELS.get(piece.main_stat, piece.main_stat)} main",
                *(f"{STAT_LABELS.get(name, name)} {value:g}{'%' if name in PERCENT_STATS else ''}" for name, value in piece.substats.items()),
            ]),
            "reason": explain(piece, policy, decision.reason),
            "better": f"{better} better {piece.slot} piece{'' if better == 1 else 's'} on the account" if better else "",
            "rank": better,
        })
    rows.sort(key=lambda row: -row["rank"])
    locked = sum(1 for kept in result.kept if kept.kind == "locked")
    worn = len(result.kept) - locked
    note = f"{locked} locked and {worn} worn +15 pieces judged with the targets of now: "
    note += f"{len(rows)} no longer worth keeping." if rows else "all still worth keeping."
    return {"kept_note": note, "outdated": rows}


SUMMARIES = {"climb": climb_summary, "theater": theater_summary, "arena": arena_summary, "shop": shop_summary, "penguins": penguin_summary, "huche": huche_summary, **{goal: gear_summary for goal in GEAR_GOALS}}
# The account evaluation's result is its page: no run numbers.
SUMMARIES[ACCOUNT_GOAL] = lambda progress, elapsed, stats: ("", [])



def role_options() -> list[dict[str, Any]]:
    """Return the roles the settings list, the main ones first, each with the score of its perfect piece and what its
    automatic efficiency adds to the general one."""
    rows = []
    for roles in FAMILIES.values():
        for role in roles:
            extra = 0 if role in MAIN_ROLES else NICHE_EXTRA
            rows.append({"key": role, "label": role_name(role), "main": role in MAIN_ROLES, "perfect": perfect_score(role), "extra": extra})
    return rows

class Panel:
    """The window's state and actions, without any drawing. The page calls these methods through pywebview.

    The page polls `view()`; every other public method is a click or an edit. They run on pywebview's threads,
    so each one holds the lock. The bot itself runs on its own worker thread.
    """

    def __init__(
        self,
        window_config: WindowConfig,
        settings: Settings,
        settings_path: Path,
        status: BotStatus,
        feed: LogFeed,
        stop_event: threading.Event,
        run_bot: Callable[[Settings, bool, str], None],
        logger: logging.Logger,
        log_dir: Path = Path("logs"),
        clock: Callable[[], float] = time.monotonic,
        is_admin: Callable[[], bool] = lambda: True,
        relaunch: Callable[[], bool] = lambda: False,
        advice_board: AdviceBoard | None = None,
        defaults: Settings | None = None,
    ) -> None:
        """Hold the bot's shared objects. `run_bot` gets the settings, whether to reset and the goal, on a worker thread.

        `is_admin` says whether the bot runs as administrator, `relaunch` starts it again as one (False if refused).
        `defaults` are the settings a fresh install starts with, which a page's reset puts back.
        """
        self.base_window_config = window_config
        self.window_config = window_config
        self.settings_path = settings_path
        self.status = status
        self.feed = feed
        self.stop_event = stop_event
        self.run_bot = run_bot
        self.logger = logger
        self.log_dir = log_dir
        self.clock = clock
        self.is_admin = is_admin
        self.relaunch = relaunch
        # What the gear advisor sees, written by its thread.
        self.advice_board = advice_board or AdviceBoard()
        # The card clicked in the advisor's grid, by its place; "" follows the game's selection.
        self.advice_pick = ""
        self.lock = threading.RLock()
        # What the window does outside the page: set by core/panel_window.py, harmless defaults for the tests.
        self.on_title: Callable[[str], None] = lambda title: None
        self.ask_save_path: Callable[[str], str | None] = lambda name: None
        self.open_path: Callable[[Path], None] = os.startfile
        self.on_quit: Callable[[], None] = lambda: None

        self.client: str | None = None
        self.client_label = ""
        self.title = "E7 Bot"
        self.state_path: Path | None = None
        self.worker: threading.Thread | None = None
        # What the settings inputs hold, saved once valid; the bot gets them at the next start.
        self.draft: dict[str, Any] = settings.model_dump()
        self.invalid: dict[str, str] = {}
        self.heroes = ranked_heroes(load_moonlight_heroes(), load_ranking())
        self.goal = ""
        self.stats_tab = "Reroll"
        self.settings_tab = "Reroll"
        self.gear_page = GEAR_PAGES[0]
        self.erase_armed_at: float | None = None
        self.reset_stats_armed_at: float | None = None
        self.reset_stats_note = RESET_STATS_NOTE
        self.defaults = (defaults or Settings()).model_dump()
        # The settings page whose reset waits for its second click, and since when.
        self.reset_settings_armed: tuple[str, float] | None = None
        self.reset_settings_note: tuple[str, str] = ("", "")
        self.export_note = ""
        self.export_failed = False
        self.error = ""
        # The log lines before this count belong to earlier runs.
        self.run_from = 0
        self.run_goal = "summons"
        self.was_running = False
        self.progress = Progress()
        self.events: list[tuple[str, str]] = []
        self.progress_at = -1
        if settings.client is not None:
            self.set_client(settings.client)

    # What the page draws

    def options(self) -> dict[str, Any]:
        """Return what never changes while the window is open: the modes, the choices of the settings, the tab names."""
        return {
            "tabs": list(TABS),
            "help": [[name, list(steps)] for name, steps in HELP],
            "tab_icons": TAB_ICONS,
            "goals": [
                {"key": goal.key, "label": goal.label, "name": goal.name, "description": goal.description, "group": goal.group, "available": goal.available, "icon": goal.icon,
                 "category": goal.category, "menu": goal.menu}
                for goal in GOALS
            ],
            "clients": [{"key": key, "label": client.label} for key, client in CLIENTS.items()],
            "servers": list(SERVERS),
            "hunts": [{"key": hunt, "label": hunt.capitalize()} for hunt in HUNTS],
            "slots": [{"key": slot, "label": slot.capitalize()} for slot in CRAFT_SLOTS],
            "craft_then": [{"key": key, "label": label} for key, label in CRAFT_THEN_LABELS.items()],
            "heroes": self.heroes,
            "stats_tabs": [name for name, _ in stat_tabs(Stats(), 0)],
            "settings_tabs": list(SETTINGS_TABS),
            "gear_pages": list(GEAR_PAGES),
            "roles": role_options(),
            "caption_icons": CAPTION_ICONS,
            "shop_refresh_price": SHOP_REFRESH_PRICE,
        }

    def view(self) -> dict[str, Any]:
        """Return everything the page shows now. The page calls it a few times a second."""
        with self.lock:
            snapshot = self.status.snapshot()
            self._follow_run(snapshot)
            self._wear_off()
            goal = self.chosen_goal()
            kept_idle = not snapshot.running and goal is not None and goal.bot_goal in KEPT_GOALS
            # At rest on a mode other than the last run's, the last result is not this mode's: it shows nothing of it.
            other_mode = not snapshot.running and goal is not None and goal.bot_goal != self.run_goal
            session = ""
            advising = snapshot.running and self.run_goal == ADVISOR_GOAL
            advice, advice_note = self.advice_board.latest() if advising else (None, "")
            if advising:
                numbers = advice_metrics(advice)
            elif not snapshot.running and goal is not None and goal.bot_goal == ADVISOR_GOAL:
                numbers = []
            elif snapshot.running:
                numbers = run_metrics(self.run_goal, snapshot, self.progress)
            elif other_mode:
                numbers = []
            elif kept_idle:
                last = self.progress
                session, numbers = SUMMARIES.get(goal.bot_goal, story_summary)(last, snapshot.elapsed_seconds, snapshot.stats)
            else:
                numbers = idle_metrics(snapshot)
            return {
                "title": self.title,
                "client": self.client,
                "needs_client": self.client is None,
                "running": snapshot.running,
                "goal": goal.key if goal else "",
                # During a run the user may look at the menu or another mode: the run's own view is shown only on its mode.
                "on_run": snapshot.running and goal is not None and goal.bot_goal == self.run_goal,
                "run_name": GOAL_BY_BOT_GOAL[self.run_goal].name if self.run_goal in GOAL_BY_BOT_GOAL else "",
                # The running mode's key, for the rail's running mark.
                "run_key": GOAL_BY_BOT_GOAL[self.run_goal].key if snapshot.running and self.run_goal in GOAL_BY_BOT_GOAL else "",
                "goal_name": goal.name if goal else "",
                "description": goal.description if goal else PICK_MODE_LABEL,
                "summary": mode_summary(goal.bot_goal, self.draft) if goal else "",
                "summary_tab": SETTINGS_TAB_OF_GOAL.get(goal.bot_goal, "Reroll") if goal else "",
                # A gear run that touches nothing must say so where the eye goes first.
                "dry_run": snapshot.running and self.run_goal in GEAR_GOALS and bool(self.draft["gear_dry_run"]),
                # The band names the open mode, unless it speaks of a run of another mode.
                "band": band(
                    snapshot, self.run_goal, self.progress, self.error, advice, advice_note,
                    goal.name if goal is not None and (not snapshot.running or goal.bot_goal == self.run_goal) else "",
                    rest=other_mode,
                ),
                "advice": advice_view(advice, advice_note, self.advice_pick) if advising else None,
                "account": account_view(load_result(self.log_dir / "gear"), self.draft) if goal is not None and goal.bot_goal == ACCOUNT_GOAL else None,
                "numbers": numbers,
                "session": session,
                "activity": self.events if snapshot.running else [],
                "can_start": not snapshot.running and goal is not None and not self.admin_missing(),
                "admin_note": ADMIN_NOTE.format(client=self.client_label) if self.admin_missing() else "",
                "can_erase": not snapshot.running and self.can_erase(),
                "erase_armed": self.erase_armed_at is not None,
                "erase_label": DELETE_ARMED_LABEL if self.erase_armed_at is not None else DELETE_LABEL,
                "kept_note": "This mode never erases the account." if snapshot.running and self.run_goal in KEPT_GOALS else "",
                "place": f"{self.client_label} · server {self.draft['server']}" if self.client_label else "No client chosen",
                "stats": stat_tabs(snapshot.stats, snapshot.total_rerolls),
                "stats_tab": self.stats_tab,
                "settings_tab": self.settings_tab,
                "gear_page": self.gear_page,
                "settings": self.draft,
                "invalid": self.invalid,
                "reset_stats_armed": self.reset_stats_armed_at is not None,
                "reset_stats_note": self.reset_stats_note,
                "reset_settings_armed": self.reset_settings_armed[0] if self.reset_settings_armed else "",
                "reset_settings_note": list(self.reset_settings_note),
                "export_note": self.export_note,
                "export_failed": self.export_failed,
                "log_added": self.feed.added,
            }

    def log_lines(self, seen: int) -> dict[str, Any]:
        """Return the log lines added since the page had `seen` of them, oldest first, and the new count.

        `reset` tells the page to throw away what it shows, when it missed more lines than the feed keeps.
        """
        with self.lock:
            added = self.feed.added
            lines = list(self.feed.lines)
            new = added - seen
            if new < 0 or new > len(lines):
                return {"added": added, "lines": lines, "reset": True}
            return {"added": added, "lines": lines[len(lines) - new:] if new else [], "reset": False}

    # Mode and tabs

    def chosen_goal(self) -> Goal | None:
        """Return the mode the user is in, or None on the main menu."""
        return GOAL_BY_KEY.get(self.goal)

    def pick_mode(self, key: str) -> None:
        """Enter a mode; the stats and settings tabs follow it. During a run, it only lets the user look: the run goes on."""
        with self.lock:
            goal = GOAL_BY_KEY.get(key)
            if goal is None or not goal.available:
                return
            self.goal = key
            self.erase_armed_at = None
            self.stats_tab = STATS_TAB_OF_GOAL.get(goal.bot_goal, self.stats_tab)
            self.settings_tab = SETTINGS_TAB_OF_GOAL.get(goal.bot_goal, "Reroll")

    def back(self) -> None:
        """Go back to the main menu with no mode chosen, even during a run, which goes on. The settings stay as they are."""
        with self.lock:
            self.goal = ""
            self.erase_armed_at = None

    def show_run(self) -> None:
        """Go back from the menu to the mode that is running."""
        with self.lock:
            run = GOAL_BY_BOT_GOAL.get(self.run_goal)
            if self.status.snapshot().running and run is not None:
                self.pick_mode(run.key)

    def select_goal(self, bot_goal: str) -> None:
        """Enter the mode that runs this goal of the bot. A goal the window does not offer changes nothing."""
        goal = GOAL_BY_BOT_GOAL.get(bot_goal)
        if goal is not None:
            self.pick_mode(goal.key)

    def show_stats(self, name: str) -> None:
        with self.lock:
            if name in STATS_TAB_OF_GOAL.values():
                self.stats_tab = name

    def show_settings(self, name: str) -> None:
        with self.lock:
            if name in SETTINGS_TABS:
                self.settings_tab = name

    def pick_piece(self, where: str) -> None:
        """Show this card of the advisor's grid in detail; the same card again goes back to the game's selection."""
        with self.lock:
            self.advice_pick = "" if where == self.advice_pick else where

    def show_gear_page(self, name: str) -> None:
        with self.lock:
            if name in GEAR_PAGES:
                self.gear_page = name

    # Start, stop, erase

    def kept_chosen(self) -> bool:
        """Return whether the chosen mode plays a kept account, which nothing may erase."""
        goal = self.chosen_goal()
        return goal is not None and goal.bot_goal in KEPT_GOALS

    def can_erase(self) -> bool:
        """Return whether the chosen mode may erase the account: only a reroll mode, never on the main menu."""
        return self.chosen_goal() is not None and not self.kept_chosen()

    def start(self, reset: bool = False) -> bool:
        """Save the settings and run the bot on a worker thread, resetting the account if asked. Returns whether it started."""
        with self.lock:
            if (self.worker is not None and self.worker.is_alive()) or self.status.snapshot().running:
                return False
            goal = self.chosen_goal()
            if goal is None or self.client is None or self.admin_missing():
                return False
            settings = self.read_settings()
            if settings is None:
                return False
            save_settings(settings, self.settings_path)
            self.stop_event.clear()
            self.erase_armed_at = None
            self.run_from = self.feed.added
            self.run_goal = goal.bot_goal
            self.progress = Progress()
            self.events = []
            self.progress_at = -1
            self.status.started()
            self.worker = threading.Thread(target=self.run_bot, args=(settings, reset, self.run_goal), name="bot", daemon=True)
            self.worker.start()
            return True

    def erase(self) -> bool:
        """Erase the account on a second click: a single one is too easy, and it cannot be undone. Returns whether it started."""
        with self.lock:
            self._wear_off()
            if not self.can_erase() or self.status.snapshot().running:
                return False
            if self.erase_armed_at is None:
                self.erase_armed_at = self.clock()
                return False
            self.erase_armed_at = None
            return self.start(reset=True)

    def stop(self) -> None:
        """Ask the bot to stop. It stops before its next input."""
        self.stop_event.set()

    def close(self) -> None:
        """Stop the bot and wait a little for it, as the window closes."""
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(STOP_WAIT_SECONDS)

    # Client and settings

    def set_client(self, client: str) -> None:
        """Aim the window at one client: its game window, its title, and its own stats and route."""
        self.client = client
        self.client_label = CLIENTS[client].label
        self.draft["client"] = client
        self.window_config = window_config_for(self.base_window_config, CLIENTS[client])
        self.title = f"E7 Bot · {self.client_label}"
        self.on_title(self.title)
        self.state_path = state_path(client, self.settings_path.parent)
        self.status.reload(load_total_rerolls(self.state_path), load_stats(self.state_path))

    def choose_client(self, client: str) -> bool:
        """Switch to the client the user picked, and save it at once. Never during a run."""
        with self.lock:
            if client not in CLIENTS or self.status.snapshot().running or (self.worker is not None and self.worker.is_alive()):
                return False
            self.set_client(client)
            self._save_if_valid()
            self.logger.info("client chosen: %s", self.client_label)
            if self.admin_missing():
                self.restart_as_admin()
            return True

    def admin_missing(self) -> bool:
        """Return whether the chosen client's game runs as administrator while the bot does not: it could not drive it."""
        return self.client is not None and CLIENTS[self.client].needs_admin and not self.is_admin()

    def restart_as_admin(self) -> bool:
        """Start the bot again as administrator and close this window. A refused prompt leaves the window as it is."""
        with self.lock:
            if self.status.snapshot().running or not self.relaunch():
                return False
            self.logger.info("started again as administrator, for %s", self.client_label)
            self.on_quit()
            return True

    def set_setting(self, name: str, value: Any) -> str:
        """Change one setting and save them all if they are valid. Returns the problem with this value, or ''."""
        with self.lock:
            if name not in Settings.model_fields or name in ("client", "stop_heroes", "top_rules"):
                return f"Unknown setting: {name}"
            if self.status.snapshot().running:
                return "Stop the bot to change the settings."
            self.draft[name] = value
            self.invalid.pop(name, None)
            try:
                Settings.model_validate(self.draft)
            except ValidationError as error:
                for problem in error.errors():
                    if problem["loc"] and problem["loc"][0] == name:
                        self.invalid[name] = problem["msg"]
            self._save_if_valid()
            return self.invalid.get(name, "")

    def set_role(self, role: str, on: bool, efficiency: Any = None) -> str:
        """Turn a role on or off and set its own efficiency; None or "" follows the general one again. Returns the
        problem, or ''."""
        with self.lock:
            if role not in ROLE_WEIGHTS:
                return "Unknown role"
            if self.status.snapshot().running:
                return "Stop the bot to change the settings."
            name = f"role.{role}"
            self.invalid.pop(name, None)
            roles = [key for key in ROLE_WEIGHTS if key in self.draft["gear_roles"] and key != role]
            if on:
                roles.append(role)
            self.draft["gear_roles"] = [key for key in ROLE_WEIGHTS if key in roles]
            own = dict(self.draft["gear_role_efficiency"])
            if efficiency in (None, ""):
                own.pop(role, None)
            elif isinstance(efficiency, (int, float)) and not isinstance(efficiency, bool) and 0 <= efficiency <= 100:
                own[role] = float(efficiency)
            else:
                self.invalid[name] = "From 0 to 100 %."
            self.draft["gear_role_efficiency"] = own
            self._save_if_valid()
            return self.invalid.get(name, "")

    def apply_account_scores(self) -> str:
        """Set the targets per slot the account evaluation recommends, per role, general and for speed. Returns the
        problem, or ''."""
        with self.lock:
            if self.status.snapshot().running:
                return "Stop the bot to change the settings."
            result = load_result(self.log_dir / "gear")
            if result is None:
                return "No account evaluation to apply: run it first."
            advice = account_advice(result, self.draft)
            self.draft["gear_slot_targets"] = {**advice.roles, "general": advice.general}
            self.draft["gear_speed_targets"] = dict(advice.speed)
            self._save_if_valid()
            self.logger.info("account targets applied: %s; speed %s", self.draft["gear_slot_targets"], advice.speed)
            return ""

    def clear_account_targets(self) -> str:
        """Drop the targets the evaluation applied: the roles' own % and the general one judge again. Returns the problem, or ''."""
        with self.lock:
            if self.status.snapshot().running:
                return "Stop the bot to change the settings."
            self.draft["gear_slot_targets"] = {}
            self.draft["gear_speed_targets"] = {}
            self._save_if_valid()
            return ""

    def set_role_heroes(self, role: str, heroes: Any) -> str:
        """Set how many heroes the user gears in a role; 0 makes it follow the general target. Returns the problem, or ''."""
        with self.lock:
            if role not in ROLE_WEIGHTS:
                return "Unknown role"
            if self.status.snapshot().running:
                return "Stop the bot to change the settings."
            # A wrong number changes nothing: the page shows the saved one again.
            if not (isinstance(heroes, int) and not isinstance(heroes, bool) and 0 <= heroes <= 1000):
                return "From 0 to 1000 heroes."
            self.draft["gear_role_heroes"] = {**self.draft["gear_role_heroes"], role: heroes}
            self._save_if_valid()
            return ""

    def set_top_rule(self, index: int, count: int, rank: int) -> str:
        """Change one rule 'at least count ML5 in the top rank'. Returns the problem, or ''."""
        with self.lock:
            if index not in (0, 1):
                return "Unknown rule"
            if self.status.snapshot().running:
                return "Stop the bot to change the settings."
            rules = ([list(rule) for rule in self.draft["top_rules"]] + [[0, 5], [0, 20]])[:2]
            rules[index] = [count, rank]
            name = f"top_rules.{index}"
            self.invalid.pop(name, None)
            if not (isinstance(count, int) and isinstance(rank, int) and 0 <= count <= 5 and 1 <= rank <= 69):
                self.invalid[name] = "From 0 to 5 heroes, in a top 1 to 69."
            self.draft["top_rules"] = [tuple(rule) for rule in rules]
            self._save_if_valid()
            return self.invalid.get(name, "")

    def set_hero(self, name: str, chosen: bool) -> None:
        """Choose or drop a hero that keeps the account. Those the search hides stay as they are."""
        with self.lock:
            if self.status.snapshot().running or name not in self.heroes:
                return
            heroes = set(self.draft["stop_heroes"])
            if chosen:
                heroes.add(name)
            else:
                heroes.discard(name)
            self.draft["stop_heroes"] = sorted(heroes)
            self._save_if_valid()

    def read_settings(self) -> Settings | None:
        """Return the settings the inputs hold, or None with a message if one is invalid."""
        self.error = ""
        if self.invalid:
            self.error = INVALID_SETTING
            return None
        try:
            return Settings.model_validate(self.draft)
        except ValidationError:
            self.error = INVALID_SETTING
            return None

    def _save_if_valid(self) -> None:
        """Keep the settings on disk as soon as they are all valid, so that nothing is lost on closing."""
        if self.invalid:
            return
        try:
            save_settings(Settings.model_validate(self.draft), self.settings_path)
            self.error = ""
        except (ValidationError, OSError):
            self.logger.debug("settings not saved", exc_info=True)

    def reset_settings(self, page: str, names: list[str]) -> None:
        """Ask once, then put the settings of one page back to their defaults; the other pages stay as they are."""
        with self.lock:
            self._wear_off()
            if self.status.snapshot().running:
                self.reset_settings_note = (page, "Stop the bot before resetting settings.")
                return
            known = [name for name in names if name in self.defaults and name not in NEVER_RESET]
            if not known:
                self.reset_settings_note = (page, "Nothing to reset on this page.")
                return
            if self.reset_settings_armed is None or self.reset_settings_armed[0] != page:
                self.reset_settings_armed = (page, self.clock())
                self.reset_settings_note = (page, "Click again within 5 seconds to confirm.")
                return
            self.reset_settings_armed = None
            for name in known:
                self.draft[name] = copy.deepcopy(self.defaults[name])
            # The errors of these settings go with their values, the role and rule rows' ones included.
            for key in list(self.invalid):
                if key.split(".")[0] in known or (key.startswith("role.") and "gear_roles" in known):
                    del self.invalid[key]
            self._save_if_valid()
            self.logger.info("settings reset to their defaults: %s", ", ".join(known))
            self.reset_settings_note = (page, "Back to the defaults.")

    def reset_stats(self) -> None:
        """Ask once, then forget every statistic. Never while the bot runs: it writes them as it goes."""
        with self.lock:
            self._wear_off()
            if self.status.snapshot().running:
                self.reset_stats_note = "Stop the bot before resetting the stats."
                return
            if self.reset_stats_armed_at is None:
                self.reset_stats_armed_at = self.clock()
                self.reset_stats_note = "Click again within 5 seconds to confirm. This cannot be undone."
                return
            self.reset_stats_armed_at = None
            if self.state_path is None:
                self.reset_stats_note = "Pick the game client first."
                return
            save_stats(self.status.reset_stats(), self.state_path)
            save_total_rerolls(0, self.state_path)
            self.reset_stats_note = "Stats reset."

    # Files

    def open_image(self) -> None:
        """Open the picture of the last summon result."""
        path = self.status.snapshot().result_image
        if path:
            self.open_path(Path(path).resolve())

    def open_gear_folder(self) -> None:
        """Open the folder of the gear screenshots, created if it does not exist yet."""
        folder = (self.log_dir / "gear").resolve()
        folder.mkdir(parents=True, exist_ok=True)
        self.open_path(folder)

    def export_journal(self) -> None:
        """Ask where to save the whole log, and say how it went."""
        chosen = self.ask_save_path(f"e7bot_log_{datetime.now():%Y%m%d_%H%M}.txt")
        if not chosen:
            return
        try:
            export_log(self.log_dir, Path(chosen))
            self.export_note, self.export_failed = f"Log exported: {chosen}", False
        except OSError as error:
            self.export_note, self.export_failed = f"Export failed: {error}", True

    # Inside

    def _run_lines(self) -> list[str]:
        """Return the log lines written since the run started."""
        count = self.feed.added - self.run_from
        if count <= 0:
            return []
        return list(self.feed.lines)[-count:]

    def _follow_run(self, snapshot: StatusSnapshot) -> None:
        """Read the run's progress from its log lines, and keep the last counts once it is over."""
        if snapshot.running and self.feed.added != self.progress_at:
            self.progress_at = self.feed.added
            lines = self._run_lines()
            self.progress = read_progress(lines)
            self.events = activity(lines, ACTIVITY_LINES)
        if self.was_running and not snapshot.running:
            self.progress = read_progress(self._run_lines())
        if snapshot.running:
            self.erase_armed_at = None
        self.was_running = snapshot.running

    def _wear_off(self) -> None:
        """Put back the buttons whose first click was not confirmed in time."""
        now = self.clock()
        if self.erase_armed_at is not None and now - self.erase_armed_at > ERASE_CONFIRM_SECONDS:
            self.erase_armed_at = None
        if self.reset_stats_armed_at is not None and now - self.reset_stats_armed_at > RESET_STATS_SECONDS:
            self.reset_stats_armed_at = None
            self.reset_stats_note = "Reset cancelled."
        if self.reset_settings_armed is not None and now - self.reset_settings_armed[1] > RESET_SETTINGS_SECONDS:
            self.reset_settings_note = (self.reset_settings_armed[0], "Reset cancelled.")
            self.reset_settings_armed = None

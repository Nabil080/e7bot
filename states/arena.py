"""NPC Challenge of the Arena on a kept account: fight until the flags run out, Quick Match when allowed.
The flag count is never read: Challenge either opens the team screen, or the refill popup when no flag is left."""

from __future__ import annotations

import re
import time

from core.config import State
from core.state import Context
from states.story import click_once, seen

# Lobby > Arena > Rookie Arena.
ARENA_POINT = (1203, 390)
HUB_REGION = (372, 632, 200, 76)
ROOKIE_POINT = (470, 640)
# The Rookie Arena: its menu on the right, the NPC Challenge list in the middle.
MENU_REGION = (1050, 80, 160, 76)
NPC_TAB_POINT = (1140, 218)
NPC_HEADER_REGION = (278, 80, 226, 76)
FIRST_NPC_POINT = (480, 225)
CHALLENGE_POINT = (640, 662)
# Challenge is dim until an opponent is picked: green mean measured at 40 dim, 88 lit.
CHALLENGE_REGION = (560, 645, 160, 35)
CHALLENGE_LIT_GREEN = 65
# "Hero with No Equipment", before a fight: its title, and Do Not Display, which goes on with the fight.
NO_GEAR_TITLE_REGION = (380, 135, 520, 56)
NO_GEAR_DO_NOT_DISPLAY_POINT = (859, 568)
# The popup of the week's rank reward, shown on the first visit of a week.
WEEKLY_REWARD_REGION = (380, 160, 520, 56)
# The team screen before a fight.
TEAM_CP_REGION = (474, 504, 144, 64)
FIGHT_POINT = (810, 661)
# The fight button is blue "Quick Match" when Quick Match is on and usable, green "Start" otherwise.
# Blue minus green measured +19 on Quick Match, -28 on Start.
FIGHT_BUTTON_REGION = (740, 645, 140, 35)
# The lightning button beside it is only ever clicked to turn Quick Match off: when no Quick Match is left,
# a click on it opens the offer to buy more.
QUICK_TOGGLE_POINT = (953, 661)
# "Today's Remaining Quick Match Chance(s) 10/10", shown only while Quick Match is on.
QUICK_LEFT_REGION = (505, 666, 95, 30)
MAX_QUICK_TOGGLES = 2
# The results of a fight and of a Quick Match.
VICTORY_REGION = (450, 195, 405, 105)
QUICK_VICTORY_REGION = (470, 115, 340, 115)
CONFIRM_REGION = (380, 560, 900, 160)
# One click per button in this time: the next screen can take a moment.
CLICK_WAIT_SECONDS = 2.5
# After a click, the next screen can take seconds to open (the quick match plays a short animation):
# meanwhile an unknown screen is waited for, never escaped.
TRANSITION_SECONDS = 6.0
# The result's title fades in after its Confirm button: a fight is judged once the title shows, or after this.
RESULT_WAIT_SECONDS = 2.0
# The flag refill popup, which Challenge opens when no flag is left. Friendship offer on the left, once a day;
# skystone offer on the right. The bot only buys the offer it chose once that offer is the framed one.
PURCHASE_REGION = (512, 120, 256, 84)
FRIENDSHIP_OFFER_REGION = (376, 298, 110, 110)
FRIENDSHIP_OFFER_POINT = (496, 372)
FRIENDSHIP_OWNED_REGION = (440, 410, 115, 26)
FRIENDSHIP_PRICE = 100
SKYSTONE_OFFER_REGION = (664, 298, 110, 110)
SKYSTONE_OFFER_POINT = (782, 372)
SKYSTONE_OWNED_REGION = (720, 410, 125, 26)
SKYSTONE_PRICE = 30
FLAGS_PER_REFILL = 5
LEFT_OFFER_FRAME = (376, 302, 240, 140)
RIGHT_OFFER_FRAME = (664, 302, 240, 140)
# Measured: 742 yellow pixels around the framed offer, 56 around the other.
FRAME_YELLOW_PIXELS = 300
BUY_POINT = (746, 550)
CANCEL_POINT = (532, 550)
MAX_OFFER_CLICKS = 2
# The popup grows in: its offers are read once it has been in view this long.
PURCHASE_SETTLE_SECONDS = 1.5
# The Opponents list of real players, when the settings ask to fight them.
OPPONENTS_TAB_POINT = (1140, 118)
# Challenge clicked this many times without the team screen or the refill popup: the bot stops.
MAX_CHALLENGE_CLICKS = 4


def arena_data(ctx: Context) -> dict:
    """Return the arena's progress in this run."""
    return ctx.route_data.setdefault("arena", {"fights": 0, "wins": 0, "quick": 0})


def read_number(ctx: Context, region: tuple[int, int, int, int]) -> int | None:
    """Read the first number of a counter such as "4/5" or "10/10", or None."""
    match = re.match(r"\d+", ctx.ocr.read(ctx.frame, region, whitelist="0123456789/", white_level=170))
    return int(match.group()) if match else None


def quick_is_on(ctx: Context) -> bool:
    """Return whether the team screen's fight button is the blue Quick Match one."""
    x, y, width, height = FIGHT_BUTTON_REGION
    blue, green, _ = ctx.frame[y : y + height, x : x + width].reshape(-1, 3).mean(axis=0)
    return float(blue) > float(green)


def challenge_is_lit(ctx: Context) -> bool:
    """Return whether Challenge is lit: an opponent is picked."""
    x, y, width, height = CHALLENGE_REGION
    return float(ctx.frame[y : y + height, x : x + width, 1].mean()) > CHALLENGE_LIT_GREEN


def click_after_wait(ctx: Context, key: str, point: tuple[int, int]) -> State:
    """Click a point, at most once per CLICK_WAIT_SECONDS for the same key."""
    data = arena_data(ctx)
    now = time.monotonic()
    if now - data.get(f"{key}_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data[f"{key}_at"] = now
    data["acted_at"] = now
    return click_once(ctx, *point) or State.ROUTE


def yellow_pixels(ctx: Context, region: tuple[int, int, int, int]) -> int:
    """Count the pixels of an offer's yellow selection frame in a region."""
    x, y, width, height = region
    area = ctx.frame[y : y + height, x : x + width].astype(int)
    return int(((area[:, :, 2] > 200) & (area[:, :, 1] > 190) & (area[:, :, 0] < 160)).sum())


def fights_left(ctx: Context) -> int | None:
    """Return the fights still to do to reach the target, or None without a target."""
    target = ctx.config.arena.target_fights
    return max(0, target - arena_data(ctx)["fights"]) if target else None


def owned(ctx: Context, region: tuple[int, int, int, int]) -> int | None:
    """Read an offer's "Owned: 9,862", or None."""
    match = re.search(r"\d[\d,]*", ctx.ocr.read(ctx.frame, region))
    return int(match.group().replace(",", "")) if match else None


def chosen_offer(ctx: Context) -> tuple[str | None, str]:
    """Return the offer to buy, "friendship" or "skystone", or None with the reason."""
    arena = ctx.config.arena
    reasons = []
    if not arena.friendship_refill:
        reasons.append("the friendship refill is turned off")
    elif ctx.find("arena_friendship_offer", FRIENDSHIP_OFFER_REGION) is None:
        reasons.append("no friendship offer (once a day)")
    elif (points := owned(ctx, FRIENDSHIP_OWNED_REGION)) is None or points < FRIENDSHIP_PRICE:
        reasons.append("not enough friendship points")
    else:
        return "friendship", ""
    left = fights_left(ctx)
    if not arena.skystone_refill:
        reasons.append("the skystone refill is turned off")
    elif left is None:
        # Without a target, skystones would be spent with no end.
        reasons.append("no target number of fights for skystones")
    elif left == 0:
        reasons.append("the target is reached")
    elif ctx.find("arena_skystone_offer", SKYSTONE_OFFER_REGION) is None:
        reasons.append("no skystone offer")
    elif (stones := owned(ctx, SKYSTONE_OWNED_REGION)) is None or stones < SKYSTONE_PRICE:
        reasons.append("not enough skystones")
    else:
        return "skystone", ""
    return None, "; ".join(reasons)


def purchase(ctx: Context) -> State:
    """Buy 5 flags with friendship points, or with skystones when allowed and needed, once that offer is framed."""
    data = arena_data(ctx)
    seen(ctx, "arena flag purchase")
    data["challenges"] = 0
    since = data.setdefault("purchase_since", time.monotonic())
    if time.monotonic() - since < PURCHASE_SETTLE_SECONDS:
        return State.ROUTE
    offer, refused = chosen_offer(ctx)
    if offer is not None:
        mine, other = (LEFT_OFFER_FRAME, RIGHT_OFFER_FRAME) if offer == "friendship" else (RIGHT_OFFER_FRAME, LEFT_OFFER_FRAME)
        if not yellow_pixels(ctx, mine) >= FRAME_YELLOW_PIXELS > yellow_pixels(ctx, other):
            # The other offer is the framed one: pick ours first, never buy otherwise.
            clicks = data.get("offer_clicks", 0)
            if clicks < MAX_OFFER_CLICKS:
                data["offer_clicks"] = clicks + 1
                return click_after_wait(ctx, "offer", FRIENDSHIP_OFFER_POINT if offer == "friendship" else SKYSTONE_OFFER_POINT)
            offer, refused = None, f"the {offer} offer cannot be selected"
    if offer is None:
        ctx.logger.info("arena: no flag refill (%s)", refused)
        data["no_refill"] = refused
        data.pop("purchase_since", None)
        return click_after_wait(ctx, "cancel", CANCEL_POINT)
    data["offer_clicks"] = 0
    now = time.monotonic()
    if now - data.get("buy_at", 0.0) < CLICK_WAIT_SECONDS * 2:
        return State.ROUTE
    data["buy_at"] = now
    data["acted_at"] = now
    data["refills"] = data.get("refills", 0) + 1
    data.pop("purchase_since", None)
    if offer == "friendship":
        ctx.logger.info("arena: 5 flags bought with %d friendship points", FRIENDSHIP_PRICE)
    else:
        data["skystones"] = data.get("skystones", 0) + SKYSTONE_PRICE
        ctx.logger.info("arena: 5 flags bought with %d skystones, %d in this run", SKYSTONE_PRICE, data["skystones"])
        if ctx.on_arena_skystones is not None:
            ctx.on_arena_skystones(SKYSTONE_PRICE)
    return click_once(ctx, *BUY_POINT) or State.ROUTE


def done_message(data: dict, why: str) -> str:
    """Return the line the bot stops with."""
    return (
        f"Arena: {why}, {data['fights']} fights ({data['wins']} won, {data['quick']} quick, "
        f"{data.get('refills', 0)} refills, {data.get('skystones', 0)} skystones) in this run"
    )


def result(ctx: Context, confirm) -> State:
    """Count the fight once, then leave its results with Confirm."""
    data = arena_data(ctx)
    if data.get("fighting"):
        won = ctx.find("arena_victory", VICTORY_REGION) is not None or ctx.find("arena_quick_victory", QUICK_VICTORY_REGION) is not None
        since = data.setdefault("result_since", time.monotonic())
        if not won and time.monotonic() - since < RESULT_WAIT_SECONDS:
            return State.ROUTE
        data.pop("result_since", None)
        data["fighting"] = False
        quick = data.get("quick_now", False)
        data["fights"] += 1
        data["wins"] += won
        data["quick"] += quick
        ctx.progressed = True
        ctx.logger.info("arena: fight %s%s, %d in this run", "won" if won else "lost", " (quick match)" if quick else "", data["fights"])
        if ctx.on_arena_fight is not None:
            ctx.on_arena_fight(won, quick)
    seen(ctx, "arena result")
    return click_after_wait(ctx, "confirm", confirm.center)


def team_screen(ctx: Context) -> State:
    """Start the fight, with Quick Match when the game offers it. The lightning button is only clicked to turn
    Quick Match off, when the settings say no or none is left today: never to turn it on."""
    data = arena_data(ctx)
    seen(ctx, "arena team")
    data["challenges"] = 0
    on = quick_is_on(ctx)
    if on and (not ctx.config.arena.quick_match or read_number(ctx, QUICK_LEFT_REGION) == 0):
        tries = data.get("toggles", 0)
        if tries < MAX_QUICK_TOGGLES:
            if time.monotonic() - data.get("toggle_at", 0.0) < CLICK_WAIT_SECONDS:
                return State.ROUTE
            if tries == 0:
                ctx.logger.info("arena: turning Quick Match off")
            data["toggles"] = tries + 1
            return click_after_wait(ctx, "toggle", QUICK_TOGGLE_POINT)
        if tries == MAX_QUICK_TOGGLES:
            data["toggles"] = tries + 1
            ctx.logger.info("arena: Quick Match does not turn off, the fight goes on with it")
    elif not on:
        data["toggles"] = 0
    now = time.monotonic()
    if now - data.get("fight_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data["fight_at"] = now
    data["acted_at"] = now
    data["fighting"] = True
    data["quick_now"] = on
    ctx.logger.info("arena: fight started%s", " (quick match)" if on else "")
    return click_once(ctx, *FIGHT_POINT) or State.ROUTE


def npc_page(ctx: Context) -> State:
    """On the Rookie Arena: open the NPC Challenge, or the players' list, then pick the first opponent and challenge it.
    No flag left shows as the refill popup, handled by purchase()."""
    data = arena_data(ctx)
    players = ctx.config.arena.attack_players
    on_npc = ctx.find("arena_npc_header", NPC_HEADER_REGION) is not None
    if players and on_npc:
        seen(ctx, "arena npc challenge")
        return click_after_wait(ctx, "players_tab", OPPONENTS_TAB_POINT)
    if not players and not on_npc:
        seen(ctx, "arena opponents")
        return click_after_wait(ctx, "npc_tab", NPC_TAB_POINT)
    seen(ctx, "arena opponents" if players else "arena npc challenge")
    if fights_left(ctx) == 0:
        return ctx.halt(done_message(data, "target reached"))
    if challenge_is_lit(ctx):
        if data.get("challenges", 0) >= MAX_CHALLENGE_CLICKS:
            # Neither the team screen nor a known refill popup comes: stop rather than click on blind.
            return ctx.halt("Arena: Challenge opens neither the fight nor the flag refill, stopping")
        if time.monotonic() - data.get("challenge_at", 0.0) >= CLICK_WAIT_SECONDS:
            data["challenges"] = data.get("challenges", 0) + 1
        return click_after_wait(ctx, "challenge", CHALLENGE_POINT)
    return click_after_wait(ctx, "pick", FIRST_NPC_POINT)


def arena(ctx: Context, unknown, at_lobby) -> State:
    """One tick of the arena. `unknown` handles fights and stray screens, `at_lobby` tells the lobby."""
    if "weekly rank reward" in ctx.ocr.read(ctx.frame, WEEKLY_REWARD_REGION).casefold():
        # The game asks the player to pick the week's reward: that choice is theirs, the bot does not make it.
        return ctx.halt("Arena: the game asks you to choose this week's rank reward. Pick it, then start again")
    if "no equipment" in ctx.ocr.read(ctx.frame, NO_GEAR_TITLE_REGION).casefold():
        # A hero of the team wears nothing: the fight goes on anyway, and the warning stays away for the day.
        seen(ctx, "arena hero with no equipment")
        return click_after_wait(ctx, "no_gear_ok", NO_GEAR_DO_NOT_DISPLAY_POINT)
    if ctx.find("arena_flag_purchase", PURCHASE_REGION) is not None:
        # Challenge or Start opened it: no flag is left. Only purchase() ever buys, as the settings allow.
        return purchase(ctx)
    data = arena_data(ctx)
    if "no_refill" in data:
        # The refill was refused and its popup is closed: the flags are spent.
        return ctx.halt(done_message(data, "no flag left"))
    confirm = ctx.find("arena_confirm", CONFIRM_REGION)
    if confirm is not None:
        return result(ctx, confirm)
    if ctx.find("arena_team_cp", TEAM_CP_REGION) is not None:
        return team_screen(ctx)
    if ctx.find("arena_opponents_tab", MENU_REGION) is not None:
        return npc_page(ctx)
    if ctx.find("arena_hub", HUB_REGION) is not None:
        seen(ctx, "arena")
        return click_after_wait(ctx, "rookie", ROOKIE_POINT)
    if at_lobby(ctx):
        seen(ctx, "lobby")
        return click_after_wait(ctx, "arena", ARENA_POINT)
    if time.monotonic() - arena_data(ctx).get("acted_at", 0.0) < TRANSITION_SECONDS:
        # The screen a click opens is still on its way: no Esc yet. Fights are still handled.
        ctx.route_data["unknown_since"] = time.monotonic()
    return unknown(ctx)

"""Huche's charms on a kept account: Battle, Labyrinth, Tirel Castle in Chaos, Area 1, teleport to the waypoint next to
Huche, walk west to him, buy every charm he sells, then yield the labyrinth.

The route is the one of the first area once it is explored: the waypoint sits between Huche's pin and a chest at the top
of the map. Yielding keeps the purchases and leaves the fights already won on the way, so the next run meets none.

Spending is guarded as in the penguin mode: a purchase is confirmed only for a charm the bot asked for, after its name
and price show in the popup; it is counted when confirmed; there are at most as many purchases as rows; and the gold of
the top bar is compared with the gold at the start, the run stopping if more went than the purchases counted.
"""

from __future__ import annotations

import time

from core.config import State
from core.state import Context
from core.stats import HUCHE_CHARMS
from states.shop import BUY_BUTTON_OFFSET, BUY_LIT_GREEN, header_number, read_number
from states.story import click_once, seen

# The lobby's Battle button, and the Battle page's cards, whose order differs between accounts.
BATTLE_POINT = (1203, 325)
BATTLE_CARD_TITLES = (0, 160, 1280, 80)
BATTLE_DRAG = ((900, 430), (400, 430))
NORMAL_TAB_REGION = (70, 105, 110, 32)
NORMAL_TAB_POINT = (122, 120)
# The Labyrinth list on the right: the Tirel Castle card is found by its artwork, the list scrolled until it shows.
LABYRINTH_LIST_REGION = (1030, 90, 230, 30)
LABYRINTH_CARDS_REGION = (1020, 110, 250, 610)
LIST_UP = ((1140, 650), (1140, 150))
LIST_DOWN = ((1140, 250), (1140, 650))
# The area page: its title and area, the list of areas, Select Team; the team page's area and Start.
TOP_TITLE_REGION = (60, 15, 160, 40)
AREA_NAME_REGION = (930, 105, 330, 45)
AREA_LIST_DOWN = ((780, 200), (780, 600))
SELECT_TEAM_POINT = (1092, 660)
READY_AREA_REGION = (925, 120, 300, 30)
START_POINT = (1092, 661)
AREA_ONE = "hall of silence"
# Walking the labyrinth: the morale counter under the minimap, the minimap, the West door, Huche and his label.
MORALE_REGION = (100, 120, 90, 60)
MINIMAP_POINT = (110, 60)
WEST_POINT = (125, 505)
HUCHE_POINT = (625, 360)
MERCHANT_LABEL_REGION = (560, 185, 160, 90)
# The map: its hint line, Huche's pin, and the waypoint, up and right of the pin. The map is dragged down to its top.
MAP_HINT_REGION = (115, 28, 330, 26)
MAP_REGION = (0, 0, 900, 720)
WAYPOINT_FROM_HUCHE = (114, -107)
MAP_TOP_Y = 80
MAP_DOWN = ((600, 200), (600, 550))
MAP_DRAGS = 4
# "Would you like to spend 3 Morale to move?"
WAYPOINT_TITLE_REGION = (540, 195, 200, 50)
WAYPOINT_CONFIRM_POINT = (748, 495)
# The Merchant: rows placed from the gold coin left of their price, as in the Secret Shop.
COIN_REGION = (1050, 60, 70, 645)
CATEGORY_OFFSET = (688, 2, 200, 26)
NAME_OFFSET = (688, 28, 380, 34)
BUY_OFFSET = (1190, 67)
LOWEST_BUY_Y = 700
SCROLL_FROM = (850, 600)
SCROLL_TO = (850, 150)
# The purchase popup, the same as the Secret Shop's.
BUY_POPUP_REGION = (430, 205, 420, 70)
POPUP_CATEGORY_REGION = (640, 312, 200, 26)
POPUP_NAME_REGION = (640, 336, 330, 28)
POPUP_PRICE_REGION = (644, 497, 96, 28)
BUY_CONFIRM_POINT = (817, 510)
BUY_CANCEL_POINT = (481, 510)
# Leaving: Esc opens the pause menu, Yield, then End in "If you quit...".
PAUSE_MENU_REGION = (470, 150, 340, 90)
YIELD_POINT = (638, 275)
QUIT_TEXT_REGION = (440, 285, 400, 40)
END_POINT = (748, 460)
# Huche sells 6 rows: never more purchases than that in a run. His dearest charm costs 247,500 gold.
MAX_PURCHASES = 6
MAX_PRICE = 300_000
# The charms are told by the words of their name.
TIERS = ("lesser", "greater", "epic")
TYPES = ("artifact", "accessory", "equipment")
CLICK_WAIT_SECONDS = 2.5
SETTLE_SECONDS = 1.6
TRANSITION_SECONDS = 6.0
# After the West door, the team walks and may fight on the way: Huche shows within this time, else the run stops.
WALK_SECONDS = 120.0


def huche_data(ctx: Context) -> dict:
    """Return the run's progress. The first call logs the start, which the window shows."""
    if "huche" not in ctx.route_data:
        ctx.logger.info("huche: on the way to Huche's shop")
    return ctx.route_data.setdefault(
        "huche", {"purchases": 0, "gold": 0, "start_gold": None, "page": "top", "teleported": False, "map_drags": 0}
    )


def click_after_wait(ctx: Context, key: str, point: tuple[int, int]) -> State:
    """Click a point, at most once per CLICK_WAIT_SECONDS for the same key."""
    data = huche_data(ctx)
    now = time.monotonic()
    if now - data.get(f"{key}_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data[f"{key}_at"] = now
    data["acted_at"] = now
    return click_once(ctx, *point) or State.ROUTE


def drag_once(ctx: Context, key: str, drag: tuple[tuple[int, int], tuple[int, int]]) -> State:
    """Drag, at most once per CLICK_WAIT_SECONDS for the same key."""
    data = huche_data(ctx)
    now = time.monotonic()
    if now - data.get(f"{key}_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data[f"{key}_at"] = now
    data["acted_at"] = now
    (x1, y1), (x2, y2) = drag
    ctx.session.input.drag(x1, y1, x2, y2)
    return State.ROUTE


def charm_kind(name: str) -> str | None:
    """Return the key of a charm or enhancement powder from its name, or None for anything else."""
    text = " ".join(name.casefold().replace("weapon", "equipment").split())
    kind = next((each for each in TYPES if each in text), None)
    if kind is None:
        return None
    if "powder" in text:
        return f"powder_{kind}" if kind != "artifact" else None
    if "charm" not in text:
        return None
    tier = next((each for each in TIERS if each in text), None)
    return f"{tier}_{kind}" if tier else None


def charm_label(kind: str) -> str:
    """Return a charm's name as the game says it."""
    return dict(HUCHE_CHARMS)[kind]


def done_message(data: dict, why: str) -> str:
    """Return the line the bot stops with."""
    charms = data["purchases"]
    return f"Huche: {why}, {charms} {'charm' if charms == 1 else 'charms'} bought for {data['gold']:,} gold"


def gold_check(ctx: Context, data: dict) -> str:
    """Read the gold of the top bar; return why the run must stop if more gold went than the purchases counted."""
    gold = header_number(ctx, "shop_header_gold", 132)
    if gold is None:
        return ""
    if data["start_gold"] is None:
        data["start_gold"] = gold
        return ""
    if data["start_gold"] - gold > data["gold"] + MAX_PRICE:
        return f"the gold dropped by {data['start_gold'] - gold:,}, more than the {data['gold']:,} counted"
    return ""


def row_lit(ctx: Context, top: int) -> bool:
    """Return whether the row's Buy button is lit: it can still be bought."""
    x, dy, width, height = BUY_BUTTON_OFFSET
    area = ctx.frame[top + dy : top + dy + height, x : x + width]
    return area.size > 0 and float(area[:, :, 1].mean()) > BUY_LIT_GREEN


def merchant(ctx: Context) -> State:
    """Huche's list: buy each charm still for sale on this page, then scroll to the last rows, then leave."""
    data = huche_data(ctx)
    seen(ctx, f"huche merchant {data['page']}")
    if time.monotonic() - data.get("acted_at", 0.0) < SETTLE_SECONDS:
        return State.ROUTE
    why = gold_check(ctx, data)
    if why:
        return ctx.halt(done_message(data, why))
    if data.get("done"):
        return leave(ctx)
    for coin in sorted(ctx.library.find_every(ctx.frame, "shop_coin", COIN_REGION), key=lambda match: match.y):
        top = coin.y
        buy_y = top + BUY_OFFSET[1]
        if buy_y > LOWEST_BUY_Y or not row_lit(ctx, top):
            continue
        x, dy, width, height = CATEGORY_OFFSET
        category = ctx.ocr.read(ctx.frame, (x, top + dy, width, height)).casefold()
        x, dy, width, height = NAME_OFFSET
        kind = charm_kind(ctx.ocr.read(ctx.frame, (x, top + dy, width, height)))
        if "charm" not in category or kind is None:
            continue
        if data["purchases"] >= MAX_PURCHASES:
            return ctx.halt(done_message(data, f"stopped at {MAX_PURCHASES} purchases, the most Huche sells"))
        data["buy_asked"] = kind
        ctx.logger.info("huche: %s for sale, buying", charm_label(kind))
        return click_after_wait(ctx, f"buy_{top // 20}", (BUY_OFFSET[0], buy_y))
    if data["page"] == "top":
        data["page"] = "bottom"
        return drag_once(ctx, "scroll", (SCROLL_FROM, SCROLL_TO))
    data["done"] = True
    ctx.logger.info("huche: every charm bought, leaving the labyrinth")
    return leave(ctx)


def buy_popup(ctx: Context) -> State:
    """Buy only the charm the bot clicked, once its name and a sane price show in the popup; cancel anything else."""
    data = huche_data(ctx)
    seen(ctx, "huche buy popup")
    asked = data.get("buy_asked")
    kind = charm_kind(ctx.ocr.read(ctx.frame, POPUP_NAME_REGION))
    category = ctx.ocr.read(ctx.frame, POPUP_CATEGORY_REGION).casefold()
    if asked is None or kind != asked or "charm" not in category:
        ctx.logger.info("huche: the purchase popup is not for the charm the bot asked, cancelling")
        data.pop("buy_asked", None)
        return click_after_wait(ctx, "cancel", BUY_CANCEL_POINT)
    price = read_number(ctx, POPUP_PRICE_REGION)
    if price is None or not 1_000 <= price <= MAX_PRICE:
        return ctx.halt(done_message(data, f"the price of {charm_label(kind)} reads {price}"))
    now = time.monotonic()
    if now - data.get("confirm_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    # Counted as it is confirmed: a purchase can never go uncounted.
    data["confirm_at"] = now
    data["acted_at"] = now
    data.pop("buy_asked", None)
    data["purchases"] += 1
    data["gold"] += price
    ctx.progressed = True
    ctx.logger.info("huche: bought %s for %s gold, %d in this run", charm_label(kind), f"{price:,}", data["purchases"])
    if ctx.on_huche_buy is not None:
        ctx.on_huche_buy(kind, price)
    return click_once(ctx, *BUY_CONFIRM_POINT) or State.ROUTE


def leave(ctx: Context) -> State:
    """Esc, one press at a time: the Merchant closes, then the labyrinth's pause menu opens."""
    data = huche_data(ctx)
    now = time.monotonic()
    if now - data.get("esc_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data["esc_at"] = now
    data["acted_at"] = now
    ctx.session.input.press("esc")
    return State.ROUTE


def room(ctx: Context) -> State:
    """Walking the labyrinth: open the map until teleported, then go West until Huche shows, then open his shop."""
    data = huche_data(ctx)
    if data.get("done"):
        seen(ctx, "labyrinth room")
        return leave(ctx)
    if "merchant" in ctx.ocr.read(ctx.frame, MERCHANT_LABEL_REGION, single_line=False).casefold():
        seen(ctx, "huche")
        return click_after_wait(ctx, "huche", HUCHE_POINT)
    if not data["teleported"]:
        seen(ctx, "labyrinth room")
        return click_after_wait(ctx, "minimap", MINIMAP_POINT)
    if "walk_since" not in data:
        seen(ctx, "labyrinth waypoint")
        data["walk_since"] = time.monotonic()
        return click_after_wait(ctx, "west", WEST_POINT)
    if time.monotonic() - data["walk_since"] > WALK_SECONDS:
        return ctx.halt(done_message(data, "Huche did not show after the West door"))
    seen(ctx, "walking to huche")
    return State.ROUTE


def labyrinth_map(ctx: Context) -> State:
    """The map: drag it to its top until Huche's pin shows, then pick the waypoint next to it."""
    data = huche_data(ctx)
    seen(ctx, "labyrinth map")
    if time.monotonic() - data.get("acted_at", 0.0) < SETTLE_SECONDS:
        return State.ROUTE
    pin = ctx.find("labyrinth_map_huche", MAP_REGION)
    if pin is not None:
        x, y = pin.center[0] + WAYPOINT_FROM_HUCHE[0], pin.center[1] + WAYPOINT_FROM_HUCHE[1]
        if y >= MAP_TOP_Y:
            return click_after_wait(ctx, "waypoint", (x, y))
    if data["map_drags"] >= MAP_DRAGS:
        return ctx.halt(done_message(data, "Huche's pin is not on the labyrinth map"))
    data["map_drags"] += 1
    return drag_once(ctx, "map", MAP_DOWN)


def waypoint_popup(ctx: Context) -> State:
    """Spend 3 morale to move to the waypoint next to Huche."""
    data = huche_data(ctx)
    seen(ctx, "waypoint popup")
    if not data["teleported"]:
        data["teleported"] = True
        ctx.logger.info("huche: teleporting next to Huche")
    return click_after_wait(ctx, "waypoint_confirm", WAYPOINT_CONFIRM_POINT)


def area_page(ctx: Context, area: str) -> State:
    """Tirel Castle's area page: once done, the run ends here; otherwise scroll to Area 1 and pick its team."""
    data = huche_data(ctx)
    if data.get("done"):
        return ctx.halt(done_message(data, "done"))
    seen(ctx, "labyrinth area")
    if AREA_ONE not in area:
        return drag_once(ctx, "areas", AREA_LIST_DOWN)
    return click_after_wait(ctx, "select_team", SELECT_TEAM_POINT)


def labyrinth_list(ctx: Context) -> State:
    """The Labyrinth list: open Tirel Castle in Chaos, scrolling down to it, or up if it was passed."""
    data = huche_data(ctx)
    seen(ctx, "labyrinth list")
    if time.monotonic() - data.get("acted_at", 0.0) < SETTLE_SECONDS:
        return State.ROUTE
    card = ctx.find("labyrinth_tirel", LABYRINTH_CARDS_REGION)
    if card is not None:
        return click_after_wait(ctx, "tirel", card.center)
    drags = data.get("list_drags", 0)
    if drags >= 4:
        return ctx.halt(done_message(data, "Tirel Castle in Chaos is not in the Labyrinth list"))
    data["list_drags"] = drags + 1
    return drag_once(ctx, "list", LIST_UP if drags < 2 else LIST_DOWN)


def huche(ctx: Context, unknown, at_lobby) -> State:
    """One tick of the Huche shop mode. `unknown` handles stray screens and fights, `at_lobby` tells the lobby."""
    data = huche_data(ctx)
    if ctx.find("shop_buy_popup", BUY_POPUP_REGION) is not None:
        return buy_popup(ctx)
    if "you quit" in ctx.ocr.read(ctx.frame, QUIT_TEXT_REGION).casefold():
        seen(ctx, "yield popup")
        return click_after_wait(ctx, "end", END_POINT)
    if ctx.find("pause_menu", PAUSE_MENU_REGION) is not None:
        if data.get("done"):
            seen(ctx, "pause menu")
            return click_after_wait(ctx, "yield", YIELD_POINT)
        return unknown(ctx)
    if "waypoint" in ctx.ocr.read(ctx.frame, WAYPOINT_TITLE_REGION).casefold():
        return waypoint_popup(ctx)
    title = ctx.ocr.read(ctx.frame, TOP_TITLE_REGION).casefold()
    if title.startswith("merchant"):
        return merchant(ctx)
    if "morale to move" in ctx.ocr.read(ctx.frame, MAP_HINT_REGION).casefold():
        return labyrinth_map(ctx)
    if ctx.find("labyrinth_morale", MORALE_REGION) is not None:
        return room(ctx)
    if title.startswith("ready"):
        seen(ctx, "labyrinth team")
        if AREA_ONE in ctx.ocr.read(ctx.frame, READY_AREA_REGION).casefold():
            return click_after_wait(ctx, "start", START_POINT)
        return unknown(ctx)
    if title.startswith("battle"):
        area = ctx.ocr.read(ctx.frame, AREA_NAME_REGION).casefold()
        if area and "tirel" in ctx.ocr.read(ctx.frame, (930, 80, 330, 30)).casefold():
            return area_page(ctx, area)
        if "labyrinths available" in ctx.ocr.read(ctx.frame, LABYRINTH_LIST_REGION).casefold():
            return labyrinth_list(ctx)
        card = ctx.find("battle_labyrinth", BATTLE_CARD_TITLES)
        if card is not None:
            seen(ctx, "battle page")
            return click_after_wait(ctx, "labyrinth", (card.center[0] + 56, 420))
        if ctx.find("battle_spirit_altar", (0, 160, 1280, 80)) is not None:
            seen(ctx, "battle page")
            return drag_once(ctx, "battle", BATTLE_DRAG)
        if "normal" in ctx.ocr.read(ctx.frame, NORMAL_TAB_REGION).casefold():
            # The page reopens on its last tab, Seasonal or Event: the Labyrinth is on Normal.
            seen(ctx, "battle page, other tab")
            return click_after_wait(ctx, "normal_tab", NORMAL_TAB_POINT)
    if at_lobby(ctx):
        seen(ctx, "lobby")
        if data.get("done"):
            return ctx.halt(done_message(data, "done"))
        return click_after_wait(ctx, "battle", BATTLE_POINT)
    if time.monotonic() - data.get("acted_at", 0.0) < TRANSITION_SECONDS:
        # The next screen is on its way (loading into the labyrinth takes seconds): no Esc yet.
        ctx.route_data["unknown_since"] = time.monotonic()
    return unknown(ctx)

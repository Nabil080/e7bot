"""Shop refresh on a kept account: buy the Secret Shop's Covenant Bookmarks and Mystic Medals, refresh it for skystones,
a set number of times or until a target is reached. Each page of the list is read from a fresh frame before a click."""

from __future__ import annotations

import re
import time

from core.config import State
from core.state import Context
from states.story import click_once, seen

# The lobby's shortcut to the Secret Shop, in its left column: its place there depends on the other shortcuts.
LOBBY_SHORTCUTS_REGION = (0, 130, 140, 530)
# The Secret Shop: its title, the gold and skystones it shows, the list on the right, the Refresh button.
TITLE_REGION = (50, 8, 175, 52)
# The gold and skystones of the top bar move left when the game adds an icon on their right: each number is read
# right of its own icon, found in this strip.
HEADER_REGION = (560, 10, 440, 50)
HEADER_NUMBER_TOP = 18
HEADER_NUMBER_HEIGHT = 30
GOLD_WIDTH = 132
SKYSTONES_WIDTH = 80
REFRESH_POINT = (268, 660)
# Each row of the list has a gold coin left of its price: the row is placed from it (its top-left corner, y = T).
COIN_REGION = (1050, 60, 70, 645)
NAME_OFFSET = (688, 22, 372, 64)
PRICE_OFFSET = (1110, 2, 135, 30)
BUY_OFFSET = (1190, 67)
# The Buy button is green while the row can be bought, dim once it is sold: green mean measured 81 and 36.
BUY_BUTTON_OFFSET = (1160, 52, 70, 28)
BUY_LIT_GREEN = 60
# A row whose Buy button is under this line is cut by the window's bottom: it is bought from the scrolled page.
LOWEST_BUY_Y = 700
# The list holds 6 rows: one drag shows the last ones. After a refresh the list is back at its top.
SCROLL_FROM = (850, 600)
SCROLL_TO = (850, 150)
# "Use Skystone to refresh?" and the purchase popup, with their buttons.
REFRESH_POPUP_REGION = (500, 290, 290, 70)
REFRESH_CONFIRM_POINT = (748, 460)
REFRESH_CANCEL_POINT = (533, 460)
BUY_POPUP_REGION = (430, 205, 420, 70)
BUY_POPUP_NAME_REGION = (640, 336, 330, 28)
BUY_POPUP_PRICE_REGION = (644, 497, 96, 28)
BUY_CONFIRM_POINT = (817, 510)
BUY_CANCEL_POINT = (481, 510)
REFRESH_PRICE = 3
# What the Secret Shop sells of them, and its price in gold when the price cannot be read.
WANTED = {
    "bookmarks": {"words": "covenant bookmark", "label": "Covenant Bookmarks", "amount": 5, "price": 184_000},
    "medals": {"words": "mystic medal", "label": "Mystic Medals", "amount": 50, "price": 280_000},
}
# A click is followed by an animation (the list reloads, the item flies in): the screen is read again after this.
SETTLE_SECONDS = 1.6
# One click per button in this time: the next screen can take a moment.
CLICK_WAIT_SECONDS = 2.5
# After a click, the next screen can take seconds to open: meanwhile an unknown screen is waited for, never escaped.
TRANSITION_SECONDS = 6.0


def shop_data(ctx: Context) -> dict:
    """Return the shop's progress in this run. The first call logs the plan, which the window shows."""
    if "shop" not in ctx.route_data:
        ctx.logger.info("shop: %d refreshes planned", ctx.config.shop.refreshes)
    return ctx.route_data.setdefault(
        "shop", {"refreshes": 0, "bookmarks": 0, "medals": 0, "gold": 0, "page": "top"}
    )


def click_after_wait(ctx: Context, key: str, point: tuple[int, int]) -> State:
    """Click a point, at most once per CLICK_WAIT_SECONDS for the same key."""
    data = shop_data(ctx)
    now = time.monotonic()
    if now - data.get(f"{key}_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data[f"{key}_at"] = now
    data["acted_at"] = now
    return click_once(ctx, *point) or State.ROUTE


def read_number(ctx: Context, region: tuple[int, int, int, int]) -> int | None:
    """Read a number such as "168,506,903", or None."""
    digits = re.sub(r"\D", "", ctx.ocr.read(ctx.frame, region, whitelist="0123456789,"))
    return int(digits) if digits else None


def header_number(ctx: Context, icon: str, width: int) -> int | None:
    """Read the gold or skystones of the top bar, right of its icon, or None."""
    found = ctx.find(icon, HEADER_REGION)
    if found is None:
        return None
    return read_number(ctx, (found.x + found.width + 4, HEADER_NUMBER_TOP, width, HEADER_NUMBER_HEIGHT))


def wanted_kind(name: str) -> str | None:
    """Return "bookmarks" or "medals" for a row's name, or None for anything else."""
    text = " ".join(name.casefold().split())
    return next((kind for kind, item in WANTED.items() if item["words"] in text), None)


def row_lit(ctx: Context, top: int) -> bool:
    """Return whether the row's Buy button is lit: it can still be bought."""
    x, dy, width, height = BUY_BUTTON_OFFSET
    area = ctx.frame[top + dy : top + dy + height, x : x + width]
    return area.size > 0 and float(area[:, :, 1].mean()) > BUY_LIT_GREEN


def done_message(data: dict, why: str) -> str:
    """Return the line the bot stops with."""
    return (
        f"Secret Shop: {why}, {data['refreshes']} refreshes ({data['refreshes'] * REFRESH_PRICE} skystones), "
        f"{data['bookmarks']} bookmarks and {data['medals']} medals bought for {data['gold']:,} gold"
    )


def target_reached(ctx: Context) -> str:
    """Return why a stop target is reached, or ''. 0 means no target."""
    shop = ctx.config.shop
    data = shop_data(ctx)
    if shop.stop_bookmarks and data["bookmarks"] >= shop.stop_bookmarks:
        return "bookmark target reached"
    if shop.stop_medals and data["medals"] >= shop.stop_medals:
        return "medal target reached"
    return ""


def refresh_popup(ctx: Context) -> State:
    """Confirm the refresh the bot asked for; cancel one it did not ask for."""
    data = shop_data(ctx)
    seen(ctx, "shop refresh popup")
    if not data.get("refresh_asked"):
        return click_after_wait(ctx, "refresh_cancel", REFRESH_CANCEL_POINT)
    now = time.monotonic()
    if now - data.get("refresh_confirm_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    data["refresh_confirm_at"] = now
    data["acted_at"] = now
    data["refresh_asked"] = False
    data["refreshes"] += 1
    # A refresh shows the new list from its top.
    data["page"] = "top"
    ctx.progressed = True
    ctx.logger.info("shop: refreshed for %d skystones, %d of %d", REFRESH_PRICE, data["refreshes"], ctx.config.shop.refreshes)
    if ctx.on_shop_refresh is not None:
        ctx.on_shop_refresh()
    return click_once(ctx, *REFRESH_CONFIRM_POINT) or State.ROUTE


def buy_popup(ctx: Context) -> State:
    """Buy only the item the bot clicked, once its name shows in the popup; cancel anything else."""
    data = shop_data(ctx)
    seen(ctx, "shop buy popup")
    kind = wanted_kind(ctx.ocr.read(ctx.frame, BUY_POPUP_NAME_REGION))
    if kind is None or kind != data.get("buy_asked"):
        ctx.logger.info("shop: the purchase popup is not for an item the bot asked, cancelling")
        data.pop("buy_asked", None)
        return click_after_wait(ctx, "buy_cancel", BUY_CANCEL_POINT)
    now = time.monotonic()
    if now - data.get("buy_confirm_at", 0.0) < CLICK_WAIT_SECONDS:
        return State.ROUTE
    item = WANTED[kind]
    price = read_number(ctx, BUY_POPUP_PRICE_REGION)
    if price is None or not 1_000 <= price <= 10_000_000:
        price = item["price"]
    data["buy_confirm_at"] = now
    data["acted_at"] = now
    data.pop("buy_asked", None)
    data[kind] += item["amount"]
    data["gold"] += price
    ctx.progressed = True
    ctx.logger.info("shop: bought %d %s for %s gold, %d in this run", item["amount"], item["label"], f"{price:,}", data[kind])
    if ctx.on_shop_buy is not None:
        ctx.on_shop_buy(kind, item["amount"], price)
    return click_once(ctx, *BUY_CONFIRM_POINT) or State.ROUTE


def shop_page(ctx: Context) -> State:
    """On the Secret Shop: buy the wanted rows of this page, then scroll to the last rows, then refresh."""
    data = shop_data(ctx)
    seen(ctx, f"shop list {data['page']}")
    why = target_reached(ctx)
    if why:
        return ctx.halt(done_message(data, why))
    if time.monotonic() - data.get("acted_at", 0.0) < SETTLE_SECONDS:
        # The list is still moving or reloading after the last click: read it once it stands still.
        return State.ROUTE
    for coin in sorted(ctx.library.find_every(ctx.frame, "shop_coin", COIN_REGION), key=lambda match: match.y):
        top = coin.y
        buy_y = top + BUY_OFFSET[1]
        if buy_y > LOWEST_BUY_Y or not row_lit(ctx, top):
            continue
        x, dy, width, height = NAME_OFFSET
        kind = wanted_kind(ctx.ocr.read(ctx.frame, (x, top + dy, width, height), single_line=False))
        if kind is None:
            continue
        x, dy, width, height = PRICE_OFFSET
        price = read_number(ctx, (x, top + dy, width, height)) or WANTED[kind]["price"]
        gold = header_number(ctx, "shop_header_gold", GOLD_WIDTH)
        if gold is not None and gold < price:
            return ctx.halt(done_message(data, f"not enough gold for {WANTED[kind]['label']} ({gold:,})"))
        data["buy_asked"] = kind
        ctx.logger.info("shop: %s for sale on the %s page, buying", WANTED[kind]["label"], data["page"])
        return click_after_wait(ctx, f"buy_{kind}", (BUY_OFFSET[0], buy_y))
    if data["page"] == "top":
        # Nothing more to buy in view: the last rows are under the window's bottom.
        data["page"] = "bottom"
        data["acted_at"] = time.monotonic()
        ctx.session.input.drag(*SCROLL_FROM, *SCROLL_TO)
        return State.ROUTE
    if data["refreshes"] >= ctx.config.shop.refreshes:
        return ctx.halt(done_message(data, "refreshes done"))
    stones = header_number(ctx, "shop_header_skystone", SKYSTONES_WIDTH)
    if stones is not None and stones < REFRESH_PRICE:
        return ctx.halt(done_message(data, "not enough skystones"))
    data["refresh_asked"] = True
    return click_after_wait(ctx, "refresh", REFRESH_POINT)


def shop(ctx: Context, unknown, at_lobby) -> State:
    """One tick of the shop refresh. `unknown` handles stray screens, `at_lobby` tells the lobby."""
    if ctx.find("shop_refresh_popup", REFRESH_POPUP_REGION) is not None:
        return refresh_popup(ctx)
    if ctx.find("shop_buy_popup", BUY_POPUP_REGION) is not None:
        return buy_popup(ctx)
    if ctx.find("shop_secret_title", TITLE_REGION) is not None:
        return shop_page(ctx)
    data = shop_data(ctx)
    if at_lobby(ctx):
        seen(ctx, "lobby")
        shortcut = ctx.find("lobby_secret_shop", LOBBY_SHORTCUTS_REGION)
        if shortcut is None:
            return ctx.halt("Secret Shop: its button is not in the lobby's left column")
        # A shop opened from the lobby shows its list from the top.
        data["page"] = "top"
        return click_after_wait(ctx, "open", shortcut.center)
    if time.monotonic() - data.get("acted_at", 0.0) < TRANSITION_SECONDS:
        # The screen a click opens is still on its way: no Esc yet.
        ctx.route_data["unknown_since"] = time.monotonic()
    return unknown(ctx)

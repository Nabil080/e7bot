"""The Shop refresh in the Secret Shop, on real screenshots of each of its screens."""

import re

from pathlib import Path

import cv2
import pytest

from core.config import State
from core.panel import describe_stop, run_metrics, stat_tabs
from core.progress import Progress, read_progress
from core.stats import Stats
from states.route import route
from states.shop import (
    BUY_CANCEL_POINT, BUY_CONFIRM_POINT, REFRESH_CANCEL_POINT, REFRESH_CONFIRM_POINT, REFRESH_POINT, SCROLL_FROM, SCROLL_TO,
    shop_data, wanted_kind,
)
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def shot(name: str):
    image = cv2.imread(str(FIXTURES / f"shop_{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture
def shop_ctx(ctx):
    ctx.goal = "shop"
    ctx.attach = lambda: True
    return ctx


def settings(ctx, **values) -> None:
    ctx.config = ctx.config.model_copy(update={"shop": ctx.config.shop.model_copy(update=values)})


def tick(ctx, name):
    ctx.frame = shot(name)
    ctx.last_click = None
    return route(ctx)


def test_the_lobby_shortcut_opens_the_secret_shop(shop_ctx):
    tick(shop_ctx, "lobby")
    x, y = shop_ctx.session.input.clicks[0]
    assert 12 <= x <= 102 and 298 <= y <= 362
    assert shop_data(shop_ctx)["page"] == "top"


def test_a_bookmark_row_is_bought(shop_ctx):
    tick(shop_ctx, "bookmarks")
    assert shop_ctx.session.input.clicks == [(1190, 162)]
    assert shop_data(shop_ctx)["buy_asked"] == "bookmarks"


def test_the_purchase_popup_buys_only_what_was_asked(shop_ctx):
    shop_data(shop_ctx)["buy_asked"] = "bookmarks"
    tick(shop_ctx, "buy_popup")
    data = shop_data(shop_ctx)
    assert shop_ctx.session.input.clicks == [BUY_CONFIRM_POINT]
    assert (data["bookmarks"], data["medals"], data["gold"]) == (5, 0, 184_000)


def test_a_purchase_popup_the_bot_did_not_ask_for_is_cancelled(shop_ctx):
    tick(shop_ctx, "buy_popup")
    assert shop_ctx.session.input.clicks == [BUY_CANCEL_POINT]
    assert shop_data(shop_ctx)["bookmarks"] == 0


def test_a_sold_row_is_not_bought_again_and_the_list_scrolls(shop_ctx):
    tick(shop_ctx, "bookmarks_sold")
    assert shop_ctx.session.input.clicks == [("drag", *SCROLL_FROM, *SCROLL_TO)]
    assert shop_data(shop_ctx)["page"] == "bottom"


def test_the_last_rows_read_then_the_shop_is_refreshed(shop_ctx):
    shop_data(shop_ctx)["page"] = "bottom"
    tick(shop_ctx, "bottom")
    assert shop_ctx.session.input.clicks == [REFRESH_POINT]
    shop_ctx.session.input.clicks.clear()
    tick(shop_ctx, "refresh_popup")
    data = shop_data(shop_ctx)
    assert shop_ctx.session.input.clicks == [REFRESH_CONFIRM_POINT]
    # The new list shows from its top: no scroll back up.
    assert (data["refreshes"], data["page"]) == (1, "top")


def test_a_refresh_the_bot_did_not_ask_for_is_cancelled(shop_ctx):
    tick(shop_ctx, "refresh_popup")
    assert shop_ctx.session.input.clicks == [REFRESH_CANCEL_POINT]
    assert shop_data(shop_ctx)["refreshes"] == 0


def test_the_bot_stops_once_the_refreshes_are_done(shop_ctx):
    settings(shop_ctx, refreshes=2)
    data = shop_data(shop_ctx)
    data.update(page="bottom", refreshes=2, bookmarks=10, gold=368_000)
    assert tick(shop_ctx, "bottom") == State.HALT
    assert shop_ctx.session.input.clicks == []
    shown = describe_stop(shop_ctx.halt_reason)
    assert shown.title == "Shop refresh done"
    assert "2 refreshes (6 skystones), 10 bookmarks and 0 medals bought for 368,000 gold" in shown.detail


def test_the_bot_stops_at_the_first_target_reached(shop_ctx):
    settings(shop_ctx, stop_bookmarks=5, stop_medals=100)
    shop_data(shop_ctx)["bookmarks"] = 5
    assert tick(shop_ctx, "bookmarks") == State.HALT
    assert shop_ctx.session.input.clicks == []
    assert describe_stop(shop_ctx.halt_reason).detail.startswith("Bookmark target reached")


def test_row_names_are_matched_loosely():
    assert wanted_kind("Covenant Bookmarks\nOnly 1 available") == "bookmarks"
    assert wanted_kind("Mystic  Medals") == "medals"
    assert wanted_kind("Friendship Points") is None


def test_the_window_counts_the_run_from_its_log():
    lines = [
        "10:00:01 shop: bought 5 Covenant Bookmarks for 184,000 gold, 5 in this run",
        "10:00:05 shop: refreshed for 3 skystones, 1 of 10",
        "10:00:09 shop: bought 50 Mystic Medals for 280,000 gold, 50 in this run",
    ]
    progress = read_progress(lines)
    assert (progress.shop_refreshes, progress.shop_bookmarks, progress.shop_medals, progress.shop_gold) == (1, 5, 50, 464_000)


def test_the_shop_stats_tab_counts_skystones_from_the_refreshes():
    stats = Stats(shop_refreshes=100, shop_bookmarks=15, shop_medals=50, shop_gold=832_000, shop_seconds=500)
    tab = dict(stat_tabs(stats, 0))["Shop"]
    tiles = {caption: value for _, section in tab for value, caption in section}
    assert (tiles["refreshes"], tiles["skystones spent"], tiles["bookmarks bought"], tiles["medals bought"], tiles["gold spent"]) == ("100", "300", "15", "50", "832k")
    assert tiles["per refresh"] == "5 s"


def test_a_running_shop_reroll_shows_its_counts():
    class Snapshot:
        elapsed_seconds = 60.0

    assert run_metrics("shop", Snapshot(), Progress(shop_refreshes=4, shop_bookmarks=5)) == [("4", "refreshes"), ("5", "bookmarks"), ("0", "medals")]


def test_the_window_settings_reach_the_bot():
    from core.config import load_config
    from core.settings import Settings, apply_settings

    config = apply_settings(load_config("config.yaml"), Settings(shop_refreshes=100, shop_stop_bookmarks=30, shop_stop_medals=200))
    assert (config.shop.refreshes, config.shop.stop_bookmarks, config.shop.stop_medals) == (100, 30, 200)


@pytest.mark.parametrize("name, gold, stones", [("bookmarks", 168_506_903, 16_695), ("header_shifted", 168_322_903, 15_462)])
def test_gold_and_skystones_are_read_where_the_top_bar_puts_them(shop_ctx, name, gold, stones):
    from states.shop import GOLD_WIDTH, SKYSTONES_WIDTH, header_number

    shop_ctx.frame = shot(name)
    assert header_number(shop_ctx, "shop_header_gold", GOLD_WIDTH) == gold
    assert header_number(shop_ctx, "shop_header_skystone", SKYSTONES_WIDTH) == stones


def test_the_events_feed_says_what_the_shop_did():
    from core.progress import humanize

    assert humanize("shop: refreshed for 3 skystones, 4 of 100") == "Shop: refresh 4 of 100"
    assert humanize("shop: bought 50 Mystic Medals for 280,000 gold, 50 in this run") == "Shop: 50 Mystic Medals bought (280,000 gold)"


def test_the_game_icons_are_local_and_shipped():
    from core.panel import CAPTION_ICONS
    from tools.package import DATA

    root = Path(__file__).parent.parent
    used = set(re.findall(r'icon: "([\w.]+)"', (root / "ui" / "app.js").read_text(encoding="utf-8")))
    for file in {*CAPTION_ICONS.values(), *used, "skystone.png"}:
        assert (root / "ui" / "icons" / file).is_file(), file
    assert "ui" in DATA
    assert "cdn" not in (root / "ui" / "app.js").read_text(encoding="utf-8").casefold()


def test_every_mode_and_page_has_its_game_glyph():
    from core.panel import GOALS, TAB_ICONS

    root = Path(__file__).parent.parent
    for file in [goal.icon for goal in GOALS] + list(TAB_ICONS.values()):
        assert file and (root / "ui" / "glyphs" / file).is_file(), file

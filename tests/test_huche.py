"""Huche's shop in the first labyrinth, on real screenshots of each screen on the way, of his list and of a purchase."""

from pathlib import Path

import cv2
import pytest

from core.panel import band, run_metrics
from core.progress import humanize, read_progress
from states.huche import (
    BUY_CANCEL_POINT, BUY_CONFIRM_POINT, END_POINT, HUCHE_POINT, MAX_PURCHASES, MINIMAP_POINT, SELECT_TEAM_POINT, START_POINT,
    WAYPOINT_CONFIRM_POINT, WEST_POINT, YIELD_POINT, charm_kind, huche_data,
)
from states.route import route
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def shot(name: str):
    image = cv2.imread(str(FIXTURES / f"huche_{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture
def huche_ctx(ctx):
    ctx.goal = "huche"
    ctx.attach = lambda: True
    return ctx


def tick(ctx, name):
    """One tick on a screenshot, as if the last click's wait and the screen's settling were over."""
    data = huche_data(ctx)
    for key in [key for key in data if key.endswith("_at") and key != "walk_since"]:
        data[key] = 0.0
    ctx.frame = shot(name)
    ctx.last_click = None
    ctx.session.input.clicks.clear()
    route(ctx)
    return ctx.session.input.clicks


def test_the_lobby_opens_the_battle_page_and_it_the_labyrinth(huche_ctx):
    assert tick(huche_ctx, "lobby") == [(1203, 325)]
    [(x, y)] = tick(huche_ctx, "battle")
    assert 960 <= x <= 1220 and 300 <= y <= 600


def test_a_battle_page_left_on_another_tab_goes_back_to_normal(huche_ctx):
    assert tick(huche_ctx, "battle_seasonal") == [(122, 120)]


def test_the_labyrinth_list_is_scrolled_until_tirel_castle_shows(huche_ctx):
    [drag] = tick(huche_ctx, "list_top")
    assert drag[0] == "drag"
    [(x, y)] = tick(huche_ctx, "list_tirel")
    assert 1040 <= x <= 1250 and 540 <= y <= 700


def test_the_area_list_goes_to_area_one_then_its_team_starts(huche_ctx):
    [drag] = tick(huche_ctx, "area_four")
    assert drag[0] == "drag"
    assert tick(huche_ctx, "area_one") == [SELECT_TEAM_POINT]
    assert tick(huche_ctx, "ready") == [START_POINT]


def test_the_map_is_dragged_to_huche_and_his_waypoint_taken(huche_ctx):
    assert tick(huche_ctx, "room_start") == [MINIMAP_POINT]
    [drag] = tick(huche_ctx, "map")
    assert drag[0] == "drag"
    [(x, y)] = tick(huche_ctx, "map_top")
    assert abs(x - 229) <= 12 and abs(y - 215) <= 12
    assert tick(huche_ctx, "waypoint") == [WAYPOINT_CONFIRM_POINT]
    assert huche_data(huche_ctx)["teleported"]


def test_after_the_teleport_it_goes_west_then_opens_huche(huche_ctx):
    huche_data(huche_ctx)["teleported"] = True
    assert tick(huche_ctx, "room_portal") == [WEST_POINT]
    # Walking on: the same room again is waited for, never the West door twice.
    assert tick(huche_ctx, "room_portal") == []
    assert tick(huche_ctx, "room_huche") == [HUCHE_POINT]


def test_a_fight_on_the_way_is_left_to_the_game(huche_ctx):
    huche_data(huche_ctx)["teleported"] = True
    assert not [click for click in tick(huche_ctx, "fight") if click[0] == "drag"]


def test_the_list_skips_artifacts_and_sold_rows_then_buys_the_charms_below(huche_ctx):
    # The top page: three artifacts and a charm already bought.
    [drag] = tick(huche_ctx, "merchant_top")
    assert drag[0] == "drag"
    assert huche_data(huche_ctx)["page"] == "bottom"
    [(x, y)] = tick(huche_ctx, "merchant_bottom")
    assert x == 1190 and abs(y - 526) <= 4
    assert huche_data(huche_ctx)["buy_asked"] == "lesser_equipment"


def test_the_popup_is_confirmed_only_for_the_charm_asked_and_counted_at_once(huche_ctx):
    assert tick(huche_ctx, "buy_popup") == [BUY_CANCEL_POINT]
    bought = []
    huche_ctx.on_huche_buy = lambda kind, gold: bought.append((kind, gold))
    huche_data(huche_ctx)["buy_asked"] = "lesser_equipment"
    assert tick(huche_ctx, "buy_popup") == [BUY_CONFIRM_POINT]
    assert bought == [("lesser_equipment", 19_800)]
    data = huche_data(huche_ctx)
    assert data["purchases"] == 1 and data["gold"] == 19_800 and "buy_asked" not in data


def test_a_popup_for_another_charm_is_cancelled(huche_ctx):
    huche_data(huche_ctx)["buy_asked"] = "epic_artifact"
    assert tick(huche_ctx, "buy_popup") == [BUY_CANCEL_POINT]


def test_no_more_purchases_than_huche_has_rows(huche_ctx):
    huche_data(huche_ctx).update(page="bottom", purchases=MAX_PURCHASES)
    tick(huche_ctx, "merchant_bottom")
    assert huche_ctx.halt_reason and f"{MAX_PURCHASES} purchases" in huche_ctx.halt_reason


def test_gold_lost_beyond_the_counted_purchases_stops_the_run(huche_ctx):
    huche_data(huche_ctx).update(page="bottom", start_gold=309_000_000)
    tick(huche_ctx, "merchant_bottom")
    assert huche_ctx.halt_reason and "gold dropped" in huche_ctx.halt_reason


def test_once_done_it_yields_the_labyrinth_and_stops_on_the_area_page(huche_ctx):
    huche_data(huche_ctx).update(done=True, purchases=2, gold=39_600)
    tick(huche_ctx, "merchant_bottom")
    assert huche_ctx.session.input.keys == ["press esc"]
    assert tick(huche_ctx, "pause") == [YIELD_POINT]
    assert tick(huche_ctx, "quit") == [END_POINT]
    tick(huche_ctx, "area_after")
    assert huche_ctx.halt_reason == "Huche: done, 2 charms bought for 39,600 gold"


def test_the_pause_menu_before_the_end_goes_back_to_the_game(huche_ctx):
    assert YIELD_POINT not in tick(huche_ctx, "pause")


@pytest.mark.parametrize(("name", "kind"), [
    ("Lesser Equipment Charm", "lesser_equipment"),
    ("Epic Weapon Charm", "epic_equipment"),
    ("Greater Artifact Charm", "greater_artifact"),
    ("Accessory Enhancement Powder", "powder_accessory"),
    ("Equipment Enhancement Powder", "powder_equipment"),
    ("Elf's Bow", None),
    ("Covenant Bookmarks", None),
    ("Dream Scroll", None),
])
def test_charms_are_told_by_their_name(name, kind):
    assert charm_kind(name) == kind


def test_the_window_counts_the_charms_and_their_gold():
    lines = [
        "16:00:00 huche: on the way to Huche's shop",
        "16:01:00 huche: Lesser Equipment Charm for sale, buying",
        "16:01:02 huche: bought Lesser Equipment Charm for 19,800 gold, 1 in this run",
        "16:01:05 huche: bought Epic Artifact Charm for 247,500 gold, 2 in this run",
    ]
    progress = read_progress(lines)
    assert progress.huche_bought == 2 and progress.huche_gold == 267_300
    assert humanize("huche: bought Epic Artifact Charm for 247,500 gold, 2 in this run") == "Epic Artifact Charm bought (247,500 gold)"


def test_a_fight_before_the_teleport_is_not_touched(huche_ctx):
    """Not the mode's own fight (the player's arena, say): Auto is never clicked."""
    huche_ctx.auto_read_at = -100.0
    for _ in range(3):
        huche_ctx.auto_read_at = -100.0
        assert tick(huche_ctx, "fight") == []

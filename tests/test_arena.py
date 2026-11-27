"""The NPC Challenge of the Arena, on real screenshots of each of its screens."""

import time
from pathlib import Path

import cv2
import pytest

from core.config import State, load_config
from core.vision import Ocr
from states.arena import CHALLENGE_POINT, FIGHT_POINT, FIRST_NPC_POINT, NPC_TAB_POINT, QUICK_TOGGLE_POINT, ROOKIE_POINT, arena_data
from states.route import route
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def shot(name: str):
    image = cv2.imread(str(FIXTURES / f"arena_{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture
def arena_ctx(ctx):
    ctx.goal = "arena"
    ctx.attach = lambda: True
    return ctx


def tick(ctx, name):
    ctx.frame = shot(name)
    ctx.last_click = None
    return route(ctx)


def quick_setting(ctx, on: bool) -> None:
    ctx.config = ctx.config.model_copy(update={"arena": ctx.config.arena.model_copy(update={"quick_match": on})})


def test_the_arena_screen_opens_the_rookie_arena(arena_ctx):
    tick(arena_ctx, "hub")
    assert arena_ctx.session.input.clicks == [ROOKIE_POINT]


def test_the_opponents_list_turns_to_the_npc_challenge(arena_ctx):
    tick(arena_ctx, "opponents")
    assert arena_ctx.session.input.clicks == [NPC_TAB_POINT]


def test_an_npc_is_picked_then_challenged(arena_ctx):
    tick(arena_ctx, "npc")
    tick(arena_ctx, "npc_picked")
    assert arena_ctx.session.input.clicks == [FIRST_NPC_POINT, CHALLENGE_POINT]


def test_no_flag_left_stops_the_bot_without_a_refill(arena_ctx):
    from states.arena import CANCEL_POINT

    refill_setting(arena_ctx, False)
    # Challenge is clicked without knowing the flags: with none left, the game opens the refill popup.
    tick(arena_ctx, "npc_picked")
    assert arena_ctx.session.input.clicks == [CHALLENGE_POINT]
    arena_data(arena_ctx)["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase")
    assert arena_ctx.session.input.clicks[-1] == CANCEL_POINT
    assert tick(arena_ctx, "npc_picked") is State.HALT
    assert "no flag left" in arena_ctx.halt_reason
    assert arena_ctx.session.input.clicks == [CHALLENGE_POINT, CANCEL_POINT]


def test_the_flag_count_is_not_needed_to_fight(arena_ctx):
    # The NPC Challenge of the recording where the bot stopped, flags past the cap ("9/5"), counter hidden.
    frame = shot("npc_flags_over_cap")
    frame[10:56, 380:800] = 0
    for _ in range(3):
        arena_ctx.frame = frame.copy()
        arena_ctx.last_click = None
        assert route(arena_ctx) is State.ROUTE
        arena_data(arena_ctx)["pick_at"] = 0.0
    assert arena_ctx.session.input.clicks == [FIRST_NPC_POINT] * 3


def test_a_challenge_that_opens_nothing_stops_the_bot(arena_ctx):
    from states.arena import MAX_CHALLENGE_CLICKS

    data = arena_data(arena_ctx)
    for _ in range(MAX_CHALLENGE_CLICKS):
        data["challenge_at"] = 0.0
        assert tick(arena_ctx, "npc_picked") is State.ROUTE
    data["challenge_at"] = 0.0
    assert tick(arena_ctx, "npc_picked") is State.HALT
    assert arena_ctx.session.input.clicks == [CHALLENGE_POINT] * MAX_CHALLENGE_CLICKS
    # The team screen in between shows Challenge works: the count starts again.
    data["challenges"] = MAX_CHALLENGE_CLICKS - 1
    tick(arena_ctx, "team_normal")
    assert data["challenges"] == 0


def test_quick_match_is_used_when_allowed(arena_ctx):
    quick_setting(arena_ctx, True)
    tick(arena_ctx, "team_quick")
    assert arena_ctx.session.input.clicks == [FIGHT_POINT]
    data = arena_data(arena_ctx)
    assert data["fighting"] and data["quick_now"]


def test_quick_match_is_never_turned_on(arena_ctx):
    # A green Start: Quick Match is off or none is left, the lightning would open the offer to buy more.
    quick_setting(arena_ctx, True)
    tick(arena_ctx, "team_normal")
    assert arena_ctx.session.input.clicks == [FIGHT_POINT]
    assert not arena_data(arena_ctx)["quick_now"]


def test_quick_match_is_turned_off_when_not_allowed(arena_ctx):
    quick_setting(arena_ctx, False)
    tick(arena_ctx, "team_quick")
    assert arena_ctx.session.input.clicks == [QUICK_TOGGLE_POINT]
    arena_data(arena_ctx)["toggle_at"] = 0.0
    tick(arena_ctx, "team_normal")
    assert arena_ctx.session.input.clicks[-1] == FIGHT_POINT
    assert not arena_data(arena_ctx)["quick_now"]


def test_a_quick_match_that_does_not_turn_off_still_fights(arena_ctx):
    quick_setting(arena_ctx, False)
    data = arena_data(arena_ctx)
    for _ in range(4):
        data["toggle_at"] = 0.0
        tick(arena_ctx, "team_quick")
    assert arena_ctx.session.input.clicks == [QUICK_TOGGLE_POINT, QUICK_TOGGLE_POINT, FIGHT_POINT]


@pytest.mark.parametrize("name, quick", [("victory", False), ("quick_victory", True)])
def test_a_won_fight_is_counted_once_and_confirmed(arena_ctx, name, quick):
    fights = []
    arena_ctx.on_arena_fight = lambda won, was_quick: fights.append((won, was_quick))
    data = arena_data(arena_ctx)
    data.update(fighting=True, quick_now=quick)
    tick(arena_ctx, name)
    data["confirm_at"] = 0.0
    tick(arena_ctx, name)
    assert fights == [(True, quick)]
    assert data["fights"] == 1 and data["wins"] == 1
    assert len(arena_ctx.session.input.clicks) == 2


def test_a_fight_is_left_to_the_auto(arena_ctx):
    tick(arena_ctx, "fight")
    assert FIGHT_POINT not in arena_ctx.session.input.clicks


def test_a_result_without_its_title_yet_is_waited_for(arena_ctx):
    import numpy as np

    data = arena_data(arena_ctx)
    data.update(fighting=True, quick_now=True)
    frame = shot("quick_victory")
    frame[100:240, 450:830] = 0
    arena_ctx.frame = frame
    route(arena_ctx)
    assert data["fights"] == 0 and arena_ctx.session.input.clicks == []
    data["result_since"] -= 5
    route(arena_ctx)
    assert data["fights"] == 1 and data["wins"] == 0


def test_a_screen_on_its_way_after_a_click_is_not_escaped(arena_ctx):
    import numpy as np

    arena_data(arena_ctx)["acted_at"] = time.monotonic()
    for _ in range(3):
        arena_ctx.frame = np.full((720, 1280, 3), 60, np.uint8)
        route(arena_ctx)
    assert arena_ctx.session.input.keys == []


def refill_setting(ctx, on: bool) -> None:
    ctx.config = ctx.config.model_copy(update={"arena": ctx.config.arena.model_copy(update={"friendship_refill": on})})


def test_flags_are_bought_with_friendship_only_when_allowed(arena_ctx):
    from states.arena import BUY_POINT, CANCEL_POINT

    refill_setting(arena_ctx, True)
    tick(arena_ctx, "purchase")
    # The popup is still growing in: nothing is clicked yet.
    assert arena_ctx.session.input.clicks == []
    arena_data(arena_ctx)["purchase_since"] -= 5
    tick(arena_ctx, "purchase")
    assert arena_ctx.session.input.clicks == [BUY_POINT]
    assert arena_data(arena_ctx)["refills"] == 1
    refill_setting(arena_ctx, False)
    arena_data(arena_ctx)["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase")
    assert arena_ctx.session.input.clicks[-1] == CANCEL_POINT


def test_the_skystone_offer_framed_is_never_bought(arena_ctx):
    from states.arena import BUY_POINT, CANCEL_POINT, FRIENDSHIP_OFFER_POINT

    refill_setting(arena_ctx, True)
    frame = shot("purchase")
    # Move the yellow frame from the friendship offer to the skystone one.
    frame[302:442, 664:904] = frame[302:442, 376:616]
    frame[302:442, 376:616] = cv2.GaussianBlur(frame[302:442, 376:616], (1, 1), 0) // 3
    data = arena_data(arena_ctx)
    for _ in range(4):
        data["purchase_since"] = time.monotonic() - 5
        data["offer_at"] = 0.0
        data["cancel_at"] = 0.0
        arena_ctx.frame = frame.copy()
        arena_ctx.last_click = None
        route(arena_ctx)
    assert BUY_POINT not in arena_ctx.session.input.clicks
    assert arena_ctx.session.input.clicks[-1] == CANCEL_POINT


def test_out_of_flags_refills_once_with_friendship_then_stops(arena_ctx):
    from states.arena import BUY_POINT, CANCEL_POINT

    refill_setting(arena_ctx, True)
    data = arena_data(arena_ctx)
    tick(arena_ctx, "npc_picked")
    data["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase")
    assert arena_ctx.session.input.clicks == [CHALLENGE_POINT, BUY_POINT]
    # The flags are back: fights go on.
    data["challenge_at"] = 0.0
    assert tick(arena_ctx, "npc_picked") is State.ROUTE
    assert arena_ctx.session.input.clicks[-1] == CHALLENGE_POINT
    # Out again, the friendship offer is spent for the day: only skystones, not allowed, so nothing is bought.
    data["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase_skystones_only")
    assert arena_ctx.session.input.clicks[-1] == CANCEL_POINT
    assert tick(arena_ctx, "npc_picked") is State.HALT
    assert arena_ctx.session.input.clicks.count(BUY_POINT) == 1 and data["refills"] == 1


def test_players_are_fought_from_the_opponents_list_when_asked(arena_ctx):
    from states.arena import OPPONENTS_TAB_POINT

    arena_ctx.config = arena_ctx.config.model_copy(update={"arena": arena_ctx.config.arena.model_copy(update={"attack_players": True})})
    tick(arena_ctx, "npc")
    tick(arena_ctx, "opponents")
    assert arena_ctx.session.input.clicks == [OPPONENTS_TAB_POINT, FIRST_NPC_POINT]


def test_after_the_daily_friendship_refill_only_skystones_are_offered_and_nothing_is_bought(arena_ctx):
    from states.arena import BUY_POINT, CANCEL_POINT

    refill_setting(arena_ctx, True)
    arena_data(arena_ctx)["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase_skystones_only")
    assert arena_ctx.session.input.clicks == [CANCEL_POINT]
    assert BUY_POINT not in arena_ctx.session.input.clicks


def target_setting(ctx, **values) -> None:
    ctx.config = ctx.config.model_copy(update={"arena": ctx.config.arena.model_copy(update=values)})


def test_skystones_refill_only_toward_a_target(arena_ctx):
    from states.arena import BUY_POINT, CANCEL_POINT

    target_setting(arena_ctx, skystone_refill=True, target_fights=0)
    data = arena_data(arena_ctx)
    data["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase_skystones_only")
    assert arena_ctx.session.input.clicks == [CANCEL_POINT]
    assert "no target" in data["no_refill"]
    target_setting(arena_ctx, skystone_refill=True, target_fights=8)
    data.pop("no_refill")
    data.update(fights=5, purchase_since=time.monotonic() - 5, cancel_at=0.0)
    spent = []
    arena_ctx.on_arena_skystones = spent.append
    tick(arena_ctx, "purchase_skystones_only")
    assert arena_ctx.session.input.clicks[-1] == BUY_POINT
    assert spent == [30] and data["skystones"] == 30


def test_the_friendship_offer_comes_before_skystones(arena_ctx):
    from states.arena import BUY_POINT

    target_setting(arena_ctx, friendship_refill=True, skystone_refill=True, target_fights=8)
    spent = []
    arena_ctx.on_arena_skystones = spent.append
    arena_data(arena_ctx)["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase")
    assert arena_ctx.session.input.clicks == [BUY_POINT]
    assert spent == []


def test_skystones_are_picked_when_the_friendship_offer_is_framed_but_not_allowed(arena_ctx):
    from states.arena import SKYSTONE_OFFER_POINT

    target_setting(arena_ctx, friendship_refill=False, skystone_refill=True, target_fights=8)
    arena_data(arena_ctx)["purchase_since"] = time.monotonic() - 5
    tick(arena_ctx, "purchase")
    assert arena_ctx.session.input.clicks == [SKYSTONE_OFFER_POINT]


def test_the_run_stops_at_its_target_of_fights(arena_ctx):
    target_setting(arena_ctx, target_fights=3)
    arena_data(arena_ctx)["fights"] = 3
    assert tick(arena_ctx, "npc") is State.HALT
    assert arena_ctx.session.input.clicks == []


def test_the_weekly_reward_popup_is_left_to_the_player():
    from states.arena import WEEKLY_REWARD_REGION
    ocr = Ocr(load_config(Path(__file__).parent.parent / "config.yaml").vision)
    popup = cv2.imread(str(Path(__file__).parent / "fixtures" / "arena_weekly_reward.png"))
    assert "weekly rank reward" in ocr.read(popup, WEEKLY_REWARD_REGION).casefold()
    npc = cv2.imread(str(Path(__file__).parent / "fixtures" / "arena_npc.png"))
    assert "weekly rank reward" not in ocr.read(npc, WEEKLY_REWARD_REGION).casefold()


def test_the_hero_with_no_equipment_warning_is_passed(arena_ctx):
    from states.arena import NO_GEAR_DO_NOT_DISPLAY_POINT
    arena_ctx.frame = cv2.imread(str(Path(__file__).parent / "fixtures" / "arena_no_equipment_popup.png"))
    route(arena_ctx)
    assert arena_ctx.session.input.clicks == [NO_GEAR_DO_NOT_DISPLAY_POINT]

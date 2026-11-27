"""The long route's steps, on real screenshots of each screen."""

import sys
import time
from pathlib import Path

import cv2
import pytest

from core.config import State
from core.state import StateMachine
from states.route import DONE, RECRUIT_VILDRED, STEPS, level_up, story_reached
from tests.test_intro_flow import BATTLE_GUIDE_SKIP, ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def shot(name: str):
    return cv2.imread(str(FIXTURES / f"route_{name}.png"))


def test_the_story_step_stops_at_its_stage_or_past_it():
    assert story_reached("6th World Episode 3-3", "6th World Episode 3-3")
    assert story_reached("6th World Episode 4-1", "6th World Episode 3-3")
    assert not story_reached("6th World Episode 3-2", "6th World Episode 3-3")
    assert not story_reached(None, "6th World Episode 3-3")


@pytest.mark.parametrize(
    ("name", "point"),
    [("rp1", (1203, 265)), ("rec6", (775, 662)), ("rec7", (746, 554))],
)
def test_recruiting_vildred_clicks_through_each_screen(ctx, name, point):
    ctx.frame = shot(name)
    assert RECRUIT_VILDRED(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [point]


def test_recruiting_vildred_is_done_once_the_blessing_is_taken(ctx):
    ctx.frame = shot("rec13")
    assert RECRUIT_VILDRED(ctx) == DONE
    assert ctx.session.input.clicks == []


def test_a_tutorial_over_the_recruit_page_is_skipped_first(ctx):
    ctx.frame = shot("rec1")
    assert RECRUIT_VILDRED(ctx) is State.ROUTE
    x, y = ctx.session.input.clicks[0]
    assert 1080 < x < 1250 and y < 70


LEVEL_VILDRED = STEPS[3]
AWAKEN_FIRST = STEPS[5]


def test_levelling_picks_penguins_once_then_levels_up(ctx):
    ctx.frame = shot("h3")
    LEVEL_VILDRED(ctx)
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("h4")
    LEVEL_VILDRED(ctx)
    assert ctx.session.input.clicks == [(1095, 662), (748, 662)]


def test_levelling_is_done_on_the_success_popup_or_at_max_level(ctx):
    ctx.frame = shot("h5")
    assert LEVEL_VILDRED(ctx) is State.ROUTE
    assert LEVEL_VILDRED(ctx) is State.ROUTE
    ctx.frame = shot("h2")
    assert LEVEL_VILDRED(ctx) == DONE
    ctx.route_data.clear()
    ctx.frame = shot("lc1")
    assert LEVEL_VILDRED(ctx) == DONE


def test_levelling_opens_the_level_up_page_from_vildred_s_page(ctx):
    ctx.frame = shot("h2")
    LEVEL_VILDRED(ctx)
    assert ctx.session.input.clicks == [(626, 640)]


@pytest.mark.parametrize(("name", "point"), [("a5", (1162, 34)), ("b1", (1108, 662)), ("b2", (740, 594)), ("b3", (740, 594))])
def test_awakening_clicks_through_nodes_and_rune_exchanges(ctx, name, point):
    ctx.frame = shot(name)
    assert AWAKEN_FIRST(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [point]


def test_an_awakening_is_done_once_its_reward_is_closed(ctx):
    ctx.frame = shot("a9")
    assert AWAKEN_FIRST(ctx) is State.ROUTE
    assert AWAKEN_FIRST(ctx) is State.ROUTE
    ctx.frame = shot("b1")
    assert AWAKEN_FIRST(ctx) == DONE


def test_the_hero_search_types_the_name_then_opens_the_result(ctx):
    level_landy = level_up("hero_name_landy", "Landy")
    ctx.frame = shot("lc2")
    level_landy(ctx)
    assert ctx.session.input.clicks == [(740, 150)]
    assert ctx.session.input.typed == ["Landy"]
    ctx.frame = shot("lc3")
    level_landy(ctx)
    assert ctx.session.input.clicks[-1] == (860, 269)


ALTAR_FIRST, ALTAR_QUICK, ALTAR_BUY = STEPS[9], STEPS[10], STEPS[11]


@pytest.mark.parametrize(("name", "point"), [("s0", (167, 430)), ("s1", (880, 182)), ("s3", (1129, 657))])
def test_the_first_altar_fight_goes_from_the_battle_page_to_start(ctx, name, point):
    ctx.frame = shot(name)
    assert ALTAR_FIRST(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [point]


def test_the_first_altar_fight_is_skipped_once_quick_battling_shows(ctx):
    ctx.frame = shot("q9")
    assert ALTAR_FIRST(ctx) == DONE


def test_the_first_altar_fight_ends_by_leaving(ctx):
    ctx.route_data["started"] = True
    ctx.frame = shot("s5")
    ALTAR_FIRST(ctx)
    ctx.frame = shot("s6")
    assert ALTAR_FIRST(ctx) == DONE
    assert ctx.session.input.clicks == [(1168, 660), (126, 660)]


def test_quick_battling_runs_then_closes(ctx):
    ctx.frame = shot("q9")
    ALTAR_QUICK(ctx)
    ctx.frame = shot("q4")
    ALTAR_QUICK(ctx)
    ctx.frame = shot("q5")
    assert ALTAR_QUICK(ctx) is State.ROUTE
    ctx.frame = shot("q9")
    assert ALTAR_QUICK(ctx) == DONE
    assert ctx.session.input.clicks == [(1065, 657), (850, 558)]


def test_more_quick_battling_is_bought_only_at_50_skystones(ctx):
    ctx.frame = shot("q7")
    ALTAR_BUY(ctx)
    ctx.frame = shot("q8")
    ALTAR_BUY(ctx)
    assert ctx.session.input.clicks == [(1234, 658), (748, 509)]
    ctx.frame = shot("q9")
    assert ALTAR_BUY(ctx) == DONE


def test_another_price_for_quick_battling_buys_nothing(ctx):
    frame = shot("q8")
    frame[495:525, 630:880] = 0
    ctx.frame = frame
    assert ALTAR_BUY(ctx) is State.HALT
    assert ctx.session.input.clicks == []


MILESTONE, BUY_SET, EQUIP_VILDRED = STEPS[14], STEPS[15], STEPS[16]


def test_milestone_claims_while_the_essence_pays(ctx):
    ctx.frame = shot("mile_151_130")
    ctx.route_data["page_seen_at"] = 0.0
    MILESTONE(ctx)
    assert ctx.session.input.clicks == [(870, 613)]


def test_milestone_stops_when_the_essence_falls_short(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_mile_80.jpg"))
    ctx.route_data["page_seen_at"] = 0.0
    assert MILESTONE(ctx) == DONE


def test_unreadable_milestone_numbers_are_read_again_before_giving_up(ctx):
    frame = cv2.imread(str(FIXTURES / "route_mile_80.jpg"))
    frame[0:70, 700:1000] = 0
    ctx.frame = frame
    ctx.route_data["page_seen_at"] = 0.0
    results = [MILESTONE(ctx) for _ in range(6)]
    assert results[:5] == [State.ROUTE] * 5 and results[5] == DONE
    assert ctx.session.input.clicks == []


def test_landy_alone_is_not_claimed(ctx):
    frame = cv2.imread(str(FIXTURES / "route_mile_80.jpg"))
    ctx.frame = frame
    ctx.route_data["page_seen_at"] = 0.0
    assert ROUTE.STEPS[19](ctx) is State.HALT
    assert ctx.halt_reason.startswith("route: 2 Radiance essence")
    assert ctx.session.input.clicks == []


def test_landy_set_is_claimed_once_landy_is_in(ctx):
    ctx.frame = shot("mile_151_130")
    ctx.route_data["page_seen_at"] = 0.0
    ROUTE.STEPS[19](ctx)
    assert ctx.session.input.clicks == [(870, 613)]


def test_milestone_takes_the_middle_hero_of_a_chest(ctx):
    ctx.frame = shot("u4")
    MILESTONE(ctx)
    assert ctx.session.input.clicks == [(640, 300), (1118, 653)]
    ctx.frame = shot("u6")
    MILESTONE(ctx)
    ctx.frame = shot("obtained")
    MILESTONE(ctx)
    assert ctx.session.input.clicks[2:] == [(747, 512), (155, 660)]


def test_the_splash_of_milestone_is_closed(ctx):
    ctx.frame = shot("e9")
    MILESTONE(ctx)
    assert ctx.session.input.clicks == [(640, 693)]


def test_the_gear_set_is_bought_at_60_coins_then_received(ctx):
    for name, point in (("g1", (603, 330)), ("z3", (800, 509))):
        ctx.frame = shot(name)
        assert BUY_SET(ctx) is State.ROUTE
        assert ctx.session.input.clicks[-1] == point
    ctx.frame = shot("z4")
    assert BUY_SET(ctx) is State.ROUTE
    ctx.frame = shot("g1")
    assert BUY_SET(ctx) == DONE


def test_another_price_for_the_gear_set_buys_nothing(ctx):
    frame = shot("z3")
    frame[494:524, 610:900] = 0
    ctx.frame = frame
    assert BUY_SET(ctx) is State.HALT


@pytest.mark.parametrize(("name", "point"), [("k5", (631, 640)), ("k6", (640, 626)), ("k7", (768, 528))])
def test_equip_all_puts_the_set_on_vildred(ctx, name, point):
    ctx.frame = shot(name)
    assert EQUIP_VILDRED(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [point]


def test_vildred_with_his_set_is_done(ctx):
    ctx.frame = shot("lc1")
    assert EQUIP_VILDRED(ctx) == DONE


VILDRED_TEAM, CLIMB_30 = STEPS[17], STEPS[18]


def settle_abyss_pages(ctx):
    """Make the Abyss pages count as in view for long enough."""
    now = time.monotonic()
    for key in ("abyss_floors", "abyss_page"):
        ctx.route_data[key] = (now - 60, now)


@pytest.mark.parametrize(("name", "point"), [("ab4", (532, 495)), ("ab2", (1141, 240)), ("ab5", (575, 437))])
def test_the_abyss_team_starts_empty_and_by_hand(ctx, name, point):
    ctx.frame = shot(name)
    VILDRED_TEAM(ctx)
    settle_abyss_pages(ctx)
    assert VILDRED_TEAM(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [point]


def test_the_abyss_card_is_found_on_the_battle_page(ctx):
    ctx.frame = shot("ab1")
    VILDRED_TEAM(ctx)
    x, y = ctx.session.input.clicks[0]
    assert 400 < x < 600 and y == 400


def test_vildred_is_picked_in_the_list_then_placed(ctx):
    ctx.frame = shot("ab6")
    VILDRED_TEAM(ctx)
    x, y = ctx.session.input.clicks[0]
    assert x > 1030 and 180 < y < 240
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("ab7")
    VILDRED_TEAM(ctx)
    x, y = ctx.session.input.clicks[1]
    assert 920 < x < 1000
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("ab8")
    VILDRED_TEAM(ctx)
    x, y = ctx.session.input.clicks[2]
    assert 100 < x < 300


LANDY_TEAM = STEPS[22]


def test_landy_is_picked_by_her_portrait_then_her_plus_then_a_place(ctx):
    ctx.frame = shot("lt3")
    LANDY_TEAM(ctx)
    x, y = ctx.session.input.clicks[0]
    assert x > 1100 and 300 < y < 380
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("lt4")
    LANDY_TEAM(ctx)
    x, y = ctx.session.input.clicks[1]
    assert 920 < x < 990 and 250 < y < 300
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("lt5")
    LANDY_TEAM(ctx)
    assert ctx.session.input.clicks[2] in ((558, 348), (426, 262), (436, 425)) or ctx.session.input.clicks[2][1] < 450
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("lt6")
    assert LANDY_TEAM(ctx) == DONE


def test_a_team_with_vildred_is_left_as_it_is(ctx):
    ctx.frame = shot("ac1")
    assert VILDRED_TEAM(ctx) == DONE


def test_the_climb_starts_clears_and_tries_again(ctx):
    ctx.frame = shot("ac1")
    CLIMB_30(ctx)
    ctx.frame = shot("abyss_clear")
    CLIMB_30(ctx)
    ctx.last_click = None
    ctx.frame = shot("fight_failed")
    CLIMB_30(ctx)
    assert ctx.session.input.clicks == [(1151, 658), (1185, 661), (1180, 661)]


def test_ten_lost_fights_in_a_row_start_a_new_account(ctx):
    ctx.route_data["fails"] = 9
    ctx.frame = shot("fight_failed")
    assert CLIMB_30(ctx) is State.RESET_ACCOUNT
    assert ctx.reset_pending


def test_the_climb_ends_on_the_clear_of_its_floor(ctx):
    climb_1 = sys.modules["states.route"].abyss_climb(1)
    ctx.frame = shot("abyss_clear")
    assert climb_1(ctx) == DONE


ROUTE = sys.modules["states.route"]


def test_the_pass_is_bought_only_at_1900_skystones(ctx):
    ctx.frame = shot("pa1")
    assert ROUTE.buy_pass(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(748, 509)]


def test_another_price_for_the_pass_buys_nothing(ctx):
    frame = shot("pa1")
    frame[495:525, 610:900] = 0
    ctx.frame = frame
    assert ROUTE.buy_pass(ctx) is State.HALT
    assert ctx.session.input.clicks == []


@pytest.mark.parametrize(("name", "point"), [("pa2", (640, 484)), ("pa3", (640, 508))])
def test_the_popups_after_buying_the_pass_are_closed(ctx, name, point):
    ctx.frame = shot(name)
    ROUTE.buy_pass(ctx)
    assert ctx.session.input.clicks == [point]


def test_a_bought_pass_is_done(ctx):
    ctx.frame = shot("pa4")
    assert ROUTE.buy_pass(ctx) == DONE


def test_too_few_skystones_buy_nothing(ctx):
    ctx.frame = shot("ep1")
    ctx.route_data["on_memory"] = True
    ROUTE.buy_pass(ctx)
    # ep1 shows 1,505 skystones
    assert ctx.halt_reason.startswith("route: 1505 skystones")
    assert ctx.session.input.clicks == []


@pytest.mark.parametrize("name", ["cv1", "bb2"])
def test_a_summon_paid_in_bookmarks_is_confirmed(ctx, name):
    ctx.frame = shot(name)
    ROUTE.pull_bookmarks(ctx)
    assert ctx.session.input.clicks == [(748, 542)]


def test_a_summon_not_paid_in_bookmarks_is_cancelled(ctx):
    frame = shot("cv1")
    frame[322:406, 518:602] = 0
    ctx.frame = frame
    assert ROUTE.pull_bookmarks(ctx) == DONE
    assert ctx.session.input.clicks == [(532, 542)]


def test_a_result_is_read_then_pulled_again(ctx):
    ctx.frame = shot("cv3")
    for _ in range(10):
        ROUTE.pull_bookmarks(ctx)
        if ctx.session.input.clicks:
            break
    assert ctx.session.input.clicks == [(1157, 662)]
    assert ctx.summon.cards_read == 10


def test_the_covenant_page_starts_a_x10(ctx):
    ctx.frame = shot("bb1")
    ROUTE.pull_bookmarks(ctx)
    assert ctx.session.input.clicks == [(1137, 662)]


def test_reputation_opens_a_tab_then_receives_all(ctx):
    ctx.frame = shot("rp2")
    ROUTE.CLAIM_REPUTATION(ctx)
    assert ctx.session.input.clicks == []
    ctx.route_data["page_seen_at"] = 0.0
    ROUTE.CLAIM_REPUTATION(ctx)
    assert ctx.session.input.clicks == [(60, 110)]
    ctx.route_data["acted_at"] = None
    ROUTE.CLAIM_REPUTATION(ctx)
    assert ctx.session.input.clicks[-1] == (1127, 662)


def test_the_reputation_popup_is_closed(ctx):
    ctx.frame = shot("rp3")
    ROUTE.CLAIM_REPUTATION(ctx)
    assert ctx.session.input.clicks == [(640, 558)]



def test_the_mail_takes_energy_first_and_not_gold(ctx):
    ctx.frame = shot("ml2")
    ROUTE.claim_mail(ctx)
    x, y = ctx.session.input.clicks[0]
    assert x == 1144 and 130 < y < 170


@pytest.mark.parametrize(("name", "low", "high"), [("ml3", 130, 175), ("ml4", 135, 180), ("ml5", 140, 185)])
def test_the_mail_takes_chests_and_penguins(ctx, name, low, high):
    ctx.frame = shot(name)
    ROUTE.claim_mail(ctx)
    x, y = ctx.session.input.clicks[0]
    assert x == 1144 and low < y < high


def test_the_mail_never_takes_bloom_gold_catalyst_or_gift(ctx):
    for name in ("ml2", "ml3", "ml5"):
        frame = shot(name)
        icons = [m for t in ROUTE.WANTED_MAIL for m in ctx.library.find_every(frame, t, ROUTE.MAIL_ICON_REGION)]
        ys = sorted(m.center[1] for m in icons)
        unwanted = {"ml2": (235, 360), "ml3": (240,), "ml5": (375,)}[name]
        for y in unwanted:
            assert all(abs(found - (y + 25)) > 30 for found in ys), (name, y, ys)


def test_a_locked_awakening_stops_the_route(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_awaken_locked.jpg"))
    assert STEPS[5](ctx) is State.HALT



def test_the_altar_confirms_auto_assign(ctx):
    ctx.frame = shot("ab4")
    ALTAR_FIRST(ctx)
    assert ctx.session.input.clicks == [(747, 495)]



def test_a_won_fight_s_clear_is_tapped_through(ctx):
    ctx.route_data["started"] = True
    ctx.frame = cv2.imread(str(FIXTURES / "route_altar_clear.jpg"))
    ALTAR_FIRST(ctx)
    assert ctx.session.input.clicks == [(640, 400)]


def test_milestone_goes_on_while_the_tier_list_changes(ctx, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(sys.modules["states.route"].time, "monotonic", lambda: clock[0])
    ctx.frame = shot("v1")
    ctx.route_data["page_seen_at"] = 0.0
    MILESTONE(ctx)
    clock[0] += 5
    ctx.frame = cv2.imread(str(FIXTURES / "route_mile_left.jpg"))
    ctx.route_data["page_seen_at"] = 0.0
    assert MILESTONE(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(870, 613), (870, 613)]


def test_no_set_is_bought_without_60_coins(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_mile_left.jpg"))
    assert BUY_SET(ctx) is State.HALT
    assert ctx.session.input.clicks == []


def test_80_coins_are_read_and_the_set_is_bought(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_mile_80.jpg"))
    assert BUY_SET(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(1215, 612)]



def test_a_tutorial_on_the_hero_page_is_skipped_before_searching(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_equip_tutorial.png"))
    EQUIP_VILDRED(ctx)
    x, y = ctx.session.input.clicks[0]
    assert x > 1080 and y < 60



def test_the_abyss_floor_list_opens_the_team(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_abyss_floors.jpg"))
    VILDRED_TEAM(ctx)
    assert ctx.session.input.clicks == []
    settle_abyss_pages(ctx)
    VILDRED_TEAM(ctx)
    assert ctx.session.input.clicks == [(1131, 661)]



def test_an_abyss_clear_left_over_is_left_with_back(ctx):
    ctx.frame = shot("abyss_clear")
    MILESTONE(ctx)
    assert ctx.session.input.clicks == [(147, 660)]



def test_landy_with_her_set_is_done(ctx):
    equip_landy = sys.modules["states.route"].equip_all("hero_name_landy", "Landy")
    ctx.frame = cv2.imread(str(FIXTURES / "route_landy_equipped.jpg"))
    assert equip_landy(ctx) == DONE


def test_a_pause_menu_on_the_route_goes_back_to_the_fight(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "pause_menu.png"))
    sys.modules["states.route"].unknown(ctx)
    assert ctx.session.input.clicks == [(640, 502)]


@pytest.mark.parametrize("goal", ["climb", "theater", "arena"])
def test_the_battle_guide_is_passed_with_do_not_display_in_the_kept_account_modes(ctx, goal):
    ctx.goal = goal
    ctx.frame = cv2.imread(str(FIXTURES / "battle_guide.png"))
    assert sys.modules["states.route"].route(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [BATTLE_GUIDE_SKIP]


def test_the_battle_guide_is_passed_on_a_step_of_the_long_route(ctx):
    ctx.goal = "abyss"
    ctx.frame = cv2.imread(str(FIXTURES / "battle_guide.png"))
    assert STEPS[18](ctx) is State.ROUTE
    assert ctx.session.input.clicks == [BATTLE_GUIDE_SKIP]


def test_no_esc_right_after_a_fight_was_seen(ctx):
    route = sys.modules["states.route"]
    ctx.route_data["fight_at"] = route.time.monotonic()
    ctx.route_data["unknown_since"] = 0.0
    ctx.frame = shot("lt6")[:, :, :] * 0
    route.unknown(ctx)
    assert ctx.session.input.keys == []



def test_each_esc_of_a_step_waits_longer_for_a_slow_page(ctx):
    route = sys.modules["states.route"]
    ctx.frame = shot("lt6")[:, :, :] * 0
    now = route.time.monotonic()
    ctx.route_data["escapes"] = 1
    ctx.route_data["unknown_since"] = now - route.UNKNOWN_WAIT_SECONDS * 1.5
    route.unknown(ctx)
    assert ctx.session.input.keys == []
    ctx.route_data["unknown_since"] = now - route.UNKNOWN_WAIT_SECONDS * 2.5
    route.unknown(ctx)
    assert len(ctx.session.input.keys) == 1


def test_a_fight_behind_a_saved_finished_route_starts_the_route_over(ctx):
    route = sys.modules["states.route"]
    ctx.set_route_step(len(route.STEPS) - 1)
    ctx.route_data["decided"] = True
    ctx.frame = cv2.imread(str(FIXTURES / "fight_our_turn.jpg"))
    assert route.decide(ctx) is State.STORY
    assert ctx.route_step == 0


def test_a_fight_after_steps_of_this_run_is_waited_out(ctx):
    route = sys.modules["states.route"]
    ctx.route_steps_this_run = 3
    ctx.route_data["decided"] = True
    ctx.frame = cv2.imread(str(FIXTURES / "fight_our_turn.jpg"))
    assert route.decide(ctx) is State.ROUTE


def test_a_scrolling_floor_list_is_not_clicked(ctx):
    frame = cv2.imread(str(FIXTURES / "route_abyss_floors.jpg"))
    ctx.frame = frame
    VILDRED_TEAM(ctx)
    ctx.route_data["still_at"] = 0.0
    ctx.frame = frame.copy()
    ctx.frame[60:640, 740:980] = 255 - ctx.frame[60:640, 740:980]
    VILDRED_TEAM(ctx)
    assert ctx.session.input.clicks == []


def test_the_last_result_is_not_read_again_after_a_pull(ctx):
    ctx.frame = shot("cv1")
    ROUTE.pull_bookmarks(ctx)
    ctx.frame = shot("cv3")
    for _ in range(10):
        assert ROUTE.pull_bookmarks(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(748, 542)]
    assert ctx.summon.cards_read == 0
    ctx.route_data["animation_seen"] = True
    for _ in range(10):
        ROUTE.pull_bookmarks(ctx)
    assert ctx.summon.cards_read == 10


@pytest.mark.parametrize(("name", "point"), [
    ("cw0", (730, 558)),
    ("cw1", (354, 622)),
    ("cw2", (747, 460)),
    ("cw4", (880, 662)),
    ("cw5", (155, 660)),
])
def test_the_covenant_wish_list_and_free_x1_come_first(ctx, name, point):
    ctx.frame = shot(name)
    assert ROUTE.prepare_covenant(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [point]


def test_the_wish_list_closes_once_applied(ctx):
    ctx.frame = shot("cw1")
    ROUTE.prepare_covenant(ctx)
    ctx.route_data["acted_at"] = None
    ctx.frame = shot("cw3")
    ROUTE.prepare_covenant(ctx)
    assert ctx.session.input.clicks == [(354, 622), (640, 694)]


def test_the_covenant_is_ready_once_the_free_x1_is_used(ctx):
    ctx.frame = shot("cw6")
    assert ROUTE.prepare_covenant(ctx) == DONE
    assert ctx.session.input.clicks == []


def test_nothing_to_keep_goes_back_from_the_results_before_the_reset(ctx):
    ctx.frame = shot("cv3")
    assert ROUTE.decide(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(155, 660)]
    assert not ctx.reset_pending


def test_nothing_to_keep_resets_from_the_lobby(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "lobby.png"))
    assert ROUTE.decide(ctx) is State.RESET_ACCOUNT
    assert ctx.reset_pending


def test_the_paid_first_pack_over_the_lobby_is_only_closed(ctx):
    ctx.frame = shot("first_pack")
    ctx.route_step = 6
    assert ROUTE.route(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(640, 697)]


def test_the_wish_list_is_not_closed_as_a_lobby_popup(ctx):
    ctx.frame = shot("cw1")
    ctx.route_step = len(ROUTE.STEPS) - 3
    ROUTE.route(ctx)
    assert (640, 697) not in ctx.session.input.clicks


def test_a_screen_that_never_changes_gets_an_esc(ctx, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ROUTE.time, "monotonic", lambda: clock[0])
    ctx.frame = shot("first_pack")
    assert not ROUTE.frozen(ctx)
    clock[0] += 10
    assert not ROUTE.frozen(ctx)
    clock[0] += 11
    assert ROUTE.frozen(ctx)
    assert not ROUTE.frozen(ctx)


def test_a_changing_screen_is_not_frozen(ctx, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ROUTE.time, "monotonic", lambda: clock[0])
    ctx.frame = shot("first_pack")
    ROUTE.frozen(ctx)
    clock[0] += 30
    ctx.frame = shot("cw0")
    assert not ROUTE.frozen(ctx)


def test_route_pulls_survive_a_restart(tmp_path):
    from core.stats import load_route_pulls, save_route_pulls
    path = tmp_path / "state.json"
    assert load_route_pulls(path) == {}
    save_route_pulls({"cards_read": 40, "moonlight_5star": ["Ran"], "unsure": [], "other": 1}, path)
    assert load_route_pulls(path) == {"cards_read": 40, "moonlight_5star": ["Ran"], "unsure": []}


def test_a_new_account_forgets_the_saved_pulls(ctx):
    saved = []
    ctx.on_route_pulls = saved.append
    ctx.summon.moonlight_5star.append("Ran")
    ctx.start_new_account()
    assert saved[-1] == {"cards_read": 0, "moonlight_5star": [], "unsure": []}


def test_a_finished_route_counts_as_a_run(ctx):
    runs = []
    ctx.on_run_done = runs.append
    ctx.account_started_at = 0.0
    ctx.frame = shot("cv3")
    ROUTE.decide(ctx)
    assert len(runs) == 1


def test_low_skystones_after_buying_mean_the_pass_is_bought(ctx):
    ctx.frame = shot("ep1")
    ctx.route_data.update(on_memory=True, bought=True)
    assert ROUTE.buy_pass(ctx) == DONE
    assert ctx.session.input.clicks == []


def test_a_pass_bought_before_a_restart_is_recognised(ctx):
    ctx.frame = shot("pass_bought")
    assert ROUTE.buy_pass(ctx) == DONE


@pytest.mark.parametrize(("name", "essence"), [("mile_52_50", 52), ("mile_151_130", 151)])
def test_the_essence_is_read_wherever_the_numbers_put_it(ctx, name, essence):
    ctx.frame = shot(name)
    assert ROUTE.read_essence(ctx) == essence


def test_the_tier_with_the_set_coins_is_claimed(ctx):
    ctx.frame = shot("mile_52_50")
    ctx.route_data["page_seen_at"] = 0.0
    MILESTONE(ctx)
    assert ctx.session.input.clicks == [(870, 613)]


def test_a_chest_takes_the_middle_hero_when_none_is_moonlight(ctx):
    ctx.frame = shot("u4")
    point, picked = ROUTE.pick_chest_hero(ctx)
    assert point == (640, 300) and picked == {"stars": 5, "moonlight": False}


def test_an_upgraded_chest_takes_its_5_star_moonlight(ctx):
    ctx.frame = shot("pick_moonlight")
    point, picked = ROUTE.pick_chest_hero(ctx)
    assert point == (235, 300) and picked == {"stars": 5, "moonlight": True, "name": "Charles", "rank": 63}


def test_a_5_star_moonlight_from_a_chest_counts_for_the_decision(ctx):
    saved = []
    ctx.on_route_pulls = saved.append
    ctx.frame = shot("obtained")
    ROUTE.note_chest_hero(ctx, {"stars": 5, "moonlight": True})
    assert ctx.summon.moonlight_5star and ctx.summon.moonlight_5star[0].startswith("Zahhak")
    assert saved[-1]["moonlight_5star"] == ctx.summon.moonlight_5star


def test_a_normal_chest_hero_does_not_count(ctx):
    ctx.frame = shot("obtained")
    ROUTE.note_chest_hero(ctx, {"stars": 5, "moonlight": False})
    ROUTE.note_chest_hero(ctx, None)
    assert ctx.summon.moonlight_5star == [] and ctx.summon.unsure == []


def test_landy_is_awakened_after_her_level(ctx):
    step = ROUTE.STEPS[20]
    assert "level up Landy" in step.__name__ and "awaken Landy" in step.__name__


def test_then_runs_the_second_step_once_the_first_is_done(ctx):
    calls = []
    first = lambda c: calls.append("first") or DONE
    second = lambda c: calls.append("second") or DONE
    first.__name__, second.__name__ = "a", "b"
    both = ROUTE.then(first, second)
    assert both(ctx) is State.ROUTE
    assert both(ctx) == DONE
    assert calls == ["first", "second"]


def test_an_awakening_without_runes_is_given_up(ctx):
    ctx.frame = shot("b1")
    step = ROUTE.awaken_hero("hero_name_landy", "Landy", times=4)
    results = []
    for _ in range(ROUTE.MAX_AWAKEN_TRIES + 1):
        ctx.route_data["acted_at"] = None
        results.append(step(ctx))
    assert results[:-1] == [State.ROUTE] * ROUTE.MAX_AWAKEN_TRIES and results[-1] == DONE


def test_awakenings_are_counted_until_the_last(ctx):
    step = ROUTE.awaken_hero("hero_name_landy", "Landy", times=2)
    for _ in range(2):
        ctx.frame = shot("a9")
        ctx.route_data["acted_at"] = None
        assert step(ctx) is State.ROUTE
        ctx.frame = shot("b1")
        ctx.route_data["acted_at"] = None
        result = step(ctx)
    assert result == DONE


def test_a_lingering_awakening_popup_counts_once(ctx):
    step = ROUTE.awaken_hero("hero_name_landy", "Landy", times=4)
    ctx.frame = shot("a9")
    for _ in range(3):
        ctx.route_data["acted_at"] = None
        step(ctx)
    assert ctx.route_data["awakened"] == 1


def test_a_quick_battling_already_used_is_done(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_altar_start.jpg"))
    results = [ALTAR_QUICK(ctx) for _ in range(3)]
    assert results == [State.ROUTE, State.ROUTE, DONE]
    assert ctx.session.input.clicks == []


def test_a_moonlight_chest_hero_with_a_hidden_star_keeps_the_account(ctx):
    ctx.frame = shot("obtained")
    ROUTE.note_chest_hero(ctx, {"stars": 4, "moonlight": True})
    assert ctx.summon.moonlight_5star == [] and len(ctx.summon.unsure) == 1


def test_the_event_summons_go_through_the_summon_state(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "lobby.png"))
    assert ROUTE.event_summons(ctx) is State.SUMMON
    ctx.summon.event_done = True
    assert ROUTE.event_summons(ctx) == DONE


def test_the_event_summons_wait_for_the_lobby_after_the_abyss(ctx):
    ctx.frame = shot("abyss_clear_50")
    assert ROUTE.event_summons(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [ROUTE.ABYSS_BACK_POINT]


def test_the_event_summons_can_be_turned_off(ctx):
    ctx.config = ctx.config.model_copy(update={"summon": ctx.config.summon.model_copy(update={"event_summons": False})})
    assert ROUTE.event_summons(ctx) == DONE


def test_the_summon_state_hands_back_to_the_route_once_no_free_summon_is_left(ctx):
    summon = sys.modules["states.summon"]
    ctx.goal = "abyss"
    assert summon._no_free_left(ctx) is State.ROUTE
    assert ctx.summon.event_done


def test_the_event_step_comes_before_the_wish_list(ctx):
    names = [step.__name__ for step in ROUTE.STEPS]
    assert names.index("claim and pull the event's free summons") + 1 == names.index("set the Covenant wish list and do the free x1")


def test_a_chest_of_moonlight_heroes_takes_the_best_ranked(ctx):
    ctx.frame = shot("pick_all_moonlight")
    point, picked = ROUTE.pick_chest_hero(ctx)
    # Charles 63, Kise 50, and a dark Tenebria: Specter Tenebria, 64.
    assert point == (640, 300) and picked["name"] == "Kise"


def test_a_hidden_hero_list_is_opened_with_a_free_place(ctx):
    ctx.frame = shot("ready_vildred")
    assert LANDY_TEAM(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(425, 365)]


def test_landy_at_the_top_of_the_list_is_picked_before_her_plus(ctx):
    ctx.frame = shot("landy_list_top")
    LANDY_TEAM(ctx)
    x, y = ctx.session.input.clicks[0]
    assert x > 1030 and 180 < y < 215


def test_a_short_pass_waits_for_the_free_summons_instead_of_stopping(ctx):
    ctx.frame = shot("ep1")
    ctx.route_data["on_memory"] = True
    assert ROUTE.buy_pass_if_enough(ctx) == DONE
    assert ctx.route_flags["pass_short"]
    assert ctx.session.input.clicks == []


def test_the_catch_up_steps_run_only_for_a_short_pass(ctx):
    calls = []
    step = lambda c: calls.append(1) or DONE
    step.__name__ = "x"
    wrapped = ROUTE.only_if_pass_short(step)
    assert wrapped(ctx) == DONE and calls == []
    ctx.route_flags["pass_short"] = True
    wrapped(ctx)
    assert calls == [1]


def test_all_the_summons_come_after_the_pass(ctx):
    names = [step.__name__ for step in ROUTE.STEPS]
    first_summon = names.index("claim and pull the event's free summons")
    assert names.index("claim the Heir's Memory bookmarks") < first_summon
    assert names.index("pull Covenant Summon with the bookmarks") > first_summon


def test_a_scrolling_floor_list_is_waited_for(ctx):
    frame = shot("abyss_floor_list")
    frame[630:700, 1000:1260] = 20
    ctx.frame = frame
    for _ in range(5):
        assert ROUTE.to_abyss(ctx) is State.ROUTE
    assert ctx.session.input.clicks == []


def test_landy_tier_still_shown_after_her_claim_does_not_stop_the_set(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_mile_80.jpg"))
    ctx.route_data.update(page_seen_at=0.0, claims=1)
    assert ROUTE.STEPS[19](ctx) == DONE
    assert ctx.halt_reason is None or "Landy" not in ctx.halt_reason


def test_a_moonlight_chest_is_kept_as_a_picture(ctx, tmp_path):
    ctx.config = ctx.config.model_copy(update={"logging": ctx.config.logging.model_copy(update={"log_dir": tmp_path})})
    ctx.frame = shot("pick_moonlight")
    ROUTE.pick_chest_hero(ctx)
    ROUTE.pick_chest_hero(ctx)
    assert len(list((tmp_path / "results").glob("*_chest_pick.png"))) == 1
    ctx.frame = shot("obtained")
    ROUTE.note_chest_hero(ctx, {"stars": 5, "moonlight": True})
    assert len(list((tmp_path / "results").glob("*_chest_obtained.png"))) == 1


def test_a_normal_chest_takes_no_picture(ctx, tmp_path):
    ctx.config = ctx.config.model_copy(update={"logging": ctx.config.logging.model_copy(update={"log_dir": tmp_path})})
    ctx.frame = shot("u4")
    ROUTE.pick_chest_hero(ctx)
    assert not (tmp_path / "results").exists()


KEPT_CLIMB = ROUTE.CLIMB


def test_the_kept_climb_starts_with_the_players_own_team(ctx):
    ctx.goal = "climb"
    ctx.frame = shot("ac1")
    assert ROUTE.route(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(1151, 658)]


def test_the_kept_climb_counts_floors_and_never_ends_on_one(ctx):
    won = []
    ctx.goal = "climb"
    ctx.on_floor_won = lambda: won.append(1)
    ctx.frame = shot("abyss_clear_50")
    assert KEPT_CLIMB(ctx) is State.ROUTE
    assert won == [1]
    assert ctx.session.input.clicks == [(1185, 661)]


def test_three_lost_fights_stop_the_kept_climb_without_a_reset(ctx):
    lost = []
    ctx.goal = "climb"
    ctx.on_floor_lost = lambda: lost.append(1)
    ctx.route_data["fails"] = 2
    ctx.route_data["logged_floor"] = 51
    ctx.route_data["abyss_started"] = True
    ctx.frame = shot("fight_failed")
    assert KEPT_CLIMB(ctx) is State.HALT
    assert ctx.halt_reason.startswith("3 lost fights in a row on abyss floor 52")
    assert lost == [1]
    assert not ctx.reset_pending
    assert ctx.session.input.clicks == []


def test_the_kept_climb_stops_on_an_empty_team_rather_than_pick_heroes(ctx):
    ctx.goal = "climb"
    ctx.frame = shot("ab5")
    settle_abyss_pages(ctx)
    assert KEPT_CLIMB(ctx) is State.HALT
    assert "team is empty" in ctx.halt_reason
    assert ctx.session.input.clicks == []


def test_the_kept_climb_declines_the_auto_assign(ctx):
    ctx.goal = "climb"
    ctx.frame = shot("ab4")
    KEPT_CLIMB(ctx)
    assert ctx.session.input.clicks == [(532, 495)]


def test_the_abyss_card_is_found_wherever_it_sits_on_the_battle_page(ctx):
    ctx.goal = "climb"
    ctx.frame = cv2.imread(str(FIXTURES / "route_battle_kept_account.jpg"))
    KEPT_CLIMB(ctx)
    assert ctx.session.input.clicks == [(797, 400)]


def test_a_full_inventory_stops_the_climb_and_says_which_one(ctx):
    """The machine checks it before any state's handler, so the climb never sees it."""
    ctx.goal = "climb"
    bot = StateMachine(ctx, {})
    bot.state = State.ROUTE
    ctx.frame = shot("inventory_full")
    assert bot._inventory_full()
    assert bot.state is State.HALT
    assert ctx.halt_reason == "cannot start: the artifacts inventory is full"
    assert ctx.session.input.clicks == []


def test_a_chest_hero_not_recognized_is_taken_first(ctx, monkeypatch):
    ctx.frame = shot("pick_all_moonlight")
    monkeypatch.setattr(ROUTE, "chest_rank", lambda name, ranking, element=None: None if name == "Charles" else 1)
    point, picked = ROUTE.pick_chest_hero(ctx)
    assert point == (235, 300)


def test_a_long_fight_counts_as_progress_up_to_a_limit(ctx, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ROUTE.time, "monotonic", lambda: now[0])
    ctx.frame = shot("fight_boss")
    ROUTE.unknown(ctx)
    assert ctx.progressed
    ctx.progressed = False
    now[0] += ROUTE.MAX_FIGHT_SECONDS + 1
    ctx.route_data["fight_at"] = now[0] - 1
    ROUTE.unknown(ctx)
    assert not ctx.progressed


def test_each_floor_starts_its_own_fight_time(ctx, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ROUTE.time, "monotonic", lambda: now[0])
    ctx.frame = shot("fight_boss")
    ROUTE.unknown(ctx)
    now[0] += ROUTE.MAX_FIGHT_SECONDS + 1
    ctx.route_data["fight_at"] = now[0] - 1
    ROUTE.act(ctx, "abyss clear", (1185, 661))
    ctx.progressed = False
    ROUTE.unknown(ctx)
    assert ctx.progressed


def test_the_climb_turns_the_auto_back_on(ctx, monkeypatch):
    ctx.goal = "climb"
    monkeypatch.setattr(ROUTE, "_auto_is_off", lambda ctx: True)
    ctx.frame = shot("fight_auto_off")
    ROUTE.unknown(ctx)
    assert ctx.session.input.clicks == [(1127, 33)]



def test_the_climb_leaves_a_lost_fight_it_did_not_start(ctx):
    ctx.goal = "climb"
    ctx.frame = shot("fight_failed")
    assert KEPT_CLIMB(ctx) is State.ROUTE
    assert ctx.session.input.clicks == [(268, 660)]
    assert "fails" not in ctx.route_data

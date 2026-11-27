"""The Moonlight Theater mode, on real screenshots of each of its screens."""

import time
from pathlib import Path

import cv2
import pytest

from core.config import State
from states.route import route
from states.theater import (
    ENTER_POINT,
    NEXT_STORY_POINT,
    PURCHASE_BUY_POINT,
    PURCHASE_CANCEL_POINT,
    act_to_watch,
    season_shown,
    selected_act_text,
    theater_data,
)
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def shot(name: str):
    image = cv2.imread(str(FIXTURES / f"theater_{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture
def theater_ctx(ctx):
    ctx.goal = "theater"
    ctx.attach = lambda: True
    theater_data(ctx).update(entered_story=0, located=True)
    return ctx


def tick(ctx, name):
    ctx.frame = shot(name)
    ctx.last_click = None
    return route(ctx)


def test_the_side_story_screen_opens_the_theater(theater_ctx):
    tick(theater_ctx, "side_story")
    assert theater_ctx.session.input.clicks == [(1160, 320)]


def test_the_carousel_enters_the_story_in_front_when_it_is_the_one(theater_ctx):
    tick(theater_ctx, "carousel_0")
    assert theater_ctx.session.input.clicks == [ENTER_POINT]


def test_the_carousel_turns_to_the_story_the_bot_is_on(theater_ctx):
    theater_data(theater_ctx)["story"] = 2
    tick(theater_ctx, "carousel_1")
    assert theater_ctx.session.input.clicks == [NEXT_STORY_POINT]


def test_a_free_act_is_watched_and_its_notice_confirmed(theater_ctx):
    data = theater_data(theater_ctx)
    tick(theater_ctx, "page_free")
    tick(theater_ctx, "no_skip")
    assert theater_ctx.session.input.clicks == [(776, 664), (748, 495)]
    assert data["playing_since"]


def test_the_dialogue_of_an_act_is_tapped_on(theater_ctx):
    theater_data(theater_ctx)["playing_since"] = time.monotonic()
    tick(theater_ctx, "dialogue")
    tick(theater_ctx, "dialogue_black")
    assert theater_ctx.session.input.clicks == [(640, 400)] * 6


def test_a_fight_inside_an_act_is_left_to_the_auto(theater_ctx):
    theater_data(theater_ctx)["playing_since"] = time.monotonic()
    tick(theater_ctx, "fight")
    assert (640, 400) not in theater_ctx.session.input.clicks


def test_the_end_of_an_act_counts_it_and_claims_its_rewards(theater_ctx):
    data = theater_data(theater_ctx)
    data["playing_since"] = time.monotonic()
    data["act_seen"] = True
    tick(theater_ctx, "page_ticket")
    assert data["watched"] == 1 and not data["playing_since"]
    assert theater_ctx.session.input.clicks == [(884, 588)]
    tick(theater_ctx, "reward")
    assert theater_ctx.session.input.clicks[-1] == (640, 528)


def test_a_ticket_act_is_confirmed_with_a_ticket(theater_ctx):
    tick(theater_ctx, "page_ticket")
    tick(theater_ctx, "use_ticket")
    assert theater_ctx.session.input.clicks == [(776, 664), (742, 528)]


def test_tickets_are_bought_with_gold_within_the_limit(theater_ctx):
    theater_data(theater_ctx)["gold"] = 47_746_877
    tick(theater_ctx, "purchase")
    tick(theater_ctx, "buy_confirm")
    assert theater_ctx.session.input.clicks == [PURCHASE_BUY_POINT, (830, 531)]
    assert theater_data(theater_ctx)["bought"] == 1


def test_the_theater_stops_at_its_target_of_acts(theater_ctx):
    theater_ctx.config = theater_ctx.config.model_copy(update={"theater": theater_ctx.config.theater.model_copy(update={"target_acts": 2})})
    data = theater_data(theater_ctx)
    data.update(watched=2, season_clicked=(0, 0))
    assert tick(theater_ctx, "page_free") is State.HALT


def no_ticket_left(monkeypatch):
    import states.theater as theater

    real = theater.read_number
    monkeypatch.setattr(theater, "read_number", lambda ctx, region: 0 if region == theater.TICKETS_REGION else real(ctx, region))


def test_with_free_acts_only_a_ticket_act_moves_on_to_the_next_season(theater_ctx, monkeypatch):
    no_ticket_left(monkeypatch)
    data = theater_data(theater_ctx)
    data.update(free_only=True)
    assert tick(theater_ctx, "page_ticket") is State.ROUTE
    assert data["season"] == 1
    assert theater_ctx.session.input.clicks == [(1200, 183)]


def test_the_last_story_done_stops_the_bot(theater_ctx, monkeypatch):
    no_ticket_left(monkeypatch)
    data = theater_data(theater_ctx)
    data.update(free_only=True, story=6, season=1, entered_story=6)
    assert tick(theater_ctx, "page_ticket") is State.HALT


def test_no_ticket_is_bought_when_the_gold_is_low_or_unknown(theater_ctx):
    tick(theater_ctx, "purchase")
    assert theater_ctx.session.input.clicks == [PURCHASE_CANCEL_POINT]
    theater_data(theater_ctx).update(gold=19_000_000, free_only=False)
    tick(theater_ctx, "purchase")
    assert theater_ctx.session.input.clicks[-1] == PURCHASE_CANCEL_POINT


def test_the_story_page_remembers_the_gold(theater_ctx):
    tick(theater_ctx, "page_ticket")
    assert theater_data(theater_ctx)["gold"] == 47_746_877


@pytest.mark.parametrize(("name", "words"), [("page_free", "available"), ("page_ticket", "left")])
def test_the_selected_act_is_read_in_its_yellow_frame(theater_ctx, name, words):
    theater_ctx.frame = shot(name)
    text = selected_act_text(theater_ctx)
    assert text is not None and words in text.casefold()
    assert act_to_watch(text)


def test_a_watched_or_locked_act_is_not_watched_again():
    assert not act_to_watch("Act 1 Complete")
    assert not act_to_watch("Act 2 Rewards Available")
    assert not act_to_watch("Act 4 Unavailable")
    assert not act_to_watch(None)
    assert act_to_watch("Act 1 Available")
    assert act_to_watch("Act 3 6d left")


def test_an_act_already_playing_at_start_is_watched_on(theater_ctx):
    tick(theater_ctx, "dialogue_black")
    assert theater_data(theater_ctx)["playing_since"]
    assert theater_ctx.session.input.clicks == [(640, 400)] * 3


def test_the_page_behind_the_ticket_popup_does_not_count_as_a_watched_act(theater_ctx):
    data = theater_data(theater_ctx)
    tick(theater_ctx, "use_ticket")
    tick(theater_ctx, "page_ticket")
    assert data["watched"] == 0 and data["playing_since"]
    assert theater_ctx.session.input.clicks == [(742, 528)]


def test_one_plus_click_until_the_purchase_popup_shows(theater_ctx):
    data = theater_data(theater_ctx)
    data["gold"] = 47_746_877
    theater_ctx.frame = shot("page_ticket")
    import states.theater as theater_module
    original = theater_module.read_number
    theater_module.read_number = lambda ctx, region: 0 if region == theater_module.TICKETS_REGION else 47_746_877
    try:
        route(theater_ctx)
        theater_ctx.last_click = None
        route(theater_ctx)
    finally:
        theater_module.read_number = original
    assert theater_ctx.session.input.clicks == [(667, 25)]


def test_a_story_page_found_at_start_goes_back_to_the_carousel(theater_ctx):
    theater_data(theater_ctx).pop("entered_story")
    tick(theater_ctx, "page_free")
    assert theater_ctx.session.input.clicks == [(37, 33)]


def test_a_hidden_watch_button_is_waited_for_before_leaving_the_season(theater_ctx):
    import numpy as np
    data = theater_data(theater_ctx)
    frame = shot("page_ticket")
    frame[628:702, 628:710] = 0
    theater_ctx.frame = frame
    route(theater_ctx)
    assert data["season"] == 0 and data["story"] == 0
    data["no_watch_since"] -= 5
    theater_ctx.last_click = None
    route(theater_ctx)
    assert data["season"] == 1


def test_a_black_fade_is_tapped_not_escaped(theater_ctx):
    import numpy as np

    # Inside an act, a black fade is tapped through.
    theater_data(theater_ctx)["playing_since"] = time.monotonic()
    theater_ctx.frame = np.zeros((720, 1280, 3), np.uint8)
    route(theater_ctx)
    assert theater_ctx.session.input.keys == []
    assert theater_ctx.session.input.clicks == [(640, 400)] * 3


def test_a_loading_screen_starts_no_act(theater_ctx):
    import numpy as np

    # From the lobby to Side Story the game shows a black loading screen: no act, no tap, no Esc.
    theater_ctx.frame = np.zeros((720, 1280, 3), np.uint8)
    route(theater_ctx)
    assert not theater_data(theater_ctx).get("playing_since")
    assert theater_ctx.session.input.clicks == [] and theater_ctx.session.input.keys == []


def test_an_act_thought_to_play_ends_when_the_side_story_shows(theater_ctx):
    theater_data(theater_ctx)["playing_since"] = time.monotonic()
    tick(theater_ctx, "side_story")
    assert not theater_data(theater_ctx)["playing_since"]
    assert theater_ctx.session.input.clicks == [(1160, 320)]


def test_a_selected_act_below_the_screen_scrolls_the_list_first(theater_ctx):
    data = theater_data(theater_ctx)
    frame = shot("page_ticket")
    frame[100:720, 940:952] = 0
    theater_ctx.frame = frame
    route(theater_ctx)
    data["no_selection_since"] -= 5
    route(theater_ctx)
    assert data["list_scrolls"] == 1 and data["season"] == 0


def test_at_the_end_of_the_list_the_lowest_act_is_clicked_instead_of_scrolling(theater_ctx):
    import states.theater as theater_module

    data = theater_data(theater_ctx)
    theater_ctx.frame = shot("page_free")
    data["list_before"] = theater_module.act_list_image(theater_ctx)
    assert theater_module.list_at_end(theater_ctx)
    assert data["list_end"] and "list_before" not in data


def test_a_video_inside_an_act_is_skipped_with_esc(theater_ctx):
    import numpy as np

    data = theater_data(theater_ctx)
    data["playing_since"] = time.monotonic()
    video = np.full((720, 1280, 3), 90, np.uint8)
    theater_ctx.frame = video
    route(theater_ctx)
    # Too soon to tell a video from a dialogue line between two log icons: tapped on.
    assert theater_ctx.session.input.keys == []
    data["video_since"] = time.monotonic() - 2
    theater_ctx.frame = video
    theater_ctx.session.capture.frames = [video]
    route(theater_ctx)
    assert theater_ctx.session.input.keys[0] == "press esc"


def test_the_dialogue_is_never_escaped(theater_ctx):
    data = theater_data(theater_ctx)
    data["playing_since"] = time.monotonic()
    data["video_since"] = time.monotonic() - 2
    tick(theater_ctx, "dialogue")
    assert theater_ctx.session.input.keys == []
    assert "video_since" not in data


def test_started_inside_the_theater_it_goes_on_from_the_story_in_front(theater_ctx):
    data = theater_data(theater_ctx)
    data["located"] = False
    tick(theater_ctx, "carousel_1")
    # The carousel may still be sliding: nothing is decided on the first look.
    assert theater_ctx.session.input.clicks == [] and not data["located"]
    data["front_seen"] = (1, time.monotonic() - 2)
    tick(theater_ctx, "carousel_1")
    assert data["story"] == 1 and data["entered_story"] == 1
    assert theater_ctx.session.input.clicks == [ENTER_POINT]


def test_started_from_the_side_story_every_story_is_checked_from_the_first(theater_ctx):
    data = theater_data(theater_ctx)
    data["located"] = False
    tick(theater_ctx, "side_story")
    assert data["located"] and data["story"] == 0


def test_the_lit_season_tab_is_read():
    assert season_shown(shot("page_free")) == 0
    assert season_shown(shot("page_ticket")) is None


def test_started_on_a_story_page_its_season_is_kept(theater_ctx):
    data = theater_data(theater_ctx)
    data.update(located=False, story=0, season=0)
    data.pop("entered_story")
    page = shot("page_free")
    # Season 2 lit instead of season 1.
    bar = page[66:144, 1118:1134].copy()
    page[66:144, 1118:1134] = page[144:222, 1118:1134]
    page[144:222, 1118:1134] = bar
    theater_ctx.frame = page
    theater_ctx.last_click = None
    route(theater_ctx)
    assert data["season"] == 1
    assert theater_ctx.session.input.clicks == [(37, 33)]


def test_a_skill_animation_after_a_fight_frame_is_neither_escaped_nor_tapped(theater_ctx):
    import numpy as np

    data = theater_data(theater_ctx)
    data["playing_since"] = time.monotonic()
    tick(theater_ctx, "fight")
    data["video_since"] = time.monotonic() - 2
    clicks = list(theater_ctx.session.input.clicks)
    theater_ctx.frame = np.full((720, 1280, 3), 90, np.uint8)
    route(theater_ctx)
    assert theater_ctx.session.input.keys == []
    assert theater_ctx.session.input.clicks == clicks


def test_tickets_won_after_running_out_are_used(theater_ctx):
    # page_ticket shows 5 tickets in the top bar.
    theater_data(theater_ctx)["free_only"] = True
    tick(theater_ctx, "page_ticket")
    assert theater_ctx.session.input.clicks == [(776, 664)]


def test_every_act_status_is_read_from_its_color():
    from states.theater import act_rows

    assert act_rows(shot("page_season_2")) == [
        (203, "complete"), (289, "complete"), (375, "complete"), (461, "complete"), (547, "watchable"), (635, "locked"),
    ]
    assert [status for _, status in act_rows(shot("page_ticket"))] == ["complete", "watchable"] + ["locked"] * 5


def on_a_complete_act(monkeypatch, theater_ctx, name):
    import states.theater as theater

    monkeypatch.setattr(theater, "selected_act", lambda ctx: ("Act 1 Complete", 190))
    data = theater_data(theater_ctx)
    data["season_clicked"] = (data["story"], data["season"])
    return tick(theater_ctx, name)


def test_the_first_act_not_complete_is_clicked_at_once(theater_ctx, monkeypatch):
    on_a_complete_act(monkeypatch, theater_ctx, "page_season_2")
    assert theater_ctx.session.input.clicks == [(1028, 547)]


def test_a_lock_after_the_complete_acts_ends_the_season(theater_ctx, monkeypatch):
    frame = shot("page_season_2")
    frame[530:565, 950:1110] = 0
    theater_ctx.frame = frame
    import states.theater as theater

    monkeypatch.setattr(theater, "selected_act", lambda ctx: ("Act 1 Complete", 190))
    data = theater_data(theater_ctx)
    data["season_clicked"] = (0, 0)
    route(theater_ctx)
    assert data["season"] == 1 and theater_ctx.session.input.clicks == [(1200, 183)]


def test_ticket_buying_follows_the_settings(theater_ctx):
    theater = theater_ctx.config.theater
    theater_ctx.config = theater_ctx.config.model_copy(update={"theater": theater.model_copy(update={"min_gold_millions": 10})})
    theater_data(theater_ctx)["gold"] = 19_000_000
    tick(theater_ctx, "purchase")
    assert theater_ctx.session.input.clicks == [PURCHASE_BUY_POINT]
    theater_ctx.config = theater_ctx.config.model_copy(update={"theater": theater.model_copy(update={"buy_tickets": False})})
    tick(theater_ctx, "purchase")
    assert theater_ctx.session.input.clicks[-1] == PURCHASE_CANCEL_POINT
    assert theater_data(theater_ctx)["free_reason"] == "buying tickets is turned off"


def test_the_ticket_settings_reach_the_config():
    from core.config import load_config
    from core.settings import apply_settings, settings_from_config

    config = load_config(Path(__file__).parent.parent / "config.yaml")
    settings = settings_from_config(config).model_copy(update={"theater_buy_tickets": False, "theater_min_gold_millions": 5})
    applied = apply_settings(config, settings).theater
    assert (applied.buy_tickets, applied.min_gold_millions) == (False, 5)

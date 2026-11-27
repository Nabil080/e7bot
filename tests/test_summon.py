"""The way through the free summons, on real screenshots."""

import logging
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import State, load_config
from core.manifest import load_manifest
from core.state import Card, Context, SummonProgress
from core.vision import Ocr, TemplateLibrary
from states.login import login
from states.recover import recover
from states.reset_account import reset_account
from states.story import LOBBY_STABLE_SECONDS, UNKNOWN_WAIT_SECONDS, story
from states.summon import (
    MAX_UNREADABLE_RETRIES,
    ASIDE_POINT,
    BACK_POINT,
    BOOK_POINT,
    BUY_CLOSE_POINT,
    CARD_HERO_STARS,
    CARD_SLOTS,
    COVENANT_TAB_POINT,
    DATE_CLOSE_POINT,
    NOTICE_CONFIRM_POINT,
    READS_PER_RESULT,
    RESULT_BACK_POINT,
    SUMMON_AGAIN_POINT,
    SUMMON_ONE_POINT,
    SUMMON_TEN_POINT,
    WISH_CLOSE_POINT,
    WISH_CONFIRM_POINT,
    WISH_LIST_POINT,
    summon,
)

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
LOGGER = logging.getLogger("test_summon")


class FakeInput:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.drags: list[tuple[int, int, int, int]] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.drags.append((x1, y1, x2, y2))

    def press(self, key: str) -> None:
        pass


class FakeSession:
    def __init__(self) -> None:
        self.input = FakeInput()

    def close(self) -> None:
        pass


@pytest.fixture(scope="module")
def library():
    config = load_config(ROOT / "config.yaml")
    loaded = TemplateLibrary(config.vision, LOGGER)
    loaded.load(load_manifest(ROOT / config.vision.manifest), ROOT / config.vision.templates_dir)
    return loaded


@pytest.fixture
def ctx(tmp_path, library) -> Context:
    config = load_config(ROOT / "config.yaml")
    config.logging.log_dir = tmp_path
    return Context(
        config=config,
        logger=LOGGER,
        library=library,
        ocr=Ocr(config.vision),
        open_session=lambda: None,
        session=FakeSession(),
        sleep=lambda seconds: None,
    )


def show(ctx: Context, name: str) -> None:
    ctx.frame = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert ctx.frame is not None, name


def clicks(ctx: Context) -> list[tuple[int, int]]:
    return ctx.session.input.clicks


def read_whole_result(ctx: Context) -> State:
    """Tick until the result has been read and acted on."""
    state = State.SUMMON
    for _ in range(READS_PER_RESULT):
        state = summon(ctx)
    return state


def test_lobby_waits_for_popups_before_doing_anything(ctx):
    show(ctx, "lobby_before_claim")
    assert summon(ctx) is State.SUMMON
    assert clicks(ctx) == []


def settled(ctx: Context) -> None:
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1


def test_lobby_goes_to_the_events_first(ctx):
    show(ctx, "lobby_before_claim")
    settled(ctx)
    assert summon(ctx) is State.SUMMON
    x, y = clicks(ctx)[0]
    assert 34 <= x <= 80 and 354 <= y <= 376


def test_lobby_goes_to_summon_once_claimed(ctx):
    ctx.summon.free_claimed = True
    show(ctx, "lobby_before_claim")
    settled(ctx)
    summon(ctx)
    x, y = clicks(ctx)[0]
    assert 1142 <= x <= 1238 and 442 <= y <= 470


def test_lobby_resets_once_everything_was_read(ctx):
    ctx.summon.finished = True
    show(ctx, "lobby_summons_done")
    settled(ctx)
    assert summon(ctx) is State.RESET_ACCOUNT


def test_event_list_scrolls_until_summon_support_shows(ctx):
    show(ctx, "event_first")
    summon(ctx)
    assert len(ctx.session.input.drags) == 1
    assert clicks(ctx) == []


def test_event_list_gives_up_when_summon_support_never_shows(ctx):
    show(ctx, "event_first")
    ctx.summon.list_drags = 6
    assert summon(ctx) is State.HALT
    assert "Summon Support" in ctx.halt_reason


def test_event_list_clicks_summon_support(ctx):
    show(ctx, "event_list")
    summon(ctx)
    x, y = clicks(ctx)[0]
    assert 24 <= x <= 202 and 436 <= y <= 464


def test_summon_support_claims_the_book(ctx):
    show(ctx, "summon_support")
    summon(ctx)
    assert clicks(ctx) == [BOOK_POINT]
    assert not ctx.summon.free_claimed


def test_claimed_book_leaves_the_event_menu(ctx):
    show(ctx, "summon_support_claimed")
    summon(ctx)
    assert clicks(ctx) == [BACK_POINT]
    assert ctx.summon.free_claimed


@pytest.mark.parametrize("name", ["summon_support_reward", "summon_checkin_reward"])
def test_reward_popups_are_closed(ctx, name):
    show(ctx, name)
    assert summon(ctx) is State.SUMMON
    assert len(clicks(ctx)) == 1
    assert ctx.summon == SummonProgress()


def test_first_banner_is_never_pulled_on(ctx):
    show(ctx, "summon_first_banner")
    summon(ctx)
    assert clicks(ctx) == [COVENANT_TAB_POINT]


def test_empty_wish_list_is_opened_before_any_pull(ctx):
    show(ctx, "covenant_all_free")
    summon(ctx)
    assert clicks(ctx) == [WISH_LIST_POINT]


def test_empty_wish_list_gets_the_recommended_one(ctx):
    show(ctx, "wish_empty")
    summon(ctx)
    x, y = clicks(ctx)[0]
    assert 262 <= x <= 446 and 610 <= y <= 634


def test_recommended_list_is_confirmed(ctx):
    show(ctx, "wish_question")
    summon(ctx)
    assert clicks(ctx) == [WISH_CONFIRM_POINT]


def test_filled_wish_list_is_closed(ctx):
    show(ctx, "wish_filled")
    summon(ctx)
    assert clicks(ctx) == [WISH_CLOSE_POINT]


def test_all_free_starts_with_the_single_summon(ctx):
    show(ctx, "covenant_all_free")
    # Same screen, once the list is filled.
    x, y, w, h = (742, 546, 62, 24)
    ctx.frame[y:y + h, x:x + w] = 0
    summon(ctx)
    assert clicks(ctx) == [SUMMON_ONE_POINT]


def test_after_the_single_summon_comes_the_x10(ctx):
    show(ctx, "covenant_twenty_free")
    summon(ctx)
    assert clicks(ctx) == [SUMMON_TEN_POINT]


def test_a_paid_summon_is_never_clicked(ctx):
    show(ctx, "covenant_no_free")
    ctx.summon.cards_read = 21
    assert summon(ctx) is State.SUMMON
    assert clicks(ctx) == [BACK_POINT]
    assert ctx.summon.finished


def test_no_free_left_without_all_results_read_refuses_to_reset(ctx):
    show(ctx, "covenant_no_free")
    ctx.summon.cards_read = 11
    assert summon(ctx) is State.HALT
    assert "11 of 21" in ctx.halt_reason
    assert not ctx.summon.finished


def decide(ctx, minimum=1, heroes=(), found=(), unsure=()):
    ctx.config.summon.min_moonlight_5star = minimum
    ctx.config.summon.stop_heroes = list(heroes)
    ctx.summon.moonlight_5star = list(found)
    ctx.summon.unsure = list(unsure)
    ctx.summon.cards_read = 21
    show(ctx, "covenant_no_free")
    return summon(ctx)


def test_one_moonlight_is_enough_by_default(ctx):
    assert decide(ctx, found=["Briar Witch Iseria"]) is State.HALT
    assert "Briar Witch Iseria ML 5*" in ctx.halt_reason


def test_nothing_found_goes_on_to_the_reset(ctx):
    assert decide(ctx, minimum=2, heroes=["Zio"]) is State.SUMMON
    assert ctx.summon.finished


def test_the_number_alone_is_enough_even_out_of_the_list(ctx):
    assert decide(ctx, minimum=2, heroes=["Zio"], found=["Briar Witch Iseria", "Maid Chloe"]) is State.HALT


def test_the_list_alone_is_enough_below_the_number_despite_ocr_noise(ctx):
    assert decide(ctx, minimum=2, heroes=["Briar Witch Iseria"], found=["Briar Witch Iseria \u2018KE"]) is State.HALT
    assert "Briar Witch Iseria ML 5*" in ctx.halt_reason


def test_neither_rule_met_is_reset(ctx):
    assert decide(ctx, minimum=2, heroes=["Zio"], found=["Briar Witch Iseria"]) is State.SUMMON
    assert ctx.summon.finished


def test_below_the_number_with_no_list_is_reset(ctx):
    assert decide(ctx, minimum=2, found=["unreadable #7"]) is State.SUMMON


def test_a_moonlight_name_that_cannot_be_read_keeps_the_account(ctx):
    assert decide(ctx, minimum=2, heroes=["Zio"], found=["xq#7 ..."]) is State.HALT
    assert "could not be matched" in ctx.halt_reason


def test_a_5_star_of_unknown_element_keeps_the_account(ctx):
    assert decide(ctx, minimum=2, heroes=["Zio"], unsure=["Who 5*"]) is State.HALT


def test_a_normal_5_star_hero_does_not_stop_the_bot(ctx):
    ctx.summon.moonlight_5star = []
    show(ctx, "results_free_left")
    paste = ctx.frame
    star_x, star_y, width, height = 175, 459, 19, 18
    paste[star_y:star_y + height, star_x + 20:star_x + 20 + width] = paste[star_y:star_y + height, star_x:star_x + width]
    read_whole_result(ctx)
    assert ctx.summon.moonlight_5star == []
    assert ctx.summon.unsure == []


def test_summon_stuck_never_resets_the_account(ctx):
    ctx.interrupted = State.SUMMON
    ctx.recover_counts[State.SUMMON] = ctx.config.watchdog.recover_max_retries
    assert recover(ctx) is State.HALT


def test_a_stuck_story_never_resets_an_account_being_pushed(ctx):
    ctx.story_only = True
    ctx.interrupted = State.STORY
    ctx.recover_counts[State.STORY] = ctx.config.watchdog.recover_max_retries
    assert recover(ctx) is State.HALT


def test_single_result_is_read_then_goes_back(ctx):
    show(ctx, "single_result")
    state = read_whole_result(ctx)
    assert state is State.SUMMON
    assert clicks(ctx) == [RESULT_BACK_POINT]
    assert ctx.summon.cards_read == 1
    assert ctx.summon.unsure == []


def test_hero_silenced_by_the_first_click_is_not_counted_twice(ctx):
    show(ctx, "single_speaking")
    read_whole_result(ctx)
    assert ctx.summon.cards_read == 1
    show(ctx, "single_result")
    ctx.last_click = None
    summon(ctx)
    assert ctx.summon.cards_read == 1
    assert clicks(ctx) == [RESULT_BACK_POINT, RESULT_BACK_POINT]


def test_results_are_not_counted_again_when_the_click_did_nothing(ctx):
    show(ctx, "results_free_left")
    read_whole_result(ctx)
    read_whole_result(ctx)
    assert ctx.summon.cards_read == 10


def test_reveal_uses_skip(ctx):
    show(ctx, "reveal_speaking")
    summon(ctx)
    x, y = clicks(ctx)[0]
    assert 1192 <= x <= 1254 and 22 <= y <= 50


def test_rare_reveal_uses_next(ctx):
    show(ctx, "reveal_rare")
    summon(ctx)
    x, y = clicks(ctx)[0]
    assert 1072 <= x <= 1172 and 648 <= y <= 676


def test_results_are_read_then_pulled_again_while_free(ctx):
    show(ctx, "results_free_left")
    read_whole_result(ctx)
    assert clicks(ctx) == [SUMMON_AGAIN_POINT]
    assert ctx.summon.cards_read == 10
    assert ctx.summon.moonlight_5star == []


def test_results_without_free_go_back(ctx):
    show(ctx, "results_no_free")
    read_whole_result(ctx)
    assert clicks(ctx) == [RESULT_BACK_POINT]
    assert ctx.summon.cards_read == 10


def test_results_keep_the_most_stars_seen_past_the_sparkle(ctx):
    """On this frame a sparkle hides stars of the 5-star artifact, which later frames show."""
    show(ctx, "results_sparkle")
    from states.summon import _results
    assert _results(ctx) is State.SUMMON
    sparkled = ctx.summon.current[5].artifact_stars
    assert sparkled < 5
    show(ctx, "results_free_left")
    assert _results(ctx) is State.SUMMON
    assert ctx.summon.current[5].artifact_stars == 5


def test_a_real_5_star_moonlight_is_counted(ctx):
    """A real pull: Briar Witch Iseria, a 5-star Moonlight hero, on the last card."""
    show(ctx, "results_five_star_hero")
    read_whole_result(ctx)
    assert len(ctx.summon.moonlight_5star) == 1
    assert "Briar Witch Iseria" in ctx.summon.moonlight_5star[0]


def test_a_5_star_artifact_counts_for_nothing(ctx):
    show(ctx, "results_free_left")
    read_whole_result(ctx)
    assert ctx.summon.moonlight_5star == []


def test_unreadable_card_stops_the_bot(ctx):
    show(ctx, "results_free_left")
    left, top = CARD_SLOTS[2]
    x, y, w, h = CARD_HERO_STARS
    ctx.frame[top + y:top + y + h, left + x:left + x + w] = 0
    for _ in range(MAX_UNREADABLE_RETRIES):
        assert read_whole_result(ctx) is State.SUMMON
    assert read_whole_result(ctx) is State.HALT
    assert "could not read" in ctx.halt_reason
    assert ctx.summon.cards_read == 0


def test_a_card_hidden_for_a_moment_is_read_again(ctx):
    show(ctx, "results_free_left")
    clean = ctx.frame.copy()
    left, top = CARD_SLOTS[2]
    x, y, w, h = CARD_HERO_STARS
    ctx.frame[top + y:top + y + h, left + x:left + x + w] = 0
    assert read_whole_result(ctx) is State.SUMMON
    ctx.frame = clean
    read_whole_result(ctx)
    assert ctx.summon.cards_read == 10


def test_card_needs_exactly_one_kind_of_stars():
    assert Card(hero_stars=3).readable()
    assert Card(artifact_stars=5).readable()
    assert not Card().readable()
    assert not Card(hero_stars=3, artifact_stars=3).readable()
    assert not Card(hero_stars=2).readable()


@pytest.mark.parametrize("name", ["covenant_no_free", "summon_first_banner", "event_first"])
def test_reset_leaves_the_menus_by_their_back_arrow(ctx, name):
    show(ctx, name)
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert clicks(ctx) == [BACK_POINT]


@pytest.mark.parametrize("name", ["results_notice", "results_notice_full"])
def test_notice_bubble_is_clicked_instead_of_reading_under_it(ctx, name):
    show(ctx, name)
    assert summon(ctx) is State.SUMMON
    x, y = clicks(ctx)[0]
    assert 437 <= x <= 847 and 100 <= y <= 235
    assert ctx.summon.reads == 0


def test_notice_question_is_confirmed(ctx):
    show(ctx, "notice_question")
    assert summon(ctx) is State.SUMMON
    assert clicks(ctx) == [NOTICE_CONFIRM_POINT]


def test_results_are_read_once_the_notice_bubble_is_gone(ctx):
    show(ctx, "results_notice")
    summon(ctx)
    show(ctx, "results_free_left")
    read_whole_result(ctx)
    assert ctx.summon.cards_read == 10


def test_artifact_details_are_closed_and_not_read_as_results(ctx):
    show(ctx, "results_notice_slim_details")
    # The bubble goes first, then the details under it.
    summon(ctx)
    ctx.frame[108:178, 358:428] = 0
    ctx.session.input.clicks.clear()
    summon(ctx)
    assert clicks(ctx) == [ASIDE_POINT]
    assert ctx.summon.reads == 0


@pytest.mark.parametrize("name", ["results_free_left", "covenant_twenty_free", "event_first"])
def test_story_hands_summon_screens_over(ctx, name):
    ctx.going_home = True
    show(ctx, name)
    assert story(ctx) is State.SUMMON
    assert clicks(ctx) == []
    assert not ctx.going_home


def test_story_does_not_hand_over_when_a_reset_is_asked(ctx):
    ctx.reset_pending = True
    show(ctx, "results_free_left")
    assert story(ctx) is State.STORY


def test_purchase_window_is_closed_and_stops_the_bot(ctx):
    show(ctx, "buy_bookmarks")
    assert summon(ctx) is State.HALT
    assert "buy bookmarks" in ctx.halt_reason
    assert clicks(ctx) == [BUY_CLOSE_POINT]


def test_date_change_stops_the_bot(ctx):
    show(ctx, "date_changed")
    assert summon(ctx) is State.HALT
    assert "day changed" in ctx.halt_reason
    assert clicks(ctx) == [DATE_CLOSE_POINT]


@pytest.mark.parametrize("name", ["checkin_daily", "checkin_new_player"])
def test_reset_closes_daily_popups_with_esc(ctx, name):
    keys = []
    ctx.session.input.press = keys.append
    show(ctx, name)
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert keys == ["esc"]
    assert clicks(ctx) == []


def test_reset_still_opens_the_menu_on_the_lobby(ctx):
    show(ctx, "lobby_before_claim")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = clicks(ctx)[0]
    assert x > 1200 and y < 70


@pytest.mark.parametrize("name", ["checkin_daily", "checkin_new_player"])
def test_summon_closes_daily_popups_with_esc(ctx, name):
    keys = []
    ctx.session.input.press = keys.append
    show(ctx, name)
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    assert summon(ctx) is State.SUMMON
    assert keys == ["esc"]
    assert clicks(ctx) == []


def unknown_screen() -> np.ndarray:
    return np.random.default_rng(1).integers(0, 255, (720, 1280, 3), dtype=np.uint8)


def test_unknown_screen_gets_esc_and_never_a_click(ctx):
    keys = []
    ctx.session.input.press = keys.append
    ctx.frame = unknown_screen()
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    assert summon(ctx) is State.SUMMON
    assert keys == ["esc"]
    assert clicks(ctx) == []


def test_nothing_is_sent_during_a_pull(ctx):
    keys = []
    ctx.session.input.press = keys.append
    ctx.summon.pulling = True
    ctx.frame = unknown_screen()
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    assert summon(ctx) is State.SUMMON
    assert keys == []
    assert clicks(ctx) == []


def test_a_result_ends_the_pull(ctx):
    ctx.summon.pulling = True
    show(ctx, "single_result")
    summon(ctx)
    assert not ctx.summon.pulling


@pytest.mark.parametrize("handler", [summon, story])
def test_connection_error_is_retried(ctx, handler):
    show(ctx, "connection_error")
    handler(ctx)
    assert clicks(ctx) == [(640, 445)]


def test_lobby_with_its_entry_covered_does_not_stop_the_bot(ctx):
    show(ctx, "lobby_before_claim")
    settled(ctx)
    x, y, w, h = (10, 335, 100, 60)
    ctx.frame[y:y + h, x:x + w] = 0
    assert summon(ctx) is State.SUMMON
    assert clicks(ctx) == []


def test_connection_error_leaves_the_server_time_between_retries(ctx):
    waits = []
    ctx.sleep = waits.append
    show(ctx, "connection_error")
    summon(ctx)
    assert sum(waits) >= 15


def test_game_patch_stops_the_story_without_a_click(ctx):
    show(ctx, "patch_update")
    assert story(ctx) is State.HALT
    assert "patch" in ctx.halt_reason
    assert clicks(ctx) == []


def test_game_patch_stops_the_login_without_a_click(ctx):
    show(ctx, "patch_update")
    assert login(ctx) is State.HALT
    assert "patch" in ctx.halt_reason
    assert clicks(ctx) == []


@pytest.mark.parametrize(
    ("name", "moonlight_slots"),
    [
        ("results_moonlight_icons", {0, 7}),
        ("results_five_star_hero", {2, 9}),
        ("results_free_left", {9}),
        ("results_no_free", set()),
    ],
)
def test_moonlight_cards_on_real_screens(ctx, name, moonlight_slots):
    seen = []
    ctx.on_cards = lambda cards: seen.extend(cards)
    show(ctx, name)
    read_whole_result(ctx)
    assert {index for index, card in enumerate(seen) if card.moonlight} == moonlight_slots


def test_a_finished_account_reports_its_time_once(ctx):
    runs = []
    ctx.on_run_done = runs.append
    ctx.account_started_at = time.monotonic() - 100
    ctx.summon.cards_read = 21
    show(ctx, "covenant_no_free")
    summon(ctx)
    assert len(runs) == 1 and runs[0] >= 100
    assert ctx.account_started_at is None


def test_a_resumed_account_does_not_count_in_the_average(ctx):
    runs = []
    ctx.on_run_done = runs.append
    ctx.summon.cards_read = 21
    show(ctx, "covenant_no_free")
    summon(ctx)
    assert runs == []


@pytest.mark.parametrize("handler", [summon, story])
def test_a_new_hero_announcement_is_closed_with_confirm_and_not_go_to_summon(ctx, handler):
    show(ctx, "hero_announcement")
    handler(ctx)
    assert clicks(ctx) == [(1142, 662)]


def test_the_event_label_is_read_over_a_bright_lobby_artwork(ctx):
    show(ctx, "lobby_bright_art")
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert summon(ctx) is State.SUMMON
    assert ctx.session.input.clicks == [(57, 355)]


def test_a_result_of_nine_cards_is_read_without_its_empty_place(ctx):
    show(ctx, "results_nine_cards")
    for _ in range(MAX_UNREADABLE_RETRIES + 1):
        state = read_whole_result(ctx)
    assert state is not State.HALT
    assert ctx.summon.cards_read == 9


def test_a_hidden_card_in_the_middle_still_stops_the_bot():
    from core.state import Card
    from states.summon import _only_empty_slots_at_the_end
    cards = [Card(hero_stars=3)] * 4 + [Card()] + [Card(artifact_stars=3)] * 5
    assert not _only_empty_slots_at_the_end(cards)
    assert _only_empty_slots_at_the_end([Card(hero_stars=3)] * 9 + [Card()])


def test_an_account_with_enough_heroes_in_the_top_is_kept(ctx):
    from core.config import TopRule
    from states.summon import _decide
    ctx.config = ctx.config.model_copy(update={"summon": ctx.config.summon.model_copy(update={
        "min_moonlight_5star": 3, "top_rules": [TopRule(count=1, rank=5), TopRule(count=2, rank=20)]})})
    ctx.summon.moonlight_5star = ["Belian"]
    assert _decide(ctx) is None
    ctx.summon.moonlight_5star = ["Belian", "Zio"]
    assert _decide(ctx) == "account kept: Belian ML 5*, Zio ML 5*"
    ctx.summon.moonlight_5star = ["Boss Arunka"]
    assert _decide(ctx) == "account kept: Boss Arunka ML 5*"


def test_a_hero_missing_from_the_ranking_counts_as_the_top(ctx):
    from core.config import TopRule
    from states.summon import _decide
    ctx.config = ctx.config.model_copy(update={"summon": ctx.config.summon.model_copy(update={
        "min_moonlight_5star": 3, "top_rules": [TopRule(count=1, rank=1)]})})
    ctx.summon.moonlight_5star = ["Renoa"]
    assert _decide(ctx) == "account kept: Renoa ML 5*"

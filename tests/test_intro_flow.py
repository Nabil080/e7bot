"""The way from Select Server to Episode, on real screenshots, plus settings and the window's logic."""

import logging
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import State, load_config
from core.manifest import load_manifest
from core.panel import GAP, PANEL_WIDTH, format_elapsed, format_stage, panel_position
from core.settings import Settings, apply_settings, load_settings, save_settings
from core.stats import load_total_rerolls, save_total_rerolls
from core.state import Context
from core.status import BotStatus, LogFeed
from core.vision import Ocr, TemplateLibrary
from core.window import WindowLostError
from states.login import find_server_row, login
from states.story import _auto_is_off, TRY_AGAIN_POINT, _cleared_stage, EPISODE_TREE_POINT, MAX_EPISODE_TRIPS, MAX_CLEARED_STEPS, NEXT_NODE_TRIES, selected_tick_y, AUTO_STILL_SECONDS, SAME_CLICK_SECONDS, SKILL_ANIMATION_SECONDS, click_once, LOBBY_STABLE_SECONDS, START_QUEST_TRIES, START_QUEST_WAIT_SECONDS, UNKNOWN_WAIT_SECONDS, read_stage, story

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
LOGGER = logging.getLogger("test_intro_flow")


def fixture(name: str) -> np.ndarray:
    return cv2.imread(str(FIXTURES / f"{name}.jpg"))


class FakeInput:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.keys: list[str] = []
        self.typed: list[str] = []

    def type_text(self, text: str, secret: bool = False) -> None:
        self.typed.append(text)

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def double_click(self, x: float, y: float) -> None:
        self.clicks.append(("double", x, y))

    def tap(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.clicks.append(("drag", x1, y1, x2, y2))

    def press(self, key: str) -> None:
        self.keys.append(f"press {key}")

    def key_down(self, key: str) -> None:
        self.keys.append(f"down {key}")

    def key_up(self, key: str) -> None:
        self.keys.append(f"up {key}")


class FakeCapture:
    """Plays a list of frames, then keeps showing the last one."""

    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.error: Exception | None = None

    def grab(self, max_age_seconds: float = 0.0) -> np.ndarray:
        if self.error:
            raise self.error
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


class FakeSession:
    def __init__(self) -> None:
        self.input = FakeInput()
        self.capture = FakeCapture()

    def close(self) -> None:
        return None


@pytest.fixture(scope="module")
def library():
    config = load_config(ROOT / "config.yaml")
    loaded = TemplateLibrary(config.vision, LOGGER)
    loaded.load(load_manifest(ROOT / config.vision.manifest), ROOT / config.vision.templates_dir)
    return loaded


@pytest.fixture
def ctx(library) -> Context:
    config = load_config(ROOT / "config.yaml")
    session = FakeSession()
    return Context(
        config=config,
        logger=LOGGER,
        library=library,
        ocr=Ocr(config.vision),
        open_session=lambda: session,
        session=session,
        sleep=lambda seconds: None,
    )


def clicked(ctx: Context) -> tuple[int, int]:
    return ctx.session.input.clicks[0]


@pytest.mark.parametrize(
    ("server", "row_y"), [("Europe", 180), ("Foundry", 267), ("Global", 354), ("Asia", 442), ("Korea", 530), ("Japan", 618)]
)
def test_every_server_row_is_found_by_name(ctx, server, row_y):
    ctx.frame = fixture("select_server")
    assert find_server_row(ctx, server) == row_y


def test_select_server_clicks_the_row_of_the_reroll_server(ctx):
    ctx.frame = fixture("select_server")
    ctx.reset_pending = True
    assert login(ctx) is State.LOGIN
    assert clicked(ctx) == (886, 267)
    assert ctx.reset_pending is False


def test_select_server_click_is_not_repeated_at_once_but_is_retried(ctx):
    ctx.frame = fixture("select_server")
    login(ctx)
    login(ctx)
    assert len(ctx.session.input.clicks) == 1
    ctx.last_server_click = time.monotonic() - 60
    login(ctx)
    assert len(ctx.session.input.clicks) == 2


def test_unknown_server_clicks_nothing(ctx):
    ctx.config = ctx.config.model_copy(update={"reroll": ctx.config.reroll.model_copy(update={"server": "Mars"})})
    ctx.frame = fixture("select_server")
    assert login(ctx) is State.LOGIN
    assert ctx.session.input.clicks == []


def test_title_screen_still_loading_is_not_tapped(ctx):
    ctx.frame = fixture("title_loading")
    assert login(ctx) is State.LOGIN
    assert ctx.session.input.clicks == []


def test_title_screen_ready_is_tapped_but_the_intro_waits_for_it_to_go(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "title.png"))
    assert login(ctx) is State.LOGIN
    assert clicked(ctx) == (640, 400)
    assert login(ctx) is State.LOGIN, "the title screen is still there"
    assert len(ctx.session.input.clicks) == 1, "not tapped again at once"


def test_lost_tap_is_retried(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "title.png"))
    login(ctx)
    ctx.last_title_tap = time.monotonic() - 60
    login(ctx)
    assert len(ctx.session.input.clicks) == 2


def test_intro_starts_once_the_title_screen_has_gone(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "title.png"))
    login(ctx)
    ctx.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert login(ctx) is State.STORY


def test_black_screen_without_a_tap_keeps_waiting(ctx):
    ctx.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert login(ctx) is State.LOGIN


def test_title_screen_with_a_reset_pending_stays_in_login(ctx):
    ctx.reset_pending = True
    ctx.frame = cv2.imread(str(FIXTURES / "title.png"))
    assert login(ctx) is State.LOGIN


def test_lobby_with_a_reset_pending_goes_to_the_reset(ctx):
    ctx.reset_pending = True
    ctx.frame = fixture("lobby_clean")
    assert login(ctx) is State.RESET_ACCOUNT


@pytest.mark.parametrize("name", ["story_skip", "dialogue_skip"])
def test_dialogue_clicks_skip(ctx, name):
    ctx.frame = fixture(name)
    assert story(ctx) is State.STORY
    x, y = clicked(ctx)
    assert x > 1180 and y < 60
    assert ctx.session.input.keys == []


def test_skip_popup_clicks_confirm_and_not_cancel(ctx):
    ctx.frame = fixture("skip_confirm")
    story(ctx)
    x, y = clicked(ctx)
    assert abs(x - 748) < 30 and abs(y - 467) < 20


def test_letter_with_a_stamp_claims_it(ctx):
    ctx.frame = fixture("letter_stamp")
    story(ctx)
    x, y = clicked(ctx)
    assert abs(x - 640) < 20 and abs(y - 468) < 20


def test_letter_already_claimed_is_closed(ctx):
    ctx.frame = fixture("letter_done")
    story(ctx)
    assert clicked(ctx) == (640, 693)


def test_reward_is_closed(ctx):
    ctx.frame = fixture("reward")
    story(ctx)
    assert clicked(ctx) == (640, 530)


def test_tutorial_is_skipped_rather_than_stopping_at_its_episode_button(ctx):
    ctx.frame = fixture("skip_tutorial")
    assert story(ctx) is State.STORY
    x, y = clicked(ctx)
    assert x > 1090 and y < 60


def test_unknown_screen_waits_then_skips_the_video_without_any_tap(ctx):
    video = fixture("cinematic")
    ctx.frame = video
    icon, plain = fixture("video_icon_scene"), fixture("cinematic")
    ctx.session.capture.frames = [icon, icon, plain, plain, plain]
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [] and ctx.session.input.keys == [], "first it only waits"
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.clicks == []
    assert ctx.session.input.keys == ["press esc", "down esc", "up esc"]
    assert not ctx.unknown_tapped


def test_unknown_screen_without_a_video_gets_a_tap_next(ctx):
    ctx.frame = fixture("cinematic")
    ctx.session.capture.frames = [fixture("cinematic")]
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.clicks == []
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.clicks == [(640, 400)]


def test_no_icon_means_the_skip_failed_and_it_is_reported(ctx, caplog):
    video = fixture("cinematic")
    ctx.frame = video
    ctx.session.capture.frames = [video]
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    with caplog.at_level(logging.INFO):
        assert story(ctx) is State.STORY
    assert "no video to skip" in caplog.text
    assert ctx.session.input.keys == ["press esc", "down esc", "up esc"]


def test_icon_on_every_background_is_recognized(library):
    for name in ("video_icon_scene", "video_icon_black"):
        assert library.find(fixture(name), "skip_icon", (580, 300, 120, 120)) is not None
    assert library.find(fixture("cinematic"), "skip_icon", (580, 300, 120, 120)) is None


def test_esc_is_released_when_the_game_closes_during_the_hold(ctx):
    ctx.frame = fixture("cinematic")
    ctx.session.capture.error = WindowLostError("gone")
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    with pytest.raises(WindowLostError):
        story(ctx)
    assert ctx.session.input.keys[-1] == "up esc"


def test_login_does_not_hop_to_the_intro_while_the_server_loads(ctx):
    ctx.frame = fixture("cinematic")
    ctx.last_server_click = time.monotonic()
    assert login(ctx) is State.LOGIN


def test_black_screen_is_left_alone(ctx):
    ctx.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [] and ctx.session.input.keys == []


def test_lobby_must_stay_visible_before_episode_is_clicked(ctx):
    ctx.frame = fixture("lobby_clean")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [] and ctx.session.input.keys == []
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.STORY
    x, y = clicked(ctx)
    assert 1128 <= x <= 1236 and 168 <= y <= 204


def test_unlocked_summon_ends_the_story_at_the_lobby(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "lobby_unlocked.png"))
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.SUMMON
    assert ctx.session.input.clicks == []


def test_locked_summon_goes_on_with_the_story(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "lobby_locked.png"))
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.STORY
    assert len(ctx.session.input.clicks) == 1


def test_reset_still_comes_first_with_summon_unlocked(ctx):
    ctx.reset_pending = True
    ctx.frame = cv2.imread(str(FIXTURES / "lobby_unlocked.png"))
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.RESET_ACCOUNT


def test_lobby_timer_restarts_when_another_screen_shows_up(ctx):
    ctx.frame = fixture("lobby_clean")
    story(ctx)
    ctx.frame = fixture("letter_stamp")
    story(ctx)
    assert ctx.lobby_since is None


@pytest.mark.parametrize(
    ("name", "x_range", "y_range"),
    [
        ("story_list_view", (1060, 1172), (646, 678)),
        ("story_list_ready", (1070, 1160), (646, 678)),
        ("story_team", (1096, 1200), (646, 678)),
        ("fight_tutorial", (1092, 1232), (18, 50)),
        ("stage_clear", (536, 786), (420, 460)),
        ("stage_results", (1110, 1238), (644, 676)),
        ("story_clear", (584, 696), (470, 494)),
        ("star_reward", (505, 775), (150, 560)),
        ("hero_joined", (622, 658), (500, 528)),
        ("skip_popup_act", (700, 800), (450, 490)),
        ("story_act", (600, 680), (380, 420)),
    ],
)
def test_story_screen_is_passed_with_a_click_on_the_right_spot(ctx, name, x_range, y_range):
    ctx.frame = fixture(name)
    assert story(ctx) is State.STORY
    assert len(ctx.session.input.clicks) == 1
    x, y = clicked(ctx)
    assert x_range[0] <= x <= x_range[1] and y_range[0] <= y <= y_range[1]


def fixture_any(name: str) -> np.ndarray:
    path = FIXTURES / f"{name}.jpg"
    return cv2.imread(str(path if path.exists() else FIXTURES / f"{name}.png"))


@pytest.mark.parametrize(
    ("name", "x"),
    [
        ("fight_our_turn", 1224),
        ("fight_two_skills", 1125),
        ("fight_cannot_use", 1026),
        ("new_skill_hint", 1224),
    ],
)
def test_our_turn_double_clicks_the_rightmost_usable_skill(ctx, name, x):
    ctx.frame = fixture_any(name)
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [], "the skills may still be sliding in"
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [("double", x, 660)]


def test_skill_that_did_nothing_is_followed_by_the_next_one_to_its_left(ctx):
    ctx.frame = fixture("fight_our_turn")
    for _ in range(5):
        story(ctx)
    assert [click[1] for click in ctx.session.input.clicks] == [1224, 1125, 1026, 1224]


def test_tries_start_over_on_the_next_turn(ctx):
    ctx.frame = fixture("fight_our_turn")
    story(ctx)
    story(ctx)
    ctx.frame = fixture("fight_waiting")
    story(ctx)
    assert ctx.skills_tried == 0


def test_going_home_opens_the_pause_menu_from_a_fight(ctx):
    ctx.going_home = True
    ctx.frame = fixture("fight_waiting")
    assert story(ctx) is State.STORY
    assert ctx.session.input.keys == ["press esc"]


def test_esc_without_effect_gives_up_and_plays_the_fight(ctx, caplog):
    ctx.going_home = True
    ctx.frame = fixture("fight_our_turn")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            story(ctx)
    assert ctx.session.input.keys == ["press esc"] * 5
    assert "playing the stage to its end" in caplog.text
    story(ctx)
    story(ctx)
    assert ctx.session.input.clicks == [("double", 1224, 660)]
    assert ctx.session.input.keys == ["press esc"] * 5


def test_going_home_yields_and_ends_the_fight(ctx):
    ctx.going_home = True
    ctx.frame = fixture_any("pause_menu")
    story(ctx)
    assert clicked(ctx) == (640, 275)
    ctx.frame = fixture_any("yield_popup")
    story(ctx)
    assert ctx.session.input.clicks[-1] == (748, 460)


def test_without_a_reason_to_leave_the_fight_goes_on(ctx):
    ctx.frame = fixture_any("pause_menu")
    story(ctx)
    assert clicked(ctx) == (640, 502)
    ctx.frame = fixture_any("yield_popup")
    story(ctx)
    assert ctx.session.input.clicks[-1] == (532, 460)


def test_enemy_turn_and_travel_do_nothing(ctx):
    for name in ("fight_waiting", "story_travel"):
        ctx.frame = fixture(name)
        assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [] and ctx.session.input.keys == []


def test_new_screen_counts_as_progress_but_the_same_one_does_not(ctx):
    ctx.frame = fixture("fight_waiting")
    story(ctx)
    assert ctx.progressed is True
    ctx.progressed = False
    story(ctx)
    assert ctx.progressed is False


def test_travelling_is_told_from_a_fight_by_the_minimap_bar(ctx):
    ctx.frame = fixture("story_travel")
    story(ctx)
    assert ctx.screen == "travelling"
    ctx.frame = fixture("fight_waiting")
    story(ctx)
    assert ctx.screen == "fight: waiting"


def test_tag_hint_is_answered_with_a_click_on_the_tag(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "tag_hint.png"))
    assert story(ctx) is State.STORY
    assert clicked(ctx) == (70, 640)


def test_rank_up_is_passed_with_a_tap(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "rank_up.png"))
    assert story(ctx) is State.STORY
    assert clicked(ctx) == (640, 400)


def test_dark_corner_is_not_taken_for_the_pause_icon(library):
    assert library.find(cv2.imread(str(FIXTURES / "rank_up.png")), "pause_hud", (1228, 14, 44, 50)) is None


def test_quit_popup_is_cancelled(ctx):
    frame = fixture("lobby_clean")
    template = ctx.library.templates["quit_popup"]
    frame[310:338, 462:818] = template
    ctx.frame = frame
    assert story(ctx) is State.STORY
    assert clicked(ctx) == (532, 460)


@pytest.mark.parametrize(
    ("name", "stage"),
    [("story_list_view", "6th World Episode 1-1"), ("story_list_ready", "6th World Episode 1-2"), ("stage_to_dun", "6th World Episode 2-2")],
)
def test_stage_numbers_are_read(ctx, name, stage):
    ctx.frame = fixture(name)
    assert read_stage(ctx) == stage


def test_stage_is_reported_when_it_changes(ctx):
    reported = []
    ctx.on_stage = reported.append
    ctx.config.reroll.stop_at_stage = None
    ctx.frame = fixture("story_list_ready")
    assert story(ctx) is State.STORY
    assert reported == ["6th World Episode 1-2"]


def test_stops_on_the_requested_stage_before_clicking(ctx):
    ctx.config.reroll.stop_at_stage = "6th World Episode 2-2"
    ctx.frame = fixture("stage_to_dun")
    assert story(ctx) is State.HALT
    assert "6th World Episode 2-2" in ctx.halt_reason
    assert ctx.session.input.clicks == []


def test_other_stages_do_not_stop_the_bot(ctx):
    ctx.config.reroll.stop_at_stage = "6th World Episode 2-2"
    ctx.frame = fixture("story_list_ready")
    assert story(ctx) is State.STORY
    assert len(ctx.session.input.clicks) == 1


def test_unreadable_stage_is_not_started(ctx, monkeypatch):
    monkeypatch.setattr(ctx.ocr, "read", lambda *args, **kwargs: "")
    ctx.frame = fixture("story_list_ready")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == []


def start_quest_clicked(ctx):
    x, y = ctx.session.input.clicks[-1]
    return 1076 <= x <= 1220 and 634 <= y <= 690


def test_team_starts_first_and_adds_a_hero_only_if_that_did_not_work(ctx):
    ctx.stage = "6th World Episode 2-2"
    ctx.frame = fixture("team_empty_slots")
    story(ctx)
    assert start_quest_clicked(ctx), "first it just tries"
    story(ctx)
    assert len(ctx.session.input.clicks) == 1 and not ctx.adding_hero, "then it leaves the game time to react"
    ctx.start_clicked_at = time.monotonic() - START_QUEST_WAIT_SECONDS - 1
    story(ctx)
    assert not ctx.adding_hero, "a click while the heroes flash in is lost: one more try first"
    story(ctx)
    assert len(ctx.session.input.clicks) == 2 and start_quest_clicked(ctx)
    ctx.start_clicked_at = time.monotonic() - START_QUEST_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.adding_hero
    story(ctx)
    assert ctx.session.input.clicks[-1] == (587, 425), "an empty slot opens the hero list"
    ctx.frame = fixture("team_hero_list")
    story(ctx)
    x, y = ctx.session.input.clicks[-1]
    assert 946 <= x <= 982 and 258 <= y <= 294, "then the first hero"
    ctx.frame = fixture("team_slot_plus")
    story(ctx)
    x, y = ctx.session.input.clicks[-1]
    assert 300 <= x <= 720 and 250 <= y <= 530, "then the plus on a slot"
    assert ctx.hero_added_on is None
    ctx.frame = fixture("team_hero_placed")
    story(ctx)
    assert ctx.hero_added_on == "6th World Episode 2-2"
    assert start_quest_clicked(ctx), "the plus has gone: try again"


def test_plus_still_there_is_clicked_again(ctx):
    ctx.adding_hero = True
    ctx.frame = fixture("team_slot_plus")
    story(ctx)
    ctx.last_click = None
    story(ctx)
    assert len(ctx.session.input.clicks) == 2
    assert ctx.hero_added_on is None


def test_hero_is_added_even_when_the_bot_starts_on_the_team_screen(ctx):
    assert ctx.stage == ""
    ctx.frame = fixture("team_empty_slots")
    ctx.start_quest_clicks = START_QUEST_TRIES - 1
    ctx.start_clicked_at = time.monotonic() - START_QUEST_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.adding_hero


def test_no_second_hero_on_the_same_stage(ctx):
    ctx.stage = ctx.hero_added_on = "6th World Episode 2-2"
    ctx.frame = fixture("team_hero_placed")
    ctx.start_clicked_at = time.monotonic() - START_QUEST_WAIT_SECONDS - 1
    story(ctx)
    assert not ctx.adding_hero
    story(ctx)
    assert start_quest_clicked(ctx)


def test_leaving_the_team_screen_forgets_the_click(ctx):
    ctx.frame = fixture("story_team")
    story(ctx)
    assert ctx.start_clicked_at is not None
    ctx.frame = fixture("fight_waiting")
    story(ctx)
    assert ctx.start_clicked_at is None


def test_continue_goes_back_to_the_lobby_from_the_stage_list_and_the_team(ctx):
    ctx.going_home = True
    for name in ("story_list_ready", "story_team"):
        ctx.frame = fixture(name)
        assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [] and ctx.session.input.keys == ["press esc", "press esc"]


def test_continue_presses_esc_on_an_unknown_menu_then_tries_the_usual_ways(ctx):
    ctx.going_home = True
    ctx.frame = fixture("cinematic")
    for _ in range(3):
        ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
        story(ctx)
    assert ctx.session.input.keys == ["press esc"] * 3
    ctx.session.capture.frames = [fixture("cinematic")]
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.keys[3:] == ["press esc", "down esc", "up esc"]
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.clicks == [(640, 400)]


def test_continue_carries_on_from_the_lobby_without_a_reset(ctx):
    ctx.going_home = True
    ctx.frame = fixture("lobby_clean")
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.STORY
    assert ctx.going_home is False
    x, y = clicked(ctx)
    assert 1128 <= x <= 1236 and 168 <= y <= 204


def test_reset_pending_leaves_the_stage_list_with_esc(ctx):
    ctx.reset_pending = True
    ctx.frame = fixture("story_list_ready")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [] and ctx.session.input.keys == ["press esc"]


def test_stage_is_shown_in_words():
    assert format_stage("6th World Episode 2-2") == "6th World Episode · Chapter 2 · stage 2-2"
    assert format_stage("") == ""


def test_settings_apply_on_top_of_the_config():
    config = load_config(ROOT / "config.yaml")
    applied = apply_settings(
        config,
        Settings(server="Global", pre_action_delay_min_ms=900, pre_action_delay_max_ms=100, skip_hold_seconds=3),
    )
    assert applied.reroll.server == "Global"
    assert applied.reroll.skip_hold_seconds == 3
    assert (applied.input.pre_action_delay_ms.min, applied.input.pre_action_delay_ms.max) == (100, 900)
    assert config.reroll.server == "Foundry", "the original config is untouched"


def test_settings_round_trip_and_broken_file_falls_back(tmp_path):
    config = load_config(ROOT / "config.yaml")
    path = tmp_path / "settings.json"
    assert load_settings(path, config).server == config.reroll.server
    save_settings(Settings(server="Asia"), path)
    assert load_settings(path, config).server == "Asia"
    path.write_text("{broken", encoding="utf-8")
    assert load_settings(path, config).server == config.reroll.server


def test_total_rerolls_survive_on_disk(tmp_path):
    path = tmp_path / "state.json"
    assert load_total_rerolls(path) == 0
    save_total_rerolls(7, path)
    assert load_total_rerolls(path) == 7
    path.write_text("nope", encoding="utf-8")
    assert load_total_rerolls(path) == 0


def test_status_counts_rerolls_per_session_and_in_total():
    status = BotStatus(total_rerolls=5)
    status.started()
    assert status.add_reroll() == 6
    status.set_state("LOGIN")
    snapshot = status.snapshot()
    assert (snapshot.running, snapshot.state, snapshot.session_rerolls, snapshot.total_rerolls) == (True, "LOGIN", 1, 6)
    status.finished("done")
    assert status.snapshot().running is False
    assert status.snapshot().last_result == "done"
    status.started()
    assert status.snapshot().session_rerolls == 0
    assert status.snapshot().total_rerolls == 6


def test_log_feed_keeps_lines_at_info_and_above():
    feed = LogFeed()
    logger = logging.getLogger("test_log_feed")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(feed)
    logger.debug("hidden")
    logger.info("shown")
    logger.removeHandler(feed)
    assert list(feed.lines) == ["shown"]


LEFT_MONITOR = (-1920, 0, 0, 1040)
RIGHT_MONITOR = (0, 0, 1920, 1040)


def test_panel_goes_right_of_the_game():
    x, _ = panel_position((-1928, 61, -632, 820), LEFT_MONITOR, [RIGHT_MONITOR])
    assert x == -632 + GAP
    assert x + PANEL_WIDTH <= 0


def test_panel_goes_left_when_the_right_is_full():
    game = (500, 100, 1800, 860)
    x, _ = panel_position(game, RIGHT_MONITOR, [])
    assert x == 500 - GAP - PANEL_WIDTH


def test_panel_uses_another_monitor_when_the_game_fills_this_one():
    game = (300, 0, 1700, 800)
    x, y = panel_position(game, RIGHT_MONITOR, [LEFT_MONITOR])
    assert (x, y) == (-1920 + GAP, GAP)


def test_panel_stays_inside_the_screen_vertically():
    _, y = panel_position((0, 900, 1296, 1030), RIGHT_MONITOR, [])
    assert y + 680 <= 1040


def test_login_during_the_intro_moves_on_to_it(ctx):
    ctx.frame = fixture("cinematic")
    assert login(ctx) is State.STORY
    assert ctx.session.input.clicks == []


def test_login_waits_on_a_black_loading_screen(ctx):
    ctx.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert login(ctx) is State.LOGIN


def test_intro_sends_select_server_and_title_back_to_login(ctx):
    for name in ("select_server",):
        ctx.frame = fixture(name)
        assert story(ctx) is State.LOGIN
    assert ctx.session.input.keys == []


def test_lobby_with_a_reset_pending_goes_to_the_reset_after_the_intro(ctx):
    ctx.reset_pending = True
    ctx.frame = fixture("lobby_clean")
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.RESET_ACCOUNT


def test_format_elapsed():
    assert format_elapsed(0) == "0:00:00"
    assert format_elapsed(3725) == "1:02:05"


def test_a_skill_recharging_is_not_clicked(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "fight_cooldown.jpg"))
    ctx.screen = "fight: our turn"
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks[0][1] != 1125


def test_a_screen_still_in_view_is_not_clicked_twice_at_once(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "new_skill_hint.png"))
    click_once(ctx, 640, 400)
    click_once(ctx, 642, 398)
    assert len(ctx.session.input.clicks) == 1
    click_once(ctx, 900, 400)
    assert len(ctx.session.input.clicks) == 2
    ctx.last_click = (900, 400, time.monotonic() - SAME_CLICK_SECONDS - 0.1)
    click_once(ctx, 900, 400)
    assert len(ctx.session.input.clicks) == 3


def test_a_skill_animation_is_not_taken_for_a_video(ctx):
    ctx.frame = fixture("cinematic")
    ctx.fight_seen_at = time.monotonic()
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.keys == [] and ctx.session.input.clicks == []


def test_a_video_long_after_a_fight_is_still_skipped(ctx):
    ctx.frame = fixture("cinematic")
    ctx.session.capture.frames = [fixture("cinematic")]
    ctx.fight_seen_at = time.monotonic() - SKILL_ANIMATION_SECONDS - 1
    ctx.unknown_since = time.monotonic() - UNKNOWN_WAIT_SECONDS - 1
    story(ctx)
    assert ctx.session.input.keys[0] == "press esc"


def test_a_screen_out_of_the_fight_ends_the_wait_for_an_animation(ctx):
    ctx.fight_seen_at = time.monotonic()
    ctx.frame = cv2.imread(str(FIXTURES / "rank_up.png"))
    story(ctx)
    assert ctx.fight_seen_at is None


def test_pushing_the_story_ignores_the_unlocked_summons(ctx):
    ctx.story_only = True
    ctx.frame = cv2.imread(str(FIXTURES / "lobby_unlocked.png"))
    ctx.lobby_since = time.monotonic() - LOBBY_STABLE_SECONDS - 1
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks


def test_a_fight_on_x1_goes_to_x2_only_when_pushing_the_story(ctx):
    ctx.frame = fixture("fight_x1")
    ctx.story_only = True
    ctx.stage = "6th World Episode 3-4"
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [(1073, 33)]


def test_rerolling_leaves_the_battle_speed_alone(ctx):
    ctx.frame = fixture("fight_x1")
    story(ctx)
    assert (1073, 33) not in ctx.session.input.clicks


def test_a_still_auto_icon_means_auto_is_off(ctx):
    ctx.story_only = True
    ctx.stage = "6th World Episode 3-4"
    ctx.frame = fixture("fight_x2")
    results = []
    for _ in range(3):
        ctx.auto_read_at = 0.0
        results.append(_auto_is_off(ctx))
    ctx.auto_still_since -= AUTO_STILL_SECONDS
    ctx.auto_read_at = 0.0
    results.append(_auto_is_off(ctx))
    assert results == [False, False, False, True]


def test_a_finishing_blow_that_stops_the_auto_icon_is_not_auto_off(ctx):
    # The icon stops for about 1.5 s on the last hit of a fight: Auto is still on, no click.
    ctx.frame = fixture("fight_x2")
    for _ in range(6):
        ctx.auto_read_at = 0.0
        assert not _auto_is_off(ctx)
        if ctx.auto_still_since is not None:
            ctx.auto_still_since -= 0.3


def test_a_moving_auto_icon_means_auto_is_on(ctx):
    ctx.story_only = True
    frame = fixture("fight_x2")
    for turn in range(8):
        ctx.frame = np.roll(frame, turn * 3, axis=1)
        ctx.auto_read_at = 0.0
        assert not _auto_is_off(ctx)


def test_the_auto_assign_popup_is_confirmed(ctx):
    ctx.frame = fixture("auto_assign_popup")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [(747, 495)]


def test_fights_stay_manual_before_chapter_3(ctx):
    ctx.frame = fixture("fight_x1")
    ctx.story_only = True
    ctx.stage = "6th World Episode 2-7"
    story(ctx)
    assert (1073, 33) not in ctx.session.input.clicks


def test_locked_fight_buttons_are_left_alone(ctx):
    ctx.frame = fixture("fight_locked")
    ctx.story_only = True
    ctx.stage = "6th World Episode 3-1"
    for _ in range(5):
        ctx.auto_read_at = 0.0
        story(ctx)
    assert (1073, 33) not in ctx.session.input.clicks
    assert (1127, 33) not in ctx.session.input.clicks


def test_a_lost_story_fight_is_tried_again(ctx):
    ctx.frame = cv2.imread(str(FIXTURES / "route_fight_failed.png"))
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [(1180, 661)]
    assert ctx.fails_in_a_row == 1


def test_ten_lost_story_fights_start_a_new_account_on_the_long_route(ctx):
    ctx.goal = "abyss"
    ctx.story_target = "6th World Episode 4-1"
    ctx.fails_in_a_row = 9
    ctx.frame = cv2.imread(str(FIXTURES / "route_fight_failed.png"))
    assert story(ctx) is State.RESET_ACCOUNT


def test_a_friend_request_is_cancelled_never_sent(ctx):
    ctx.frame = fixture_any("friend_request")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [(532, 527)]


# Do Not Display, the third button of the Battle Guide popup: never Cancel at 422 nor Confirm at 637.
BATTLE_GUIDE_SKIP = (853, 532)


@pytest.mark.parametrize("goal", ["reroll", "story", "abyss"])
def test_the_battle_guide_before_a_strong_enemy_is_passed_with_do_not_display(ctx, goal):
    ctx.goal = goal
    ctx.story_target = "6th World Episode 3-3"
    ctx.frame = fixture_any("battle_guide")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [BATTLE_GUIDE_SKIP]


def test_a_locked_stage_selects_the_one_above_it(ctx):
    ctx.story_only = True
    ctx.frame = fixture("story_locked_stage")
    assert story(ctx) is State.STORY
    assert len(ctx.session.input.clicks) == 1
    x, y = ctx.session.input.clicks[0]
    assert x == 52 and 125 <= y <= 140
    assert ctx.locked_stage_picks == 1


def test_a_stage_that_stays_locked_stops_the_bot(ctx):
    ctx.story_only = True
    ctx.locked_stage_picks = 3
    ctx.frame = fixture("story_locked_stage")
    assert story(ctx) is State.HALT
    assert ctx.session.input.clicks == []


def cleared_list() -> np.ndarray:
    # Seen in a real run: the episode opened on 2-1, a story stage cleared long ago; View Story replayed it for ever.
    return cv2.imread(str(FIXTURES / "story_list_cleared.png"))


def frame(name: str) -> np.ndarray:
    return cv2.imread(str(FIXTURES / f"{name}.png"))


def test_a_cleared_stage_is_not_replayed_but_select_episode_opened(ctx):
    ctx.story_only = True
    ctx.frame = cleared_list()
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [EPISODE_TREE_POINT]
    assert ctx.episode_trips == 1


def test_select_episode_opens_the_blue_pinned_episode_not_the_orange_world_one(ctx):
    ctx.story_only = True
    ctx.episode_trips = 1
    ctx.frame = frame("episode_select_world")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [(180, 573)]
    assert ctx.episode_picked


def test_select_episode_then_opens_the_pinned_chapter(ctx):
    ctx.story_only = True
    ctx.episode_trips = 1
    ctx.episode_picked = True
    ctx.frame = frame("episode_select_aki")
    assert story(ctx) is State.STORY
    x, y = ctx.session.input.clicks[0]
    assert 1040 <= x <= 1120 and 570 <= y <= 630


def test_select_episode_without_a_pin_on_a_chapter_scrolls_the_map(ctx):
    ctx.story_only = True
    ctx.episode_trips = 1
    ctx.episode_picked = True
    ctx.frame = frame("episode_select_tundra")
    assert story(ctx) is State.STORY
    assert ctx.session.input.clicks == [("drag", 1150, 480, 500, 480)]


def test_select_episode_not_opened_by_the_bot_is_left(ctx):
    ctx.story_only = True
    ctx.frame = frame("episode_select_world")
    assert story(ctx) is State.STORY
    assert ctx.session.input.keys == ["press esc"]


def test_the_pinned_stage_is_started_and_the_trip_count_cleared(ctx):
    ctx.story_only = True
    ctx.episode_trips = 1
    ctx.config.reroll.stop_at_stage = None
    ctx.frame = frame("story_list_aki")
    assert story(ctx) is State.STORY
    x, y = ctx.session.input.clicks[0]
    assert 1060 <= x <= 1170 and 646 <= y <= 678
    assert ctx.episode_trips == 0


def test_a_cleared_stage_after_the_trip_walks_to_the_next_node(ctx):
    ctx.story_only = True
    ctx.episode_trips = MAX_EPISODE_TRIPS
    ctx.frame = cleared_list()
    assert story(ctx) is State.STORY
    assert len(ctx.session.input.clicks) == 1
    x, y = ctx.session.input.clicks[0]
    assert x == 52 and 320 <= y <= 345


@pytest.mark.parametrize("name", ["story_list_ready", "story_list_view", "stage_six", "story_episode3_list", "story_locked_stage"])
def test_a_new_stage_is_not_taken_for_a_cleared_one(ctx, name):
    ctx.frame = fixture(name) if (FIXTURES / f"{name}.jpg").exists() else cv2.imread(str(FIXTURES / f"{name}.png"))
    assert selected_tick_y(ctx) is None


def test_a_cleared_stage_low_in_the_list_scrolls_it(ctx):
    ctx.story_only = True
    ctx.episode_trips = MAX_EPISODE_TRIPS
    ctx.frame = cleared_list()
    _cleared_stage(ctx, "6th World Episode 2-4", 545)
    assert ctx.session.input.clicks == [("drag", 52, 520, 52, 300)]


def test_a_selection_that_does_not_move_opens_the_next_chapter(ctx):
    ctx.story_only = True
    ctx.episode_trips = MAX_EPISODE_TRIPS
    ctx.frame = cleared_list()
    for _ in range(NEXT_NODE_TRIES):
        story(ctx)
    ctx.session.input.clicks.clear()
    assert story(ctx) is State.STORY
    x, y = ctx.session.input.clicks[0]
    assert 240 <= x <= 256 and 100 <= y <= 118


def test_a_cleared_stage_that_cannot_be_left_stops_the_bot(ctx):
    ctx.story_only = True
    ctx.episode_trips = MAX_EPISODE_TRIPS
    ctx.frame = cleared_list()
    for _ in range(MAX_CLEARED_STEPS):
        assert story(ctx) is State.STORY
    ctx.session.input.clicks.clear()
    assert story(ctx) is State.HALT
    assert ctx.session.input.clicks == []


def test_a_moved_selection_starts_the_steps_again(ctx):
    ctx.story_only = True
    ctx.episode_trips = MAX_EPISODE_TRIPS
    ctx.frame = cleared_list()
    _cleared_stage(ctx, "6th World Episode 2-1", 248)
    _cleared_stage(ctx, "6th World Episode 2-1", 248)
    assert ctx.cleared_stage_steps == 2
    _cleared_stage(ctx, "6th World Episode 2-1", 345)
    assert ctx.cleared_stage_steps == 1


def test_an_unlocked_stage_is_not_taken_for_a_locked_one(ctx):
    ctx.story_only = True
    ctx.locked_stage_picks = 2
    ctx.frame = fixture("story_episode3_list")
    assert story(ctx) is State.STORY
    assert ctx.locked_stage_picks == 0
    assert all(x != 52 for x, _ in ctx.session.input.clicks)


def test_three_lost_fights_stop_the_story_of_a_kept_account(ctx):
    ctx.goal = "story"
    ctx.story_only = True
    ctx.stage = "Ritania 5-3"
    ctx.fails_in_a_row = 2
    ctx.frame = cv2.imread(str(FIXTURES / "route_fight_failed.png"))
    assert story(ctx) is State.HALT
    assert "Ritania 5-3" in ctx.halt_reason
    assert ctx.session.input.clicks == []
    assert not ctx.reset_pending


def test_the_long_route_takes_over_any_screen_when_no_story_is_asked(ctx):
    ctx.goal = "abyss"
    ctx.frame = cv2.imread(str(FIXTURES / "route_fight_failed.png"))
    assert story(ctx) is State.ROUTE
    assert ctx.session.input.clicks == []


def test_a_pending_reset_keeps_the_story_until_the_lobby(ctx):
    ctx.goal = "abyss"
    ctx.reset_pending = True
    ctx.frame = cv2.imread(str(FIXTURES / "route_fight_failed.png"))
    assert story(ctx) is not State.ROUTE


def test_the_story_goal_counts_each_stage_won_once(ctx):
    won = []
    ctx.goal = "story"
    ctx.story_only = True
    ctx.on_story_won = lambda: won.append(1)
    ctx.frame = fixture("stage_clear")
    story(ctx)
    ctx.last_click = None
    story(ctx)
    assert won == [1]


def test_the_story_goal_counts_a_lost_fight(ctx):
    lost = []
    ctx.goal = "story"
    ctx.story_only = True
    ctx.on_story_lost = lambda: lost.append(1)
    ctx.frame = cv2.imread(str(FIXTURES / "route_fight_failed.png"))
    story(ctx)
    assert lost == [1]


def story_failed() -> np.ndarray:
    # Episode 6 2-3: FAILED in the middle, with Orbis Guide and Help. Unseen, it was taken for the fight
    # and the first skill's click hit Try again: the stage restarted for an hour, no loss counted.
    return cv2.imread(str(FIXTURES / "story_failed.png"))


def test_a_lost_story_stage_is_counted_and_tried_again(ctx):
    lost = []
    ctx.goal = "story"
    ctx.story_only = True
    ctx.on_story_lost = lambda: lost.append(1)
    ctx.frame = story_failed()
    assert story(ctx) is State.STORY
    assert lost == [1] and ctx.fails_in_a_row == 1
    assert ctx.session.input.clicks == [TRY_AGAIN_POINT]


def test_a_story_stage_lost_three_times_stops_the_bot(ctx):
    ctx.goal = "story"
    ctx.story_only = True
    ctx.stage = "Episode 6 2-3"
    ctx.fails_in_a_row = 2
    ctx.frame = story_failed()
    assert story(ctx) is State.HALT
    assert "Episode 6 2-3" in ctx.halt_reason
    assert ctx.session.input.clicks == []


def test_the_help_icon_after_the_episode_name_is_not_read_as_part_of_it(ctx):
    ctx.story_only = True
    ctx.frame = fixture_any("story_help_icon_list")
    assert read_stage(ctx) == "Episode 3 3-8"


def test_energy_is_refilled_with_a_leaf_picked_first_then_bought(ctx):
    ctx.frame = fixture("energy_popup")
    assert story(ctx) is State.STORY
    x, y = ctx.session.input.clicks[0]
    assert 380 < x < 615 and 305 < y < 440
    ctx.last_click = None
    story(ctx)
    assert ctx.session.input.clicks[1] == (746, 550)
    assert ctx.leaves_used == 1


def test_the_skystone_option_is_never_clicked(ctx):
    ctx.frame = fixture("energy_popup")
    for _ in range(6):
        ctx.last_click = None
        story(ctx)
    assert all(not (665 < x < 900 and 305 < y < 440) for x, y in ctx.session.input.clicks)


def test_too_many_leaves_stop_the_bot(ctx):
    ctx.leaves_used = 20
    ctx.frame = fixture("energy_popup")
    assert story(ctx) is State.HALT
    assert ctx.session.input.clicks == []


def test_start_quest_is_clicked_right_after_the_supporters_button_at_the_same_place(ctx):
    # The supporter screen's button and Start Quest sit at the same place: the second click must go.
    ctx.last_click = (1142, 656, time.monotonic())
    ctx.frame = fixture("story_team")
    story(ctx)
    assert ctx.session.input.clicks and start_quest_clicked(ctx)


def test_a_full_team_stops_the_hero_adding(ctx):
    # Every hero of the list is already in the team: after a few tries it starts the quest as it is.
    ctx.stage = "Episode 5 10-6"
    ctx.adding_hero = True
    ctx.frame = fixture_any("team_full_already_selected")
    for _ in range(8):
        ctx.last_click = None
        story(ctx)
    assert not ctx.adding_hero and ctx.hero_added_on == "Episode 5 10-6"
    assert start_quest_clicked(ctx)

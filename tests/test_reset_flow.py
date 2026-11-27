"""LOGIN, LAUNCH and RESET_ACCOUNT on real screenshots of the game, with real templates and OCR."""

import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import State, load_config
from core.manifest import load_manifest
from core.state import Context
from core.vision import Ocr, TemplateLibrary
from states.launch import launch
from states.login import login
from states.reset_account import nickname_spellings, parse_nickname, parse_server, reset_account

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
LOGGER = logging.getLogger("test_reset_flow")
DELETE_ACCOUNT_RIGHT_EDGE = 1040


def fixture(name: str) -> np.ndarray:
    return cv2.imread(str(FIXTURES / f"{name}.png"))


class FakeInput:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def type_text(self, text: str, secret: bool = False) -> None:
        self.typed.append(text)


class FakeSession:
    def __init__(self) -> None:
        self.input = FakeInput()

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
    reset = config.reset.model_copy(update={"enabled": True})
    session = FakeSession()
    return Context(
        config=config.model_copy(update={"reset": reset}),
        logger=LOGGER,
        library=library,
        ocr=Ocr(config.vision),
        open_session=lambda: session,
        session=session,
        sleep=lambda seconds: None,
    )


def test_shipped_config_rerolls_on_foundry():
    assert load_config(ROOT / "config.yaml").reroll.server == "Foundry"


def test_disabled_reset_clicks_nothing(ctx):
    ctx.config = ctx.config.model_copy(
        update={"reset": ctx.config.reset.model_copy(update={"enabled": False})}
    )
    ctx.frame = fixture("settings")
    assert reset_account(ctx) is State.HALT
    assert "reset is turned off" in ctx.halt_reason
    assert ctx.session.input.clicks == []


@pytest.mark.parametrize("goal", ["story", "climb"])
def test_a_kept_account_is_never_reset(ctx, goal):
    ctx.goal = goal
    ctx.reset_pending = True
    ctx.frame = fixture("settings")
    assert reset_account(ctx) is State.HALT
    assert "never resetting" in ctx.halt_reason
    assert ctx.session.input.clicks == []


def test_lobby_opens_the_menu(ctx):
    ctx.frame = fixture("lobby")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = ctx.session.input.clicks[0]
    assert x > 1200 and y < 60


def test_hidden_lobby_menu_is_woken_with_a_click(ctx):
    ctx.frame = fixture("lobby_hidden")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert ctx.session.input.clicks == [(640, 400)]


def test_reset_gives_up_waking_a_lobby_that_is_not_there(ctx):
    ctx.frame = fixture("lobby_hidden")
    results = [reset_account(ctx) for _ in range(9)]
    assert results[:8] == [State.RESET_ACCOUNT] * 8
    assert results[8] is State.STORY
    assert len(ctx.session.input.clicks) == 8


def test_a_new_account_behind_a_saved_finished_route_is_not_reset(ctx):
    ctx.reset_pending = True
    ctx.reset_from_saved_route = True
    ctx.set_route_step(35)
    ctx.frame = fixture("lobby_hidden")
    results = [reset_account(ctx) for _ in range(9)]
    assert results[8] is State.STORY
    assert not ctx.reset_pending
    assert ctx.route_step == 0


def test_a_reset_asked_by_the_user_still_goes_through_the_intro(ctx):
    ctx.reset_pending = True
    ctx.frame = fixture("lobby_hidden")
    results = [reset_account(ctx) for _ in range(9)]
    assert results[8] is State.STORY
    assert ctx.reset_pending


def test_menu_opens_the_settings(ctx):
    ctx.frame = fixture("menu")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = ctx.session.input.clicks[0]
    assert abs(x - 1218) < 20 and abs(y - 590) < 20


def test_settings_clicks_reset_server_and_not_delete_account(ctx):
    ctx.frame = fixture("settings")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = ctx.session.input.clicks[0]
    assert x > DELETE_ACCOUNT_RIGHT_EDGE
    assert abs(y - 512) < 20


def test_button_with_another_label_is_refused(ctx, monkeypatch):
    ctx.frame = fixture("settings")
    monkeypatch.setattr(ctx.ocr, "read", lambda *args, **kwargs: "Delete Account")
    assert reset_account(ctx) is State.HALT
    assert ctx.session.input.clicks == []


def test_generated_nickname_is_read_exactly(ctx):
    region = (480, 456, 320, 26)
    text = ctx.ocr.read(fixture("dialog_empty"), region, whitelist="abcdefghijklmnopqrstuvwxyz0123456789#N:")
    assert parse_nickname(text) == "epic7#6kmix0"


def test_empty_dialog_types_the_nickname_it_displays(ctx):
    ctx.frame = fixture("dialog_empty")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert ctx.session.input.typed == ["epic7#6kmix0"]
    assert ctx.reset_confirmed is False


def test_filled_dialog_confirms(ctx):
    ctx.frame = fixture("dialog_typed")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = ctx.session.input.clicks[0]
    assert abs(x - 747) < 30 and abs(y - 543) < 20
    assert ctx.reset_confirmed is False, "a second dialog still follows"


def test_final_dialog_confirms_and_nothing_else_after(ctx):
    ctx.frame = fixture("dialog_final")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = ctx.session.input.clicks[0]
    assert abs(x - 748) < 30 and abs(y - 496) < 20
    assert ctx.reset_confirmed is True
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert len(ctx.session.input.clicks) == 1


def test_final_dialog_about_a_deletion_is_refused(ctx, monkeypatch):
    ctx.frame = fixture("dialog_final")
    monkeypatch.setattr(ctx.ocr, "read", lambda *args, **kwargs: "Tap Confirm to Delete the Account")
    assert reset_account(ctx) is State.HALT
    assert ctx.session.input.clicks == [] and ctx.reset_confirmed is False


def test_final_templates_do_not_match_the_first_dialog(ctx):
    for name in ("dialog_empty", "dialog_typed"):
        assert ctx.library.find(fixture(name), "reset_final_body") is None


def test_disabled_confirm_button_does_not_match(ctx):
    assert ctx.library.find(fixture("dialog_empty"), "reset_confirm") is None
    assert ctx.library.find(fixture("dialog_typed"), "reset_confirm") is not None


def test_server_not_allowed_is_refused_before_anything_is_typed(ctx):
    ctx.config = ctx.config.model_copy(
        update={"reroll": ctx.config.reroll.model_copy(update={"server": "Global"})}
    )
    ctx.frame = fixture("dialog_empty")
    assert reset_account(ctx) is State.HALT
    assert "refusing to reset" in ctx.halt_reason
    assert ctx.session.input.clicks == [] and ctx.session.input.typed == []


def test_delete_dialog_is_refused(ctx, monkeypatch):
    ctx.frame = fixture("dialog_typed")
    monkeypatch.setattr(ctx.ocr, "read", lambda *args, **kwargs: "Delete Account")
    assert reset_account(ctx) is State.HALT
    assert ctx.session.input.clicks == []


def test_disabled_confirm_with_a_typed_field_cancels(ctx, monkeypatch):
    ctx.frame = fixture("dialog_typed")
    original = ctx.library.find

    def no_confirm(frame, name, region=None):
        return None if name == "reset_confirm" else original(frame, name, region)

    monkeypatch.setattr(ctx.library, "find", no_confirm)
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert ctx.reset_confirmed is False
    x, y = ctx.session.input.clicks[0]
    assert abs(x - 533) < 30 and abs(y - 543) < 20


def test_field_misread_by_ocr_still_goes_on_when_the_game_enables_confirm(ctx, monkeypatch):
    ctx.frame = fixture("dialog_typed")
    original = ctx.ocr.read

    def misread_field(frame, region=None, **kwargs):
        return "epic7#9mu7w" if region == (505, 350, 266, 40) else original(frame, region, **kwargs)

    monkeypatch.setattr(ctx.ocr, "read", misread_field)
    assert reset_account(ctx) is State.RESET_ACCOUNT
    x, y = ctx.session.input.clicks[0]
    assert x > 640 and abs(y - 543) < 20


def test_lookalike_spellings_start_with_the_reading_and_keep_the_prefix():
    spellings = nickname_spellings("epic7#d814t9")
    assert spellings[0] == "epic7#d814t9"
    assert "epic7#d8l4t9" in spellings[:3]
    assert all(spelling.startswith("epic7#") for spelling in spellings)
    assert len(spellings) == len(set(spellings))


def test_nickname_without_lookalike_has_one_spelling():
    assert nickname_spellings("epic7#abcdef") == ["epic7#abcdef"]


def test_the_next_attempt_types_the_next_spelling(ctx):
    ctx.nickname_attempt = 1
    ctx.frame = fixture("dialog_empty")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert len(ctx.session.input.typed) == 1
    first = ctx.session.input.typed[0]
    assert first.startswith("epic7#")


def test_all_spellings_refused_stops_the_bot(ctx):
    ctx.nickname_attempt = 99
    ctx.frame = fixture("dialog_empty")
    assert reset_account(ctx) is State.HALT
    assert "spellings" in ctx.halt_reason
    assert ctx.session.input.typed == []


def test_the_new_account_inherits_nothing_from_the_old_one(ctx):
    ctx.reset_confirmed = True
    ctx.reset_pending = True
    ctx.hero_added_on = "6th World Episode 2-2"
    ctx.nickname_attempt = 2
    ctx.stage = "6th World Episode 2-2"
    ctx.summon.cards_read = 21
    ctx.summon.finished = True
    ctx.frame = cv2.imread(str(FIXTURES / "select_server.jpg"))
    assert reset_account(ctx) is State.LOGIN
    assert ctx.hero_added_on is None
    assert ctx.nickname_attempt == 0
    assert ctx.stage == ""
    assert ctx.summon.cards_read == 0
    assert not ctx.summon.finished


def test_after_confirm_nothing_is_clicked_until_select_server(ctx):
    counted = []
    ctx.on_reset = lambda: counted.append(1)
    ctx.reset_confirmed = True
    ctx.reset_pending = True
    ctx.frame = fixture("lobby")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert ctx.session.input.clicks == []
    ctx.frame = cv2.imread(str(FIXTURES / "select_server.jpg"))
    assert reset_account(ctx) is State.LOGIN
    assert ctx.resets_done == 1 and ctx.reset_confirmed is False
    assert ctx.reset_pending is False and counted == [1]


def test_title_template_behind_select_server_does_not_count_as_the_end(ctx):
    ctx.reset_confirmed = True
    ctx.frame = fixture("title")
    assert reset_account(ctx) is State.RESET_ACCOUNT
    assert ctx.reset_confirmed is True


def test_once_stops_after_the_reset(ctx):
    ctx.once = True
    ctx.reset_confirmed = True
    ctx.frame = cv2.imread(str(FIXTURES / "select_server.jpg"))
    assert reset_account(ctx) is State.HALT


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Current Server: Foundry", "Foundry"), ("current server :Global", "Global"), ("nothing", None)],
)
def test_parse_server(text, expected):
    assert parse_server(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nickname:epic7#6kmix0", "epic7#6kmix0"),
        ("Nickname: epic7#abcdef", "epic7#abcdef"),
        ("Nickname: epic7#6kmix0O", None),
        ("Nickname: epic7#6kmix00", None),
        ("Nickname:epic7#73vo04q", "epic7#73v04q"),
        ("Nickname:epic7#73vo04qq", None),
        ("Nickname: epic7#6kmi", None),
        ("Nickname: SomeName", None),
        ("Nickname:", None),
        ("", None),
    ],
)
def test_parse_nickname(text, expected):
    assert parse_nickname(text) == expected


def test_login_recognizes_the_lobby(ctx):
    ctx.frame = fixture("lobby")
    assert login(ctx) is State.STORY
    assert ctx.session.input.clicks == []


def test_launch_starts_the_client_once_then_waits(ctx, monkeypatch):
    started = []
    monkeypatch.setattr("os.startfile", started.append)
    ctx.session = None
    ctx.open_session = lambda: None
    assert launch(ctx) is State.LAUNCH
    assert launch(ctx) is State.LAUNCH
    assert started == [ctx.config.reset.launch_command]


def test_launch_moves_on_when_the_window_is_there(ctx, monkeypatch):
    monkeypatch.setattr("os.startfile", lambda command: pytest.fail("started twice"))
    assert launch(ctx) is State.LOGIN


def test_dry_run_never_launches(ctx, monkeypatch):
    monkeypatch.setattr("os.startfile", lambda command: pytest.fail("started"))
    ctx.session = None
    ctx.open_session = lambda: None
    ctx.dry_run = True
    assert launch(ctx) is State.HALT


def test_a_doubled_lookalike_still_offers_the_real_nickname():
    assert "epic7#73vo4q" in nickname_spellings(parse_nickname("Nickname:epic7#73vo04q"))

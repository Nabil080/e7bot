"""Skystone revives of the story mode, on a fight screenshot with a synthetic revive popup drawn over it."""

import importlib
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import State, load_config
from core.settings import Settings, apply_settings
from core.vision import TemplateLibrary
from states.story import REVIVE_CLOSE_SECONDS, story

# states/__init__ exports the story function under the module's name.
story_module = importlib.import_module("states.story")
from tests.test_intro_flow import LOGGER, ctx, library  # noqa: F401  (pytest fixtures)

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
# Where the synthetic popup draws its parts, as (x, y) of the top left corner.
TITLE_AT = (540, 200)
SKYSTONE_AT = (760, 470)
GIVE_UP_AT = (420, 470)


def noise(height: int, width: int, seed: int) -> np.ndarray:
    """Return a patch no other part of the screen looks like."""
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


PARTS = {
    "story_revive_title": (noise(40, 200, 1), TITLE_AT),
    "story_revive_skystone": (noise(40, 40, 2), SKYSTONE_AT),
    "story_revive_give_up": (noise(44, 120, 3), GIVE_UP_AT),
}


def center(name: str) -> tuple[int, int]:
    patch, (x, y) = PARTS[name]
    return x + patch.shape[1] // 2, y + patch.shape[0] // 2


def fight() -> np.ndarray:
    return cv2.imread(str(FIXTURES / "fight_x1.jpg"))


def popup(*names: str) -> np.ndarray:
    """Return the fight with the chosen parts of the revive popup drawn over it."""
    frame = fight()
    for name in names or tuple(PARTS):
        patch, (x, y) = PARTS[name]
        frame[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return frame


@pytest.fixture
def revive_ctx(ctx, library, monkeypatch):
    """A story mode context whose templates include the revive popup's, answered as soon as it shows."""
    with_popup = TemplateLibrary(ctx.config.vision, LOGGER)
    with_popup.templates = {**library.templates, **{name: patch for name, (patch, _) in PARTS.items()}}
    ctx.library = with_popup
    ctx.goal = "story"
    ctx.story_only = True
    ctx.stage = "Ritania 5-3"
    monkeypatch.setattr(story_module, "REVIVE_SETTLE_SECONDS", 0.0)
    return ctx


def allow(ctx, revives: int) -> None:
    ctx.config = ctx.config.model_copy(update={"story": ctx.config.story.model_copy(update={"max_revives": revives})})


def popup_clicks(ctx) -> list[tuple[int, int]]:
    """Return the clicks on the revive popup, leaving out those of the fight behind it."""
    centers = {center(name) for name in PARTS}
    return [click for click in ctx.session.input.clicks if click in centers]


def tick(ctx, frame: np.ndarray) -> State:
    ctx.frame = frame
    ctx.last_click = None
    return story(ctx)


def test_by_default_the_revive_is_declined(revive_ctx):
    assert revive_ctx.config.story.max_revives == 0
    assert tick(revive_ctx, popup()) is State.STORY
    assert revive_ctx.session.input.clicks == [center("story_revive_give_up")]
    assert revive_ctx.revives_this_fight == 0


def test_two_revives_are_used_then_the_fight_counts_as_lost(revive_ctx):
    revived, lost = [], []
    revive_ctx.on_story_revive = lambda: revived.append(1)
    revive_ctx.on_story_lost = lambda: lost.append(1)
    allow(revive_ctx, 2)
    for _ in range(2):
        tick(revive_ctx, popup())
        assert popup_clicks(revive_ctx)[-1] == center("story_revive_skystone")
        tick(revive_ctx, fight())
    tick(revive_ctx, popup())
    assert popup_clicks(revive_ctx) == [center("story_revive_skystone")] * 2 + [center("story_revive_give_up")]
    assert revived == [1, 1] and lost == []
    tick(revive_ctx, cv2.imread(str(FIXTURES / "route_fight_failed.png")))
    assert lost == [1]
    assert revive_ctx.fails_in_a_row == 1


def test_the_revives_count_again_from_zero_in_the_next_fight(revive_ctx):
    allow(revive_ctx, 1)
    tick(revive_ctx, popup())
    tick(revive_ctx, fight())
    tick(revive_ctx, popup())
    assert popup_clicks(revive_ctx)[-1] == center("story_revive_give_up")
    tick(revive_ctx, cv2.imread(str(FIXTURES / "route_fight_failed.png")))
    assert revive_ctx.revives_this_fight == 0
    tick(revive_ctx, popup())
    assert popup_clicks(revive_ctx)[-1] == center("story_revive_skystone")


def test_a_popup_still_closing_is_not_paid_twice(revive_ctx):
    allow(revive_ctx, 3)
    for _ in range(4):
        tick(revive_ctx, popup())
    assert revive_ctx.session.input.clicks == [center("story_revive_skystone")]
    assert revive_ctx.revives_this_fight == 1


def test_a_popup_that_stays_after_revive_stops_the_bot(revive_ctx):
    allow(revive_ctx, 3)
    tick(revive_ctx, popup())
    revive_ctx.revive_clicked_at = time.monotonic() - REVIVE_CLOSE_SECONDS - 1
    assert tick(revive_ctx, popup()) is State.HALT
    assert revive_ctx.session.input.clicks == [center("story_revive_skystone")]


def test_other_modes_never_pay_a_revive(revive_ctx):
    allow(revive_ctx, 2)
    revive_ctx.goal = "abyss"
    revive_ctx.story_target = "6th World Episode 4-1"
    tick(revive_ctx, popup())
    assert revive_ctx.session.input.clicks == [center("story_revive_give_up")]


def test_without_a_skystone_on_the_button_the_revive_is_declined(revive_ctx):
    allow(revive_ctx, 2)
    tick(revive_ctx, popup("story_revive_title", "story_revive_give_up"))
    assert revive_ctx.session.input.clicks == [center("story_revive_give_up")]


def test_without_give_up_esc_declines_then_the_bot_stops(revive_ctx):
    frame = popup("story_revive_title")
    for _ in range(3):
        assert tick(revive_ctx, frame) is State.STORY
    assert revive_ctx.session.input.keys == ["press esc"] * 3
    assert tick(revive_ctx, frame) is State.HALT
    assert revive_ctx.session.input.clicks == []


def test_the_shipped_templates_never_buy_a_revive(ctx):
    allow(ctx, 2)
    ctx.goal = "story"
    ctx.story_only = True
    tick(ctx, popup())
    assert center("story_revive_skystone") not in ctx.session.input.clicks


def test_the_revive_setting_reaches_the_config():
    config = load_config(ROOT / "config.yaml")
    assert config.story.max_revives == 0
    assert apply_settings(config, Settings(story_max_revives=2)).story.max_revives == 2

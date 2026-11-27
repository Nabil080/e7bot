"""The Insufficient Inventory popup stops every mode, on a real screenshot of it."""

from pathlib import Path

import cv2

from core.config import State
from core.state import StateMachine
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def machine(ctx, frame_name: str) -> StateMachine:
    bot = StateMachine(ctx, {})
    bot.state = State.ROUTE
    ctx.frame = cv2.imread(str(FIXTURES / frame_name))
    return bot


def test_a_full_hero_inventory_halts_with_the_reason(ctx):
    bot = machine(ctx, "inventory_full_hero.png")
    assert bot._inventory_full()
    assert bot.state is State.HALT
    assert ctx.halt_reason == "cannot start: the heroes inventory is full"


def test_other_screens_do_not_halt(ctx):
    bot = machine(ctx, "huche_ready.png")
    assert not bot._inventory_full()
    assert bot.state is State.ROUTE

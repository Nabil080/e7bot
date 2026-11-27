"""Penguins at the Growth Altar, on real screenshots of each screen on the way and of the purchase."""

from pathlib import Path

import cv2
import pytest

from core.panel import penguin_line, run_metrics
from core.progress import read_progress
from states.penguins import (
    BUY_CONFIRM_POINT, BUY_MAX_POINT, GROWTH_ALTAR_POINT, PENGUIN_BUY_POINT, PRICE, RESULT_CLOSE_POINT, SANCTUARY_FOREST_POINT,
    penguin_data,
)
from states.route import route
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def shot(name: str):
    image = cv2.imread(str(FIXTURES / f"penguins_{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture
def penguin_ctx(ctx):
    ctx.goal = "penguins"
    ctx.attach = lambda: True
    return ctx


def limit(ctx, kind: str, amount: int) -> None:
    ctx.config = ctx.config.model_copy(update={"penguins": ctx.config.penguins.model_copy(update={"kind": kind, "amount": amount})})


def tick(ctx, name):
    ctx.frame = shot(name)
    ctx.last_click = None
    return route(ctx)


def test_the_lobby_opens_the_sanctuary(penguin_ctx):
    tick(penguin_ctx, "lobby")
    x, y = penguin_ctx.session.input.clicks[0]
    assert 15 <= x <= 100 and 140 <= y <= 205


def test_the_sanctuary_opens_the_forest_and_the_forest_its_altar(penguin_ctx):
    tick(penguin_ctx, "sanctuary")
    assert penguin_ctx.session.input.clicks == [SANCTUARY_FOREST_POINT]
    penguin_ctx.session.input.clicks.clear()
    tick(penguin_ctx, "forest")
    assert penguin_ctx.session.input.clicks == [GROWTH_ALTAR_POINT]


def test_the_altar_opens_the_penguin_nest_purchase(penguin_ctx):
    tick(penguin_ctx, "altar")
    assert penguin_ctx.session.input.clicks == [PENGUIN_BUY_POINT]


def test_the_purchase_is_set_to_50_before_it_is_confirmed(penguin_ctx):
    tick(penguin_ctx, "buy_one")
    assert penguin_ctx.session.input.clicks == [BUY_MAX_POINT]
    penguin_ctx.session.input.clicks.clear()
    tick(penguin_ctx, "buy_max")
    data = penguin_data(penguin_ctx)
    assert penguin_ctx.session.input.clicks == [BUY_CONFIRM_POINT]
    # Counted as it is confirmed: it can never go uncounted.
    assert (data["purchases"], data["stigma"]) == (1, PRICE)


def test_after_a_confirm_nothing_is_clicked_until_the_result_shows(penguin_ctx):
    # The result popup fades in over the Altar: clicking the Altar's Buy then would close it unseen. That happened
    # live and bought 57 times instead of 2.
    tick(penguin_ctx, "buy_max")
    penguin_ctx.session.input.clicks.clear()
    tick(penguin_ctx, "altar")
    assert penguin_ctx.session.input.clicks == []
    tick(penguin_ctx, "result")
    assert penguin_ctx.session.input.clicks == [RESULT_CLOSE_POINT]
    assert penguin_data(penguin_ctx)["confirmed_at"] is None
    assert penguin_data(penguin_ctx)["purchases"] == 1


def test_the_altar_stigma_stops_purchases_that_went_uncounted(penguin_ctx):
    # Two purchases set, none counted, but the Altar shows 10,200 stigma less than at the start: done.
    limit(penguin_ctx, "purchases", 2)
    penguin_data(penguin_ctx)["start_stigma"] = 2_750_129 + 2 * PRICE
    tick(penguin_ctx, "altar")
    assert "purchases done" in penguin_ctx.halt_reason
    assert "2 purchases" in penguin_ctx.halt_reason and "10,200 stigma" in penguin_ctx.halt_reason
    assert penguin_ctx.session.input.clicks == []


def test_it_stops_after_the_purchases_set(penguin_ctx):
    limit(penguin_ctx, "purchases", 2)
    penguin_data(penguin_ctx).update(purchases=2, stigma=2 * PRICE)
    tick(penguin_ctx, "altar")
    assert "purchases done" in penguin_ctx.halt_reason
    assert penguin_ctx.session.input.clicks == []


def test_a_stigma_budget_never_goes_over(penguin_ctx):
    # 12,000 stigma: two purchases fit (10,200), a third would spend 15,300.
    limit(penguin_ctx, "stigma", 12_000)
    penguin_data(penguin_ctx).update(purchases=2, stigma=2 * PRICE)
    tick(penguin_ctx, "buy_max")
    assert "stigma budget spent" in penguin_ctx.halt_reason
    assert BUY_CONFIRM_POINT not in penguin_ctx.session.input.clicks


def test_the_window_counts_the_purchases_and_shows_the_plan():
    progress = read_progress([
        "10:00:00 penguins: 3 purchases planned",
        "10:00:09 penguins: bought 50 penguins for 5,100 stigma, 1 purchases in this run",
        "10:00:12 screen: growth altar",
    ])
    assert progress.penguin_purchases == 1
    detail, bar = penguin_line(progress)
    assert detail == "Purchase 2 of 3"
    assert bar == {"text": "1 of 3 purchases · 5,100 stigma", "fraction": 1 / 3}
    stigma = read_progress(["10:00:00 penguins: 20000 stigma planned"])
    assert penguin_line(stigma)[1]["text"] == "0 of 20,000 stigma · 0 purchases"
    numbers = run_metrics("penguins", None, progress)
    assert numbers == [("1", "purchase"), ("50", "penguins"), ("5,100", "stigma spent")]


def test_the_result_is_counted_by_kind_of_penguin(penguin_ctx):
    from states.penguins import read_kinds

    penguin_ctx.frame = shot("result")
    # 41 newbie (green leaves), 8 experienced (brown feather), 1 epic (crown, no count under it).
    assert read_kinds(penguin_ctx) == {"newbie": 41, "experienced": 8, "epic": 1}


def test_the_kinds_are_logged_once_per_purchase(penguin_ctx):
    got = []
    penguin_ctx.on_penguin_kinds = got.append
    tick(penguin_ctx, "buy_max")
    tick(penguin_ctx, "result")
    tick(penguin_ctx, "result")
    assert got == [{"newbie": 41, "experienced": 8, "epic": 1}]

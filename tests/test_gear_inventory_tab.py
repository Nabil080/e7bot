"""The gear modes open the Inventory's Equipment tab, wherever the Inventory was left."""

from pathlib import Path

import cv2

from states.gear import EQUIPMENT_TAB_POINT, equipment_tab_open
from states.route import route
from tests.test_intro_flow import ctx, library  # noqa: F401  (pytest fixtures)

GEAR = Path(__file__).parent / "fixtures" / "gear"


def shot(name: str):
    image = cv2.imread(str(GEAR / f"{name}.png"))
    assert image is not None, name
    return image


def test_the_lit_tab_of_the_left_rail_is_seen():
    assert equipment_tab_open(shot("inventory_equipment_tab"))
    assert not equipment_tab_open(shot("inventory_heroes_tab"))


def test_an_inventory_left_on_its_heroes_tab_is_switched_to_the_equipment(ctx):
    ctx.goal = "gear_sort"
    ctx.attach = lambda: True
    ctx.frame = shot("inventory_heroes_tab")
    route(ctx)
    assert ctx.session.input.clicks == [EQUIPMENT_TAB_POINT]

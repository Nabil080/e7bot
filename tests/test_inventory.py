"""Reading pieces from the Inventory's detail panel, and finding the way through its grid."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import load_config
from core.gear import GearPolicy
from core.inventory import (
    PieceReading,
    has_card,
    parse_value,
    read_piece,
    scroll_shift,
    stat_key,
    verdict,
    visible_rows,
)
from core.vision import Ocr

ROOT = Path(__file__).parent.parent
GEAR = Path(__file__).parent / "fixtures" / "gear"
EARLY = GearPolicy()


def frame(name: str) -> np.ndarray:
    image = cv2.imread(str(GEAR / f"{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture(scope="module")
def ocr() -> Ocr:
    return Ocr(load_config(ROOT / "config.yaml").vision)


def test_a_percent_value_makes_attack_defense_and_health_percent_stats():
    assert stat_key("Attack", "23%") == "attack_percent"
    assert stat_key("Attack", "525") == "attack"
    assert stat_key("Critical Hit Damage", "13%") == "crit_damage"
    assert stat_key("Speed", "7") == "speed"
    assert stat_key("ao. 6 (eae", "") is None
    assert parse_value("2,765") == 2765
    assert parse_value("12%") == 12
    assert parse_value("") is None


def test_an_equipped_weapon_at_plus_15(ocr):
    piece = read_piece(frame("panel_weapon_15_equipped"), ocr)
    assert piece == PieceReading(
        grade="epic", slot="weapon", level=15, main_stat="attack",
        substats={"attack_percent": 23, "speed": 7, "crit_chance": 10, "crit_damage": 13},
        set_name="speed", locked=False, equipped=True,
    )


def test_a_locked_ring(ocr):
    piece = read_piece(frame("panel_ring_15_locked"), ocr)
    assert piece is not None
    assert (piece.slot, piece.level, piece.main_stat, piece.locked, piece.equipped) == ("ring", 15, "attack_percent", True, False)
    assert piece.substats == {"crit_damage": 6, "crit_chance": 9, "health_percent": 22, "speed": 11}


def test_an_orange_badge_below_plus_15(ocr):
    piece = read_piece(frame("panel_weapon_4"), ocr)
    assert piece is not None and piece.level == 4
    assert piece.substats == {"crit_chance": 4, "effect_resistance": 12, "attack_percent": 6, "speed": 4}


def test_a_single_digit_percent_is_not_read_with_an_extra_one(ocr):
    piece = read_piece(frame("panel_boots_15_seven_percent"), ocr)
    assert piece is not None and piece.main_stat == "speed"
    assert piece.substats["defense_percent"] == 7


def test_pieces_under_epic_have_fewer_substats(ocr):
    rare = read_piece(frame("panel_boots_rare_two_subs"), ocr)
    assert rare is not None and (rare.grade, rare.level) == ("rare", 0)
    assert rare.substats == {"speed": 3, "defense": 27}
    heroic = read_piece(frame("panel_necklace_heroic_flat_sub"), ocr)
    assert heroic is not None and heroic.grade == "heroic"
    assert heroic.substats == {"health": 192, "speed": 2, "health_percent": 7}


def test_a_screen_without_the_panel_is_not_read(ocr):
    assert read_piece(np.zeros((720, 1280, 3), np.uint8), ocr) is None


def test_the_grid_scroll_is_measured_from_the_frames():
    assert scroll_shift(frame("grid_a"), frame("grid_b_scrolled_200")) == 200
    assert scroll_shift(frame("grid_a"), frame("grid_a")) == 0


def test_cards_and_empty_slots():
    grid = frame("grid_a")
    assert has_card(grid, 190, 200)
    assert not has_card(np.zeros((720, 1280, 3), np.uint8), 190, 200)


def test_rows_in_reach_follow_the_scroll():
    assert visible_rows(0) == [(0, 200), (1, 329), (2, 458), (3, 587)]
    assert [row for row, _ in visible_rows(387)] == [3, 4, 5, 6]


def test_the_sort_never_touches_worn_or_locked_pieces():
    worn = PieceReading("epic", "boots", 15, "speed", {"attack_percent": 4}, equipped=True)
    locked = PieceReading("epic", "boots", 15, "speed", {"attack_percent": 4}, locked=True)
    bad = PieceReading("epic", "boots", 15, "speed", {"attack_percent": 4})
    fast = PieceReading("epic", "weapon", 15, "attack", {"speed": 18, "attack_percent": 12}, set_name="speed")
    assert verdict(worn, EARLY)[0] == "leave"
    assert verdict(locked, EARLY)[0] == "leave"
    assert verdict(bad, EARLY)[0] == "sell"
    assert verdict(fast, EARLY)[0] == "lock"

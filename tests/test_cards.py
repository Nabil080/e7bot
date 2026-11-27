"""Reading the small Inventory cards, +0 and enhanced: every card here was checked against the detail panel."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.cards import ART_PORTRAIT, CardModels, art_without_portrait, read_card, stat_key, trusted
from core.config import load_config
from core.vision import Ocr

ROOT = Path(__file__).parent.parent
CARDS = Path(__file__).parent / "fixtures" / "cards"
PAD = 20


@pytest.fixture(scope="module")
def models() -> CardModels:
    loaded = CardModels.load(ROOT / "templates" / "cards" / "models.npz")
    assert loaded is not None
    return loaded


@pytest.fixture(scope="module")
def ocr() -> Ocr:
    return Ocr(load_config(ROOT / "config.yaml").vision)


def cases():
    return json.loads((CARDS / "truth.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["image"])
def test_a_card_reads_as_its_panel_or_says_it_is_unsure(case, models, ocr):
    card = cv2.imread(str(CARDS / case["image"]))
    frame = cv2.copyMakeBorder(card, PAD, PAD, PAD, PAD, cv2.BORDER_REPLICATE)
    result = read_card(frame, (50 + PAD, 50 + PAD), ocr, models)
    truth = case["truth"]
    if truth["locked"]:
        assert result.status == "locked"
        return
    # Never a wrong reading: the right one, or "unsure" (the bot then leaves the piece alone).
    assert result.status in ("enhanced" if truth["level"] > 0 else "new", "unsure")
    if result.status != "unsure":
        r = result.reading
        assert (r.grade, r.slot, r.level, r.main_stat, r.set_name) == (truth["grade"], truth["slot"], truth["level"], truth["main"], truth["set"])
        assert sorted(r.substats.items()) == sorted((key, value) for key, value in truth["subs"])


def test_most_fixture_cards_are_read_in_full(models, ocr):
    read, other_levels = 0, 0
    for case in cases():
        card = cv2.imread(str(CARDS / case["image"]))
        frame = cv2.copyMakeBorder(card, PAD, PAD, PAD, PAD, cv2.BORDER_REPLICATE)
        result = read_card(frame, (50 + PAD, 50 + PAD), ocr, models)
        read += result.status != "unsure"
        other_levels += result.why.startswith("item level")
    # Cards 06 to 08 are level 70 gear: only levels 85 and 88 are judged.
    assert other_levels == 3
    assert read >= len(cases()) - other_levels - 2


def test_the_item_level_is_read_on_the_card(models):
    from core.cards import read_item_level
    for image in sorted((ROOT / "templates" / "cards" / "levels").glob("*.png")):
        card = cv2.copyMakeBorder(cv2.imread(str(image)), 0, 80, 0, 120, cv2.BORDER_REPLICATE)
        level = read_item_level(card, models)
        # Never a wrong level: the right one, or None (the card is then left alone).
        assert level in (int(image.stem.split("_")[0]), None), image.name


def test_a_locked_card_tells_its_level_by_its_second_digit(models):
    from core.cards import LOCKED_LEVELS, crop_card, read_locked_level
    for image in sorted((ROOT / "templates" / "cards" / "levels").glob("*.png")):
        card = cv2.copyMakeBorder(cv2.imread(str(image)), 0, 80, 0, 120, cv2.BORDER_REPLICATE)
        text = image.stem.split("_")[0]
        # Only the second digit is seen: 70 and 80 would read as 90, 78 as 88 (old gear, rarely locked at +15).
        assert read_locked_level(card, models) in (LOCKED_LEVELS.get(text[1]), None), image.name
    # A real locked card: its padlock covers the "8" of 85.
    frame = cv2.imread(str(ROOT / "tests" / "fixtures" / "gear" / "bulk_plus4_boots_stove.png"))
    assert read_locked_level(crop_card(frame, (59, 181)), models) == 85


def test_reforged_cards_are_read_only_when_asked(models, ocr):
    from core.cards import CardReading
    from core.gear import unreforged
    # A level 90 card is skipped by the gear modes; the account evaluation reads it and brings it back to level 85.
    reading = CardReading("epic", "weapon", "attack", {"attack_percent": 19, "crit_chance": 6, "crit_damage": 14, "speed": 16}, "speed", 15, 90)
    assert reading.piece().item_level == 90
    assert unreforged("epic", reading.substats)[0]["speed"] == 13


def test_level_88_gear_is_not_reforged():
    from core.gear import Piece, speed_potential
    subs = {"speed": 4, "attack_percent": 7, "crit_chance": 5, "health_percent": 7}
    plain = Piece("boots", "speed", 0, subs, "epic", "speed", {name: 0 for name in subs})
    final = Piece("boots", "speed", 0, subs, "epic", "speed", {name: 0 for name in subs}, item_level=88)
    # Five rolls left at 4 speed each, plus the reforge on level 85 gear only.
    assert speed_potential(plain) == 4 + 20 + 4
    assert speed_potential(final) == 4 + 20


def test_missing_templates_are_a_warning_not_a_crash(tmp_path, caplog):
    assert CardModels.load(tmp_path / "missing.npz", logger=__import__("logging").getLogger("test")) is None
    assert "card templates missing" in caplog.text


def test_crit_chance_needs_a_clear_lead_over_crit_damage():
    assert trusted([("crit_chance", 0.9), ("crit_damage", 0.75), ("speed", 0.2)]) is True
    assert trusted([("crit_chance", 0.9), ("crit_damage", 0.8), ("speed", 0.2)]) is False
    assert trusted([("speed", 0.9), ("health", 0.89)]) is False


def test_a_value_form_that_cannot_exist_is_refused():
    assert stat_key("attack", True) == "attack_percent"
    assert stat_key("attack", False) == "attack"
    assert stat_key("speed", True) is None
    assert stat_key("crit_chance", False) is None


def test_a_red_badge_is_plus_15_and_an_orange_one_is_read(models):
    from core.cards import read_badge
    plus15 = cv2.imread(str(CARDS / "card_e00.png"))
    assert read_badge(plus15, models) == 15
    for image in sorted((ROOT / "templates" / "cards" / "badges").glob("*.png")):
        level = int(image.stem.split("_")[0])
        card = np.zeros((120, 180, 3), np.uint8)
        badge = cv2.imread(str(image))
        card[0 : badge.shape[0], 56 : 56 + badge.shape[1]] = badge
        assert read_badge(card, models) == level, image.name


def test_a_value_followed_by_a_digit_it_cannot_tell_is_not_a_percent():
    from core.cards import estimate_rolls
    assert estimate_rolls("epic", 15, {"crit_damage": 21, "effect_resistance": 5, "speed": 13, "effectiveness": 5}) == {
        "crit_damage": 2, "effect_resistance": 0, "speed": 3, "effectiveness": 0,
    }


def test_an_empty_frame_is_unsure(models, ocr):
    frame = np.zeros((200, 260, 3), np.uint8)
    assert read_card(frame, (70, 70), ocr, models).status == "unsure"


def test_the_account_evaluation_reads_locked_otherworldly_and_88_cards(models, ocr):
    from core.cards import read_card
    # The All tab sorted by Max Enhance on a real account (Steam): rows at 199, 328, 457, 586. The scan reads each
    # slot's tab, which gives the slot.
    frame = cv2.imread(str(ROOT / "tests" / "fixtures" / "gear" / "account_all_top.jpg"))
    # A locked Otherworldly necklace: its padlock hides the 8 of 85.
    necklace = read_card(frame, (190, 328), ocr, models, slot="necklace", read_kept=True, otherworldly_epic=True, reforged=True)
    assert necklace.status == "locked" and necklace.reading is not None
    assert (necklace.reading.main_stat, necklace.reading.level, necklace.reading.item_level) == ("defense_percent", 15, 85)
    assert necklace.reading.substats == {"speed": 7, "crit_damage": 17, "crit_chance": 10, "effect_resistance": 15}
    # A locked level 88 weapon, checked by eye: health 14 %, attack 17 %, speed 13, crit chance 12 %, Lifesteal set.
    weapon = read_card(frame, (530, 328), ocr, models, slot="weapon", read_kept=True, otherworldly_epic=True, reforged=True)
    assert weapon.reading is not None and (weapon.reading.item_level, weapon.reading.set_name) == (88, "lifesteal")
    assert weapon.reading.substats == {"health_percent": 14, "attack_percent": 17, "speed": 13, "crit_chance": 12}
    # A locked level 85 weapon: the second digit, 5.
    locked = read_card(frame, (360, 586), ocr, models, slot="weapon", read_kept=True, reforged=True)
    assert locked.reading is not None and locked.reading.item_level == 85
    assert locked.reading.substats == {"attack_percent": 20, "speed": 12, "crit_chance": 9, "crit_damage": 6}


def test_a_hero_portrait_does_not_change_the_artwork_outline():
    rng = np.random.default_rng(1)
    art = rng.integers(0, 255, (58, 48, 3), dtype=np.uint8)
    worn = art.copy()
    x, y, _ = ART_PORTRAIT
    cv2.circle(worn, (x, y), 17, (40, 200, 230), -1)
    assert float(art_without_portrait(art) @ art_without_portrait(worn)) > 0.99


def test_level_88_and_90_artworks_are_known(models):
    # Their art differs from level 85's: the examples cover every slot.
    assert models.art_extra_vectors is not None
    assert set(models.art_extra_slots) == {"weapon", "helmet", "armor", "necklace", "ring", "boots"}

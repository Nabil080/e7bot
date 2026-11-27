"""The gear advisor on real screenshots: it finds the screen, reads every piece it can, and judges as the gear modes do."""

import base64
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.advisor import (
    Advice,
    Advisor,
    AdviceBoard,
    ScreenAdvice,
    alike,
    grid_rows,
    small_copy,
    tag_for,
    to_game_size,
    verdict,
)
from core.cards import CardModels
from core.config import load_config
from core.gear import GearPolicy, Piece
from core.gear_tags import COLORS, Tag, tag_rect
from core.panel import advice_metrics, advice_view
from core.vision import Ocr

ROOT = Path(__file__).parent.parent
GEAR = Path(__file__).parent / "fixtures" / "gear"
POLICY = GearPolicy(min_speed_speed_set=17, min_speed_other_sets=18)


@pytest.fixture(scope="module")
def advisor() -> Advisor:
    models = CardModels.load(ROOT / "templates" / "cards" / "models.npz")
    assert models is not None
    return Advisor(Ocr(load_config(ROOT / "config.yaml").vision), models, POLICY)


def frame(name: str) -> np.ndarray:
    image = cv2.imread(str(GEAR / name))
    assert image is not None, name
    return image


def test_the_inventory_grid_and_its_detail_panel(advisor):
    advice = advisor.look(frame("grid_two_worn.png"))
    assert advice.screen == "inventory"
    # Four full rows of four cards: the top one sits higher than in Arrange, the fifth is cut by the screen's edge.
    assert len(advice.cards) == 16
    assert all(card.read for card in advice.cards)
    # The panel shows the selected card: an epic Fervor necklace with 4 speed, worth enhancing.
    focus = advice.focus
    assert focus is not None and focus.focus and focus.where == "Detail panel"
    assert (focus.grade, focus.slot, focus.level, focus.set_name, focus.main_stat) == ("epic", "necklace", 0, "fervor", "defense_percent")
    assert focus.substats == {"health_percent": 6, "crit_damage": 5, "attack_percent": 7, "speed": 4}
    # Fast enough, but good for a role first: the role is the reason, speed comes as a bonus.
    # Fervor is a damage dealers' set, and a defense % main stat serves no damage dealer: only its speed keeps it.
    assert focus.action == "speed" and focus.rule == "speed potential 28 (needs 17)"
    assert focus.reason.startswith("Speed piece: 4 speed now, and up to 28 after reforge")
    # It is the selected card, top right: marked, and its box is the panel's piece's.
    marked = [card for card in advice.cards if card.focus]
    assert [card.where for card in marked] == ["Row 1, card 4"] and marked[0].box == focus.box


def test_worn_pieces_are_judged_and_flagged(advisor):
    advice = advisor.look(frame("grid_two_worn.png"))
    worn = [card for card in advice.cards if card.equipped]
    assert len(worn) == 2
    assert {(card.slot, card.level) for card in worn} == {("boots", 15), ("armor", 15)}
    assert all(card.action for card in worn)


def test_cards_follow_the_scroll():
    models = CardModels.load(ROOT / "templates" / "cards" / "models.npz")
    image = frame("grid_two_worn.png")
    rows = grid_rows(image, models)
    shifted = np.zeros_like(image)
    shifted[:-60] = image[60:]
    # The grid moved up 60 pixels: so did its rows (a row may leave or come into the band read).
    moved = grid_rows(shifted, models)
    assert set(y - 60 for y in rows) & set(moved)
    assert all(abs(a - b) % 129 in (0,) for a in moved for b in moved)


def test_arrange_reads_the_untouched_cards(advisor):
    advice = advisor.look(frame("arrange_one_ticked.png"))
    assert advice.screen == "arrange"
    read = [card for card in advice.cards if card.read]
    assert len(read) >= 12
    # The +15 pieces are locked or sold (some keep only with a stone the setting does not spend on them), the +6 ones are
    # worth going on.
    assert all(card.action in ("lock", "sell") for card in read if card.level == 15)
    assert sum(card.action == "lock" for card in read if card.level == 15) >= 6
    assert all(card.action in ("enhance", "speed") for card in read if card.level in (6, 7))


def test_the_enhance_screen_knows_the_roll_counts(advisor):
    advice = advisor.look(frame("enhance_heroic_boots_rolled.png"))
    assert advice.screen == "enhance" and not advice.cards
    focus = advice.focus
    assert (focus.grade, focus.slot, focus.level, focus.main_stat) == ("heroic", "boots", 4, "defense_percent")
    assert focus.rolls == {"speed": 0, "defense": 0, "effect_resistance": 1}
    assert focus.action == "sell"


def test_a_locked_piece_on_the_enhance_screen(advisor):
    focus = advisor.look(frame("enhance_rare_weapon_locked.png")).focus
    assert focus.locked and focus.action == "extract" and focus.rule == "rare piece"
    assert focus.reason.startswith("Blue (rare) piece")


def test_ten_crafted_pieces(advisor):
    advice = advisor.look(frame("craft_result_ten.png"))
    assert advice.screen == "craft" and advice.focus is None
    assert len(advice.cards) == 10 and all(card.read for card in advice.cards)
    actions = [card.action for card in advice.cards]
    # The kept ones are all kept for their speed alone.
    assert "speed" in actions and "extract" in actions and "enhance" not in actions
    assert all(card.slot == "weapon" and card.level == 0 for card in advice.cards)


def test_a_single_crafted_piece(advisor):
    advice = advisor.look(frame("craft_result_single_heroic.png"))
    assert advice.screen == "craft" and not advice.cards
    focus = advice.focus
    assert (focus.grade, focus.slot, focus.set_name, focus.action) == ("heroic", "weapon", "hit", "extract")


def test_the_bulk_screen_reads_its_slots_with_their_gains(advisor):
    advice = advisor.look(frame("bulk_after_plus3.png"))
    assert advice.screen == "bulk"
    assert [card.where for card in advice.cards] == ["Slot 1", "Slot 2", "Slot 3"]
    boots, armor, ring = advice.cards
    assert (boots.slot, boots.level, boots.main_stat, boots.substats["defense_percent"]) == ("boots", 3, "speed", 11)
    assert (armor.slot, armor.level, armor.substats["speed"]) == ("armor", 3, 3)
    assert (ring.slot, ring.level, ring.main_stat) == ("ring", 0, "effect_resistance")


def test_the_bulk_screen_flags_a_locked_slot(advisor):
    advice = advisor.look(frame("bulk_one_locked.png"))
    assert [card.locked for card in advice.cards] == [True]


def test_another_screen_shows_nothing(advisor):
    image = frame("grid_two_worn.png").copy()
    image[:70] = 0
    assert advisor.look(image).screen == "other"


def test_a_still_screen_is_not_read_twice(advisor, monkeypatch):
    image = frame("craft_result_ten.png")
    advisor.look(image)
    calls = []
    monkeypatch.setattr("core.advisor.read_result_card", lambda *args, **kwargs: calls.append(1))
    advice = advisor.look(image)
    assert len(advice.cards) == 10 and not calls


def test_the_verdict_locks_only_finished_pieces():
    finished = Piece("weapon", "attack", 15, {"speed": 18, "attack_percent": 12, "crit_chance": 9, "crit_damage": 14}, set_name="speed")
    assert verdict(finished, POLICY)[0] == "lock"
    at_twelve = Piece("weapon", "attack", 12, {"speed": 18, "attack_percent": 12, "crit_chance": 9, "crit_damage": 14}, set_name="speed")
    assert verdict(at_twelve, GearPolicy(max_level=12))[0] == "keep"


def test_tags_say_what_the_rules_think_of_a_worn_piece():
    box = (100, 150, 180, 120)
    sell = Advice("Row 1, card 1", box, "sell", "no potential", slot="boots")
    assert tag_for(sell).text == "SELL" and tag_for(sell).action == "sell"
    worn = Advice("Row 1, card 1", box, "sell", "no potential", slot="boots", equipped=True)
    assert tag_for(worn).text == "could sell" and tag_for(worn).action == ""
    assert tag_for(Advice("Detail panel", None, "keep", "ok")) is None
    assert tag_for(Advice("Row 1, card 2", box, "", "grade")).text == "?"
    # Rolled for its speed alone: the tag says so, in the enhance colour.
    assert tag_for(Advice("Row 1, card 3", box, "speed", "speed potential 23")) == Tag(box, "speed", "SPEED")
    assert COLORS["speed"] == COLORS["enhance"]
    # The tag sits on the card's icon.
    left, top, right, bottom = tag_rect(box)
    assert box[0] <= left < right <= box[0] + 100 and box[1] < top < bottom <= box[1] + box[3]


def test_frames_of_another_size_are_scaled():
    big = np.zeros((1080, 1920, 3), np.uint8)
    assert to_game_size(big).shape == (720, 1280, 3)
    same = np.zeros((720, 1280, 3), np.uint8)
    assert to_game_size(same) is same


def test_a_moving_screen_is_not_still():
    image = frame("grid_two_worn.png")
    moved = np.zeros_like(image)
    moved[:-129] = image[129:]
    assert alike(small_copy(image), small_copy(image.copy()))
    assert not alike(small_copy(image), small_copy(moved))


def test_the_window_counts_and_describes_the_pieces(advisor):
    advice = advisor.look(frame("grid_two_worn.png"))
    numbers = advice_metrics(advice)
    total = sum(int(value) for value, _ in numbers)
    # The panel's piece is one of the cards: counted once.
    assert total == len([card for card in advice.cards if card.read])
    view = advice_view(advice, "")
    assert view["screen"] == "Inventory" and view["focus"]["focus"]
    worn = [card for card in view["cards"] if "Worn" in card["flags"]]
    assert len(worn) == 2
    first = view["cards"][0]
    assert first["title"].startswith(("Epic", "Heroic", "Rare")) and first["subs"] and first["label"]


def test_the_board_hands_the_latest_advice_over():
    board = AdviceBoard()
    assert board.latest() == (None, "")
    advice = ScreenAdvice("inventory")
    board.publish(advice, "note")
    assert board.latest() == (advice, "note")
    assert advice_view(None, "Waiting for the game window.")["note"] == "Waiting for the game window."


def test_otherworldly_cards_and_plus_10_badges_are_read(advisor):
    advice = advisor.look(frame("grid_otherworldly_steam.png"))
    by_place = {card.where: card for card in advice.cards}
    # The purple Otherworldly frame left the grade open at +12: those red pieces are read as epic.
    assert all(by_place[f"Row 3, card {column}"].grade == "epic" for column in (2, 3, 4))
    assert (by_place["Row 1, card 2"].level, by_place["Row 4, card 2"].level) == (10, 10)
    # The selected card's frame hides its icons: it takes the detail panel's piece, found by its place.
    selected = by_place["Row 3, card 1"]
    assert selected.focus and selected.read and selected.main_stat == "effectiveness" and selected.level == 12
    assert advice.focus.box == selected.box
    # The padlock hides the item level's first digit: the second one tells it is 85.
    locked = by_place["Row 2, card 4"]
    assert locked.locked and locked.read and (locked.slot, locked.main_stat, locked.level, locked.item_level) == ("ring", "effect_resistance", 12, 85)
    assert locked.substats == {"speed": 2, "crit_damage": 17, "defense_percent": 8, "health_percent": 19}
    assert tag_for(locked).text in ("KEPT", "ENHANCE", "SPEED", "could sell", "could extract")
    assert sum(card.read for card in advice.cards) == 16


def test_reasons_are_plain_sentences(advisor):
    advice = advisor.look(frame("grid_otherworldly_steam.png"))
    necklace = next(card for card in advice.cards if card.where == "Row 1, card 4")
    assert necklace.rule == "attack or health necklace with crit substats"
    assert necklace.reason.startswith("Crit is wasted on an attack % necklace (crit chance and crit damage)")
    assert all("_" not in card.reason for card in advice.cards if card.read)


def test_the_game_is_kept_at_1280x720(monkeypatch):
    import logging
    from core import advisor as module

    calls = []

    class Game:
        def verify(self):
            calls.append("verify")
            raise module.WindowError("no luck")

    module.keep_size(Game(), logging.getLogger("test"))
    assert calls == ["verify"]


def tick(image: np.ndarray, card) -> np.ndarray:
    """Return the frame with a card ticked as the game does: a green check on its icon, the card dimmed."""
    x, y, width, height = card.box
    ticked = image.copy()
    area = ticked[y : y + height, x : x + width]
    area[:] = (area * 0.45).astype(np.uint8)
    cx, cy = x + 50, y + 50
    ticked[cy + 5 : cy + 25, cx + 5 : cx + 40] = (60, 220, 60)
    return ticked


def test_a_ticked_card_keeps_its_verdict(advisor):
    image = frame("grid_two_worn.png")
    before = {card.where: card for card in advisor.look(image).cards}
    card = before["Row 3, card 1"]
    ticked_frame = tick(image, card)
    after = {card.where: card for card in advisor.look(ticked_frame).cards}
    kept = after["Row 3, card 1"]
    assert kept.ticked and kept.read and (kept.action, kept.substats) == (card.action, card.substats)


def test_the_tick_memory_follows_the_scroll(advisor, monkeypatch):
    read = Advice("Row 3, card 1", (140, 399, 180, 120), "enhance", "fast", slot="boots", substats={"speed": 4.0})
    advisor.grid_frame, advisor.grid_cards = np.zeros((720, 1280, 3), np.uint8), [read]
    unread_card = Advice("Row 2, card 1", (140, 270, 180, 120), "", "item level None")
    monkeypatch.setattr("core.advisor.scroll_shift", lambda before, after: 129)
    kept = advisor.before_tick(np.zeros((720, 1280, 3), np.uint8), unread_card)
    assert kept.ticked and kept.substats == {"speed": 4.0} and kept.box == unread_card.box and kept.where == "Row 2, card 1"
    # Not scrolled: nothing was there before, the card stays unread.
    monkeypatch.setattr("core.advisor.scroll_shift", lambda before, after: 0)
    assert not advisor.before_tick(np.zeros((720, 1280, 3), np.uint8), unread_card).read


def test_a_tick_keeps_the_tags_a_scroll_hides_them(advisor):
    from core.advisor import local_change

    image = frame("grid_two_worn.png")
    card = advisor.look(image).cards[5]
    assert local_change(small_copy(image), small_copy(tick(image, card)))
    scrolled = np.zeros_like(image)
    scrolled[:-129] = image[129:]
    assert not local_change(small_copy(image), small_copy(scrolled))
    bulk = frame("bulk_after_plus3.png")
    pressed = bulk.copy()
    pressed[650:695, 20:190] = 230
    assert local_change(small_copy(bulk), small_copy(pressed))


def test_bulk_badges_and_bigger_main_values_on_stove(advisor):
    for name, level in (("bulk_plus4_boots_stove.png", 4), ("bulk_plus6_boots_stove.png", 6)):
        cards = advisor.look(frame(name)).cards
        boots = next(card for card in cards if card.where == "Slot 6")
        assert (boots.slot, boots.main_stat, boots.level) == ("boots", "speed", level)
        # Only the locked, worn +12 weapon stays unread (its padlock hides the item level).
        assert [card.where for card in cards if not card.read] == ["Slot 1"]


def test_the_window_shows_a_grid_like_the_game_and_one_piece_in_detail(advisor):
    advice = advisor.look(frame("grid_otherworldly_steam.png"))
    assert all(card.thumb.startswith("data:image/png;base64,") for card in advice.cards)
    assert [card.cell for card in advice.cards[:5]] == [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0)]
    view = advice_view(advice, "")
    # The game's selected card is shown by default, outlined in the grid.
    assert view["detail"]["where"] == "Row 3, card 1" and [tile["where"] for tile in view["grid"] if tile["selected"]] == ["Row 3, card 1"]
    # A card clicked in the window wins.
    armor = advice_view(advice, "", "Row 1, card 2")["detail"]
    # A Fervor armor is judged for damage dealers only (the user's set table): health % and effectiveness are useless,
    # and a good damage armor (no attack to roll) is rare, so it asks 3.6 points less, still out of reach.
    assert armor["summary"] == "No potential left" and armor["label"] == "Sell"
    states = {row["name"]: row["state"] for row in armor["stats"]}
    assert states == {"Effectiveness": "dead", "Health %": "dead", "Crit damage": "good", "Speed": "good"}
    assert [line["role"] for line in armor["roles"]] == ["damage dealer", "speed"] and not armor["roles"][0]["passes"]
    assert "(−3.6: a good armor for this role is harder to get)" in armor["roles"][0]["text"]
    # Each card carries its role's short name and its efficiency, rounded, for the bar on the card.
    tiles = {tile["where"]: tile for tile in view["grid"]}
    assert (tiles["Row 3, card 1"]["role"], tiles["Row 3, card 1"]["efficiency"]) == ("Dmg sure crit", 78)
    assert all(tile["efficiency"] is None and tile["role"] == "" for tile in view["grid"] if not tile["action"])


def test_card_pictures_are_never_shrunk_below_their_window_size(advisor):
    advice = advisor.look(frame("grid_otherworldly_steam.png"))
    png = base64.b64decode(advice.cards[0].thumb.split(",", 1)[1])
    picture = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    # The window shows a card's picture at 80 pixels: a smaller one would be blown up, blurry.
    assert picture.shape[:2] == (80, 80)


def test_role_lines_name_every_role_tried():
    from core.explain import role_lines

    subs = {"attack_percent": 4, "crit_damage": 5, "health_percent": 22, "effectiveness": 5}
    boots = Piece("boots", "speed", 6, subs, "epic", "hit", {"attack_percent": 0, "crit_damage": 0, "health_percent": 2, "effectiveness": 0})
    lines = role_lines(boots, GearPolicy(min_speed_speed_set=16))
    # The roles that pass come first, then the others with what stops them.
    # Speed boots: speed is the main stat, the speed line says it does not carry them.
    assert [(line.role, line.passes) for line in lines] == [("debuffer", True), ("burner", True), ("speed", False)]
    # Speed boots with a fitting main stat are rare: 10 points less than a helmet.
    assert lines[0].text == "78.8% / 55% (score 39 of 49.5)" and lines[1].text.startswith("70.7% / 55%")


def test_bulk_values_read_on_a_warm_tinted_screen(advisor):
    # The user's screen tints white warm (red 254, blue 162): the digits must not be taken for the orange gains.
    cards = advisor.look(frame("bulk_plus9_weapons_warm_stove.png")).cards
    assert all(card.read for card in cards) and [card.level for card in cards] == [9] * 5
    assert cards[3].substats == {"health": 534, "crit_damage": 6, "crit_chance": 9, "speed": 3}
    assert cards[4].substats == {"crit_chance": 10, "crit_damage": 11, "health": 158, "health_percent": 12}


def test_the_tags_over_the_game_wear_the_windows_verdict_colours():
    import re
    import win32api
    from core.gear_tags import COLORS

    css = (Path(__file__).parent.parent / "ui" / "app.css").read_text(encoding="utf-8")

    def token(name: str) -> int:
        value = re.search(rf"--{name}: #([0-9a-f]{{6}})", css).group(1)
        return win32api.RGB(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    assert COLORS["enhance"][0] == token("green")
    assert COLORS["keep"][0] == COLORS["lock"][0] == token("gold")
    assert COLORS["sell"][0] == COLORS["extract"][0] == token("coral")


def test_level_90_pieces_are_judged_with_their_slot_from_the_open_tab(advisor):
    from core.advisor import open_slot_tab

    shot = frame("grid_helmets_locked_90_stove.png")
    assert open_slot_tab(shot) == "helmet" and open_slot_tab(frame("grid_otherworldly_steam.png")) is None
    cards = {card.where: card for card in advisor.look(shot).cards}
    # Two reforged helmets, locked: their padlock hides the first digit, the second tells 90.
    for where in ("Row 2, card 2", "Row 2, card 3"):
        assert cards[where].read and cards[where].locked and (cards[where].slot, cards[where].item_level) == ("helmet", 90)
    # Its speed is final: no reforge counted again on a level 90 piece (20 on the card, 20 at best).
    assert cards["Row 2, card 2"].substats["speed"] == 20 and cards["Row 2, card 2"].speed_reach == 20
    # Worn but not locked: a padlock only where the game draws one.
    assert [cards[f"Row 1, card {n}"].locked for n in (1, 2, 3, 4)] == [True, False, True, False]


def test_level_90_pieces_are_read_on_the_all_tab_too(advisor):
    from core.advisor import SLOT_TABS, open_slot_tab

    # The same screen with no slot tab lit (the All tab): the artwork alone must tell the slot, a hero's portrait
    # over it or not.
    shot = frame("grid_helmets_locked_90_stove.png").copy()
    for x, y in SLOT_TABS.values():
        shot[y - 20 : y + 20, x - 35 : x + 35] = 30
    assert open_slot_tab(shot) is None
    cards = {card.where: card for card in Advisor(advisor.ocr, advisor.models, POLICY).look(shot).cards}
    for where in ("Row 2, card 2", "Row 2, card 3"):
        assert cards[where].read and (cards[where].slot, cards[where].item_level) == ("helmet", 90), cards[where].reason
    assert all(card.slot == "helmet" for card in cards.values() if card.read)


def test_a_locked_piece_worth_keeping_says_kept_not_lock(advisor):
    cards = {card.where: card for card in advisor.look(frame("grid_helmets_locked_90_stove.png")).cards}
    assert tag_for(cards["Row 1, card 1"]).text == "KEPT" and tag_for(cards["Row 1, card 1"]).action == ""
    # Worn, not locked, worth keeping: locking it is still advice.
    assert tag_for(cards["Row 1, card 2"]).text == "LOCK"


def test_dimmed_cards_keep_their_item_level(advisor):
    # Ticked cards are drawn at half brightness, with a check mark: their level is read all the same.
    fresh = Advisor(advisor.ocr, advisor.models, POLICY)
    cards = {card.where: card for card in fresh.look(frame("bulk_select_three_ticked_stove.png")).cards}
    for where in ("Row 1, card 2", "Row 2, card 1"):
        assert not cards[where].rule.startswith("item level"), cards[where].rule
    assert "Level 78" in cards["Row 3, card 3"].reason
    # Every ticked card has the selected card's yellow frame: none takes the detail panel's piece (the 78 hood).
    assert not any(card.read and card.substats == {"health_percent": 7, "attack_percent": 7, "defense_percent": 7, "speed": 4} for card in cards.values())
    # On the Bulk screen, a ticked slot keeps the reading from before its tick: the same piece, its stat lines match.
    fresh = Advisor(advisor.ocr, advisor.models, POLICY)
    before = {card.where: card for card in fresh.look(frame("bulk_three_stove.png")).cards}
    after = {card.where: card for card in fresh.look(frame("bulk_two_ticked_dim_stove.png")).cards}
    assert after["Slot 2"].read and after["Slot 2"].ticked and after["Slot 2"].substats == before["Slot 2"].substats


def test_role_lines_follow_the_piece_best_fit_first():
    from core.explain import role_lines

    piece = Piece("armor", "defense", 15, {"speed": 9, "crit_chance": 12, "crit_damage": 14, "effectiveness": 8}, set_name="speed")
    lines = role_lines(piece, GearPolicy())
    assert [line.role for line in lines[:3]] == ["damage dealer", "crit bruiser", "defense bruiser"]
    # Rejected roles: short of efficiency first, then by their useless stats, fewest first; speed always last.
    useless = [int(line.text.split(" of its stats")[0]) for line in lines if " of its stats are useless" in line.text]
    assert useless == sorted(useless) and lines[-1].role == "speed"

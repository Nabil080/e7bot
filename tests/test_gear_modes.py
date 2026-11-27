"""The gear modes: the sweeps' plan and choices, the selections' ticks and counts, and the Bulk screen."""

import logging
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.cards import CardModels, CardReading, CardResult, read_bulk_card
from core.config import GearConfig, load_config
from core.enhance import EnhanceReading, plausible, read_amount, read_enhance
from core.vision import Ocr
from states.gear import (
    BULK_ARRANGE_COUNT_REGION,
    BULK_CARD_CENTER,
    BULK_COST_REGION,
    build_plan,
    card_ticked,
    end_sweep,
    gear_data,
    judge_card,
    pieces_left,
    selection_count,
    slot_locked,
    slot_point,
    slot_selected,
    split_refused,
    TAP_TO_CLOSE_REGION,
    RESULT_SELECTED,
    RESULT_SELECTED_REGION,
    decide,
    summary,
    wants,
)

ROOT = Path(__file__).parent.parent
GEAR = Path(__file__).parent / "fixtures" / "gear"


def frame(name: str) -> np.ndarray:
    image = cv2.imread(str(GEAR / f"{name}.png"))
    assert image is not None, name
    return image


@pytest.fixture(scope="module")
def ocr() -> Ocr:
    return Ocr(load_config(ROOT / "config.yaml").vision)


@pytest.fixture(scope="module")
def models() -> CardModels:
    loaded = CardModels.load(ROOT / "templates" / "cards" / "models.npz")
    assert loaded is not None
    return loaded


def make_ctx(goal: str = "gear_roll", log_dir: Path = Path("no-logs-in-tests"), **gear) -> SimpleNamespace:
    """A context with only what the sweeps' bookkeeping uses."""
    halted: list[str] = []
    ctx = SimpleNamespace(
        goal=goal,
        route_data={},
        config=SimpleNamespace(gear=GearConfig(**gear), logging=SimpleNamespace(log_dir=log_dir)),
        logger=logging.getLogger("test"),
        on_gear=None,
        halted=halted,
        progressed=False,
        frame=None,
    )
    ctx.halt = lambda why: halted.append(why) or "halted"
    return ctx


def reading(level: int = 0, **changes) -> CardReading:
    values = dict(
        grade="epic", slot="boots", main_stat="speed", set_name="speed",
        substats={"attack_percent": 7, "crit_chance": 5, "crit_damage": 7, "health_percent": 7},
    )
    values.update(changes)
    return CardReading(**values, level=level)


JUNK = dict(grade="heroic", slot="ring", main_stat="defense", substats={"attack": 40, "health": 170, "effect_resistance": 5}, set_name="attack")


# The plan.


def test_sort_sells_then_extracts_and_roll_rolls_after():
    # The sale comes first: on a grid sorted by level it stops at the first +0 piece.
    assert build_plan(make_ctx("gear_sort")) == [{"kind": "arrange", "action": "sell"}, {"kind": "arrange", "action": "extract"}]
    assert build_plan(make_ctx("gear_roll"))[-1] == {"kind": "bulk"}
    # Fresh pieces sold instead of extracted: one sale for both.
    assert build_plan(make_ctx("gear_sort", new_bad_action="sell")) == [{"kind": "arrange", "action": "sell"}]
    # Only new pieces: the enhanced ones are never sold.
    assert build_plan(make_ctx("gear_sort", only_new=True)) == [{"kind": "arrange", "action": "extract"}]


def test_crafted_pieces_are_only_sorted():
    # An old setting asking to roll them reads as sorting.
    assert GearConfig(craft_then="roll").craft_then == "sort"
    # A crafted piece worth rolling is kept for the Roll mode.
    assert decide(make_ctx("gear_craft"), reading())[0] == "keep"


def test_the_crafted_pieces_are_read_on_the_result_screen(models, ocr):
    from core.cards import read_result_card, result_icons
    image = frame("craft_result_ten")
    corners = result_icons(image)
    assert len(corners) == 10
    readings = [read_result_card(image, corner, ocr, models, slot="weapon").reading for corner in corners]
    assert readings[0].describe() == "heroic weapon +0 speed set, main attack: crit_damage 7, effectiveness 8, effect_resistance 8"
    assert readings[5].substats == {"health": 188, "health_percent": 6, "attack_percent": 4}
    assert [r.grade for r in readings].count("rare") == 4
    # The result's Arrange counts its ticks; the pieces extracted leave the screen.
    selected = RESULT_SELECTED.search(ocr.read(frame("craft_result_nine_ticked"), RESULT_SELECTED_REGION).casefold())
    assert selected and selected.group(1) == "9"
    assert len(result_icons(frame("craft_result_after_extraction"))) == 1


def test_a_single_crafted_piece_is_read_on_its_panel(ocr):
    from core.inventory import read_single
    from states.gear import CRAFT_RESULT_REGION, SINGLE_EXTRACT_REGION
    rare = read_single(frame("craft_result_single"), ocr)
    assert rare.describe() == "rare weapon +0 critical set, main attack: health 173, attack_percent 4"
    heroic = read_single(frame("craft_result_single_heroic"), ocr)
    assert (heroic.grade, heroic.set_name, heroic.substats) == ("heroic", "hit", {"health_percent": 4, "crit_chance": 4, "effectiveness": 5})
    # "Success" shows in the same place over one piece and over ten; only the single one has its Extract button.
    for name in ("craft_result_single", "craft_result_ten"):
        assert "success" in ocr.read(frame(name), CRAFT_RESULT_REGION).casefold()
    assert "extract" in ocr.read(frame("craft_result_single"), SINGLE_EXTRACT_REGION).casefold()
    assert "extract" not in ocr.read(frame("craft_result_ten"), SINGLE_EXTRACT_REGION).casefold()


def test_after_a_craft_the_workshop_waits_for_its_result(monkeypatch):
    import time
    import states.gear as gear_state
    monkeypatch.setattr(gear_state, "seen", lambda ctx, screen: None)
    clicks = []
    monkeypatch.setattr(gear_state, "click_once", lambda ctx, x, y: clicks.append((x, y)))
    ctx = make_ctx("gear_craft")
    data = gear_data(ctx)
    data["crafting"] = time.monotonic()
    assert gear_state.workshop(ctx) == gear_state.State.ROUTE and not clicks and not ctx.halted
    data["crafting"] = time.monotonic() - gear_state.CRAFT_RESULT_SECONDS - 1
    gear_state.workshop(ctx)
    assert not clicks and ctx.halted and "never showed" in ctx.halted[0]


def test_sweeps_stop_where_the_grid_sorted_by_level_leaves_their_pieces():
    from states.gear import past_its_cards
    ctx = make_ctx("gear_sort")
    selling = {"kind": "arrange", "action": "sell"}
    assert not past_its_cards(ctx, selling, CardResult("enhanced", reading(6)))
    assert past_its_cards(ctx, selling, CardResult("new", reading(0)))
    # Selling fresh pieces too, or a sale of a few known pieces: no early end.
    assert not past_its_cards(make_ctx("gear_sort", new_bad_action="sell"), selling, CardResult("new", reading(0)))
    assert not past_its_cards(ctx, dict(selling, only={("x",)}), CardResult("new", reading(0)))
    # A roll takes every level: it never stops early.
    assert not past_its_cards(ctx, {"kind": "bulk"}, CardResult("new", reading(0)))


def test_a_sweep_started_again_forgets_its_level():
    from states.gear import restart_sweep
    sweep = {"kind": "bulk", "level": 6, "levels": {6}, "ticked": [{"index": 0}]}
    restart_sweep(sweep)
    assert sweep["level"] is None and not sweep["ticked"]


def test_back_from_an_emptied_bulk_screen_the_sweep_ends(monkeypatch):
    import states.gear as gear_state
    ctx = make_ctx("gear_roll")
    data = gear_data(ctx)
    data["cycle"] = {"seen_at": 1.0, "slots": []}
    sweep = {"kind": "bulk", "stage": "bulk", "ticked": [], "levels": set()}
    gear_state.run_sweep(ctx, sweep, "bulk")
    assert sweep["stage"] == "leave" and data["cycle"] is None


# What a sweep ticks.


def test_an_extraction_sweep_ticks_fresh_junk_only():
    ctx = make_ctx("gear_sort")
    sweep = {"kind": "arrange", "action": "extract", "levels": set(), "next": 1, "ticked": []}
    assert judge_card(ctx, sweep, CardResult("new", reading(**JUNK)))
    # Worth rolling: Sort leaves it for the Roll mode.
    assert not judge_card(ctx, sweep, CardResult("new", reading()))
    # Locked or unread: never touched.
    assert not judge_card(ctx, sweep, CardResult("locked"))
    assert not judge_card(ctx, sweep, CardResult("unsure", why="line 2: value unread"))
    assert gear_data(ctx)["unread"] == 1


def test_a_piece_the_game_would_not_extract_is_not_ticked_again_even_in_the_next_run(tmp_path):
    from core.cards import card_key
    junk = reading(**JUNK)
    sweep = {"kind": "arrange", "action": "extract", "levels": set()}
    ctx = make_ctx("gear_sort", log_dir=tmp_path)
    assert wants(ctx, sweep, junk, "extract")
    split_refused(ctx, {"ticked": [{"key": card_key(junk)}]})
    assert not wants(ctx, sweep, junk, "extract")
    later = make_ctx("gear_sort", log_dir=tmp_path)
    extracting = {"kind": "arrange", "action": "extract", "levels": set()}
    assert not wants(later, extracting, junk, "extract")
    # It is kept aside, to be sold once the extraction sweep is over.
    assert extracting["sell_after"] == {card_key(junk)}


def test_a_bulk_sweep_takes_pieces_of_any_level():
    ctx = make_ctx("gear_roll")
    sweep = {"kind": "bulk", "levels": set()}
    assert wants(ctx, sweep, reading(0), "enhance")
    assert wants(ctx, sweep, reading(6, substats={"attack_percent": 12, "crit_chance": 5, "crit_damage": 7, "health_percent": 7}), "enhance")
    assert not wants(ctx, sweep, reading(0), "keep")


def test_each_bulk_step_raises_only_the_pieces_under_its_target(monkeypatch):
    import time
    import states.gear as gear_state
    ctx = make_ctx("gear_roll")
    ctx.ocr = None
    data = gear_data(ctx)
    cycle = {"stage": "enhance", "seen_at": 0.0, "slots": [
        {"slot": 0, "level": 0}, {"slot": 1, "level": 3}, {"slot": 2, "level": 6},
    ]}
    data["cycle"] = cycle
    monkeypatch.setattr(gear_state, "read_amount", lambda frame, ocr, region: 1000)
    monkeypatch.setattr(gear_state, "click_once", lambda ctx, x, y: None)
    monkeypatch.setattr(gear_state, "equipped", lambda frame, center: False)
    cycle["worn_checked"] = True
    gear_state.bulk_enhance(ctx, cycle)
    # The lowest piece is at +0: the step goes to +3, which only raises it.
    assert cycle["target"] == 3 and cycle["stage"] == "enhancing"
    monkeypatch.setattr(gear_state, "screen_still", lambda ctx, key: True)
    monkeypatch.setattr(gear_state, "seen", lambda ctx, screen: None)
    monkeypatch.setattr(gear_state, "title", lambda ctx: "")
    ctx.ocr = type("O", (), {"read": lambda self, *a, **k: ""})()
    gear_state.bulk_screen(ctx)
    assert [(e["level"], e["raised"]) for e in cycle["slots"]] == [(3, True), (3, False), (6, False)]


def test_sort_review_ticks_what_it_would_sell_or_extract_and_stops():
    ctx = make_ctx("gear_sort", review=True)
    assert build_plan(ctx) == [{"kind": "arrange", "action": "review"}]
    sweep = {"kind": "arrange", "action": "review", "levels": set()}
    assert wants(ctx, sweep, reading(**JUNK), "extract")
    assert wants(ctx, sweep, reading(6), "sell")
    assert not wants(ctx, sweep, reading(), "keep")
    # Roll ignores it.
    assert build_plan(make_ctx("gear_roll", review=True))[-1] == {"kind": "bulk"}


def test_a_bulk_sweep_skips_pieces_done_in_this_run_and_those_at_the_last_level():
    ctx = make_ctx("gear_roll", max_level=12)
    sweep = {"kind": "bulk", "levels": set()}
    gear_data(ctx)["done_keys"].add(__import__("core.cards", fromlist=["card_key"]).card_key(reading(0)))
    assert not wants(ctx, sweep, reading(0), "enhance")
    assert not wants(ctx, {"kind": "bulk", "levels": set()}, reading(12), "enhance")


def test_a_refused_extraction_is_tried_in_halves_and_a_piece_refused_alone_is_sold(tmp_path):
    ctx = make_ctx("gear_sort", log_dir=tmp_path)
    halves = split_refused(ctx, {"ticked": [{"key": (i,)} for i in range(4)]})
    assert halves == [
        {"kind": "arrange", "action": "extract", "only": {(0,), (1,)}},
        {"kind": "arrange", "action": "extract", "only": {(2,), (3,)}},
    ]
    key = ("helmet", "health", (("attack_percent", 5.0),), "protection", 0)
    assert split_refused(ctx, {"ticked": [{"key": key}]}) == [{"kind": "arrange", "action": "sell", "only": {key}}]
    assert gear_data(ctx)["unextractable"] == {key}
    assert "1 fresh pieces the game would not extract, sold instead" in summary(ctx, "done")


def test_a_bulk_sweep_comes_back_while_pieces_are_left_to_roll():
    ctx = make_ctx("gear_roll")
    data = gear_data(ctx)
    data["plan"] = []
    data["sweep"] = {"kind": "bulk", "ticked": [{"index": 0}], "levels": {0}}
    end_sweep(ctx)
    assert data["sweep"]["kind"] == "bulk"
    # Nothing found: the run ends.
    data["sweep"] = {"kind": "bulk", "ticked": [], "levels": set()}
    end_sweep(ctx)
    assert ctx.halted and "every sweep is done" in ctx.halted[0]


def test_the_pieces_limit_counts_the_ticked_pieces():
    ctx = make_ctx("gear_sort", max_pieces=3)
    data = gear_data(ctx)
    data["changed"] = 1
    data["sweep"] = {"ticked": [{"index": 0}]}
    assert pieces_left(ctx) == 1


def test_the_summary_says_what_the_run_did():
    ctx = make_ctx()
    data = gear_data(ctx)
    data["judged"] = 5
    data["tally"] = {"enhance": 2, "sell": 3}
    data["bulks"] = 1
    data["arranges"] = 1
    data["gold"] = 48_000
    data["unread"] = 2
    assert summary(ctx, "every sweep is done") == (
        "Gear: every sweep is done. 5 decisions: 2 enhance, 3 sell "
        "(1 bulk enhancements, 1 sales or extractions); 2 cards unread, left as they are; 48,000 gold spent"
    )


# The selections.


def test_arrange_shows_how_many_cards_are_ticked(ocr):
    assert selection_count(SimpleNamespace(ocr=ocr, frame=frame("arrange_one_ticked")), "arrange") == 1
    assert selection_count(SimpleNamespace(ocr=ocr, frame=frame("arrange_128_ticked")), "arrange") == 128


def test_a_ticked_card_shows_its_check_mark():
    image = frame("arrange_one_ticked")
    ticked = [(x, y) for y in (200, 329, 458) for x in (190, 360, 530, 700) if card_ticked(image, (x, y))]
    assert ticked == [(700, 458)]
    many = frame("arrange_128_ticked")
    assert all(card_ticked(many, (x, y)) for y in (199, 328, 457, 586) for x in (190, 360, 530, 700))


def test_the_bulk_selection_shows_its_count(ocr):
    assert selection_count(SimpleNamespace(ocr=ocr, frame=frame("bulk_select_two_ticked")), "bulk") == 2


# The Bulk Enhancement screen.


def test_the_bulk_screen_shows_the_cost_of_the_chosen_level(ocr):
    assert read_amount(frame("bulk_cost_24000"), ocr, BULK_COST_REGION) == 24_000


def test_bulk_slots_show_whether_they_are_selected_or_locked():
    assert [slot_selected(frame("bulk_two_of_three_selected"), slot) for slot in range(4)] == [True, True, False, False]
    assert [slot_locked(frame("bulk_one_locked"), slot) for slot in range(4)] == [False, False, True, False]
    assert not any(slot_locked(frame("bulk_after_plus3"), slot) for slot in range(4))


def test_the_bulk_screens_arrange_shows_its_ticks_and_count(ocr):
    image = frame("bulk_arrange_two_ticked")
    assert [card_ticked(image, slot_point(slot, BULK_CARD_CENTER)) for slot in range(3)] == [True, True, False]
    assert "2" in ocr.read(image, BULK_ARRANGE_COUNT_REGION, whitelist="0123456789[]")


def test_a_piece_a_hero_wears_shows_his_portrait():
    from core.cards import equipped
    from core.inventory import GRID_COLUMNS_X
    grid = frame("grid_two_worn")
    assert [equipped(grid, (x, 578)) for x in GRID_COLUMNS_X] == [False, False, True, True]
    bulk = cv2.imread(str(GEAR / "bulk_one_worn.jpg"))
    assert [equipped(bulk, slot_point(slot, BULK_CARD_CENTER)) for slot in range(6)] == [False] * 5 + [True]
    assert not any(equipped(frame("arrange_one_ticked"), (x, y)) for y in (200, 329, 458) for x in GRID_COLUMNS_X)


def test_a_worn_card_is_never_ticked():
    ctx = make_ctx("gear_sort")
    assert not judge_card(ctx, {"kind": "arrange", "action": "extract", "levels": set(), "next": 0}, CardResult("equipped"))


def test_the_refund_popup_after_plus_15_is_seen(ocr):
    popup = cv2.imread(str(GEAR / "bulk_reward_popup.jpg"))
    assert "tap to close" in ocr.read(popup, TAP_TO_CLOSE_REGION).casefold()
    assert "tap to close" not in ocr.read(frame("bulk_after_plus3"), TAP_TO_CLOSE_REGION).casefold()


def test_the_pieces_are_read_again_after_an_enhancement(models):
    boots = CardReading("heroic", "boots", "speed", {"defense_percent": 5, "effectiveness": 6, "effect_resistance": 8}, "critical", 0)
    armor = CardReading("heroic", "armor", "defense", {"health_percent": 5, "speed": 3, "crit_damage": 6}, "critical", 0)
    image = frame("bulk_after_plus3")
    after = read_bulk_card(image, slot_point(0, BULK_CARD_CENTER), models, boots, 3)
    assert after.substats == {"defense_percent": 11, "effectiveness": 6, "effect_resistance": 8} and after.level == 3
    assert read_bulk_card(image, slot_point(1, BULK_CARD_CENTER), models, armor, 3).substats["health_percent"] == 12
    # Not the piece expected there: no reading.
    assert read_bulk_card(image, slot_point(1, BULK_CARD_CENTER), models, boots, 3) is None


def test_a_value_followed_by_its_gain_reads_and_the_item_level_stays(models):
    image = cv2.imread(str(GEAR / "bulk_gain_after_value.jpg"))
    known = CardReading("epic", "ring", "effect_resistance", {"crit_chance": 12, "defense_percent": 7, "speed": 8, "health_percent": 6}, "weakening", 9)
    after = read_bulk_card(image, slot_point(0, BULK_CARD_CENTER), models, known, 12)
    # "8 (+4) (1)": the speed is 8, the "(+4)" is not a "%".
    assert after.substats == {"crit_chance": 12, "defense_percent": 7, "speed": 8, "health_percent": 13}
    level_88 = CardReading(**{**known.__dict__, "item_level": 88})
    assert read_bulk_card(image, slot_point(0, BULK_CARD_CENTER), models, level_88, 12).item_level == 88


# The Enhance Equipment screen's reader, still used by the stats tools.


def test_a_new_piece_on_the_enhance_screen(ocr):
    reading_ = read_enhance(frame("enhance_heroic_boots_new"), ocr)
    assert reading_ == EnhanceReading(
        grade="heroic", slot="boots", level=0, main_stat="defense_percent",
        substats={"speed": 4, "defense": 27, "effect_resistance": 4},
        rolls={"speed": 0, "defense": 0, "effect_resistance": 0},
        set_name="lifesteal", locked=False,
    )
    assert plausible(reading_) == ""


def test_impossible_readings_are_refused():
    good = EnhanceReading("epic", "ring", 3, "attack_percent", {"speed": 4, "crit_chance": 9, "health_percent": 5, "defense": 30}, {"crit_chance": 1})
    assert plausible(good) == ""
    misread = EnhanceReading("epic", "ring", 0, "attack_percent", {"speed": 4, "crit_chance": 5, "health_percent": 71, "defense": 30}, {})
    assert "out of range" in plausible(misread)


# The account evaluation.


def test_the_account_scan_reads_worn_plus_15_cards_and_stops_under_15(ocr, models, tmp_path):
    from core.account import load_result
    from states.gear import account_done, account_screen

    ctx = make_ctx("gear_account", log_dir=tmp_path, screenshots=False)
    ctx.ocr = ocr
    ctx.route_data["card_models"] = models
    ctx.frame = frame("grid_two_worn")
    # Row 2 holds two +15 pieces, then two worn +15 pieces; row 3 starts under +15.
    scan = {"stage": "read", "next": 6, "offset": 0, "pieces": [], "unread": 0, "other": 0, "screens": 0}
    assert account_screen(ctx, scan) == "under +15"
    assert [(piece.slot, piece.level) for piece in scan["pieces"]] == [("ring", 15), ("necklace", 15)]
    # The last row in view: the scan scrolls on.
    scan["next"] = 14
    assert account_screen(ctx, scan) == ""
    assert {(piece.slot, piece.level) for piece in scan["pieces"][2:]} == {("boots", 15), ("armor", 15)}
    assert scan["kept"] == {2: "worn", 3: "worn"}, "the two worn pieces are known as worn"
    gear_data(ctx)["scan"] = scan
    assert account_done(ctx, "test") == "halted"
    assert ctx.halted[0].startswith("Account: test. 4 +15 pieces read (0 reforged), 0 unread; level ")
    result = load_result(tmp_path / "gear")
    assert result is not None and result.read == 4
    assert sorted((kept.kind, kept.slot) for kept in result.kept) == [("worn", "armor"), ("worn", "boots")]


def test_the_account_scan_writes_the_lines_the_window_counts(ocr, models, tmp_path):
    from core.progress import read_progress
    from states.gear import account_done, account_screen, account_step

    ctx = make_ctx("gear_account", log_dir=tmp_path, screenshots=False)
    ctx.ocr = ocr
    ctx.route_data["card_models"] = models
    lines = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(f"12:00:00 {record.getMessage()}")
    level = ctx.logger.level
    ctx.logger.addHandler(handler)
    ctx.logger.setLevel(logging.INFO)
    try:
        account_step(ctx, "opening the inventory")
        account_step(ctx, "opening the inventory")
        ctx.frame = frame("grid_two_worn")
        scan = {"stage": "read", "next": 6, "offset": 0, "pieces": [], "unread": 0, "other": 0, "screens": 0}
        account_screen(ctx, scan)
        gear_data(ctx)["scan"] = scan
        account_done(ctx, "test")
    finally:
        ctx.logger.removeHandler(handler)
        ctx.logger.setLevel(level)
    assert lines.count("12:00:00 account: opening the inventory") == 1, "a step is said once"
    progress = read_progress(lines)
    assert progress.account_read == len(scan["pieces"]) == 2
    assert progress.doing == "Computing the scores"


def test_account_steps_are_silent_in_the_other_gear_modes():
    from states.gear import account_step

    ctx = make_ctx("gear_sort")
    account_step(ctx, "opening the inventory")
    assert "account_steps" not in gear_data(ctx)


def test_the_account_scan_unticks_the_filters():
    from states.gear import filters_ok

    ctx = make_ctx("gear_account")
    clicks = []
    ctx.ocr = SimpleNamespace(read=lambda frame, region: "max enhance")
    ctx.session = None
    ctx.frame = np.zeros((720, 1280, 3), np.uint8)
    # A ticked box: a green check where the box is.
    ctx.frame[614:634, 298:318] = (40, 200, 40)
    import states.gear as gear_states

    original = gear_states.click_once
    gear_states.click_once = lambda ctx, x, y: clicks.append((x, y))
    try:
        gear_data(ctx)["panel_seen_at"] = 0.0
        gear_data(ctx)["wrong_308_624"] = 0.0
        filters_ok(ctx)
    finally:
        gear_states.click_once = original
    assert clicks == [(308, 624)]

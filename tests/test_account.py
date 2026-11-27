"""Account evaluation: reforged pieces brought back to level 85, values per role, stats, level and Kth-best targets."""

from __future__ import annotations

from core.account import (
    AccountResult,
    ScannedPiece,
    advise,
    as_level_85,
    default_role_heroes,
    evaluate,
    kth_best,
    level,
    load_result,
    piece_values,
    reforged_speed,
    role_values,
    save_result,
    stats,
)
from core.gear import GearPolicy, Piece, efficiency_needed, odds_offset, role_target, role_view, speed_target, unreforged

POLICY = GearPolicy()
HELMET = Piece("helmet", "health", 15, {"health_percent": 24, "defense_percent": 8, "crit_damage": 14, "speed": 12})


def test_a_reforged_piece_comes_back_to_its_level_85_values():
    # At level 85: attack % 1 roll, crit chance none, crit damage 1, speed 3; reforging added 3, 1, 2 and 3.
    before = {"attack_percent": 16, "crit_chance": 5, "crit_damage": 12, "speed": 13}
    after = {"attack_percent": 19, "crit_chance": 6, "crit_damage": 14, "speed": 16}
    subs, rolls = unreforged("epic", after)
    assert subs == before
    assert rolls == {"attack_percent": 1, "crit_chance": 0, "crit_damage": 1, "speed": 3}


def test_only_reforged_pieces_are_changed():
    piece = Piece("weapon", "attack", 15, {"attack_percent": 19, "crit_chance": 6, "crit_damage": 14, "speed": 16}, item_level=90)
    assert as_level_85(piece).substats["speed"] == 13 and as_level_85(piece).item_level == 85
    plain = Piece("weapon", "attack", 15, {"attack_percent": 19}, item_level=85)
    assert as_level_85(plain) is plain


def test_a_helmet_on_a_good_set_is_worth_its_efficiency():
    # A helmet is the target's own scale: no offset, and an unknown set counts as good.
    assert piece_values(HELMET)["bruiser"] == role_view(HELMET, "bruiser", stone=False).efficiency


def test_a_slot_whose_good_pieces_come_easily_counts_for_less():
    armor = Piece("armor", "defense", 15, {"health_percent": 24, "defense_percent": 8, "crit_damage": 14, "speed": 12})
    assert odds_offset(armor, "bruiser") > 0
    assert piece_values(armor)["bruiser"] == round(role_view(armor, "bruiser", stone=False).efficiency - odds_offset(armor, "bruiser"), 1)


def test_a_role_with_too_many_dead_stats_is_not_counted():
    # Effectiveness and resistance are dead for a damage dealer: two dead stats on a helmet is one too many.
    helmet = Piece("helmet", "health", 15, {"attack_percent": 20, "crit_chance": 12, "effectiveness": 8, "effect_resistance": 8})
    assert "damage" not in piece_values(helmet)


def test_speed_counts_the_reforge_of_level_85_gear_only():
    # Three rolls into speed (4 + 4 + 4 + 3): reforging adds 3. Level 88 gear is never reforged.
    subs = {"speed": 15, "attack_percent": 16, "crit_chance": 10, "crit_damage": 7}
    assert reforged_speed(Piece("weapon", "attack", 15, subs)) == 18
    assert reforged_speed(Piece("weapon", "attack", 15, subs, item_level=88)) == 15


def scanned(slot: str, value: float, speed: float = 0.0, role: str = "bruiser", score: float = 0.0) -> ScannedPiece:
    return ScannedPiece(slot, "speed", 85, {role: value}, speed, score)


def result_of(pieces: list[ScannedPiece]) -> AccountResult:
    return AccountResult("2026-11-12 20:00", len(pieces), 0, 0, tuple(pieces))


def test_the_target_of_a_slot_is_its_kth_best_piece():
    # Twelve bruiser helmets from 90 down to 79, two rings.
    pieces = [scanned("helmet", 90 - index) for index in range(12)] + [scanned("ring", 95), scanned("ring", 94)]
    result = result_of(pieces)
    advice = advise(result, 66, {"bruiser": 10}, 50, 0, 18, ("bruiser",))
    # Ten bruisers: the 10th best helmet. Two rings only: any ring at the user's target is welcome.
    assert advice.roles["bruiser"]["helmet"] == 81 and advice.roles["bruiser"]["ring"] == 66
    # Fewer heroes geared, a higher target; a role at 0 follows the general target.
    assert advise(result, 66, {"bruiser": 2}, 50, 0, 18, ("bruiser",)).roles["bruiser"]["helmet"] == 89
    assert "bruiser" not in advise(result, 66, {"bruiser": 0}, 50, 0, 18, ("bruiser",)).roles
    # The general targets take each piece's best role: 50 heroes is more than the account has, so the user's 66 stays.
    assert advice.general["helmet"] == 66 and advise(result, 66, {}, 3, 0, 18).general["helmet"] == 88


def test_a_target_never_goes_under_the_users_nor_over_100():
    pieces = [scanned("helmet", value) for value in (120, 50, 40)]
    advice = advise(result_of(pieces), 66, {"bruiser": 1, "tank": 1}, 50, 0, 18, ("bruiser",), {"bruiser": 70})
    assert advice.roles["bruiser"]["helmet"] == 100
    assert advise(result_of(pieces), 66, {"bruiser": 3}, 50, 0, 18, ("bruiser",), {"bruiser": 70}).roles["bruiser"]["helmet"] == 70


def test_the_speed_target_beats_the_openers_pieces():
    pieces = [scanned("weapon", 70, speed) for speed in (17, 16, 14)] + [scanned("ring", 70, 12)]
    advice = advise(result_of(pieces), 66, {}, 50, 1, 16, ())
    assert advice.speed["weapon"] == 18 and advice.speed["ring"] == 16 and "boots" not in advice.speed
    assert advise(result_of(pieces), 66, {}, 50, 2, 16, ()).speed["weapon"] == 17
    assert advise(result_of(pieces), 66, {}, 50, 0, 16, ()).speed == {}


def test_stats_and_level():
    result = result_of([scanned("helmet", 80, score=70), scanned("ring", 70, score=58), scanned("weapon", 60, score=55)])
    assert stats(role_values(result, "bruiser")) == stats([80, 70, 60])
    found = stats([80, 70, 60])
    assert (found.count, found.mean, found.median, found.best, found.worst) == (3, 70, 70, 80, 60)
    # The level goes by gear score: a median of 58 is a starter account (the user's).
    assert level(result) == ("Starting", 58)
    assert level(result_of([scanned("helmet", 80, score=72)])) == ("Endgame", 72)
    assert kth_best([3, 1, 2], 2) == 2 and kth_best([1], 2) is None


def test_applied_targets_judge_per_slot():
    policy = GearPolicy(
        efficiency=66, role_efficiency=(("tank", 90),),
        slot_targets=(("bruiser", "helmet", 85), ("general", "ring", 75)), speed_slot_targets=(("weapon", 19),),
        min_speed_speed_set=16, min_speed_other_sets=18,
    )
    assert role_target(policy, "bruiser", "helmet") == 85 and role_target(policy, "bruiser", "armor") == 66
    # A role without its own falls to the general target of the slot; a niche one asks 15 more.
    assert role_target(policy, "damage", "ring") == 75 and role_target(policy, "damage_slow", "ring") == 90
    assert role_target(policy, "tank", "ring") == 90
    assert efficiency_needed(HELMET, policy, "bruiser") == 85
    weapon = Piece("weapon", "attack", 0, {"speed": 4}, set_name="speed")
    other = Piece("weapon", "attack", 0, {"speed": 4}, set_name="critical")
    assert speed_target(weapon, policy) == 19 and speed_target(other, policy) == 21
    assert speed_target(Piece("ring", "attack_percent", 0, {"speed": 4}, set_name="speed"), policy) == 16


def test_the_result_is_saved_and_read_back(tmp_path):
    result = evaluate([HELMET] * 3 + [Piece("helmet", "health", 12, {"health_percent": 8})], unread=2)
    assert result.read == 3 and result.unread == 2 and result.pieces[0].values == piece_values(HELMET)
    # Gear score: health 24, defense 8, crit damage 14 × 1.14, speed 12 × 2.
    assert result.pieces[0].score == 72
    save_result(result, tmp_path)
    assert load_result(tmp_path) == result
    assert load_result(tmp_path / "nowhere") is None


def test_the_default_heroes_make_about_one_seat_per_piece_of_a_starting_account():
    heroes = default_role_heroes()
    assert heroes["damage"] == 6 and heroes["damage_slow"] == 1 and heroes["tank"] == 2
    assert sum(heroes.values()) == 24


def piece(slot: str, **values: float) -> "ScannedPiece":
    from core.account import ScannedPiece

    return ScannedPiece(slot, "speed", 85, values)


def test_each_piece_takes_one_seat_in_its_best_role_with_room():
    from core.account import AccountResult, seat_pieces

    pieces = (
        piece("armor", damage=95, bruiser=90),   # damage, its best role
        piece("armor", damage=92, bruiser=91),   # damage is full (1 hero): bruiser
        piece("armor", damage=80, bruiser=70),   # both full: no hero needs it
        piece("armor", tank=60),                 # tank has no hero: not a seat, nor unneeded
    )
    seating = seat_pieces(AccountResult("now", 4, 0, 0, pieces), {"damage": 1, "bruiser": 1}, ("damage", "bruiser", "tank"))
    assert seating.role_of == {0: "damage", 1: "bruiser"}
    assert seating.seated["damage"]["armor"] == [95] and seating.seated["bruiser"]["armor"] == [91]
    assert seating.unseated == [2]


def test_a_role_target_is_its_weakest_seat_once_full_else_the_floor():
    from core.account import AccountResult, advise

    pieces = tuple(piece("helmet", damage=90 - index, bruiser=85 - index) for index in range(3))
    result = AccountResult("now", 3, 0, 0, pieces)
    advice = advise(result, 65, {"damage": 2, "bruiser": 2}, 50, 0, 0, ("damage", "bruiser"))
    # Damage takes the two best helmets; bruiser gets one seat filled of two: its floor stays.
    assert advice.roles["damage"]["helmet"] == 89
    assert advice.roles["bruiser"]["helmet"] == 65


def test_heroes_left_at_the_old_defaults_take_the_new_ones(tmp_path):
    import json

    from pathlib import Path

    from core.account import old_default_role_heroes
    from core.config import load_config
    from core.settings import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"gear_role_heroes": old_default_role_heroes()}), encoding="utf-8")
    config = load_config(Path(__file__).parent.parent / "config.yaml")
    assert load_settings(path, config).gear_role_heroes == default_role_heroes()
    mine = {**old_default_role_heroes(), "damage": 7}
    path.write_text(json.dumps({"gear_role_heroes": mine}), encoding="utf-8")
    assert load_settings(path, config).gear_role_heroes == mine


def test_locked_and_worn_pieces_count_and_are_kept_in_full(tmp_path):
    import json

    unfinished = Piece("helmet", "health", 12, {"health_percent": 8})
    result = evaluate([HELMET, HELMET, unfinished], kept={1: "locked", 2: "worn"})
    assert result.read == 2, "a locked piece counts like the others"
    assert [(kept.kind, kept.slot, kept.substats) for kept in result.kept] == [("locked", "helmet", HELMET.substats)]
    assert result.kept[0].piece().substats == HELMET.substats
    save_result(result, tmp_path)
    assert load_result(tmp_path) == result
    # A result saved before the locked and worn check still opens.
    old = json.loads((tmp_path / "account_eval.json").read_text(encoding="utf-8"))
    del old["kept"], old["kept_checked"]
    (tmp_path / "account_eval.json").write_text(json.dumps(old), encoding="utf-8")
    assert load_result(tmp_path).kept == () and not load_result(tmp_path).kept_checked


def test_a_scan_marks_its_worn_and_locked_pieces():
    from core.account import evaluate

    worn = Piece("helmet", "health", 15, {"health_percent": 20, "speed": 10, "crit_chance": 9, "defense_percent": 8})
    free = Piece("helmet", "health", 15, {"health_percent": 18, "speed": 8, "crit_chance": 7, "defense_percent": 6})
    result = evaluate([worn, free], kept={0: "worn"})
    assert [piece.kept for piece in result.pieces] == ["worn", ""]

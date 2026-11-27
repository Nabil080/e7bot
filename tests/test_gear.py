"""Judging a piece of gear, and the gear settings."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from core.config import load_config
from core.gear import (
    MAIN_ROLES,
    estimate_rolls,
    good_main,
    GearPolicy,
    Piece,
    dead_stats,
    gear_score,
    judge,
    judged_outlook,
    outlook,
    role_of,
    role_view,
    rolls_left,
    speed_potential,
    stone_outlook,
)
from core.settings import apply_settings, gear_policy, load_settings, settings_from_config

ROOT = Path(__file__).parent.parent
NEW = GearPolicy()
# The six main roles only: the rules below are about them, the niche roles would find other ways to keep a piece.
MAIN = GearPolicy(roles=MAIN_ROLES)


def test_the_gear_score_weighs_each_substat_by_one_roll():
    assert gear_score({"attack_percent": 8, "speed": 4, "crit_chance": 5, "crit_damage": 7}) == 8 + 8 + 8 + 8
    assert gear_score({"attack": 40, "health": 180}) == 0


@pytest.mark.parametrize("grade, level, speed, speed_rolls, subs, expected", [
    # The six worked examples of the guide.
    ("epic", 0, 3, 0, 4, 27),
    ("heroic", 0, 4, 0, 3, 24),
    ("heroic", 3, 4, 0, 3, 19),
    ("heroic", 0, 1, 0, 3, 21),
    ("heroic", 6, 5, 1, 3, 16),
    ("epic", 6, 2, 0, 4, 17),
])
def test_the_speed_potential_matches_the_guide(grade, level, speed, speed_rolls, subs, expected):
    names = ["speed", "attack_percent", "crit_chance", "crit_damage"][:subs]
    substats = {name: (speed if name == "speed" else 6) for name in names}
    piece = Piece("weapon", "attack", level, substats, grade, rolls={"speed": speed_rolls})
    assert speed_potential(piece) == expected


def test_a_heroic_piece_loses_the_roll_that_adds_its_fourth_substat():
    heroic = Piece("helmet", "health", 9, {"speed": 4, "attack_percent": 6, "crit_chance": 4}, "heroic")
    assert rolls_left(heroic) == 1
    after = Piece("helmet", "health", 12, {"speed": 4, "attack_percent": 6, "crit_chance": 4, "crit_damage": 5}, "heroic")
    assert rolls_left(after) == 1


def test_the_role_and_dead_stats_follow_the_substats_and_the_set():
    damage = Piece("weapon", "attack", 0, {"attack_percent": 6, "crit_chance": 4, "crit_damage": 6, "effect_resistance": 5})
    assert role_of(damage) == "damage" and dead_stats(damage) == ["effect_resistance"]
    tank = Piece("armor", "defense", 0, {"health_percent": 6, "effect_resistance": 6, "health": 180, "speed": 3})
    assert role_of(tank) == "support_res" and dead_stats(tank) == []


@pytest.mark.parametrize("piece, reason", [
    (Piece("weapon", "attack", 0, {"crit_chance": 5, "attack_percent": 8}, "rare", "speed"), "rare piece"),
    (Piece("boots", "health", 0, {"attack_percent": 6, "crit_chance": 4, "crit_damage": 6, "health_percent": 6}), "boots with a flat main stat"),
    (Piece("ring", "health", 0, {"attack_percent": 8, "crit_chance": 5, "crit_damage": 7, "health_percent": 8}), "flat main stat without speed"),
    (Piece("armor", "defense", 0, {"attack": 40, "health": 180, "health_percent": 8, "crit_chance": 5}), "two flat substats without speed"),
    (Piece("helmet", "health", 0, {"attack_percent": 6, "crit_chance": 4, "crit_damage": 6}, "heroic"), "heroic piece without speed"),
])
def test_the_craft_discards(piece, reason):
    decision = judge(piece, NEW)
    assert (decision.action, decision.reason) == ("extract", reason)


def test_a_speed_set_piece_with_one_speed_is_rolled():
    piece = Piece("weapon", "attack", 0, {"speed": 1, "health_percent": 6, "effect_resistance": 5}, "heroic", "speed")
    decision = judge(piece, NEW)
    assert decision.action == "enhance" and decision.reason.startswith("speed potential 21")


@pytest.mark.parametrize("chase", ["speed", "both"])
def test_a_piece_rolled_for_its_speed_alone_is_told_apart_from_a_good_one(chase):
    policy = GearPolicy(chase=chase, min_speed_speed_set=16)
    # Good as a damage dealer too: even when only speed is chased, it is not a speed-only piece.
    good = Piece("weapon", "attack", 0, {"crit_chance": 5, "crit_damage": 7, "attack_percent": 8, "speed": 4}, "epic", "speed")
    # Its crit damage main stat serves none of the Hit set's roles: only its speed keeps it.
    only = Piece("necklace", "crit_damage", 9, {"defense_percent": 14, "crit_chance": 3, "speed": 11, "effectiveness": 6}, "epic", "hit")
    assert (judge(good, policy).action, judge(good, policy).for_speed) == ("enhance", False)
    assert (judge(only, policy).action, judge(only, policy).for_speed) == ("enhance", True)


def test_a_piece_rolled_for_its_stats_is_never_a_speed_piece():
    piece = Piece("weapon", "attack", 0, {"crit_chance": 5, "crit_damage": 7, "attack_percent": 8, "health_percent": 6}, "epic", "critical")
    decision = judge(piece, NEW)
    assert decision.action == "enhance" and not decision.for_speed


def test_a_speed_piece_stops_once_it_cannot_reach_the_threshold():
    # Started on 4, missed +3 and +6: 4 + 8 + 2 = 14 at best.
    piece = Piece("weapon", "attack", 6, {"speed": 4, "attack_percent": 14, "crit_chance": 4}, "heroic", "speed", rolls={"speed": 0, "attack_percent": 2})
    # Chasing speed only: its substats are no reason to go on.
    assert judge(piece, GearPolicy(chase="speed")).action == "sell"
    assert judge(piece, GearPolicy(chase="speed", sell_failed=False)).action == "keep"


def test_the_threshold_is_split_between_the_speed_set_and_the_others():
    piece = Piece("weapon", "attack", 3, {"speed": 4, "attack_percent": 12, "crit_chance": 4}, "heroic", "critical", rolls={"speed": 0, "attack_percent": 1})
    # 4 + 12 + 3 = 19 at best.
    assert judge(piece, GearPolicy(min_speed_other_sets=20, chase="speed")).action == "sell"
    assert judge(piece, GearPolicy(min_speed_other_sets=18, chase="speed")).action == "enhance"


def test_an_epic_piece_may_try_one_point_under_the_threshold():
    piece = Piece("necklace", "crit_damage", 6, {"speed": 2, "attack_percent": 14, "crit_chance": 4, "health_percent": 6}, "epic", "speed", rolls={"speed": 0, "attack_percent": 2})
    assert speed_potential(piece) == 17
    assert judge(piece, GearPolicy(chase="speed")).action == "enhance"


def test_the_chase_mode_picks_what_is_rolled():
    # Speed and two stats that serve no role together: worth its speed only.
    fast = Piece("weapon", "attack", 0, {"speed": 4, "crit_chance": 4, "effectiveness": 6, "health": 160}, "epic", "speed")
    strong = Piece("weapon", "attack", 0, {"attack_percent": 8, "crit_chance": 5, "crit_damage": 7, "health_percent": 7}, "epic", "critical")
    assert judge(fast, GearPolicy(chase="speed")).action == "enhance"
    assert judge(strong, GearPolicy(chase="speed")).action == "extract"
    assert judge(strong, GearPolicy(chase="score")).action == "enhance"
    assert judge(strong, GearPolicy(chase="both")).action == "enhance"


def test_new_bad_pieces_are_extracted_or_sold_as_asked():
    rare = Piece("weapon", "attack", 0, {"crit_chance": 5, "attack_percent": 8}, "rare")
    assert judge(rare, GearPolicy(new_bad_action="sell")).action == "sell"


def test_a_finished_piece_is_locked_or_kept():
    done = Piece("weapon", "attack", 15, {"speed": 18, "attack_percent": 14, "crit_chance": 10, "crit_damage": 13}, "epic", "speed", rolls={"speed": 3})
    assert judge(done, NEW).action == "lock"
    assert judge(done, GearPolicy(lock_finished=False)).action == "keep"
    at_twelve = Piece("weapon", "attack", 12, {"speed": 15, "attack_percent": 14, "crit_chance": 10, "crit_damage": 13}, "epic", "speed", rolls={"speed": 3})
    assert judge(at_twelve, GearPolicy(max_level=12)).action == "lock"
    assert judge(at_twelve, NEW).action == "enhance"


def test_the_gear_settings_reach_the_config_and_the_policy():
    config = load_config(ROOT / "config.yaml")
    settings = settings_from_config(config).model_copy(update={"gear_chase": "speed", "gear_min_speed_speed_set": 20, "gear_max_level": 12, "gear_new_bad_action": "sell"})
    policy = gear_policy(apply_settings(config, settings).gear)
    assert (policy.chase, policy.min_speed_speed_set, policy.max_level, policy.new_bad_action) == ("speed", 20, 12, "sell")


def test_settings_saved_by_an_older_version_keep_what_is_still_known(tmp_path):
    config = load_config(ROOT / "config.yaml")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"server": "Europe", "gear_action": "sell", "gear_min_speed": [3, 5, 7, 9, 11, 14]}), encoding="utf-8")
    assert load_settings(path, config).server == "Europe"


def test_an_epic_piece_with_speed_is_rolled_whatever_its_low_rolls():
    # Seen in the lab: 3 speed, 4 % attack and 3 crit chance on an epic critical weapon, 27 speed at best.
    piece = Piece("weapon", "attack", 0, {"speed": 3, "health": 155, "attack_percent": 4, "crit_chance": 3}, "epic", "critical")
    assert judge(piece, NEW).action == "enhance"


def test_speed_overrides_every_other_rule():
    # A rare piece with two dead stats and two lowest rolls, but 3 speed: 3 + 12 + 3 = 18 at best.
    rare = Piece("helmet", "health", 0, {"speed": 3, "effectiveness": 4}, "rare", "hit")
    assert judge(rare, NEW).action == "enhance"
    heroic = Piece("weapon", "attack", 0, {"speed": 4, "effectiveness": 4, "effect_resistance": 4}, "heroic", "critical")
    assert judge(heroic, NEW).action == "enhance"


def test_a_dead_stat_counts_as_changed_by_a_modification_stone():
    # The user's sword: flat health can become speed, 26.1 once changed.
    sword = Piece("weapon", "attack", 0, {"attack_percent": 6, "crit_chance": 4, "crit_damage": 5, "health": 7}, "epic", "speed")
    decision = judge(sword, replace(NEW, stone="always"))
    assert decision.action == "enhance" and "changed into speed" in decision.reason


def test_effectiveness_and_resistance_are_bad_only_once_both_rolled():
    # Asked 55 %: the rule is about the rolls, not the target (a bulky armor asks 8 more than a helmet).
    low = replace(NEW, efficiency=55)
    armor = Piece("armor", "defense", 0, {"effectiveness": 6, "effect_resistance": 4, "defense_percent": 5, "health_percent": 7}, "epic", "speed")
    assert judge(armor, low).action == "enhance"
    rolled = Piece("armor", "defense", 6, {"effectiveness": 12, "effect_resistance": 10, "defense_percent": 5, "health_percent": 7}, "epic", "speed",
                   rolls={"effectiveness": 1, "effect_resistance": 1, "defense_percent": 0, "health_percent": 0})
    assert judge(rolled, low).action == "sell"


def test_no_stone_on_a_dead_stat_with_two_rolls():
    piece = Piece("weapon", "attack", 6, {"attack_percent": 6, "crit_chance": 4, "crit_damage": 5, "effect_resistance": 14}, "epic", "critical",
                  rolls={"effect_resistance": 2})
    assert judge(piece, GearPolicy(chase="score")).action == "sell"


def test_a_finished_piece_under_the_speed_target_is_kept_when_usable():
    usable = Piece("weapon", "attack", 15, {"speed": 13, "attack_percent": 12, "crit_chance": 9, "crit_damage": 11}, "epic", "speed", rolls={"speed": 2})
    assert judge(usable, NEW).action == "lock"
    slow_weak = Piece("weapon", "attack", 15, {"speed": 6, "attack_percent": 9, "crit_chance": 5, "effect_resistance": 6}, "epic", "speed", rolls={"speed": 0, "attack_percent": 1})
    assert judge(slow_weak, NEW).action == "sell"


def test_percent_boots_are_kept_only_with_very_good_substats():
    dead = Piece("boots", "attack_percent", 0, {"effectiveness": 6, "effect_resistance": 5, "health": 150, "crit_chance": 5}, "epic", "critical")
    # Even with one stat changed by a stone, a dead stat is left: other boots must have none.
    assert judge(dead, NEW).action == "extract"
    bruiser = Piece("boots", "health_percent", 0, {"speed": 4, "crit_chance": 5, "crit_damage": 7, "defense_percent": 5}, "epic", "critical")
    assert judge(bruiser, NEW).action == "enhance"
    great = Piece("boots", "attack_percent", 0, {"speed": 4, "crit_chance": 5, "crit_damage": 7, "attack": 42}, "epic", "critical")
    assert judge(great, NEW).action == "enhance"
    # Attack % boots are for damage: health % and effectiveness on them are dead, and one stone fixes only one.
    bulky = Piece("boots", "attack_percent", 0, {"speed": 4, "crit_chance": 5, "effectiveness": 6, "health_percent": 7}, "epic", "critical")
    assert judge(bulky, NEW).action == "extract"
    # With one off-role line, the stone turns it into flat attack when no % stat of the role is left.
    one_off = Piece("boots", "attack_percent", 0, {"speed": 4, "crit_chance": 5, "crit_damage": 7, "health_percent": 7}, "epic", "critical")
    assert judge(one_off, NEW).action == "enhance"


def test_the_role_is_chosen_with_the_stone_and_the_potential_counts_the_rolls_left():
    # The user's armor: effectiveness, flat health, health %, crit damage. As an effectiveness support, crit damage
    # becomes speed: flat health serves a support.
    armor = Piece("armor", "defense", 0, {"effectiveness": 6, "health": 191, "health_percent": 4, "crit_damage": 5}, "epic", "speed")
    view = role_view(armor, "support_eff")
    # Flat health counts a quarter of health % (an armor can roll health %): 6 + 4 + 191 × 8/201 × 0.25 + 8 for the
    # speed, plus 5 rolls of 8 on 3.25 useful stats out of 4. A perfect armor for a debuffer (health %, effectiveness,
    # speed, defense % at half) reaches 63.
    assert (view.swap, view.dead_after, view.potential, view.perfect, view.efficiency) == (("crit_damage", "speed", 4), 0, 52.4, 63.0, 83.2)
    assert judge(armor, replace(NEW, stone="always")).action == "enhance"
    # With the default, 90.2 % is not worth a stone on an armor (100 %), and without it 61.6 % falls under the 68.3 % a
    # bulky armor asks on a best set (60 % and 8.3 for how easily a good one comes).
    assert judge(armor, NEW).action == "extract"


def test_the_set_restricts_the_roles():
    # Attack and health with no crit: a bruiser without crit, fine on a health set, nothing on a critical set.
    subs = {"attack_percent": 7, "health_percent": 7, "defense_percent": 6, "speed": 4}
    assert outlook(Piece("helmet", "health", 0, subs, "epic", "Health")).role in ("bruiser_nocrit", "burner")
    critical = judged_outlook(Piece("helmet", "health", 0, subs, "epic", "critical"), MAIN)
    assert critical.role in ("damage", "bruiser") and critical.dead
    # A damage piece without crit chance has no dead stat: some heroes only want crit damage.
    cleaver = Piece("weapon", "attack", 0, {"attack_percent": 7, "crit_damage": 6, "speed": 3, "health_percent": 5}, "epic", "destruction")
    assert outlook(cleaver).dead_after == 0


def test_the_set_tier_moves_the_score_asked():
    # Gear score 45.7: over the 38 of a best set, under the 58 of a bad one.
    subs = {"attack_percent": 16, "crit_chance": 10, "crit_damage": 12, "health": 180}
    weapon = Piece("weapon", "attack", 15, subs, "epic", "destruction", {name: 0 for name in subs})
    assert judge(weapon, NEW).action == "lock"
    rage = Piece("weapon", "attack", 15, subs, "epic", "rage", {name: 0 for name in subs})
    assert judge(rage, NEW).action == "sell"


def test_a_bad_set_is_rolled_only_perfect():
    # Good for a crit bruiser (68.8 %), but its crit damage is dead for the bruisers without crit: a bad set needs both.
    subs = {"health_percent": 8, "defense_percent": 8, "defense": 34, "crit_damage": 7}
    rolls = {name: 0 for name in subs}
    never = replace(NEW, stone="never")
    assert judge(Piece("helmet", "health", 0, subs, "epic", "counter", rolls), never).action == "enhance"
    revenge = judge(Piece("helmet", "health", 0, subs, "epic", "revenge", rolls), never)
    assert revenge.action == "extract"


def test_effectiveness_and_resistance_are_fine_when_one_leads():
    subs = {"effectiveness": 30, "effect_resistance": 10, "health_percent": 8, "defense_percent": 7}
    led = Piece("ring", "health_percent", 9, subs, "epic", "hit", {"effectiveness": 3, "effect_resistance": 1})
    assert judge(led, NEW).action == "enhance"
    split = Piece("ring", "health_percent", 9, subs, "epic", "hit", {"effectiveness": 2, "effect_resistance": 1})
    assert judge(split, NEW).action != "enhance"


def test_the_modification_stone_setting():
    from core.explain import explain

    # As a crit bruiser it reaches 85.7 % of a perfect necklace only with a stone turning attack % into speed.
    piece = Piece(
        "necklace", "crit_damage", 0, {"effect_resistance": 7, "crit_chance": 5, "health_percent": 8, "attack_percent": 5},
        "epic", "speed", {"effect_resistance": 0, "crit_chance": 0, "health_percent": 0, "attack_percent": 0},
    )
    # Asked 75 % (65 % on a necklace, a rare piece): without the stone it reaches only 57.1 %.
    high = replace(MAIN, efficiency=75)
    assert judge(piece, replace(high, stone="always")).action == "enhance"
    assert judge(piece, replace(high, stone="great", stone_min_efficiency_right=85)).action == "enhance"
    great = replace(high, stone="great", stone_min_efficiency_right=90)
    assert judge(piece, great).action == "extract"
    assert "would bring it to 87.2%, but you count on one only from 90% on a necklace" in explain(piece, great, judge(piece, great).reason)
    # The right side asks less than the left: the same stats on a weapon need 99 %.
    left = Piece("weapon", "attack", 0, dict(piece.substats), "epic", "speed", dict(piece.rolls))
    assert judge(left, MAIN).action != "enhance" or stone_outlook(left, MAIN).efficiency >= 100
    never = replace(high, stone="never")
    assert judge(piece, never).action == "extract"
    assert "you never count on one" in explain(piece, never, judge(piece, never).reason)


def test_a_dead_stat_at_its_lowest_roll_does_not_matter():
    # The user's ring: crit chance 16 after three rolls, effect resistance 4 (dead, at its lowest) for the stone.
    subs = {"defense_percent": 7, "effect_resistance": 4, "speed": 4, "crit_chance": 16}
    ring = Piece("ring", "attack_percent", 10, subs, "epic", "fervor", estimate_rolls("epic", 10, subs))
    assert judge(ring, GearPolicy()).action == "enhance"


def test_the_right_main_stat_counts_as_a_good_stat():
    # Two dead stats and no stone: a weapon carries them as two faults, an attack % ring as one (its main is good).
    subs = {"crit_chance": 8, "crit_damage": 7, "effect_resistance": 7, "effectiveness": 7}
    rolls = {name: 0 for name in subs}
    never = replace(MAIN, stone="never")
    ring = Piece("ring", "attack_percent", 0, subs, "epic", "destruction", rolls)
    weapon = Piece("weapon", "attack", 0, subs, "epic", "destruction", rolls)
    assert (good_main(ring, "damage"), good_main(weapon, "damage")) == (True, False)
    assert judge(weapon, never).action == "extract"
    assert judge(ring, never).action == "enhance"
    # A flat main is no good stat, and boots other than speed boots stay strict.
    assert not good_main(Piece("ring", "attack", 0, subs, "epic", "destruction", rolls), "damage")
    assert not good_main(Piece("boots", "attack_percent", 0, subs, "epic", "destruction", rolls), "damage")


def test_a_piece_good_for_one_role_is_good():
    # The user's speed boots: health % and effectiveness, perfect for an effectiveness support. As a burner (fewest
    # dead stats) attack % sits at its lowest roll; as a support attack % is useless and the stone replaces it.
    subs = {"attack_percent": 4, "crit_damage": 5, "health_percent": 22, "effectiveness": 5}
    boots = Piece("boots", "speed", 6, subs, "epic", "hit", {"attack_percent": 0, "crit_damage": 0, "health_percent": 2, "effectiveness": 0})
    decision = judge(boots, GearPolicy())
    assert decision.action == "enhance" and "support_eff" in decision.reason


def test_the_efficiency_weighs_each_stat_for_the_role_against_a_perfect_piece():
    from core.gear import efficiency_needed, forced_dead, odds_offset, perfect_potential, perfect_score

    # The user's weapon: attack, effectiveness, health (half for a burner) and resistance, which a stone makes speed.
    weapon = Piece("weapon", "attack", 0, {"effectiveness": 8, "health_percent": 8, "effect_resistance": 4, "attack_percent": 5}, "epic", "weakening")
    decision = judge(weapon, replace(NEW, stone="always"))
    assert decision.action == "enhance" and decision.reason.startswith("efficiency 95.2% as burner")
    # Without a stone, health counting half for a burner: one dead stat and 66.7 %, under the 69.6 % a weapon asks of a
    # burner (65 % and 4.6: a good burner weapon comes easily).
    assert judge(weapon, NEW).action == "extract"
    assert judge(weapon, replace(NEW, efficiency=60)).reason.startswith("efficiency 66.7% as burner")
    # A perfect piece with four useful stats is worth 72: 4 best rolls and 5 more of 8.
    # Without crit, a bruiser's fourth stat is flat defense or health, a quarter where the % could roll.
    assert perfect_score("damage") == 72 and perfect_score("bruiser_nocrit") == 49.5
    # A ring with a health % main stat for a bruiser without crit: defense % (half), speed, flat health and defense.
    ring = Piece("ring", "health_percent", 0, {}, "epic", "health")
    # Flat health counts half there (health % is the main stat, it cannot roll), flat defense a quarter.
    assert perfect_potential(ring, "bruiser_nocrit") == 38.2
    # A weapon for a damage dealer without crit can only have attack % and speed: two dead stats are forced.
    assert forced_dead(Piece("weapon", "attack", 0), "damage_nocrit") == 2 and forced_dead(Piece("weapon", "attack", 0), "damage") == 0
    # The set and boots without speed move the efficiency asked.
    boots = Piece("boots", "health_percent", 0, {}, "epic", "rage")
    assert efficiency_needed(boots, NEW, "bruiser") == NEW.efficiency + 20 + 10 + odds_offset(boots, "bruiser")


def test_only_the_roles_ticked_keep_a_piece():
    # Sure crit: crit chance is dead, so the stone makes it attack % and the weapon is perfect for that niche role.
    weapon = Piece("weapon", "attack", 0, {"crit_damage": 7, "crit_chance": 3, "speed": 4, "health": 160}, "epic", "speed")
    sure = replace(MAIN, roles=("damage_sure_crit",), stone="always")
    assert judge(weapon, sure).reason.startswith("efficiency") and "damage_sure_crit" in judge(weapon, sure).reason
    # Unticked, the role is never what keeps a piece.
    assert "damage_sure_crit" not in judge(weapon, replace(MAIN, stone="always")).reason


def test_a_role_is_tried_only_on_a_slot_with_three_of_its_stats():
    from core.gear import candidate_roles

    # An attack ring for a damage dealer without crit could only have speed and flat attack: with its stone it would be
    # 96 % of that "perfect" ring. It is not tried there, nor an armor (speed only, an armor never rolls attack).
    ring = Piece("ring", "attack_percent", 0, {"attack": 34, "effect_resistance": 4, "health_percent": 6, "effectiveness": 8}, "epic", "fervor")
    assert "damage_nocrit" not in candidate_roles(ring)
    assert judge(ring, replace(NEW, stone="always")).action == "extract"
    assert "damage_nocrit" not in candidate_roles(Piece("armor", "defense", 0, {}, "epic", "speed"))
    # A helmet can have attack %, flat attack and speed: tried.
    assert "damage_nocrit" in candidate_roles(Piece("helmet", "health", 0, {}, "epic", "speed"))
    # Niche roles ask 15 points more than the general efficiency.
    from core.gear import role_target
    assert (role_target(NEW, "damage"), role_target(NEW, "tank")) == (65, 80)


def test_the_target_moves_with_how_likely_a_good_piece_of_the_slot_is():
    from core.explain import explain
    from core.gear import ODDS_CAP, efficiency_needed, odds_offset

    def offset(slot: str, main: str, role: str) -> float:
        return odds_offset(Piece(slot, main, 0), role)

    # Against a helmet: a weapon for a damage dealer rolls fewer dead stats (no defense), so it asks more.
    assert offset("helmet", "health", "damage") == 0 and offset("weapon", "attack", "damage") > 0
    # A bulky armor (no attack to roll) is easy for a bruiser, hard for a damage dealer.
    assert offset("armor", "defense", "bruiser") > 5 and offset("armor", "defense", "damage") < 0
    # A necklace, ring or boots with a fitting main stat is rare: up to 10 points less, never more.
    assert offset("necklace", "crit_damage", "damage") == -ODDS_CAP and offset("boots", "speed", "bruiser") == -ODDS_CAP
    assert all(-ODDS_CAP <= offset(slot, main, role) <= ODDS_CAP for slot, main in (("weapon", "attack"), ("armor", "defense"), ("ring", "effectiveness")) for role in ("damage", "tank", "support_eff"))
    # It moves a role's own % too, and the reason says it.
    weapon = Piece("weapon", "attack", 0, {"attack_percent": 4, "crit_chance": 3, "crit_damage": 4, "effectiveness": 4}, "epic", "fervor")
    own = replace(MAIN, role_efficiency=(("damage", 70),))
    assert efficiency_needed(weapon, own, "damage") == 70 + offset("weapon", "attack", "damage")
    decision = judge(weapon, MAIN)
    assert "a good weapon for this role is easier to get" in explain(weapon, MAIN, decision.reason)


def test_the_set_table_of_2026_11_12():
    from core.gear import SETS, set_id, set_tier

    def roles(name: str) -> set[str]:
        return set(SETS[name][0])

    assert roles("fervor") == {"damage", "damage_sure_crit", "damage_nocrit", "damage_slow"}
    assert {"bruiser", "bruiser_nocrit", "damage"} <= roles("warfare") and "support_res" not in roles("warfare")
    assert "bruiser_nocrit" not in roles("critical") and SETS["critical"][1] == "ok"
    assert roles("defense") == {"support_res", "tank", "support_eff"}
    assert {"damage", "bruiser", "bruiser_nocrit", "support_res"} <= roles("counter")
    assert {"support_eff", "support_res", "burner"} <= roles("pursuit")
    assert [SETS[name][1] for name in ("torrent", "penetration", "lifesteal", "reversal", "defense")] == ["best", "best", "good", "good", "bad"]
    # Barrier is Protection.
    barrier = Piece("ring", "health_percent", 0, set_name="Barrier")
    assert set_id(barrier) == "protection" and set_tier(barrier) == "good"


def test_the_main_stat_of_a_right_side_piece_is_a_condition_and_speed_is_always_listed():
    from core.explain import role_lines

    # A crit damage necklace on the Hit set: its set is for debuffers and burners, its main stat serves neither.
    necklace = Piece("necklace", "crit_damage", 9, {"defense_percent": 14, "crit_chance": 3, "speed": 11, "effectiveness": 6}, "epic", "hit")
    policy = GearPolicy(min_speed_speed_set=16)
    lines = role_lines(necklace, policy)
    assert [(line.role, line.passes) for line in lines] == [("debuffer", False), ("burner", False), ("speed", True)]
    assert lines[0].text == "its crit damage main stat does not serve this role"
    assert lines[-1].text == "up to 23 after reforge / 17"
    # Only its speed keeps it.
    assert judge(necklace, policy).reason == "speed potential 23 (needs 17)"
    # Roles unticked in Settings are listed too, and say so.
    helmet = Piece("helmet", "health", 0, {"attack_percent": 8, "crit_damage": 7, "speed": 4, "health": 160}, "epic", "speed")
    listed = {line.role: line for line in role_lines(helmet, MAIN)}
    assert "damage dealer with sure crit" in listed and "Settings" in listed["damage dealer with sure crit"].text
    assert listed["speed"].text.startswith("up to")

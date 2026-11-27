"""The gear judge's reasons, said in plain words with the piece's own numbers, for the advisor.

`core.gear.judge` gives short reasons such as "two flat substats without speed" or "efficiency 81% as damage on a best
set (needs 60%), score 58 of 72": exact, but written for the log. Here each one becomes a sentence saying what is wrong or
right with the piece, and against which of the user's settings. A reason this module does not know is shown as is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.gear import (
    judged_outlook,
    role_plan,
    role_views,
    every_role_view,
    speed_needed,
    speed_plan,
    plan_view,
    stone_threshold,
    stone_outlook,
    stone_used,
    EFF_RES_FOCUS_ROLLS,
    FLAT_STATS,
    NO_REFORGE_LEVEL,
    GearPolicy,
    Piece,
    dead_allowed,
    main_fits,
    good_main,
    reforge_speed,
    efficiency_needed,
    odds_offset,
    RIGHT_SIDE,
    set_tier,
    speed_potential,
    speed_rolls,
    speed_target,
)

STAT_LABELS = {
    "attack": "Attack", "attack_percent": "Attack %", "defense": "Defense", "defense_percent": "Defense %",
    "health": "Health", "health_percent": "Health %", "speed": "Speed", "crit_chance": "Crit chance",
    "crit_damage": "Crit damage", "effectiveness": "Effectiveness", "effect_resistance": "Effect resistance",
}
ROLE_WORDS = {
    "damage": "a damage dealer", "damage_sure_crit": "a damage dealer with sure crit",
    "damage_nocrit": "a damage dealer without crit", "damage_slow": "a damage dealer without speed",
    "bruiser": "a crit bruiser", "bruiser_def": "a defense bruiser", "bruiser_sure_crit": "a bruiser with sure crit",
    "bruiser_nocrit": "a bruiser without crit", "support_res": "a support", "tank": "a tank",
    "support_eff": "a debuffer", "burner": "a burner",
}


def role_name(role: str) -> str:
    """Return a role's name for a title: "Damage dealer with sure crit"."""
    words = ROLE_WORDS.get(role, role)
    return words.removeprefix("a ").removeprefix("an ").capitalize()
GRADE_WORDS = {"epic": "red (epic)", "heroic": "purple (heroic)", "rare": "blue (rare)"}
SPEED_PLAN = re.compile(r"speed potential ([\d.]+) \(needs ([\d.]+)\)")
SCORE_PLAN = re.compile(r"efficiency ([\d.]+)% as (.+?) on a (\w+) set \(needs ([\d.]+)%\), score ([\d.]+) of ([\d.]+)")
FINISHED = re.compile(r"finished at \+(\d+): speed ([\d.]+) [\w ]+?, efficiency ([\d.]+)% as (.+)")


def stat(name: str) -> str:
    """Return a stat's name in words, lower case: "attack %", "effect resistance"."""
    return STAT_LABELS.get(name, name.replace("_", " ")).lower()


def listed(names: list[str]) -> str:
    """Return stat names joined as a sentence: "a", "a and b", "a, b and c"."""
    words = [stat(name) for name in names]
    if not words:
        return "none"
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def after_reforge(piece: Piece) -> str:
    """Return how the final speed is counted: after the reforge at +15, or as it is on gear never reforged."""
    return "at +15 (this gear is never reforged)" if piece.item_level >= NO_REFORGE_LEVEL else "after reforge"


def speed_bonus(piece: Piece, policy: GearPolicy) -> str:
    """Return the sentence saying the piece is also fast enough, or ""."""
    if piece.level >= policy.max_level or speed_plan(piece, policy) is None:
        return ""
    return f" Its speed can also reach {speed_potential(piece):g} {after_reforge(piece)} (target {speed_target(piece, policy):g})."


def view_stone_words(view) -> str:
    """Return the sentence about the stone one role's view counts on, or ""."""
    if view.swap is None:
        return ""
    old, new, value = view.swap
    return f" It counts on a modification stone turning {stat(old)} into {stat(new)} {value:g}."


def view_dead_words(view) -> str:
    """Return the sentence naming the dead stats left in one role's view, or ""."""
    left = [name for name in view.dead if view.swap is None or name != view.swap[0]]
    return f" Useless for {ROLE_WORDS.get(view.role, view.role)}: {listed(left)}." if left else ""


def stone_words(piece: Piece, policy: GearPolicy) -> str:
    """Return the sentence about the modification stone the judge counts on, or ""."""
    change = judged_outlook(piece, policy).swap
    if change is None:
        return ""
    old, new, value = change
    return f" It counts on a modification stone turning {stat(old)} into {stat(new)} {value:g}."


def dead_words(piece: Piece, policy: GearPolicy) -> str:
    """Return the sentence naming the dead stats left after the stone, or ""."""
    view = judged_outlook(piece, policy)
    left = left_dead(piece, policy)
    if not left:
        return ""
    return f" Useless for {ROLE_WORDS.get(view.role, view.role)}: {listed(left)}."


def stone_note(piece: Piece, policy: GearPolicy) -> str:
    """Return the sentence saying a modification stone was ruled out by the user's setting, or ""."""
    full = stone_outlook(piece, policy)
    if full.swap is None or stone_used(piece, policy):
        return ""
    old, new, value = full.swap
    if policy.stone == "never":
        why = "you never count on one"
    else:
        where = "boots" if piece.slot == "boots" else f"a {piece.slot}"
        why = f"you count on one only from {stone_threshold(piece, policy):g}% on {where}"
    return f" A modification stone ({stat(old)} into {stat(new)} {value:g}) would bring it to {full.efficiency:g}%, but {why}."


def left_dead(piece: Piece, policy: GearPolicy | None) -> list[str]:
    """Return the useless stats left once the modification stone is counted."""
    view = judged_outlook(piece, policy)
    return [name for name in view.dead if view.swap is None or name != view.swap[0]]


def speed_words(piece: Piece, policy: GearPolicy) -> str:
    """Return why speed alone does not carry the piece."""
    if "speed" not in piece.substats:
        return "no speed"
    if piece.slot == "boots":
        return "speed does not carry boots (they are judged on their other stats)"
    if policy.chase not in ("speed", "both"):
        return "you do not chase speed"
    return f"its speed can reach {speed_potential(piece):g} at best (target {speed_target(piece, policy):g})"


def score_blocker(piece: Piece, policy: GearPolicy, view=None) -> str:
    """Return what stops the piece from being enhanced for its stats in a role (its best one when none is given): the
    first check of `core.gear.role_plan` it fails.

    Keep in step with role_plan: this says in words the checks it makes, in the same order.
    """
    if policy.chase not in ("score", "both"):
        return "you chase speed only"
    view = view or judged_outlook(piece, policy)
    who = ROLE_WORDS.get(view.role, view.role)
    dead_left = [name for name in view.dead if view.swap is None or name != view.swap[0]]
    if view.role not in policy.roles:
        return f"you do not judge pieces for {who} (Settings › Gear)"
    if not main_fits(piece, view.role):
        return f"its {stat(piece.main_stat)} main stat does not serve {who}"
    if view.dead_after > dead_allowed(piece, view.role):
        stone = " even after a modification stone" if view.swap else ""
        return f"{view.dead_after} of its stats are useless for {who}{stone} ({listed(dead_left)})"
    subs = piece.substats
    if piece.rolls is not None and "speed" not in subs and "effectiveness" in subs and "effect_resistance" in subs:
        eff, res = piece.rolls.get("effectiveness", 0), piece.rolls.get("effect_resistance", 0)
        if min(eff, res) >= 1 and max(eff, res) < EFF_RES_FOCUS_ROLLS:
            return "effectiveness and effect resistance both took rolls: a stone can save only one of them"
    need = efficiency_needed(piece, policy, view.role)
    tier = set_tier(piece)
    if tier == "bad" and view.dead_after > dead_allowed(piece, view.role) - 1:
        return f"a piece on a weak set must have no useless stat, but it has {listed(dead_left)}"
    if piece.slot == "boots" and piece.main_stat != "speed" and view.dead_after:
        return f"boots without a speed main stat must have no useless stat, but it has {listed(dead_left)}"
    return (
        f"its efficiency as {who} can reach {view.efficiency:g}% at best (score {view.potential:g} of a perfect "
        f"{view.perfect:g}), under the {need:g}% you ask on a {tier} set{odds_words(piece, view.role)}"
    )


def odds_words(piece: Piece, role: str) -> str:
    """Return the words for the efficiency added or taken for how likely a good piece of its kind is, or ""."""
    offset = odds_offset(piece, role)
    if offset > 0:
        return f" (+{offset:g}: a good {piece.slot} for this role is easier to get)"
    if offset < 0 and piece.slot in RIGHT_SIDE:
        return f" (−{-offset:g}: a right-side piece with this main stat is rare)"
    if offset < 0:
        return f" (−{-offset:g}: a good {piece.slot} for this role is harder to get)"
    return ""


@dataclass(frozen=True)
class RoleLine:
    """One role the piece's set and main stat allow, and how the piece fares in it."""

    role: str
    passes: bool
    text: str


def role_lines(piece: Piece, policy: GearPolicy) -> tuple[RoleLine, ...]:
    """Return every role the piece's set and main stat allow (unticked ones too), the ones it is good for first (best
    efficiency first), each with its efficiency against the target or what stops it; then speed, always."""
    lines = []
    for view in every_role_view(piece, policy):
        who = ROLE_WORDS.get(view.role, view.role).removeprefix("a ").removeprefix("an ")
        if role_plan(piece, policy, view) is not None:
            stone = f", stone {stat(view.swap[0])} → {stat(view.swap[1])}" if view.swap else ""
            need = efficiency_needed(piece, policy, view.role)
            text = f"{view.efficiency:g}% / {need:g}% (score {view.potential:g} of {view.perfect:g}){stone}"
            lines.append(((0, 0), -view.efficiency, RoleLine(who, True, text)))
        else:
            # The line names the role already: the blocker need not say it again.
            words = ROLE_WORDS.get(view.role, view.role)
            text = score_blocker(piece, policy, view).replace(f" for {words}", "").replace(f" as {words}", "").replace(f" serve {words}", " serve this role")
            lines.append((fail_rank(piece, policy, view), -view.efficiency, RoleLine(who, False, text)))
    ordered = [line for _, _, line in sorted(lines, key=lambda item: item[:2])]
    return (*ordered, speed_line(piece, policy))


def fail_rank(piece: Piece, policy: GearPolicy, view) -> tuple[int, int]:
    """Return where a role the piece fails sits in its list, closest first: short of efficiency only, then by its
    useless stats (fewest first), then a main stat that does not serve the role, then a role turned off."""
    if view.role not in policy.roles:
        return (4, 0)
    if not main_fits(piece, view.role):
        return (3, 0)
    if view.dead_after > dead_allowed(piece, view.role):
        return (2, view.dead_after)
    return (1, 0)


def speed_line(piece: Piece, policy: GearPolicy) -> RoleLine:
    """Return the speed line of the roles list: how fast the piece can get, against the target, or why speed does not
    carry it."""
    if piece.level >= policy.max_level:
        speed = piece.substats.get("speed", 0) + reforge_speed(piece, speed_rolls(piece))
        target = speed_target(piece, policy)
        return RoleLine("speed", speed >= target, f"finished: {speed:g} {after_reforge(piece)} / {target:g}")
    if speed_plan(piece, policy) is not None:
        return RoleLine("speed", True, f"up to {speed_potential(piece):g} {after_reforge(piece)} / {speed_needed(piece, policy):g}")
    if "speed" in piece.substats and piece.slot != "boots" and policy.chase in ("speed", "both"):
        return RoleLine("speed", False, f"up to {speed_potential(piece):g} {after_reforge(piece)} at best, under {speed_needed(piece, policy):g}")
    return RoleLine("speed", False, speed_words(piece, policy))


SHORT = {
    "rare piece": "Blue piece",
    "boots with a flat main stat": "Boots with a flat main stat",
    "flat main stat without speed": "Flat main stat, no speed",
    "two flat substats without speed": "Two flat substats, no speed",
    "heroic piece without speed": "Purple piece without speed",
    "attack or health necklace with crit substats": "Crit wasted on this necklace",
    "crit chance necklace on a critical set": "Crit necklace on a Critical set",
    "no dead stat at +12": "Every stat useful at +12",
    "no potential left at its level": "No potential left",
}


def summary(piece: Piece, policy: GearPolicy, rule: str) -> str:
    """Return the reason in a few words, for the top of a piece's details."""
    if rule in SHORT:
        return SHORT[rule]
    found = SPEED_PLAN.fullmatch(rule)
    if found:
        return f"Speed piece: up to {float(found.group(1)):g} (needs {float(found.group(2)):g})"
    found = SCORE_PLAN.match(rule)
    if found:
        passing = plan_view(piece, policy)
        role = ROLE_WORDS.get(passing[0].role, found.group(2)) if passing else found.group(2)
        return f"{role.removeprefix('a ').removeprefix('an ').capitalize()}: {float(found.group(1)):g}% efficient (needs {float(found.group(4)):g}%)"
    found = FINISHED.match(rule)
    if found:
        return f"Finished and good: {float(found.group(3)):g}% efficient"
    if rule.startswith("finished at +"):
        return "Finished, falls short"
    return rule


def explain(piece: Piece, policy: GearPolicy, rule: str) -> str:
    """Return the judge's reason for a piece as a plain sentence, with its numbers."""
    subs = piece.substats
    target_speed = speed_target(piece, policy)
    if rule == "rare piece":
        return "Blue (rare) piece: it starts with only 2 substats and never catches up with a red one."
    if rule == "boots with a flat main stat":
        return f"Boots with flat {stat(piece.main_stat)} as main stat: heroes want speed boots, and a flat main stat is weak."
    if rule == "flat main stat without speed":
        return f"Flat {stat(piece.main_stat)} is a weak main stat on a {piece.slot}. Only speed in the substats would save it, and it has none."
    if rule == "two flat substats without speed":
        flats = [name for name in subs if name in FLAT_STATS]
        return f"Two flat substats ({listed(flats)}) are worth almost nothing, and there is no speed to make up for it."
    if rule == "heroic piece without speed":
        return "Purple (heroic) piece without speed: it starts with 3 substats and is worth keeping only for speed."
    if rule == "attack or health necklace with crit substats":
        crit = [name for name in subs if name in ("crit_chance", "crit_damage")]
        return f"Crit is wasted on an {stat(piece.main_stat)} necklace ({listed(crit)}): crit heroes wear a crit necklace. No speed to save it."
    if rule == "crit chance necklace on a critical set":
        return "Crit chance necklace on a Critical set: the set already gives crit chance, so the two overlap."
    if rule == "no dead stat at +12":
        return f"Already +12 and every substat is useful: worth finishing to +{policy.max_level}."
    found = SPEED_PLAN.fullmatch(rule)
    if found:
        reach, need = float(found.group(1)), float(found.group(2))
        aim = f"{target_speed:g} ({need:g} for a red piece)" if need < target_speed else f"{target_speed:g}"
        return f"Speed piece: {subs.get('speed', 0):g} speed now, and up to {reach:g} {after_reforge(piece)} if the rolls left go into speed. Your target is {aim}."
    found = SCORE_PLAN.match(rule)
    if found:
        reach, role, tier, need = float(found.group(1)), found.group(2), found.group(3), float(found.group(4))
        score, perfect = float(found.group(5)), float(found.group(6))
        passing = plan_view(piece, policy)
        view = passing[0] if passing is not None else judged_outlook(piece, policy)
        changed = dict(piece.substats)
        if view.swap is not None:
            del changed[view.swap[0]]
            changed[view.swap[1]] = view.swap[2]
        useful = [name for name in changed if name not in view.dead or name not in subs]
        who = ROLE_WORDS.get(view.role, role)
        if good_main(piece, view.role):
            # On a necklace, ring or boots, the right main stat is the hard part: it counts as a good stat.
            useful = [piece.main_stat, *useful]
        return (
            f"Good stats for {who} ({listed(useful)}): with the rolls left it can reach {reach:g}% of a perfect piece "
            f"(score {score:g} of {perfect:g}), and you ask {need:g}% on a {tier} set{odds_words(piece, view.role)}."
            f"{view_stone_words(view)}{view_dead_words(view)}{speed_bonus(piece, policy)}"
        )
    found = FINISHED.match(rule)
    if found:
        speed, efficiency, role = float(found.group(2)), float(found.group(3)), found.group(4)
        view = next((view for view in role_views(piece, policy) if role_plan(piece, policy, view) is not None), None)
        parts = []
        if speed >= target_speed:
            parts.append(f"{speed:g} speed {after_reforge(piece)} (target {target_speed:g})")
        if view is not None:
            parts.append(f"{efficiency:g}% efficient as {ROLE_WORDS.get(view.role, role)} (you ask {efficiency_needed(piece, policy, view.role):g}%)")
        return f"Finished at +{piece.level} and good: {' and '.join(parts) or 'it meets your targets'}. Worth keeping."
    if rule.startswith("finished at +"):
        speed = subs.get("speed", 0) + reforge_speed(piece, speed_rolls(piece))
        return (
            f"Finished at +{piece.level} but short: {speed:g} speed {after_reforge(piece)} (target {target_speed:g}), "
            f"and {score_blocker(piece, policy)}.{stone_note(piece, policy)}"
        )
    if rule == "no potential left at its level":
        start = "Not worth enhancing" if piece.level == 0 else f"Not worth more charms at +{piece.level}"
        return f"{start}: {speed_words(piece, policy)}, and {score_blocker(piece, policy)}.{stone_note(piece, policy)}"
    return rule


def explain_unread(why: str) -> str:
    """Return why a piece was not read, in plain words. The advisor never guesses a reading."""
    if why.startswith("item level None"):
        return "Not read: its item level is hidden (by a tick or a padlock)."
    if why.startswith("item level"):
        level = why.split()[2].rstrip(":")
        return f"Level {level} gear: only level 85 and 88 gear is judged."
    if why in ("no single piece", "no piece"):
        return "No piece read here."
    if why == "no card here":
        return "Not read: the card is covered or cut."
    if why == "grade":
        return "Not read: its grade (red, purple or blue) is unclear."
    if why.startswith("its +N badge") or why == "level badge unread":
        return "Not judged: its +N badge could not be read. Open its Enhance screen to judge it."
    return f"Not read for sure ({why}): the advisor never guesses."

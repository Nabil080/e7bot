"""Judging a piece of gear: its gear score, its speed potential, its dead stats, and what to do with it.

The rules come from a crafting guide for new accounts; every threshold is the user's to set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

# Enhancement levels where a substat gets a roll.
LEVELS = (0, 3, 6, 9, 12, 15)
ROLL_TIERS = (3, 6, 9, 12, 15)
# Substats are weighed by how much one roll of them is worth, on the scale of a % stat roll (about 8).
# The game's own Equipment Score is about this plus the main stat's share (measured on 105 pieces).
SCORE_WEIGHTS = {
    "attack_percent": 1.0,
    "defense_percent": 1.0,
    "health_percent": 1.0,
    "effectiveness": 1.0,
    "effect_resistance": 1.0,
    "speed": 2.0,
    "crit_chance": 1.6,
    "crit_damage": 1.14,
}
FLAT_STATS = ("attack", "defense", "health")
# Main stats that are weak on the pieces that can carry them, unless speed makes up for it.
FLAT_MAIN_PIECES = ("necklace", "ring", "boots")
# One roll of each substat on T85 gear, as (lowest, highest). Speed depends on the grade.
ROLL_RANGES = {
    "attack_percent": (4, 8),
    "defense_percent": (4, 8),
    "health_percent": (4, 8),
    "effectiveness": (4, 8),
    "effect_resistance": (4, 8),
    "crit_chance": (3, 5),
    "crit_damage": (4, 7),
}
# A 5 speed roll exists on epic gear (0.332 %), but is never counted on.
SPEED_RANGES = {"epic": (2, 4), "heroic": (1, 4), "rare": (1, 4)}
MAX_SPEED_ROLL = 4
# Reforging adds 1 speed per roll that went into speed after the first, 4 at most.
MAX_SPEED_REFORGE = 4
# Gear of this item level and above is never reforged.
NO_REFORGE_LEVEL = 88
# An epic piece's lowest speed roll is 2: it may try one point under the speed threshold.
EPIC_SPEED_EASE = 1
# How much one roll of each % stat serves each role: 1 a main stat of the role, 0.5 a
# secondary one, missing a useless (dead) stat. A flat stat is worth FLAT_SHARE of its % stat's weight. A piece is
# measured against the perfect piece of its role (perfect_potential): the niche roles (sure crit, no crit, slow, def
# scaling, tank) have a weaker one.
ROLE_WEIGHTS = {
    "damage": {"attack_percent": 1, "crit_chance": 1, "crit_damage": 1, "speed": 1},
    "damage_sure_crit": {"attack_percent": 1, "crit_damage": 1, "speed": 1},
    "damage_nocrit": {"attack_percent": 1, "speed": 1},
    "damage_slow": {"attack_percent": 1, "crit_chance": 1, "crit_damage": 1},
    "bruiser": {"health_percent": 1, "defense_percent": 0.5, "crit_chance": 1, "crit_damage": 1, "speed": 1},
    "bruiser_def": {"health_percent": 0.5, "defense_percent": 1, "crit_chance": 1, "crit_damage": 1, "speed": 1},
    "bruiser_sure_crit": {"health_percent": 1, "defense_percent": 0.5, "crit_damage": 1, "speed": 1},
    "bruiser_nocrit": {"health_percent": 1, "defense_percent": 0.5, "speed": 1},
    "support_res": {"health_percent": 1, "defense_percent": 0.5, "effect_resistance": 1, "speed": 1},
    "tank": {"health_percent": 1, "defense_percent": 1, "effect_resistance": 1},
    "support_eff": {"health_percent": 1, "defense_percent": 0.5, "effectiveness": 1, "speed": 1},
    "burner": {"attack_percent": 1, "health_percent": 0.5, "effectiveness": 1, "speed": 1},
}
# A flat roll adds about half what a % roll adds on most heroes (46 attack against 8 % of about 1,000 base attack). It
# is worth that only where its % stat cannot roll, being the piece's main stat (flat health on a health % ring); else
# the piece could have had the % stat, and a flat one counts a quarter.
FLAT_SHARE = 0.5
FLAT_SHARE_ROLLABLE = 0.25
PERCENT_OF = {"attack": "attack_percent", "defense": "defense_percent", "health": "health_percent"}
# The game's one best roll of a flat stat at T85, worth one best % roll (8 points) before its role weight.
FLAT_ROLL_MAX = {"attack": 46, "defense": 34, "health": 201}


def stat_weight(role: str, name: str, main: str = "") -> float:
    """Return how much one roll of a stat serves a role on a piece with this main stat: 0 for a dead stat."""
    weights = ROLE_WEIGHTS[role]
    if name in PERCENT_OF:
        share = FLAT_SHARE if PERCENT_OF[name] == main else FLAT_SHARE_ROLLABLE
        return share * weights.get(PERCENT_OF[name], 0)
    return weights.get(name, 0)


# Stats that serve each role, the flat ones included. A substat outside the role of its piece is dead. A missing stat
# is never a fault: a damage piece without crit chance suits the heroes that only want crit damage.
ROLES = {
    role: (*weights, *(flat for flat, percent in PERCENT_OF.items() if percent in weights))
    for role, weights in ROLE_WEIGHTS.items()
}
ALL_ROLES = tuple(ROLES)
# The niche roles go with the sets of their family.
FAMILIES = {
    "damage": ("damage", "damage_sure_crit", "damage_nocrit", "damage_slow"),
    "bruiser": ("bruiser", "bruiser_def", "bruiser_sure_crit"),
    "bruiser_nocrit": ("bruiser_nocrit",),
    "burner": ("burner",),
    "support_eff": ("support_eff",),
    "support_res": ("support_res", "tank"),
}
BULK_ROLES = ("bruiser", "bruiser_nocrit", "support_eff", "support_res")
# The main roles; the others are niche ones. All are judged by default (the user's choice), each niche role asking
# NICHE_EXTRA more efficiency: its pieces suit fewer heroes.
MAIN_ROLES = tuple(FAMILIES)
# A role is tried on a slot only when the slot can have this many of its stats: an armor for a damage dealer without
# crit could only have speed, so any armor with speed would be "perfect" for it.
MIN_ROLE_STATS = 3
# Points of efficiency a niche role asks above the general one, unless the user sets its own.
NICHE_EXTRA = 15


def perfect_score(role: str) -> float:
    """Return the potential of a perfect piece in a role on a slot that can have every stat: what the settings show
    the user next to a role's efficiency."""
    weights = sorted((stat_weight(role, name) for name in ROLES[role]), reverse=True)[:4]
    return round(ROLL_WORTH * (sum(weights) + len(ROLL_TIERS) * sum(weights) / 4), 1)


# Each set, by its English name in the game: the roles that wear it, and how good the set is for them. Roles come
# from the sheet "Sets for Heroes' Gear Stats Guideline" (a role gets a set when at least two of its heroes wear it)
# merged with my own lists. Critical is only "ok": low priority, though most early gear comes from the wyvern farm.
SETS = {
    "speed": (ALL_ROLES, "best"),
    "immunity": (ALL_ROLES, "best"),
    "destruction": (("damage", "bruiser"), "best"),
    "torrent": (("damage",), "best"),
    "penetration": (("damage", "bruiser", "bruiser_nocrit"), "best"),
    "health": (("bruiser", "bruiser_nocrit", "support_res", "support_eff", "burner"), "best"),
    "hit": (("support_eff", "burner"), "good"),
    "resist": (("support_res",), "good"),
    "counter": (("damage", "bruiser", "bruiser_nocrit", "support_res"), "good"),
    "pursuit": (("support_eff", "support_res", "burner"), "good"),
    "weakening": (("support_eff", "burner"), "good"),
    "fervor": (("damage",), "good"),
    "warfare": (("damage", "bruiser", "bruiser_nocrit"), "good"),
    "reversal": (("support_res", "bruiser"), "good"),
    "riposte": (("damage",), "good"),
    "injury": (("bruiser",), "good"),
    "protection": (("bruiser_nocrit",), "good"),
    "lifesteal": (("damage",), "good"),
    "critical": (("damage", "bruiser"), "ok"),
    # Supports and tanks (the tank is in the resistance support's family).
    "defense": (("support_res", "support_eff"), "bad"),
    # In neither the sheet nor the user's lists: the roles they had.
    "attack": (("damage", "bruiser_nocrit", "burner"), "bad"),
    "rage": (("damage", "bruiser"), "bad"),
    "unity": (("damage", "bruiser"), "bad"),
    "revenge": (BULK_ROLES, "bad"),
}
# Each family brings its niche roles.
SETS = {
    name: (tuple(dict.fromkeys(role for family in roles for role in FAMILIES.get(family, (family,)))), tier)
    for name, (roles, tier) in SETS.items()
}
# Efficiency asked above the user's, in points of %, per tier. A bad set is kept only when perfect: no dead stat
# left either.
TIER_EXTRA = {"best": -5, "good": 0, "ok": 10, "bad": 20}
# A bad-set piece must reach this much more speed to be rolled for its speed.
BAD_SET_SPEED_EXTRA = 2
# Other spellings of a set: the French names of the gear lab, and OCR variants.
SET_ALIASES = {
    "vitesse": "speed", "critique": "critical", "toucher": "hit", "attaque": "attack", "défense": "defense",
    "santé": "health", "pv": "health", "résistance": "resist", "resistance": "resist", "vol de vie": "lifesteal",
    "contre": "counter", "immunité": "immunity", "unité": "unity", "révolte": "rage", "vengeance": "revenge",
    "blessure": "injury", "pénétration": "penetration", "retour": "reversal", "guerre": "warfare",
    "poursuite": "pursuit", "affaiblissement": "weakening", "ferveur": "fervor",
    # Protection is called Barrier in some places.
    "barrier": "protection", "barrière": "protection",
}
RED_GRADES = ("epic",)
# Boots with a % main stat other than speed are kept only with very good substats: no dead stat, and this many points
# of efficiency above the target (about one roll).
PERCENT_BOOTS_EXTRA = 10
# One roll at its best is worth about this much gear score, whatever the stat (8 %, 4 speed, 5 crit chance...).
ROLL_WORTH = 8
# Effectiveness and resistance on one piece waste rolls, unless one of them took this many already.
EFF_RES_FOCUS_ROLLS = 3
# A modification stone replaces one substat. The new stat gets its best value only while the old one took at
# most one enhancement roll: the guide never changes a stat with three rolls counting the first.
MAX_SWAP_ROLLS = 1
# Changed into crit chance, a roll is worth 4 at most, not 5.
SWAP_ROLL_MAX = {"speed": MAX_SPEED_ROLL, "crit_chance": 4}
# Substats a slot never rolls, on top of its main stat.
# The right side of the equipment: a random main stat, rarer charms.
RIGHT_SIDE = ("necklace", "ring", "boots")
SLOT_BANS = {"weapon": ("defense", "defense_percent"), "armor": ("attack", "attack_percent")}


@dataclass(frozen=True)
class Piece:
    """What the bot knows of a piece. `rolls` counts the enhancement rolls per substat, when they are known."""

    slot: str
    main_stat: str
    level: int
    substats: dict[str, float] = field(default_factory=dict)
    grade: str = "epic"
    set_name: str = ""
    rolls: dict[str, int] | None = None
    # Level 85 gear is reforged at +15; level 88 gear cannot be: its speed at +15 is final.
    item_level: int = 85


@dataclass(frozen=True)
class GearPolicy:
    """The user's gear settings. Defaults are the guide's, for a new account."""

    chase: str = "both"
    min_speed_speed_set: float = 18
    min_speed_other_sets: float = 18
    # The efficiency asked, in % of a perfect piece for the role (see role_view): a finished piece is kept at the
    # target speed once reforged, or from this efficiency, and a piece is rolled while its potential can reach it.
    # role_efficiency sets it apart for some roles, as (role, %) pairs; the others follow `efficiency`.
    efficiency: float = 65
    role_efficiency: tuple[tuple[str, float], ...] = ()
    # Targets per slot from the account evaluation, as (role or "general", slot, %): a role's own one first, then its
    # own %, then the general one. Speed targets per slot for the speed set, as (slot, speed).
    slot_targets: tuple[tuple[str, str, float], ...] = ()
    speed_slot_targets: tuple[tuple[str, float], ...] = ()
    # The roles a piece is judged for.
    roles: tuple[str, ...] = ALL_ROLES
    max_level: int = 15
    new_bad_action: str = "extract"
    sell_failed: bool = True
    lock_finished: bool = True
    flat_main_needs_speed: bool = True
    # The modification stone: counted on always, never, or only for a piece whose efficiency with it reaches
    # stone_min_efficiency ("great"). A necklace, ring or boots asks less: its main stat is random and its charms are
    # rarer, so a good one is worth a stone sooner.
    stone: str = "great"
    stone_min_efficiency: float = 100
    stone_min_efficiency_right: float = 98


@dataclass(frozen=True)
class Decision:
    """What to do with a piece, and why. Actions: enhance, keep, lock, extract, sell."""

    action: str
    reason: str
    # Enhanced for its speed alone: no role would roll it for its stats, whatever is chased.
    for_speed: bool = False


def gear_score(substats: dict[str, float]) -> float:
    """Return the gear score of a piece's substats. Flat stats and unknown names count for nothing."""
    return round(sum(SCORE_WEIGHTS.get(name, 0.0) * value for name, value in substats.items()), 1)


def rolls_left(piece: Piece) -> int:
    """Return the enhancement rolls still to come that raise an existing substat.

    A piece with fewer than four substats gains one at +12, and a second one at +9: those tiers raise nothing.
    """
    missing = max(0, 4 - len(piece.substats))
    adding = (12, 9)[:missing]
    return sum(1 for tier in ROLL_TIERS if tier > piece.level and tier not in adding)


def speed_rolls(piece: Piece) -> int:
    """Return the enhancement rolls that went into speed: known, or the fewest that explain its value."""
    if piece.rolls is not None:
        return piece.rolls.get("speed", 0)
    return max(0, math.ceil((piece.substats.get("speed", 0) - MAX_SPEED_ROLL) / MAX_SPEED_ROLL))


def speed_potential(piece: Piece) -> float:
    """Return the highest speed the piece can still reach at +15 once reforged, every roll left at 4."""
    if "speed" not in piece.substats:
        return 0.0
    left = rolls_left(piece)
    return piece.substats["speed"] + MAX_SPEED_ROLL * left + reforge_speed(piece, speed_rolls(piece) + left)


def reforge_speed(piece: Piece, rolls: int) -> int:
    """Return the speed reforging adds for this many speed rolls: none on gear that is never reforged."""
    if piece.item_level >= NO_REFORGE_LEVEL:
        return 0
    return min(MAX_SPEED_REFORGE, rolls)


def set_id(piece: Piece) -> str:
    """Return the piece's set as a key of SETS, or "" when it was not read or is unknown."""
    name = piece.set_name.strip().casefold().removesuffix(" set")
    name = SET_ALIASES.get(name, name)
    return name if name in SETS else ""


def set_roles(piece: Piece) -> tuple[str, ...]:
    """Return the roles that wear the piece's set: every role when the set is unknown."""
    key = set_id(piece)
    return SETS[key][0] if key else ALL_ROLES


def set_tier(piece: Piece) -> str:
    """Return how good the piece's set is: best, good, ok or bad. An unknown set counts as good."""
    key = set_id(piece)
    return SETS[key][1] if key else "good"


def role_target(policy: GearPolicy, role: str, slot: str = "") -> float:
    """Return the efficiency the user asks of a role on a slot: the account evaluation's for this role and slot, the
    role's own %, the evaluation's general one for the slot, or the general efficiency (more for a niche role)."""
    measured = {(key, name): value for key, name, value in policy.slot_targets}
    if (role, slot) in measured:
        return measured[(role, slot)]
    own = dict(policy.role_efficiency).get(role)
    if own is not None:
        return own
    return auto_target(measured.get(("general", slot), policy.efficiency), role)


def auto_target(efficiency: float, role: str) -> float:
    """Return the efficiency a role asks when the user sets none: the general one, NICHE_EXTRA more for a niche role.
    A niche piece serves few heroes; a main role's piece serves many, so a bit less is still worth it."""
    return min(100.0, efficiency + (0 if role in MAIN_ROLES else NICHE_EXTRA))


def efficiency_needed(piece: Piece, policy: GearPolicy, role: str) -> float:
    """Return the efficiency asked of the piece in a role, after its set's tier, how likely a good piece of its kind is
    (odds_offset), and boots without a speed main stat asking one roll more. The role's own % in Settings counts as
    the target of a helmet: the offset applies to it too."""
    return role_target(policy, role, piece.slot) + target_extra(piece, role)


def target_extra(piece: Piece, role: str) -> float:
    """Return what the target asks of this very piece above the role's own %: its set's tier, how likely a good piece
    of its kind is, and one roll more for boots without a speed main stat."""
    extra = TIER_EXTRA[set_tier(piece)] + odds_offset(piece, role)
    if piece.slot == "boots" and piece.main_stat != "speed":
        extra += PERCENT_BOOTS_EXTRA
    return extra


# The substats a piece can roll, and the main stats a necklace, ring or boots can have. Every stat is taken as equally
# likely: the game does not publish its odds.
SUB_POOL = (
    "attack", "attack_percent", "defense", "defense_percent", "health", "health_percent", "speed", "crit_chance",
    "crit_damage", "effectiveness", "effect_resistance",
)
RIGHT_MAINS = {
    "necklace": ("attack", "attack_percent", "defense", "defense_percent", "health", "health_percent", "crit_chance", "crit_damage"),
    "ring": ("attack", "attack_percent", "defense", "defense_percent", "health", "health_percent", "effectiveness", "effect_resistance"),
    "boots": ("attack", "attack_percent", "defense", "defense_percent", "health", "health_percent", "speed"),
}
# Points of efficiency per doubling of the odds of a good piece, and the most they move the target either way.
ODDS_SCALE = 5
ODDS_CAP = 10


def piece_odds(slot: str, main: str, role: str) -> float:
    """Return how likely a good piece of this slot is for a role, on a relative scale: the share of the slot's main
    stats that fit the role (a % main stat of weight 1; always 1 on a weapon, helmet or armor), times the mean weight
    of the substats the slot can roll, to the fourth (four substats)."""
    pool = [name for name in SUB_POOL if name != main and name not in SLOT_BANS.get(slot, ())]
    share = sum(stat_weight(role, name, main) for name in pool) / len(pool)
    mains = RIGHT_MAINS.get(slot)
    fit = 1.0 if mains is None else sum(1 for name in mains if name not in FLAT_STATS and stat_weight(role, name) == 1) / len(mains)
    return fit * share**4


def odds_offset(piece: Piece, role: str) -> float:
    """Return the points added to the efficiency asked for how likely a good piece of its slot is, against a helmet:
    a slot whose good pieces come easily asks more, a rare one (a necklace with a fitting main stat) less."""
    odds = piece_odds(piece.slot, piece.main_stat, role)
    base = piece_odds("helmet", "health", role)
    if odds <= 0 or base <= 0:
        return -ODDS_CAP if odds <= 0 else 0.0
    return round(max(-ODDS_CAP, min(ODDS_CAP, ODDS_SCALE * math.log2(odds / base))), 1)


def speed_target(piece: Piece, policy: GearPolicy) -> float:
    """Return the target speed after reforge: what speed alone must bring for the piece to be worth it."""
    need = policy.min_speed_speed_set if set_id(piece) == "speed" else policy.min_speed_other_sets
    measured = dict(policy.speed_slot_targets).get(piece.slot)
    if measured is not None:
        # The account's fastest pieces set the speed set's target; the other sets keep the user's gap above it.
        need = measured + (0 if set_id(piece) == "speed" else policy.min_speed_other_sets - policy.min_speed_speed_set)
    if set_tier(piece) == "bad":
        # Speed alone rolls a bad-set piece only when it is faster: most are thrown out at the end.
        need += BAD_SET_SPEED_EXTRA
    return need


def speed_needed(piece: Piece, policy: GearPolicy) -> float:
    """Return the speed the piece must still be able to reach to be worth rolling for speed."""
    need = speed_target(piece, policy)
    return need - EPIC_SPEED_EASE if piece.grade in RED_GRADES else need


@dataclass(frozen=True)
class Outlook:
    """How a piece serves its best role: dead stats, the modification stone, and its score potential."""

    role: str
    dead: tuple[str, ...]
    swap: tuple[str, str, float] | None
    dead_after: int
    useful_score: float
    potential: float
    # The potential of a perfect piece of the same kind in this role, and the piece's potential in % of it.
    perfect: float = 0.0
    efficiency: float = 0.0


def swap_for(piece: Piece, role: str, dead: tuple[str, ...]) -> tuple[str, str, float] | None:
    """Return (old stat, new stat, new value) for the modification stone in this role, or None.

    The stone takes the weakest dead stat that took at most one enhancement roll, and brings the role's heaviest
    stat the piece can have: speed first on a tie (attack % first for a damage dealer), a flat stat last. Rolls of an
    enhanced piece read in the inventory are unknown: no stone is counted on then.
    """
    if piece.rolls is None and piece.level > 0:
        return None
    rolls = piece.rolls or {}
    candidates = [name for name in dead if rolls.get(name, 0) <= MAX_SWAP_ROLLS]
    if not candidates:
        return None
    old = min(candidates, key=lambda name: point(name) * piece.substats[name])
    banned = {piece.main_stat, *SLOT_BANS.get(piece.slot, ()), *piece.substats}
    # A damage dealer cannot do without attack %: a damage piece lacking it gets it first, even before speed.
    first = ("attack_percent", "speed") if role.startswith("damage") else ("speed",)
    allowed = [name for name in ROLES[role] if name not in banned]
    if not allowed:
        return None
    order = [*first, *ROLES[role]]
    target = min(allowed, key=lambda name: (-stat_weight(role, name, piece.main_stat), order.index(name)))
    per_roll = FLAT_ROLL_MAX.get(target) or SWAP_ROLL_MAX.get(target, ROLL_RANGES.get(target, (0, 0))[1])
    return old, target, per_roll * (1 + rolls.get(old, 0))


def candidate_roles(piece: Piece) -> tuple[str, ...]:
    """Return the roles the piece can serve: those of its set, narrowed by the main stat of a necklace, ring or boots.

    The main stat must serve the role: an effectiveness ring is for a burner or an effectiveness support, so crit on
    it is dead; a crit necklace is for damage or a bruiser. A main stat that serves none of the set's roles leaves them
    all, as before.
    """
    roles = set_roles(piece)
    if piece.slot in FLAT_MAIN_PIECES:
        roles = tuple(role for role in roles if piece.main_stat in ROLES[role]) or roles
    # A role the slot can serve with too few stats is not tried (MIN_ROLE_STATS), unless no role is left.
    return tuple(role for role in roles if forced_dead(piece, role) <= 4 - MIN_ROLE_STATS) or roles


def role_view(piece: Piece, role: str, stone: bool = True) -> Outlook:
    """Return how the piece serves one role: its dead stats, the modification stone, and its score potential.

    The potential is the score of the useful stats, plus every roll left at its best value (about 8 points),
    weighed by the share of the substats that serve the role and are not flat: a roll falls on any substat.
    """
    useful = ROLES[role]
    dead = tuple(name for name in piece.substats if name not in useful)
    change = swap_for(piece, role, dead) if stone else None
    subs = dict(piece.substats)
    if change is not None:
        del subs[change[0]]
        subs[change[1]] = change[2]
    left = [name for name in subs if name not in useful]
    useful_score = role_score(subs, role, piece.main_stat)
    # A roll falls on any substat: it is worth the mean weight of the four.
    share = sum(stat_weight(role, name, piece.main_stat) for name in subs) / 4
    potential = round(useful_score + rolls_left(piece) * ROLL_WORTH * share, 1)
    perfect = perfect_potential(piece, role)
    efficiency = round(100 * potential / perfect, 1) if perfect else 0.0
    return Outlook(role, dead, change, len(left), useful_score, potential, perfect, efficiency)


def point(name: str) -> float:
    """Return the gear score of one point of a stat, a best roll of any stat being worth about 8 (ROLL_WORTH)."""
    if name in FLAT_ROLL_MAX:
        return ROLL_WORTH / FLAT_ROLL_MAX[name]
    return SCORE_WEIGHTS.get(name, 0.0)


def role_score(substats: dict[str, float], role: str, main: str = "") -> float:
    """Return the substats' gear score for a role: each stat weighed by how much it serves the role."""
    return round(sum(stat_weight(role, name, main) * point(name) * value for name, value in substats.items()), 1)


def perfect_potential(piece: Piece, role: str) -> float:
    """Return the potential of a perfect red piece of the same slot and main stat in a role: its four best substats
    (those the slot can have), each at its best roll, and the five enhancement rolls at their best.

    A role with few stats has a lower perfect piece: a ring for a bruiser without crit is perfect with defense %, speed
    and two flat stats, so the efficiency asked of it is easier to reach than for a damage dealer.
    """
    banned = {piece.main_stat, *SLOT_BANS.get(piece.slot, ())}
    weights = sorted((stat_weight(role, name, piece.main_stat) for name in ROLES[role] if name not in banned), reverse=True)[:4]
    total = sum(weights)
    return round(ROLL_WORTH * (total + len(ROLL_TIERS) * total / 4), 1)


def ranked_views(views: list[Outlook]) -> list[Outlook]:
    """Return the views best first: fewest dead stats left after the stone, then fewest before it (stones are rare:
    with as many dead stats left, the role that needs no stone comes first), then the best efficiency."""
    return sorted(views, key=lambda view: (view.dead_after, len(view.dead), -view.efficiency))


def outlook(piece: Piece, stone: bool = True) -> Outlook:
    """Return the piece's best role among those its set and main stat allow."""
    return ranked_views([role_view(piece, role, stone) for role in candidate_roles(piece)])[0]


def role_label(piece: Piece, view: Outlook) -> str:
    """Return the role as the log says it: a damage piece without speed, even after the stone, is for a slow damage dealer."""
    speed = "speed" in piece.substats or (view.swap is not None and view.swap[1] == "speed")
    return "damage (low speed)" if view.role == "damage" and not speed else view.role


def role_of(piece: Piece) -> str:
    """Return the role the piece's substats serve best, the modification stone counted."""
    return outlook(piece).role


def dead_stats(piece: Piece) -> list[str]:
    """Return the substats that do not serve the piece's role, before any modification stone."""
    return list(outlook(piece).dead)


def good_main(piece: Piece, role: str) -> bool:
    """Return whether the main stat counts as one more good stat: on a necklace, ring or boots, a main stat that serves
    the role and is not flat. It is the hard part of a right-side piece (random), worth a good substat. Boots other than
    speed boots do not count: almost every hero wears speed boots."""
    if piece.slot not in RIGHT_SIDE or piece.main_stat in FLAT_STATS or piece.main_stat not in ROLES.get(role, ()):
        return False
    return piece.slot != "boots" or piece.main_stat == "speed"


def dead_allowed(piece: Piece, role: str) -> int:
    """Return how many useless stats a piece may keep (after the stone): one, two when its main stat counts as good,
    plus those the role forces on the slot (a weapon for a damage dealer without crit can only have attack % and speed:
    its two other substats are dead on the best piece)."""
    return 1 + good_main(piece, role) + forced_dead(piece, role)


def main_fits(piece: Piece, role: str) -> bool:
    """Return whether the piece's main stat serves the role: a condition on a necklace, ring or boots, whose main stat is
    random (a crit damage necklace is no debuffer's, whatever its set). A weapon, helmet or armor always fits."""
    return piece.slot not in FLAT_MAIN_PIECES or piece.main_stat in ROLES[role]


def forced_dead(piece: Piece, role: str) -> int:
    """Return how many substats are dead even on the best piece of this slot and main stat for a role."""
    banned = {piece.main_stat, *SLOT_BANS.get(piece.slot, ())}
    return max(0, 4 - sum(1 for name in ROLES[role] if name not in banned))


def near_max(piece: Piece) -> bool:
    """Return whether every substat is within one point of its highest roll."""
    for name, value in piece.substats.items():
        high = MAX_SPEED_ROLL if name == "speed" else ROLL_RANGES.get(name, (0, None))[1]
        if high is None or value < high - 1:
            return False
    return True


def craft_discard(piece: Piece, policy: GearPolicy) -> str | None:
    """Return why a fresh piece goes at once, or None."""
    subs = piece.substats
    speed = "speed" in subs
    if piece.grade == "rare":
        return "rare piece"
    if piece.slot == "boots" and piece.main_stat in FLAT_STATS:
        return "boots with a flat main stat"
    if policy.flat_main_needs_speed and piece.slot in FLAT_MAIN_PIECES and piece.main_stat in FLAT_STATS and not speed:
        return "flat main stat without speed"
    if not speed and sum(name in FLAT_STATS for name in subs) >= 2:
        return "two flat substats without speed"
    if piece.grade == "heroic" and not speed and not (near_max(piece) and not dead_stats(piece)):
        return "heroic piece without speed"
    if piece.slot == "necklace" and piece.main_stat in ("attack_percent", "health_percent") and not speed and (
        "crit_chance" in subs or "crit_damage" in subs
    ):
        return "attack or health necklace with crit substats"
    if piece.slot == "necklace" and piece.main_stat == "crit_chance" and set_id(piece) == "critical" and not (piece.grade in RED_GRADES and speed):
        return "crit chance necklace on a critical set"
    return None


def speed_plan(piece: Piece, policy: GearPolicy) -> str | None:
    """Return why the piece is worth rolling for its speed, or None."""
    if policy.chase not in ("speed", "both") or "speed" not in piece.substats:
        return None
    if piece.slot == "boots":
        # Boots are judged on their substats: the speed is in the main stat.
        return None
    potential, need = speed_potential(piece), speed_needed(piece, policy)
    if potential < need:
        return None
    return f"speed potential {potential:g} (needs {need:g})"


def stone_used(piece: Piece, policy: GearPolicy, view: Outlook | None = None) -> bool:
    """Return whether the user's setting lets the judge count on a modification stone for this piece, in this role's
    view (the best role's when none is given)."""
    if policy.stone == "never":
        return False
    if policy.stone == "great":
        return (view or stone_outlook(piece, policy)).efficiency >= stone_threshold(piece, policy)
    return True


def judged_roles(piece: Piece, policy: GearPolicy | None) -> tuple[str, ...]:
    """Return the roles a piece is judged for: those its set and main stat allow that the user ticked. With none of
    them left, the others still say what the piece is (role_plan turns them all down)."""
    roles = candidate_roles(piece)
    if policy is None:
        return roles
    return tuple(role for role in roles if role in policy.roles) or roles


def stone_outlook(piece: Piece, policy: GearPolicy) -> Outlook:
    """Return the piece's best role among those judged, a modification stone always counted."""
    return ranked_views([role_view(piece, role) for role in judged_roles(piece, policy)])[0]


def role_views(piece: Piece, policy: GearPolicy | None = None) -> list[Outlook]:
    """Return every role the piece can serve, best first, each with the stone only where the user's setting allows it."""
    views = []
    for role in judged_roles(piece, policy):
        view = role_view(piece, role)
        if policy is not None and view.swap is not None and not stone_used(piece, policy, view):
            view = role_view(piece, role, stone=False)
        views.append(view)
    return ranked_views(views)


def every_role_view(piece: Piece, policy: GearPolicy) -> list[Outlook]:
    """Return every role the piece's set and main stat allow, those unticked in Settings too, best first: what the
    advisor lists. The stone counts only where the user's setting allows it."""
    views = []
    for role in candidate_roles(piece):
        view = role_view(piece, role)
        if view.swap is not None and not stone_used(piece, policy, view):
            view = role_view(piece, role, stone=False)
        views.append(view)
    return ranked_views(views)


def stone_threshold(piece: Piece, policy: GearPolicy) -> float:
    """Return the efficiency a piece must reach with a stone to be worth one: lower on the right side."""
    return policy.stone_min_efficiency_right if piece.slot in RIGHT_SIDE else policy.stone_min_efficiency


def judged_outlook(piece: Piece, policy: GearPolicy | None = None) -> Outlook:
    """Return the piece's best role, with the modification stone only where the user's setting allows it."""
    return role_views(piece, policy)[0]


def swap(piece: Piece, policy: GearPolicy | None = None) -> tuple[str, str, float] | None:
    """Return (old stat, new stat, new value) of the modification stone counted on, or None."""
    return judged_outlook(piece, policy).swap


def swapped(piece: Piece, policy: GearPolicy | None = None) -> dict[str, float]:
    """Return the substats after the modification stone, or as they are when none applies."""
    change = swap(piece, policy)
    if change is None:
        return dict(piece.substats)
    old, new, value = change
    subs = {name: value_ for name, value_ in piece.substats.items() if name != old}
    subs[new] = value
    return subs


def score_plan(piece: Piece, policy: GearPolicy) -> str | None:
    """Return why the piece is worth rolling for its substats' score, or None."""
    found = plan_view(piece, policy)
    return None if found is None else found[1]


def plan_view(piece: Piece, policy: GearPolicy) -> tuple[Outlook, str] | None:
    """Return the role the piece is worth rolling for and why, or None: every role its set and main stat allow is
    tried, and the one with the best potential among those that pass is kept. A piece good for one role is good."""
    if policy.chase not in ("score", "both"):
        return None
    passing = [(view, reason) for view in role_views(piece, policy) if (reason := role_plan(piece, policy, view)) is not None]
    return max(passing, key=lambda found: found[0].efficiency, default=None)


def role_plan(piece: Piece, policy: GearPolicy, view: Outlook) -> str | None:
    """Return why the piece is worth rolling for its substats in one role, or None."""
    if view.role not in policy.roles or not main_fits(piece, view.role) or view.dead_after > dead_allowed(piece, view.role):
        return None
    subs = piece.substats
    if piece.rolls is not None and "speed" not in subs and "effectiveness" in subs and "effect_resistance" in subs:
        eff, res = piece.rolls.get("effectiveness", 0), piece.rolls.get("effect_resistance", 0)
        if min(eff, res) >= 1 and max(eff, res) < EFF_RES_FOCUS_ROLLS:
            # Both rolled and neither leads: changing one leaves the other's rolls wasted.
            return None
    # Low rolls are not a rule of their own: the score potential counts the real
    # values, so a piece with bad rolls falls short of the score asked by itself.
    need = efficiency_needed(piece, policy, view.role)
    if set_tier(piece) == "bad" and view.dead_after > dead_allowed(piece, view.role) - 1:
        # A bad set is worth it only perfect.
        return None
    if piece.slot == "boots" and piece.main_stat != "speed" and view.dead_after:
        # Almost every hero wears speed boots: other boots must be very good to be worth it.
        return None
    if view.efficiency < need:
        return None
    stone = f", {view.swap[0]} changed into {view.swap[1]} {view.swap[2]:g}" if view.swap else ""
    return (
        f"efficiency {view.efficiency:g}% as {role_label(piece, view)} on a {set_tier(piece)} set (needs {need:g}%), "
        f"score {view.potential:g} of {view.perfect:g}{stone}"
    )


def bad(piece: Piece, policy: GearPolicy, reason: str) -> Decision:
    """Return what happens to a piece that is not worth more charms."""
    if piece.level == 0:
        return Decision(policy.new_bad_action, reason)
    return Decision("sell" if policy.sell_failed else "keep", reason)


def judge(piece: Piece, policy: GearPolicy) -> Decision:
    """Return what to do with a piece at its level: enhance it, keep or lock it, or get rid of it."""
    finished = piece.level >= policy.max_level
    # Speed overrides every other rule: a piece that can still reach the speed asked is rolled.
    fast = None if finished else speed_plan(piece, policy)
    if fast is not None:
        # Speed decides, but a piece good for a role is said to be so: speed is the reason only when it is the last one.
        # Its potential elsewhere is judged even when only speed is chased: it tells a speed-only piece from a good one.
        role = plan_view(piece, replace(policy, chase="both"))
        return Decision("enhance", score_plan(piece, policy) or fast, for_speed=role is None)
    if piece.level == 0:
        discard = craft_discard(piece, policy)
        if discard is not None:
            return bad(piece, policy, discard)
    if finished:
        speed = piece.substats.get("speed", 0) + reforge_speed(piece, speed_rolls(piece))
        # No roll is left: the potential is the stats as they are, and a role keeps the piece as it would roll it. The
        # stone setting holds here too (the user's choice): a finished piece counts on a stone only when it is worth one.
        # Whatever is chased: a finished piece good for a role is worth keeping.
        passing = [view for view in role_views(piece, policy) if role_plan(piece, policy, view) is not None]
        found = (max(passing, key=lambda view: view.efficiency),) if passing else None
        # Speed alone keeps a finished piece only at the target speed; below it, the piece must be good by its stats.
        if speed >= speed_target(piece, policy) or found is not None:
            when = "final" if piece.item_level >= NO_REFORGE_LEVEL else "once reforged"
            view = found[0] if found is not None else judged_outlook(piece, policy)
            reason = f"finished at +{piece.level}: speed {speed:g} {when}, efficiency {view.efficiency:g}% as {role_label(piece, view)}"
            return Decision("lock" if policy.lock_finished else "keep", reason)
        return bad(piece, policy, f"finished at +{piece.level} without the speed or score asked")
    reason = score_plan(piece, policy)
    if reason is None and piece.level >= 12 and not dead_stats(piece):
        # From +12 the piece is mostly paid for: one without a dead stat is finished.
        reason = "no dead stat at +12"
    if reason is not None:
        return Decision("enhance", reason)
    return bad(piece, policy, "no potential left at its level")


def keep_reason(piece: Piece, policy: GearPolicy) -> str | None:
    """Return why a piece is kept or enhanced, or None when it goes."""
    decision = judge(piece, policy)
    return decision.reason if decision.action in ("enhance", "keep", "lock") else None


# A card shows a piece's values and level, not where its rolls went. A flat substat's one roll at T85, as
# (lowest, highest): the lowest is the smallest first roll seen on level 85 gear.
FLAT_ROLL_RANGES = {"attack": (31, 46), "defense": (24, 34), "health": (148, 201)}


def roll_range(name: str, grade: str) -> tuple[float, float]:
    """Return one roll of a substat as (lowest, highest)."""
    if name == "speed":
        return SPEED_RANGES.get(grade, (1, 4))
    return ROLL_RANGES.get(name) or FLAT_ROLL_RANGES.get(name, (1, 1000))


def enhancement_rolls(grade: str, level: int, substats: int) -> int:
    """Return how many enhancement rolls went into existing substats: the tiers reached, less those that added one."""
    added = max(0, substats - {"epic": 4, "heroic": 3, "rare": 2}.get(grade, 4))
    return max(0, level // 3 - added)


# Reforging (level 85 to 90 at +15) adds to each substat by how many rolls it had, its first one included (1 to 6).
# From the Fribbels optimizer's reforge.js (fribbels/Fribbels-Epic-7-Optimizer), to be checked in the game.
REFORGED_LEVEL = 90
REFORGE_GAINS = {
    "attack_percent": (1, 3, 4, 5, 7, 8),
    "defense_percent": (1, 3, 4, 5, 7, 8),
    "health_percent": (1, 3, 4, 5, 7, 8),
    "effectiveness": (1, 3, 4, 5, 7, 8),
    "effect_resistance": (1, 3, 4, 5, 7, 8),
    "crit_chance": (1, 2, 3, 4, 5, 6),
    "crit_damage": (1, 2, 3, 4, 6, 7),
    "speed": (0, 1, 2, 3, 4, 4),
    "attack": (11, 22, 33, 44, 55, 66),
    "defense": (9, 18, 27, 36, 45, 54),
    "health": (56, 112, 168, 224, 280, 336),
}


def reforge_gain(name: str, rolls: int) -> float:
    """Return what reforging added to a substat that had this many enhancement rolls (its first roll not counted)."""
    gains = REFORGE_GAINS.get(name)
    return gains[min(rolls, len(gains) - 1)] if gains else 0


def unreforged(grade: str, substats: dict[str, float]) -> tuple[dict[str, float], dict[str, int]]:
    """Return a reforged +15 piece's substats as they were at level 85, and their roll counts.

    Every split of the rolls is tried, as estimate_rolls does, each substat less what reforging added for its rolls:
    the split whose level 85 values fit their ranges best wins.
    """
    names = list(substats)
    total = enhancement_rolls(grade, 15, len(names))
    best: tuple[float, dict[str, int]] | None = None
    for split in roll_splits(len(names), total) if names else [()]:
        cost = 0.0
        for name, rolls in zip(names, split):
            cost += roll_cost(name, grade, substats[name] - reforge_gain(name, rolls), rolls)
        if best is None or cost < best[0]:
            best = (cost, dict(zip(names, split)))
    rolls = best[1] if best else {}
    return {name: value - reforge_gain(name, rolls.get(name, 0)) for name, value in substats.items()}, rolls


def roll_splits(count: int, left: int):
    """Yield every way to share `left` rolls among `count` substats."""
    if count == 1:
        yield (left,)
        return
    for first in range(left + 1):
        for rest in roll_splits(count - 1, left - first):
            yield (first, *rest)


def roll_cost(name: str, grade: str, value: float, rolls: int) -> float:
    """Return how badly a value fits a substat with this many enhancement rolls: a lot out of its range, a little
    the farther each roll is from the middle of it."""
    low, high = roll_range(name, grade)
    each = value / (rolls + 1)
    outside = max(0.0, low - each, each - high) / (high - low)
    return 100 * outside + abs(each - (low + high) / 2) / (high - low)


def estimate_rolls(grade: str, level: int, substats: dict[str, float]) -> dict[str, int]:
    """Return the likeliest roll counts of a piece seen on its card, from its values and level.

    Every split of the piece's rolls among its substats is tried; a split that every value fits, each roll near
    the middle of its range, wins. Only reforging and the "rolled into dead stats" rules need the counts: the
    potential comes from the values and the rolls left.
    """
    names = list(substats)
    total = enhancement_rolls(grade, level, len(names))
    best: tuple[float, dict[str, int]] | None = None
    for split in roll_splits(len(names), total) if names else [()]:
        cost = sum(roll_cost(name, grade, substats[name], rolls) for name, rolls in zip(names, split))
        if best is None or cost < best[0]:
            best = (cost, dict(zip(names, split)))
    return best[1] if best else {}

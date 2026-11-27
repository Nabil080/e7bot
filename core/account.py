"""Account evaluation: what the account's finished gear is worth, and the targets that would improve it.

The scan reads every +15 piece of the Inventory, worn or not, and keeps for each one its value in every role it suits
(its set and main stat allow the role, its dead stats are within the limit) and its speed once reforged. A value is
the piece's efficiency in the role brought to the target's scale: what the target would ask of this very piece above
the role's own % (set tier, odds of its slot, boots without a speed main stat) is taken off.

The targets come from the Kth best piece of each slot, K being how many heroes the user gears: a new piece is an upgrade when it beats that one, since it improves one of those K heroes. A slot
with fewer than K pieces keeps the user's target: it lacks pieces, any good one is welcome. The evaluation only ever
raises a target. Per role with the heroes geared in that role; for the roles set to follow the general target, from
the best value of each piece; for speed, from the fastest pieces, K being the openers.

Known limit, accepted for now: a piece counts for every role it suits, so a role can look better stocked than the
heroes can wear (a damage piece serves damage, sure crit and slow damage at once).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.gear import (
    ALL_ROLES,
    MAIN_ROLES,
    REFORGED_LEVEL,
    GearPolicy,
    Piece,
    auto_target,
    dead_allowed,
    estimate_rolls,
    gear_score,
    judged_roles,
    reforge_speed,
    role_view,
    target_extra,
    unreforged,
)

SLOTS = ("weapon", "helmet", "armor", "necklace", "ring", "boots")
# Boots carry their speed in the main stat: the speed targets are for the other slots.
SPEED_SLOTS = ("weapon", "helmet", "armor", "necklace", "ring")
RESULT_FILE = "account_eval.json"
# Heroes geared in each role, by default: 24 in all, about a team of four for each kind of fight. Each hero has one
# seat per slot, filled by one piece: about as many seats as a starting account has +15 pieces per slot.
DEFAULT_ROLE_HEROES = {
    "damage": 6, "damage_sure_crit": 2, "damage_nocrit": 1, "damage_slow": 1,
    "bruiser": 3, "bruiser_def": 1, "bruiser_sure_crit": 1, "bruiser_nocrit": 1,
    "support_eff": 3, "support_res": 2, "tank": 2, "burner": 1,
}
# The defaults before seats (10 per main role, 5 per niche one): a saved set equal to them takes the new defaults.
OLD_MAIN_HEROES = 10
OLD_NICHE_HEROES = 5
GENERAL_HEROES = 50
OPENERS = 1
# The account's level, from the median gear score of its finished pieces (the substats' score as players count it:
# % stats 1, speed 2, crit chance 1.6, crit damage 1.14). Efficiency could not tell it: a plain +15 piece (attack 16,
# speed 7, crit 11, crit damage 6, gear score 54) is 76 % efficient. A first scale from common player benchmarks
# (60 a decent piece, 70 a great one), to be tuned on more accounts; the user's starter account has a median of 58.
LEVELS = ((70, "Endgame"), (65, "Advanced"), (60, "Building"), (0, "Starting"))


def default_role_heroes() -> dict[str, int]:
    return {role: DEFAULT_ROLE_HEROES.get(role, 1) for role in ALL_ROLES}


def old_default_role_heroes() -> dict[str, int]:
    """Return the defaults from before the seats: 10 in a main role, 5 in a niche one."""
    return {role: OLD_MAIN_HEROES if role in MAIN_ROLES else OLD_NICHE_HEROES for role in ALL_ROLES}


@dataclass(frozen=True)
class ScannedPiece:
    """What the evaluation keeps of a finished piece: its slot and set, its value in each role, its speed."""

    slot: str
    set_name: str
    item_level: int
    values: dict[str, float] = field(default_factory=dict)
    speed: float = 0.0
    # The gear score of its substats as the card shows them.
    score: float = 0.0
    # "worn" or "locked" for a piece the player already keeps, "" for a free one (and in scans from before this).
    kept: str = ""

    @property
    def best(self) -> float | None:
        """Return the piece's value in its best main role (its best niche one when it suits no main role), or None
        when it suits none. A niche role's perfect piece is weaker: counted, it would lift every piece."""
        main = [value for role, value in self.values.items() if role in MAIN_ROLES]
        return max(main or self.values.values(), default=None)


@dataclass(frozen=True)
class KeptPiece:
    """A +15 piece the player already keeps, locked or worn by a hero, in full: judged again whenever the targets
    change, to tell when better pieces made it not worth keeping any more."""

    kind: str
    slot: str
    main_stat: str
    level: int
    substats: dict[str, float]
    grade: str
    set_name: str
    item_level: int

    def piece(self) -> Piece:
        """Return the piece as the judge takes it, its rolls estimated as the card reader does."""
        rolls = estimate_rolls(self.grade, self.level, self.substats)
        return Piece(self.slot, self.main_stat, self.level, dict(self.substats), self.grade, self.set_name, rolls, self.item_level)


def kept_piece(piece: Piece, kind: str) -> KeptPiece:
    """Return what the evaluation keeps of a locked ("locked") or worn ("worn") piece."""
    return KeptPiece(kind, piece.slot, piece.main_stat, piece.level, dict(piece.substats), piece.grade, piece.set_name, piece.item_level)


@dataclass(frozen=True)
class AccountResult:
    """A scan: when, how many +15 pieces were read, left unread and reforged, the pieces, and the locked or worn ones."""

    when: str
    read: int
    unread: int
    reforged: int
    pieces: tuple[ScannedPiece, ...]
    kept: tuple[KeptPiece, ...] = ()
    # False for a scan from before the locked and worn pieces were kept apart.
    kept_checked: bool = False


@dataclass(frozen=True)
class Stats:
    """A set of values: how many, the mean, the median, the best and the worst."""

    count: int
    mean: float | None = None
    median: float | None = None
    best: float | None = None
    worst: float | None = None


@dataclass(frozen=True)
class Seating:
    """Where each piece serves: one seat per hero geared in a role and slot, each piece in one seat at most.

    `seated` holds the values of each role's seats per slot, best first; `role_of` the role of each seated piece by its
    place in the result; `unseated` the places of the pieces no seat takes: no hero needs them.
    """

    seated: dict[str, dict[str, list[float]]]
    role_of: dict[int, str]
    unseated: list[int]


@dataclass(frozen=True)
class Advice:
    """The targets the evaluation recommends: per role and slot, general per slot, and speed per slot."""

    roles: dict[str, dict[str, float]]
    general: dict[str, float]
    speed: dict[str, float]


def as_level_85(piece: Piece) -> Piece:
    """Return a reforged piece with its substats as they were at level 85, the scale of the targets."""
    if piece.item_level != REFORGED_LEVEL:
        return piece
    subs, rolls = unreforged(piece.grade, piece.substats)
    return Piece(piece.slot, piece.main_stat, piece.level, subs, piece.grade, piece.set_name, rolls, 85)


def piece_values(piece: Piece, policy: GearPolicy | None = None) -> dict[str, float]:
    """Return a finished piece's value in each role it suits, on the target's scale."""
    policy = policy or GearPolicy()
    piece = as_level_85(piece)
    values = {}
    for role in judged_roles(piece, policy):
        if role not in policy.roles:
            continue
        view = role_view(piece, role, stone=False)
        if view.dead_after > dead_allowed(piece, role):
            continue
        values[role] = round(view.efficiency - target_extra(piece, role), 1)
    return values


def reforged_speed(piece: Piece) -> float:
    """Return a finished piece's speed substat once reforged: as it is on level 88 and 90 gear."""
    speed = piece.substats.get("speed", 0)
    if piece.item_level != 85 or not speed:
        return speed
    rolls = piece.rolls if piece.rolls is not None else estimate_rolls(piece.grade, piece.level, piece.substats)
    return speed + reforge_speed(piece, rolls.get("speed", 0))


def scan_piece(piece: Piece, kept: str = "") -> ScannedPiece:
    """Return what the evaluation keeps of a finished piece, its value in every role, and whether it is worn or locked."""
    return ScannedPiece(
        piece.slot, piece.set_name, piece.item_level, piece_values(piece), reforged_speed(piece), gear_score(piece.substats), kept,
    )


def evaluate(pieces: list[Piece], unread: int = 0, kept: dict[int, str] | None = None) -> AccountResult:
    """Return the scan of the account's +15 pieces. Pieces under +15 are left out. `kept` tells the locked or worn
    ones by their place in `pieces`: they count like the others, and are kept in full to be judged again."""
    kept = kept or {}
    finished = [(index, piece) for index, piece in enumerate(pieces) if piece.level >= 15]
    reforged = sum(1 for _, piece in finished if piece.item_level == REFORGED_LEVEL)
    return AccountResult(
        datetime.now().strftime("%Y-%m-%d %H:%M"), len(finished), unread, reforged,
        tuple(scan_piece(piece, kept.get(index, "")) for index, piece in finished),
        tuple(kept_piece(piece, kept[index]) for index, piece in finished if index in kept), True,
    )


def stats(values: list[float]) -> Stats:
    """Return the count, mean, median, best and worst of some values."""
    if not values:
        return Stats(0)
    return Stats(len(values), round(statistics.mean(values), 1), round(statistics.median(values), 1), max(values), min(values))


def role_values(result: AccountResult, role: str, slot: str = "") -> list[float]:
    return [piece.values[role] for piece in result.pieces if role in piece.values and slot in ("", piece.slot)]


def best_values(result: AccountResult, slot: str = "") -> list[float]:
    """Return each piece's value in its best role, on one slot or all."""
    return [piece.best for piece in result.pieces if piece.best is not None and slot in ("", piece.slot)]


def seat_pieces(result: AccountResult, role_heroes: dict[str, int], roles: tuple[str, ...] = ALL_ROLES) -> Seating:
    """Give each piece one seat: the pieces, best value first, each take a seat in their best role that has one left.

    A piece counts in one role only: a great crit piece no longer raises the bar of every crit role at once. A role
    with 0 heroes has no seats (it follows the general targets). Equal values keep the roles' order, then the pieces'.
    """
    order = {role: rank for rank, role in enumerate(roles)}
    seated = {role: {slot: [] for slot in SLOTS} for role in roles if role_heroes.get(role, 0) > 0}
    offers = sorted(
        (-value, order[role], index, role)
        for index, piece in enumerate(result.pieces) for role, value in piece.values.items() if role in seated
    )
    role_of: dict[int, str] = {}
    for minus_value, _, index, role in offers:
        slot = result.pieces[index].slot
        if index in role_of or len(seated[role][slot]) >= role_heroes[role]:
            continue
        role_of[index] = role
        seated[role][slot].append(-minus_value)
    unseated = [index for index, piece in enumerate(result.pieces) if index not in role_of and any(role in seated for role in piece.values)]
    return Seating(seated, role_of, unseated)


def kth_best(values: list[float], k: int) -> float | None:
    """Return the Kth best value, or None with fewer than K."""
    ranked = sorted(values, reverse=True)
    return ranked[k - 1] if 0 < k <= len(ranked) else None


def slot_target(values: list[float], heroes: int, floor: float) -> float:
    """Return the target of a slot: its Kth best piece, never under the user's target nor over 100."""
    kth = kth_best(values, heroes)
    # A real piece can beat the model's perfect one: a target stops at 100.
    return round(min(100.0, max(floor, kth if kth is not None else floor)), 1)


def advise(
    result: AccountResult, efficiency: float, role_heroes: dict[str, int], general_heroes: int, openers: int, min_speed: float,
    roles: tuple[str, ...] = ALL_ROLES, role_efficiency: dict[str, float] | None = None,
) -> Advice:
    """Return the targets that would improve the account. A role with 0 heroes follows the general target. The floor
    of a role is the user's target for it: its own % when set.

    A role's target on a slot is its weakest seated piece once every hero has one (a new piece is kept when it would
    take a seat), the floor while seats are empty."""
    own = role_efficiency or {}
    seating = seat_pieces(result, role_heroes, roles)
    advice_roles = {}
    for role in roles:
        heroes = role_heroes.get(role, 0)
        if heroes > 0:
            floor = own.get(role, auto_target(efficiency, role))
            advice_roles[role] = {slot: slot_target(seating.seated[role][slot], heroes, floor) for slot in SLOTS}
    general = {slot: slot_target(best_values(result, slot), general_heroes, efficiency) for slot in SLOTS}
    speed = {}
    if openers > 0:
        for slot in SPEED_SLOTS:
            kth = kth_best([piece.speed for piece in result.pieces if piece.slot == slot], openers)
            # An upgrade for the opener is one point faster than its piece.
            speed[slot] = max(min_speed, kth + 1 if kth is not None else min_speed)
    return Advice(advice_roles, general, speed)


def level(result: AccountResult) -> tuple[str, float | None]:
    """Return the account's level and the median gear score of its finished pieces."""
    median = stats([piece.score for piece in result.pieces]).median
    if median is None:
        return LEVELS[-1][1], None
    return next(name for lowest, name in LEVELS if median >= lowest), median


def save_result(result: AccountResult, folder: Path) -> Path:
    """Write the result where the window finds it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RESULT_FILE
    path.write_text(json.dumps(asdict(result), indent=1), encoding="utf-8")
    return path


def load_result(folder: Path) -> AccountResult | None:
    """Return the last scan's result, or None when there is none or it cannot be read (an older format too)."""
    try:
        data = json.loads((folder / RESULT_FILE).read_text(encoding="utf-8"))
        pieces = tuple(ScannedPiece(**piece) for piece in data.pop("pieces"))
        kept = tuple(KeptPiece(**piece) for piece in data.pop("kept", []))
        return AccountResult(pieces=pieces, kept=kept, **data)
    except (OSError, ValueError, TypeError, KeyError):
        return None

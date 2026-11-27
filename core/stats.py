"""Statistics over all the accounts, kept in state.json so that they survive a restart."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Protocol


# Gold paid for one Moonlight Theater ticket.
TICKET_PRICE = 500_000
# Skystones paid for one refresh of the Secret Shop.
SHOP_REFRESH_PRICE = 3
# Huche's charms and enhancement powders, each counted under huche_<key>, with the name the game gives it.
HUCHE_CHARMS = (
    ("epic_artifact", "Epic Artifact Charm"),
    ("epic_equipment", "Epic Equipment Charm"),
    ("epic_accessory", "Epic Accessory Charm"),
    ("greater_artifact", "Greater Artifact Charm"),
    ("greater_equipment", "Greater Equipment Charm"),
    ("greater_accessory", "Greater Accessory Charm"),
    ("lesser_artifact", "Lesser Artifact Charm"),
    ("lesser_equipment", "Lesser Equipment Charm"),
    ("lesser_accessory", "Lesser Accessory Charm"),
    ("powder_equipment", "Equipment Enhancement Powder"),
    ("powder_accessory", "Accessory Enhancement Powder"),
)


class CardLike(Protocol):
    """What the statistics need from a summon result."""

    hero_stars: int
    artifact_stars: int
    moonlight: bool


@dataclass
class Stats:
    """Counters added to as accounts go by. An average is only computed when shown."""

    runs: int = 0
    run_seconds: float = 0.0
    summons: int = 0
    heroes_5: int = 0
    moonlight_5: int = 0
    moonlight_4: int = 0
    normal_4: int = 0
    artifacts_5: int = 0
    story_stages_won: int = 0
    story_fights_lost: int = 0
    story_revives: int = 0
    story_seconds: float = 0.0
    abyss_floors_won: int = 0
    abyss_fights_lost: int = 0
    abyss_seconds: float = 0.0
    theater_acts: int = 0
    theater_tickets: int = 0
    theater_seconds: float = 0.0
    arena_fights: int = 0
    arena_wins: int = 0
    arena_quick: int = 0
    arena_skystones: int = 0
    arena_seconds: float = 0.0
    shop_refreshes: int = 0
    shop_bookmarks: int = 0
    shop_medals: int = 0
    shop_gold: int = 0
    shop_seconds: float = 0.0
    penguin_purchases: int = 0
    penguin_stigma: int = 0
    penguin_newbie: int = 0
    penguin_experienced: int = 0
    penguin_epic: int = 0
    huche_purchases: int = 0
    huche_gold: int = 0
    huche_epic_artifact: int = 0
    huche_epic_equipment: int = 0
    huche_epic_accessory: int = 0
    huche_greater_artifact: int = 0
    huche_greater_equipment: int = 0
    huche_greater_accessory: int = 0
    huche_lesser_artifact: int = 0
    huche_lesser_equipment: int = 0
    huche_lesser_accessory: int = 0
    huche_powder_equipment: int = 0
    huche_powder_accessory: int = 0
    leaves_used: int = 0
    gear_judged: int = 0
    gear_enhanced: int = 0
    gear_sold: int = 0
    gear_extracted: int = 0
    gear_locked: int = 0
    gear_crafted: int = 0
    gear_gold: int = 0

    def add_cards(self, cards: list[CardLike]) -> None:
        """Count the results of one pull."""
        for card in cards:
            self.summons += 1
            self.artifacts_5 += card.artifact_stars == 5
            if card.hero_stars == 5:
                self.heroes_5 += 1
                self.moonlight_5 += card.moonlight
            elif card.hero_stars == 4:
                self.moonlight_4 += card.moonlight
                self.normal_4 += not card.moonlight

    def add_run(self, seconds: float) -> None:
        """Count one account played from its reset to the decision."""
        self.runs += 1
        self.run_seconds += seconds

    def average_stage_seconds(self) -> float | None:
        """Return the mean time to win a story stage, or None before the first one."""
        return self.story_seconds / self.story_stages_won if self.story_stages_won else None

    def average_floor_seconds(self) -> float | None:
        """Return the mean time to win an Abyss floor on a kept account, or None before the first one."""
        return self.abyss_seconds / self.abyss_floors_won if self.abyss_floors_won else None

    def average_act_seconds(self) -> float | None:
        """Return the mean time of a Moonlight Theater act, or None before the first one."""
        return self.theater_seconds / self.theater_acts if self.theater_acts else None

    def average_arena_seconds(self) -> float | None:
        """Return the mean time of an NPC Challenge fight, or None before the first one."""
        return self.arena_seconds / self.arena_fights if self.arena_fights else None

    def average_refresh_seconds(self) -> float | None:
        """Return the mean time of a Secret Shop refresh, or None before the first one."""
        return self.shop_seconds / self.shop_refreshes if self.shop_refreshes else None

    def average_run_seconds(self) -> float | None:
        """Return the mean time of an account, or None before the first one is finished."""
        return self.run_seconds / self.runs if self.runs else None


def _read_state(path: Path) -> dict:
    """Return the content of the state file, empty if it is missing or broken."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(path: Path, **values: object) -> None:
    """Change some values of the state file, keeping the others."""
    path.write_text(json.dumps({**_read_state(path), **values}), encoding="utf-8")


def load_stats(path: Path) -> Stats:
    """Return the saved statistics, or empty ones. A value that cannot be read counts as none."""
    saved = _read_state(path).get("stats")
    if not isinstance(saved, dict):
        return Stats()
    known = {field.name for field in fields(Stats)}
    values = {key: value for key, value in saved.items() if key in known and isinstance(value, (int, float))}
    return Stats(**values)


def save_stats(stats: Stats, path: Path) -> None:
    """Write the statistics to disk."""
    _write_state(path, stats=asdict(stats))


def load_total_rerolls(path: Path) -> int:
    """Return the number of rerolls done so far, 0 if the file is missing or broken."""
    try:
        return int(_read_state(path)["total_rerolls"])
    except (KeyError, ValueError, TypeError):
        return 0


def save_total_rerolls(total: int, path: Path) -> None:
    """Write the reroll count to disk, so that it survives a crash or a restart."""
    _write_state(path, total_rerolls=total)


def load_route_step(path: Path) -> int:
    """Return the step the long route reached on the current account, 0 if unknown."""
    try:
        return int(_read_state(path)["route_step"])
    except (KeyError, ValueError, TypeError):
        return 0


def save_route_step(step: int, path: Path) -> None:
    """Write the long route's step to disk, so that a restart carries on from it."""
    _write_state(path, route_step=step)


PULL_FIELDS = ("cards_read", "moonlight_5star", "unsure")


def load_route_pulls(path: Path) -> dict:
    """Return what the long route's pulls found on the current account, empty if unknown."""
    saved = _read_state(path).get("route_pulls")
    if not isinstance(saved, dict):
        return {}
    return {key: saved[key] for key in PULL_FIELDS if key in saved}


def save_route_pulls(pulls: dict, path: Path) -> None:
    """Write what the long route's pulls found, so that a restart still decides on all of them."""
    _write_state(path, route_pulls={key: pulls[key] for key in PULL_FIELDS if key in pulls})

"""Settings changed from the bot window, and the lasting reroll count. Both stay local."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from core.account import GENERAL_HEROES, OPENERS, default_role_heroes, old_default_role_heroes
from core.config import AppConfig, GearConfig, Range, StrictModel, TopRule
from core.gear import ALL_ROLES, ROLE_WEIGHTS, GearPolicy

SERVERS = ["Europe", "Foundry", "Global", "Asia", "Korea", "Japan"]
# The hunts of the Steel Workshop, in the order of its tabs, and the pieces it crafts.
HUNTS = ("wyvern", "golem", "banshee", "azimanak", "caides")
CRAFT_SLOTS = ("weapon", "helmet", "armor", "necklace", "ring", "boots")


class Settings(StrictModel):
    """What the window lets the user change. Applied on top of config.yaml at each start."""

    # The official client to drive, chosen at the first launch: there is no default.
    client: Literal["stove", "steam"] | None = None
    server: str = "Foundry"
    pre_action_delay_min_ms: int = Field(default=60, ge=0, le=5000)
    pre_action_delay_max_ms: int = Field(default=200, ge=0, le=5000)
    skip_hold_seconds: float = Field(default=5.0, gt=0, le=10)
    click_marker: bool = False
    min_moonlight_5star: int = Field(default=1, ge=1, le=21)
    stop_heroes: list[str] = []
    event_summons: bool = True
    reset_account: bool = True
    # Up to two rules "at least count ML5 in the top rank", as (count, rank). A count of 0 turns one off.
    top_rules: list[tuple[int, int]] = [(0, 5), (0, 20)]
    gear_chase: str = "both"
    gear_min_speed_speed_set: float = Field(default=18, ge=0, le=60)
    gear_min_speed_other_sets: float = Field(default=18, ge=0, le=60)
    gear_efficiency: float = Field(default=65, ge=0, le=100)
    # The efficiency of the roles set apart from gear_efficiency, and the roles judged.
    gear_role_efficiency: dict[str, float] = {}
    gear_roles: list[str] = list(ALL_ROLES)
    # The account evaluation: heroes geared per role (0 follows the general target), in all, and openers; then the
    # targets per slot it applied, per role or "general", and the speed set's speed per slot.
    gear_role_heroes: dict[str, int] = Field(default_factory=default_role_heroes)
    gear_general_heroes: int = Field(default=GENERAL_HEROES, ge=1, le=1000)
    gear_openers: int = Field(default=OPENERS, ge=0, le=50)
    gear_slot_targets: dict[str, dict[str, float]] = {}
    gear_speed_targets: dict[str, float] = {}
    gear_max_level: int = 15
    gear_new_bad_action: str = "extract"
    gear_sell_failed: bool = True
    gear_lock_finished: bool = True
    gear_flat_main_needs_speed: bool = True
    gear_stone: str = "great"
    gear_stone_min_efficiency: float = Field(default=100, ge=0, le=100)
    gear_stone_min_efficiency_right: float = Field(default=98, ge=0, le=100)
    gear_dry_run: bool = False
    gear_review: bool = False
    gear_screenshots: bool = False
    gear_max_pieces: int = Field(default=0, ge=0, le=10_000)
    gear_max_gold_millions: float = Field(default=0, ge=0, le=100_000)
    gear_only_new: bool = False
    gear_craft_hunt: str = "wyvern"
    gear_craft_slot: str = "weapon"
    gear_craft_pieces: int = Field(default=10, ge=1, le=500)
    gear_craft_then: str = "sort"
    theater_buy_tickets: bool = True
    theater_min_gold_millions: float = Field(default=20, ge=0, le=100_000)
    arena_quick_match: bool = True
    arena_attack_players: bool = False
    arena_friendship_refill: bool = False
    arena_skystone_refill: bool = False
    arena_target_fights: int = Field(default=0, ge=0, le=1000)
    theater_target_acts: int = Field(default=0, ge=0, le=1000)
    story_max_revives: int = Field(default=0, ge=0, le=10)
    shop_refreshes: int = Field(default=10, ge=0, le=5000)
    shop_stop_bookmarks: int = Field(default=0, ge=0, le=100_000)
    shop_stop_medals: int = Field(default=0, ge=0, le=1_000_000)
    penguins_kind: Literal["purchases", "stigma"] = "purchases"
    penguins_amount: int = Field(default=1, ge=1, le=100_000_000)


def settings_from_config(config: AppConfig) -> Settings:
    """Return the settings config.yaml describes, used until the user changes them."""
    return Settings(
        server=config.reroll.server,
        pre_action_delay_min_ms=round(config.input.pre_action_delay_ms.min),
        pre_action_delay_max_ms=round(config.input.pre_action_delay_ms.max),
        skip_hold_seconds=config.reroll.skip_hold_seconds,
        click_marker=config.input.click_marker,
        min_moonlight_5star=config.summon.min_moonlight_5star,
        stop_heroes=config.summon.stop_heroes,
        event_summons=config.summon.event_summons,
        reset_account=config.reset.enabled,
        top_rules=[(rule.count, rule.rank) for rule in config.summon.top_rules] or [(0, 5), (0, 20)],
        gear_chase=config.gear.chase,
        gear_min_speed_speed_set=config.gear.min_speed_speed_set,
        gear_min_speed_other_sets=config.gear.min_speed_other_sets,
        gear_efficiency=config.gear.efficiency,
        gear_role_efficiency=dict(config.gear.role_efficiency),
        gear_roles=list(config.gear.roles),
        gear_max_level=config.gear.max_level,
        gear_new_bad_action=config.gear.new_bad_action,
        gear_sell_failed=config.gear.sell_failed,
        gear_lock_finished=config.gear.lock_finished,
        gear_flat_main_needs_speed=config.gear.flat_main_needs_speed,
        gear_stone=config.gear.stone,
        gear_stone_min_efficiency=config.gear.stone_min_efficiency,
        gear_stone_min_efficiency_right=config.gear.stone_min_efficiency_right,
        gear_dry_run=config.gear.dry_run,
        gear_review=config.gear.review,
        gear_screenshots=config.gear.screenshots,
        gear_max_pieces=config.gear.max_pieces,
        gear_max_gold_millions=config.gear.max_gold_millions,
        gear_only_new=config.gear.only_new,
        gear_craft_hunt=config.gear.craft_hunt,
        gear_craft_slot=config.gear.craft_slot,
        gear_craft_pieces=config.gear.craft_pieces,
        gear_craft_then=config.gear.craft_then,
        theater_buy_tickets=config.theater.buy_tickets,
        theater_min_gold_millions=config.theater.min_gold_millions,
        arena_quick_match=config.arena.quick_match,
        arena_attack_players=config.arena.attack_players,
        arena_friendship_refill=config.arena.friendship_refill,
        arena_skystone_refill=config.arena.skystone_refill,
        arena_target_fights=config.arena.target_fights,
        theater_target_acts=config.theater.target_acts,
        story_max_revives=config.story.max_revives,
        shop_refreshes=config.shop.refreshes,
        shop_stop_bookmarks=config.shop.stop_bookmarks,
        shop_stop_medals=config.shop.stop_medals,
        penguins_kind=config.penguins.kind,
        penguins_amount=config.penguins.amount,
    )


def load_settings(path: Path, config: AppConfig) -> Settings:
    """Read the saved settings. A missing or broken file falls back to config.yaml."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        # Settings an older version saved, and since removed, are dropped instead of losing all the others.
        known = {key: value for key, value in saved.items() if key in Settings.model_fields}
        # Heroes per role left at the defaults from before the seats take today's: those were far too many.
        if known.get("gear_role_heroes") == old_default_role_heroes():
            known.pop("gear_role_heroes")
        return Settings.model_validate(known)
    except (OSError, ValueError, ValidationError):
        return settings_from_config(config)


def save_settings(settings: Settings, path: Path) -> None:
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def gear_updates(settings: Settings) -> dict:
    """Return the gear config's values that the settings set."""
    return {
        "chase": settings.gear_chase if settings.gear_chase in ("speed", "score", "both") else "both",
        "min_speed_speed_set": settings.gear_min_speed_speed_set,
        "min_speed_other_sets": settings.gear_min_speed_other_sets,
        "efficiency": settings.gear_efficiency,
        "role_efficiency": {role: value for role, value in settings.gear_role_efficiency.items() if role in ROLE_WEIGHTS},
        "roles": [role for role in settings.gear_roles if role in ROLE_WEIGHTS],
        "slot_targets": {key: dict(slots) for key, slots in settings.gear_slot_targets.items() if key in ROLE_WEIGHTS or key == "general"},
        "speed_targets": dict(settings.gear_speed_targets),
        "max_level": 12 if settings.gear_max_level == 12 else 15,
        "new_bad_action": "sell" if settings.gear_new_bad_action == "sell" else "extract",
        "sell_failed": settings.gear_sell_failed,
        "lock_finished": settings.gear_lock_finished,
        "flat_main_needs_speed": settings.gear_flat_main_needs_speed,
        "stone": settings.gear_stone if settings.gear_stone in ("always", "great", "never") else "great",
        "stone_min_efficiency": settings.gear_stone_min_efficiency,
        "stone_min_efficiency_right": settings.gear_stone_min_efficiency_right,
        "dry_run": settings.gear_dry_run,
        "review": settings.gear_review,
        "screenshots": settings.gear_screenshots,
        "max_pieces": settings.gear_max_pieces,
        "max_gold_millions": settings.gear_max_gold_millions,
        "only_new": settings.gear_only_new,
        "craft_hunt": settings.gear_craft_hunt if settings.gear_craft_hunt in HUNTS else "wyvern",
        "craft_slot": settings.gear_craft_slot if settings.gear_craft_slot in CRAFT_SLOTS else "weapon",
        "craft_pieces": settings.gear_craft_pieces,
        "craft_then": "none" if settings.gear_craft_then == "none" else "sort",
    }


def settings_policy(settings: Settings) -> GearPolicy:
    """Return the gear rules the settings make, on the default gear config: what the gear modes would judge with."""
    return gear_policy(GearConfig().model_copy(update=gear_updates(settings)))


def apply_settings(config: AppConfig, settings: Settings) -> AppConfig:
    low = min(settings.pre_action_delay_min_ms, settings.pre_action_delay_max_ms)
    high = max(settings.pre_action_delay_min_ms, settings.pre_action_delay_max_ms)
    reroll = config.reroll.model_copy(
        update={
            "server": settings.server,
            "skip_hold_seconds": settings.skip_hold_seconds,
        }
    )
    input_config = config.input.model_copy(update={"pre_action_delay_ms": Range(min=low, max=high), "click_marker": settings.click_marker})
    summon = config.summon.model_copy(
        update={
            "min_moonlight_5star": settings.min_moonlight_5star,
            "stop_heroes": settings.stop_heroes,
            "event_summons": settings.event_summons,
            "top_rules": [TopRule(count=count, rank=rank) for count, rank in settings.top_rules if count > 0 and rank > 0],
        }
    )
    reset = config.reset.model_copy(update={"enabled": settings.reset_account})
    gear = config.gear.model_copy(update=gear_updates(settings))
    theater = config.theater.model_copy(update={
        "buy_tickets": settings.theater_buy_tickets,
        "min_gold_millions": settings.theater_min_gold_millions,
        "target_acts": settings.theater_target_acts,
    })
    arena = config.arena.model_copy(update={
        "quick_match": settings.arena_quick_match,
        "attack_players": settings.arena_attack_players,
        "friendship_refill": settings.arena_friendship_refill,
        "skystone_refill": settings.arena_skystone_refill,
        "target_fights": settings.arena_target_fights,
    })
    story = config.story.model_copy(update={"max_revives": settings.story_max_revives})
    shop = config.shop.model_copy(update={
        "refreshes": settings.shop_refreshes,
        "stop_bookmarks": settings.shop_stop_bookmarks,
        "stop_medals": settings.shop_stop_medals,
    })
    penguins = config.penguins.model_copy(update={"kind": settings.penguins_kind, "amount": settings.penguins_amount})
    return config.model_copy(update={
        "reroll": reroll, "input": input_config, "summon": summon, "reset": reset, "gear": gear, "story": story,
        "theater": theater, "arena": arena, "shop": shop, "penguins": penguins,
    })


def gear_policy(gear: GearConfig) -> GearPolicy:
    """Return the gear rules of the config, as the judge takes them."""
    return GearPolicy(
        chase=gear.chase,
        min_speed_speed_set=gear.min_speed_speed_set,
        min_speed_other_sets=gear.min_speed_other_sets,
        efficiency=gear.efficiency,
        role_efficiency=tuple(gear.role_efficiency.items()),
        roles=tuple(gear.roles),
        slot_targets=tuple((key, slot, value) for key, slots in gear.slot_targets.items() for slot, value in slots.items()),
        speed_slot_targets=tuple(gear.speed_targets.items()),
        max_level=gear.max_level,
        new_bad_action=gear.new_bad_action,
        sell_failed=gear.sell_failed,
        lock_finished=gear.lock_finished,
        flat_main_needs_speed=gear.flat_main_needs_speed,
        stone=gear.stone,
        stone_min_efficiency=gear.stone_min_efficiency,
        stone_min_efficiency_right=gear.stone_min_efficiency_right,
    )

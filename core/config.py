"""Pydantic schema and loader for config.yaml. Any invalid value fails at startup."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.gear import ALL_ROLES


class ConfigError(Exception):
    """Raised when config.yaml is missing, unparsable or invalid."""


class State(str, Enum):
    """Every state the bot can be in."""

    BOOT = "BOOT"
    LAUNCH = "LAUNCH"
    LOGIN = "LOGIN"
    STORY = "STORY"
    TUTORIAL_COMBAT = "TUTORIAL_COMBAT"
    POPUP_DISMISS = "POPUP_DISMISS"
    STAGE_SELECT = "STAGE_SELECT"
    STAGE_LOADING = "STAGE_LOADING"
    COMBAT_IDLE = "COMBAT_IDLE"
    COMBAT_TARGET = "COMBAT_TARGET"
    COMBAT_RESULT = "COMBAT_RESULT"
    REWARD_COLLECT = "REWARD_COLLECT"
    SUMMON = "SUMMON"
    ROUTE = "ROUTE"
    RESET_ACCOUNT = "RESET_ACCOUNT"
    RECOVER = "RECOVER"
    HALT = "HALT"


class StrictModel(BaseModel):
    """Base model that rejects unknown keys, so a typo in the YAML fails loud."""

    model_config = ConfigDict(extra="forbid")


class Range(StrictModel):
    """Inclusive numeric range used for randomized durations."""

    min: float = Field(ge=0)
    max: float = Field(ge=0)

    @model_validator(mode="after")
    def check_order(self) -> "Range":
        """Reject inverted ranges."""
        if self.min > self.max:
            raise ValueError(f"min ({self.min}) is greater than max ({self.max})")
        return self


class WindowConfig(StrictModel):
    """How to find the game window and which client size to enforce."""

    process_name: str = Field(min_length=1)
    class_names: list[str] = []
    titles: list[str] = []
    excluded_process_names: list[str] = []
    client_width: int = Field(gt=0)
    client_height: int = Field(gt=0)
    resize_max_attempts: int = Field(default=10, gt=0)


class VisionConfig(StrictModel):
    """Template matching, OCR and loading detection settings."""

    templates_dir: Path
    manifest: Path
    default_threshold: float = Field(default=0.86, gt=0, le=1)
    template_thresholds: dict[str, float] = {}
    scale_min: float = Field(default=0.9, gt=0)
    scale_max: float = Field(default=1.1, gt=0)
    scale_step: float = Field(default=0.05, gt=0)
    ocr_engine: Literal["tesseract"] = "tesseract"
    tesseract_cmd: str | None = None
    ocr_language: str = "eng"
    loading_no_match_seconds: float = Field(default=1.5, gt=0)
    loading_frame_diff_threshold: float = Field(default=2.0, ge=0)

    @field_validator("template_thresholds")
    @classmethod
    def check_thresholds(cls, value: dict[str, float]) -> dict[str, float]:
        """Keep per-template thresholds in the range TM_CCOEFF_NORMED can use."""
        for name, threshold in value.items():
            if not 0 < threshold <= 1:
                raise ValueError(f"threshold for {name!r} must be in (0, 1], got {threshold}")
        return value

    @model_validator(mode="after")
    def check_scales(self) -> "VisionConfig":
        """Reject an inverted scale range."""
        if self.scale_min > self.scale_max:
            raise ValueError("scale_min is greater than scale_max")
        return self


class InputConfig(StrictModel):
    """Mouse and keyboard timing."""

    click_hold_ms: Range
    pre_action_delay_ms: Range
    jitter_px: int = Field(default=3, ge=0)
    seed: int | None = None
    mouse_backend: Literal["sendinput", "pyautogui"] = "sendinput"
    background: bool = False
    click_marker: bool = False


class WatchdogConfig(StrictModel):
    """Per-state timeouts and recovery limits."""

    state_timeouts_seconds: dict[State, float]
    recover_max_retries: int = Field(default=3, ge=0)
    recover_wait_seconds: float = Field(default=2.0, ge=0)
    max_consecutive_reset_failures: int = Field(default=2, gt=0)
    tick_seconds: float = Field(default=0.1, ge=0)

    @field_validator("state_timeouts_seconds")
    @classmethod
    def check_timeouts(cls, value: dict[State, float]) -> dict[State, float]:
        """Require a positive timeout for every state except HALT, so nothing blocks forever."""
        missing = [s.value for s in State if s is not State.HALT and s not in value]
        if missing:
            raise ValueError(f"missing timeouts for: {', '.join(missing)}")
        not_positive = [s.value for s, seconds in value.items() if seconds <= 0]
        if not_positive:
            raise ValueError(f"timeouts must be positive for: {', '.join(not_positive)}")
        return value


class PopupConfig(StrictModel):
    """Templates scanned and dismissed before every state action."""

    preempt_templates: list[str]
    disabled_states: list[State] = []


class ResetConfig(StrictModel):
    """In-game account reset and client launch settings."""

    enabled: bool = False
    launch_command: str = Field(min_length=1)


class RerollConfig(StrictModel):
    """What the reroll aims at. The bot window can change the first three."""

    server: str = Field(min_length=1)
    skip_hold_seconds: float = Field(default=5.0, gt=0)
    stop_at_stage: str | None = Field(default=None, pattern=r"^.+ \d+-\d+$")


class CombatConfig(StrictModel):
    """Combat loop settings."""

    use_auto_battle: bool = True
    poll_interval_seconds: float = Field(default=0.5, gt=0)
    enemy_templates: list[str] = []


class TopRule(StrictModel):
    """Keep an account holding at least `count` 5-star Moonlight heroes among the `rank` best of the ranking."""

    count: int = Field(ge=1, le=21)
    rank: int = Field(ge=1, le=200)


class SummonConfig(StrictModel):
    """Which summon results keep an account."""

    min_moonlight_5star: int = Field(default=1, ge=1, le=21)
    stop_heroes: list[str] = []
    top_rules: list[TopRule] = []
    expected_pulls: int = Field(default=21, gt=0)
    # The long route: claim the free summons of the current event and pull them too.
    event_summons: bool = True


class LoggingConfig(StrictModel):
    """Log files and screenshots."""

    log_dir: Path
    file_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    console_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = Field(default=5_000_000, gt=0)
    backup_count: int = Field(default=5, ge=0)
    state_screenshots: bool = False


class GearConfig(StrictModel):
    """How the gear modes judge a piece. Defaults are for a new account."""

    # speed: roll only what is worth its speed; score: only what is worth its substats; both.
    chase: Literal["speed", "score", "both"] = "both"
    # Speed a piece must still be able to reach at +15 once reforged, on the speed set and on the others.
    min_speed_speed_set: float = Field(default=18, ge=0)
    min_speed_other_sets: float = Field(default=18, ge=0)
    # The efficiency asked, in % of a perfect piece for the role: a finished piece under the target speed is kept from
    # it, and a piece is rolled while its potential can reach it. role_efficiency sets it apart for some roles; roles
    # are those judged (core.gear.ROLE_WEIGHTS).
    efficiency: float = Field(default=65, ge=0, le=100)
    role_efficiency: dict[str, float] = {}
    roles: list[str] = list(ALL_ROLES)
    # Targets per slot applied from the account evaluation: per role or "general", and the speed set's speed.
    slot_targets: dict[str, dict[str, float]] = {}
    speed_targets: dict[str, float] = {}
    max_level: Literal[12, 15] = 15
    new_bad_action: Literal["extract", "sell"] = "extract"
    sell_failed: bool = True
    lock_finished: bool = True
    flat_main_needs_speed: bool = True
    # The modification stone: always counted on, never, or only for pieces whose efficiency with it reaches
    # stone_min_efficiency.
    stone: Literal["always", "great", "never"] = "great"
    stone_min_efficiency: float = Field(default=100, ge=0, le=100)
    # The same for a necklace, ring or boots: lower, a good one is rarer.
    stone_min_efficiency_right: float = Field(default=98, ge=0, le=100)
    # The gear modes that act in the game. Dry run judges every piece and acts on none.
    dry_run: bool = False
    # Sort only: tick in Arrange every piece it would sell or extract, then stop there with nothing done.
    review: bool = False
    # Save the Enhance screen of every piece judged, with the decision, under logs/gear.
    screenshots: bool = False
    # Pieces changed in a run (enhanced, sold or extracted) and gold spent, 0 for no limit.
    max_pieces: int = Field(default=0, ge=0)
    max_gold_millions: float = Field(default=0, ge=0)
    # Judge only the pieces still at +0.
    only_new: bool = False
    # The Craft mode: the hunt and the piece of the Steel Workshop, how many, and what follows.
    craft_hunt: Literal["wyvern", "golem", "banshee", "azimanak", "caides"] = "wyvern"
    craft_slot: Literal["weapon", "helmet", "armor", "necklace", "ring", "boots"] = "weapon"
    craft_pieces: int = Field(default=10, ge=1, le=500)
    # Crafted pieces are +0: they can only be sorted (the bad ones extracted or sold, as new_bad_action says).
    craft_then: Literal["sort", "none"] = "sort"

    @field_validator("craft_then", mode="before")
    @classmethod
    def _no_roll_after_craft(cls, value: object) -> object:
        """Read the old "roll" as "sort": crafted pieces are not rolled."""
        return "sort" if value == "roll" else value


class StoryConfig(StrictModel):
    """How the story mode of a kept account spends on a lost fight."""

    # Skystone revives the bot may accept in one fight. 0 never spends a skystone on a revive.
    max_revives: int = Field(default=0, ge=0, le=10)


class TheaterConfig(StrictModel):
    """Whether the Moonlight Theater buys tickets with gold, and the gold it never goes under."""

    buy_tickets: bool = True
    min_gold_millions: float = Field(default=20, ge=0)
    # Acts to watch in a run, 0 for every one left.
    target_acts: int = Field(default=0, ge=0)


class ArenaConfig(StrictModel):
    """How the NPC Challenge of the Arena fights."""

    quick_match: bool = True
    attack_players: bool = False
    friendship_refill: bool = False
    skystone_refill: bool = False
    # Fights to do in a run, 0 for as long as flags are left without skystones.
    target_fights: int = Field(default=0, ge=0)


class ShopConfig(StrictModel):
    """How the Shop refresh refreshes the Secret Shop, and when it stops."""

    # Refreshes to pay for in a run, 3 skystones each. 0 buys from the shop in view and stops.
    refreshes: int = Field(default=10, ge=0)
    # Stop once this many Covenant Bookmarks or Mystic Medals were bought in the run, 0 for no target.
    stop_bookmarks: int = Field(default=0, ge=0)
    stop_medals: int = Field(default=0, ge=0)


class PenguinsConfig(StrictModel):
    """How many Penguin Nests the penguin mode buys, 50 penguins for 5,100 stigma each."""

    # "purchases": stop after `amount` purchases; "stigma": stop before a purchase would spend more than `amount`.
    kind: Literal["purchases", "stigma"] = "purchases"
    amount: int = Field(default=1, ge=1)


class AppConfig(StrictModel):
    """Root of config.yaml."""

    window: WindowConfig
    vision: VisionConfig
    input: InputConfig
    watchdog: WatchdogConfig
    popups: PopupConfig
    reset: ResetConfig
    reroll: RerollConfig
    combat: CombatConfig
    summon: SummonConfig
    gear: GearConfig = GearConfig()
    story: StoryConfig = StoryConfig()
    theater: TheaterConfig = TheaterConfig()
    arena: ArenaConfig = ArenaConfig()
    shop: ShopConfig = ShopConfig()
    penguins: PenguinsConfig = PenguinsConfig()
    route: list[str] = []
    logging: LoggingConfig
    transitions: dict[State, list[State]]

    @model_validator(mode="after")
    def check_transitions(self) -> "AppConfig":
        """Reject a transition table the state machine could get stuck in."""
        problems = transition_problems(self.transitions)
        if problems:
            raise ValueError("invalid transitions: " + "; ".join(problems))
        return self

    def startup_warnings(self) -> list[str]:
        """Return settings that are valid but leave part of the loop unable to do its job."""
        warnings = []
        if not self.reset.enabled:
            warnings.append("reset.enabled is false: the bot stops instead of resetting an account")
        return warnings


def transition_problems(transitions: dict[State, list[State]]) -> list[str]:
    """Return every structural problem in a transition table."""
    problems = []
    missing = [s.value for s in State if s not in transitions]
    if missing:
        problems.append(f"no entry for {', '.join(missing)}")
    if transitions.get(State.HALT):
        problems.append("HALT must have no outgoing transitions")
    for state, targets in transitions.items():
        if state in (State.HALT, State.RECOVER):
            continue
        if State.RECOVER not in targets:
            problems.append(f"{state.value} cannot reach RECOVER, the watchdog needs it")
    recover_targets = transitions.get(State.RECOVER, [])
    for required in (State.RESET_ACCOUNT, State.HALT):
        if required not in recover_targets:
            problems.append(f"RECOVER cannot reach {required.value}")

    reached = {State.BOOT}
    to_visit = [State.BOOT]
    while to_visit:
        for target in transitions.get(to_visit.pop(), []):
            if target not in reached:
                reached.add(target)
                to_visit.append(target)
    unreachable = [s.value for s in State if s not in reached]
    if unreachable:
        problems.append(f"unreachable from BOOT: {', '.join(unreachable)}")
    return problems


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load and validate a config file, raising ConfigError with a readable message."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as error:
        lines = [
            f"  {'.'.join(str(part) for part in item['loc']) or '(root)'}: {item['msg']}"
            for item in error.errors()
        ]
        raise ConfigError(f"{path} is invalid:\n" + "\n".join(lines)) from error

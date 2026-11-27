"""StateMachine: tick loop, transition checks, popup preemption, watchdog and failure handling."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from core.config import AppConfig, State
from core.input import InputController
from core.logger import log_state_event, save_failure_screenshot, save_state_screenshot
from core.stop import BotStopped
from core.vision import LoadingDetector, Match, Ocr, Region, TemplateLibrary
from core.window import WindowLostError

BLANK_STD = 1.0
MIN_CARD_STARS = 3
MAX_CARD_STARS = 5
# The Insufficient Inventory popup's title, and its text, which names the full inventory.
INVENTORY_FULL_REGION = (460, 190, 360, 66)
INVENTORY_TEXT_REGION = (380, 335, 520, 50)
INVENTORIES = (("artifact", "artifacts"), ("equipment", "equipment"), ("hero", "heroes"))
# States that never get popups dismissed: nothing to click yet, or already handling something.
NO_PREEMPT_STATES = {State.BOOT, State.LAUNCH, State.POPUP_DISMISS, State.RECOVER, State.HALT}


class GameSession(Protocol):
    """The live connection to the game window."""

    capture: "CaptureLike"
    input: InputController
    window: "ForegroundWindow"

    def close(self) -> None:
        ...


class CaptureLike(Protocol):
    """What the machine needs from a Capture."""

    def grab(self, max_age_seconds: float = 0.0) -> np.ndarray:
        ...


class ForegroundWindow(Protocol):
    """What the machine needs from a GameWindow."""

    def is_foreground(self) -> bool:
        """Return whether the game has the focus."""
        ...

    def ensure_foreground(self) -> bool:
        ...


@dataclass
class Card:
    """One summon result. A card is a hero or an artifact, whichever shows stars at its place."""

    hero_stars: int = 0
    artifact_stars: int = 0
    moonlight: bool = False
    name: str = ""
    # The single summon's screen shows no icon the bot reads, so its element stays unknown.
    element_known: bool = True

    def readable(self) -> bool:
        """Return whether exactly one kind of stars was read, and a plausible number of them."""
        stars = (self.hero_stars, self.artifact_stars)
        return 0 in stars and MIN_CARD_STARS <= max(stars) <= MAX_CARD_STARS

    def __str__(self) -> str:
        if self.hero_stars:
            return f"{self.name or '?'}{' ML' if self.moonlight else ''} {self.hero_stars}*"
        return f"artifact {self.artifact_stars}*"


@dataclass
class SummonProgress:
    """What has been done with the free summons of the current account."""

    free_claimed: bool = False
    book_clicked: bool = False
    list_drags: int = 0
    cards_read: int = 0
    kept: list[str] = field(default_factory=list)
    moonlight_5star: list[str] = field(default_factory=list)
    unsure: list[str] = field(default_factory=list)
    reads: int = 0
    current: list[Card] = field(default_factory=list)
    result_done: bool = False
    finished: bool = False
    pulling: bool = False
    # The long route's pass over the event's free summons is over.
    event_done: bool = False
    unread_retries: int = 0


@dataclass
class Context:
    """Everything a state handler can use. Handlers do one short step and return the next state."""

    config: AppConfig
    logger: logging.Logger
    library: TemplateLibrary
    ocr: Ocr
    open_session: Callable[[], GameSession | None]
    stop_event: threading.Event = field(default_factory=threading.Event)
    dry_run: bool = False
    once: bool = False
    story_only: bool = False
    # The long route: which goal, which step, and where the story must hand over to it.
    goal: str = "summons"
    route_step: int = 0
    route_data: dict = field(default_factory=dict)
    # What one route step tells a later one on the same account, such as the pass still unbought.
    route_flags: dict = field(default_factory=dict)
    story_target: str | None = None
    story_reached: str | None = None
    fails_in_a_row: int = 0
    # Skystone revives accepted in the current fight, and when the last one was clicked while its popup still shows.
    revives_this_fight: int = 0
    revive_clicked_at: float | None = None
    revive_seen_at: float | None = None
    revive_escapes: int = 0
    # Times the stage list had a locked stage selected and the one above it was clicked.
    locked_stage_picks: int = 0
    # A cleared stage selected in the list: the stage it was, and how many steps were tried on it to move on.
    cleared_stage: str | None = None
    cleared_stage_steps: int = 0
    # Select Episode, opened from a cleared stage to reach the stage the story stands at: trips there since a stage
    # was last started, whether the pinned episode was clicked, and the scrolls tried to find a pin.
    episode_trips: int = 0
    episode_picked: bool = False
    episode_scrolls: int = 0
    # Leaves spent on energy in this run, and whether the leaf option was just picked in the popup.
    leaves_used: int = 0
    leaf_picked: bool = False
    on_route_step: Callable[[int], None] | None = None
    on_route_pulls: Callable[[dict], None] | None = None
    auto_icon: np.ndarray | None = None
    auto_read_at: float = 0.0
    auto_still_since: float | None = None
    session: GameSession | None = None
    frame: np.ndarray | None = None
    interrupted: State | None = None
    pending_popup: Match | None = None
    halt_reason: str = ""
    recover_counts: dict[State, int] = field(default_factory=dict)
    reset_failures: int = 0
    reset_confirmed: bool = False
    resets_done: int = 0
    launched: bool = False
    reset_pending: bool = False
    # The route asked for this reset without running a single step since the start: its saved state
    # may belong to an account the user has reset by hand.
    reset_from_saved_route: bool = False
    route_steps_this_run: int = 0
    going_home: bool = False
    skills_tried: int = 0
    leave_presses: int = 0
    home_presses: int = 0
    wake_clicks: int = 0
    last_click: tuple[int, int, float] | None = None
    fight_seen_at: float | None = None
    nickname_attempt: int = 0
    screen: str = ""
    stage: str = ""
    start_clicked_at: float | None = None
    start_quest_clicks: int = 0
    adding_hero: bool = False
    placing_hero: bool = False
    hero_added_on: str | None = None
    progressed: bool = False
    unknown_since: float | None = None
    unknown_tapped: bool = False
    on_stage: Callable[[str], None] | None = None
    on_result: Callable[[int, int, str], None] | None = None
    on_cards: Callable[[list[Card]], None] | None = None
    on_run_done: Callable[[float], None] | None = None
    # Pushing the story of a kept account: a stage won, a fight lost.
    on_story_won: Callable[[], None] | None = None
    on_story_lost: Callable[[], None] | None = None
    on_story_revive: Callable[[], None] | None = None
    # Climbing the Abyss of a kept account: a floor won, a fight lost.
    on_floor_won: Callable[[], None] | None = None
    on_floor_lost: Callable[[], None] | None = None
    on_theater_act: Callable[[], None] | None = None
    on_theater_ticket: Callable[[], None] | None = None
    on_arena_fight: Callable[[bool, bool], None] | None = None
    on_arena_skystones: Callable[[int], None] | None = None
    on_shop_refresh: Callable[[], None] | None = None
    on_shop_buy: Callable[[str, int, int], None] | None = None
    on_penguin_buy: Callable[[int], None] | None = None
    on_penguin_kinds: Callable[[dict[str, int]], None] | None = None
    on_huche_buy: Callable[[str, int], None] | None = None
    # Gear modes: an event (judged, enhanced, sold, extracted, locked, crafted, gold) and its amount.
    on_gear: Callable[[str, int], None] | None = None
    on_leaf: Callable[[], None] | None = None
    account_started_at: float | None = None
    last_server_click: float = 0.0
    last_title_tap: float = 0.0
    title_tapped: bool = False
    lobby_since: float | None = None
    summon: SummonProgress = field(default_factory=SummonProgress)
    on_reset: Callable[[], None] | None = None
    matched_this_tick: bool = False
    sleep: Callable[[float], None] | None = None

    def start_new_account(self) -> None:
        """Forget what belongs to the account that was just reset, or the next one would inherit it."""
        self.summon = SummonProgress()
        self.save_route_pulls()
        self.account_started_at = time.monotonic()
        self.fight_seen_at = None
        self.nickname_attempt = 0
        self.stage = ""
        self.hero_added_on = None
        self.adding_hero = False
        self.placing_hero = False
        self.start_clicked_at = None
        self.start_quest_clicks = 0
        self.skills_tried = 0
        self.leave_presses = 0
        self.home_presses = 0
        self.going_home = False
        self.lobby_since = None
        self.set_route_step(0)
        self.route_flags = {}
        self.story_target = None
        self.story_reached = None
        self.cleared_stage = None
        self.cleared_stage_steps = 0
        self.episode_trips = 0

    def save_route_pulls(self) -> None:
        """Save what the pulls found so far, which the keep decision needs after a restart."""
        if self.on_route_pulls is not None:
            progress = self.summon
            self.on_route_pulls({"cards_read": progress.cards_read, "moonlight_5star": list(progress.moonlight_5star),
                                 "unsure": list(progress.unsure)})

    def set_route_step(self, step: int) -> None:
        """Move the long route to a step, with fresh scratch data, and save it to survive a restart."""
        self.route_step = step
        self.route_data = {}
        if self.on_route_step is not None:
            self.on_route_step(step)

    def wait(self, seconds: float) -> None:
        """Wait for a short time. The emergency stop interrupts it at once."""
        if self.sleep is not None:
            self.sleep(seconds)
        elif self.stop_event.wait(seconds):
            raise BotStopped()

    def find(self, name: str, region: Region | None = None) -> Match | None:
        """Look for a template in the current frame and remember that the game showed something."""
        if self.frame is None:
            return None
        match = self.library.find(self.frame, name, region)
        if match is not None:
            self.matched_this_tick = True
        return match

    def attach(self) -> bool:
        """Connect to the game window if needed. Returns whether a session is open."""
        if self.session is None:
            self.session = self.open_session()
        return self.session is not None

    def detach(self) -> None:
        """Drop the session after the game has closed."""
        if self.session is not None:
            self.session.close()
        self.session = None
        self.frame = None

    def logged_in_elsewhere(self) -> bool:
        """Return whether the frame shows something, rather than a blank loading screen."""
        return self.frame is not None and float(self.frame.std()) >= BLANK_STD

    def halt(self, reason: str) -> State:
        """Record why the bot stops and return HALT."""
        self.halt_reason = reason
        return State.HALT


StateHandler = Callable[[Context], State]


def inventory_full(ctx: Context) -> str | None:
    """Return why the bot stops if the game refuses something for a full inventory, otherwise None.

    The bot clicks nothing on it: making room is the user's job, at least for now.
    """
    if ctx.find("inventory_full", INVENTORY_FULL_REGION) is None:
        return None
    text = ctx.ocr.read(ctx.frame, INVENTORY_TEXT_REGION, single_line=False).casefold()
    kind = next((name for word, name in INVENTORIES if word in text), "unknown")
    return f"cannot start: the {kind} inventory is full"


class StateMachine:
    """Runs the state handlers until HALT."""

    def __init__(
        self,
        context: Context,
        handlers: dict[State, StateHandler],
        clock: Callable[[], float] = time.monotonic,
        on_frame: Callable[[np.ndarray, State], None] | None = None,
        on_state: Callable[[State], None] | None = None,
    ) -> None:
        """Start in BOOT. `clock`, `on_frame` and `on_state` serve the tests, overlay and window."""
        self.on_state = on_state
        self.ctx = context
        self.handlers = handlers
        self.clock = clock
        self.on_frame = on_frame
        self.config = context.config
        self.logger = context.logger
        self.state = State.BOOT
        self.entered_at = clock()
        self.loading = LoadingDetector(
            self.config.vision.loading_no_match_seconds,
            self.config.vision.loading_frame_diff_threshold,
        )
        self.is_loading = False

    def run(self) -> str:
        """Tick until HALT and return the reason."""
        log_state_event(self.logger, "enter", self.state.value, None)
        while self.state is not State.HALT:
            self.tick()
            if self.state is not State.HALT:
                try:
                    self.ctx.wait(self.config.watchdog.tick_seconds)
                except BotStopped:
                    self._halt("stopped by the user")
        self._run_handler_safely()
        return self.ctx.halt_reason

    def tick(self) -> None:
        """Do one step: frame, watchdog, popups, then the current state's handler."""
        try:
            if self.ctx.stop_event.is_set():
                raise BotStopped()
            self._grab_frame()
            if self._inventory_full():
                return
            if self._watchdog_expired():
                self._fail(f"watchdog expired in {self.state.value}")
                return
            if self._preempt_popup():
                return
            self.ctx.matched_this_tick = False
            next_state = self._run_handler()
            self._track_loading()
            # The story can last an hour. What counts there is not staying stuck on one screen.
            if self.ctx.progressed:
                self.ctx.progressed = False
                self.entered_at = self.clock()
            self._transition(next_state)
        except BotStopped:
            self._halt("stopped by the user")
        except WindowLostError as error:
            self.logger.error("the game window is gone: %s", error)
            self.ctx.detach()
            self._fail("game window lost")
        except Exception as error:
            self.logger.exception("error in state %s", self.state.value)
            self._fail(f"{type(error).__name__} in {self.state.value}")

    def _grab_frame(self) -> None:
        """Read the current frame and take the focus back if it was lost."""
        session = self.ctx.session
        if session is None:
            self.ctx.frame = None
            return
        if not self.ctx.dry_run and not self.config.input.background and not session.window.is_foreground():
            self.logger.warning("the game lost the focus, taking it back")
            session.window.ensure_foreground()
        self.ctx.frame = session.capture.grab()
        if self.on_frame is not None:
            self.on_frame(self.ctx.frame, self.state)

    def _watchdog_expired(self) -> bool:
        """Return whether the current state has run out of time.

        POPUP_DISMISS shares the timer of the state it interrupted and does one short
        click, so it is checked when that state resumes.
        """
        if self.state is State.POPUP_DISMISS:
            return False
        limit = self.config.watchdog.state_timeouts_seconds.get(self.state)
        return limit is not None and self.clock() - self.entered_at > limit

    def _inventory_full(self) -> bool:
        """Stop on the Insufficient Inventory popup, in any mode: only the player can make room."""
        if self.state is State.HALT:
            return False
        reason = inventory_full(self.ctx)
        if reason is None:
            return False
        self._save_failure("inventory full")
        self._halt(reason)
        return True

    def _preempt_popup(self) -> bool:
        """Switch to POPUP_DISMISS if a popup is visible and this state allows it."""
        if self.state in NO_PREEMPT_STATES or self.state in self.config.popups.disabled_states:
            return False
        if self.ctx.frame is None:
            return False
        match = self.ctx.library.find_first(self.ctx.frame, self.config.popups.preempt_templates)
        if match is None:
            return False
        self.ctx.matched_this_tick = True
        self.ctx.pending_popup = match
        self.ctx.interrupted = self.state
        self._enter(State.POPUP_DISMISS)
        return True

    def _run_handler(self) -> State:
        """Call the current state's handler. A state without one stops the bot cleanly."""
        handler = self.handlers.get(self.state)
        if handler is None:
            return self.ctx.halt(f"no handler for state {self.state.value}")
        return handler(self.ctx)

    def _run_handler_safely(self) -> None:
        """Run the HALT handler, which must not prevent a clean exit."""
        try:
            self._run_handler()
        except Exception:
            self.logger.exception("error in the HALT handler")

    def _track_loading(self) -> None:
        """Log when the game starts and stops looking like it is loading."""
        if self.ctx.frame is None:
            return
        loading = self.loading.update(self.ctx.frame, self.ctx.matched_this_tick, self.clock())
        if loading != self.is_loading:
            self.logger.info("loading signal %s in %s", "on" if loading else "off", self.state.value)
            self.is_loading = loading

    def _transition(self, next_state: State) -> None:
        """Apply a handler's result. Anything the table does not allow counts as a failure."""
        if next_state is self.state:
            return
        if next_state is not State.HALT and next_state not in self.config.transitions[self.state]:
            self._fail(f"unexpected transition {self.state.value} -> {next_state.value}")
            return
        self._enter(next_state)

    def _enter(self, next_state: State) -> None:
        """Log the change and start the new state's timer."""
        previous = self.state
        if self.on_state is not None:
            self.on_state(next_state)
        log_state_event(self.logger, "exit", previous.value, self.ctx.frame)
        # A popup must not restart the timer, or one that never closes would block the bot forever.
        back_from_popup = previous is State.POPUP_DISMISS and next_state is self.ctx.interrupted
        self.state = next_state
        if next_state is not State.POPUP_DISMISS and not back_from_popup:
            self.entered_at = self.clock()
        if previous is State.RESET_ACCOUNT and next_state is State.LOGIN:
            self.ctx.reset_failures = 0
        if next_state not in (State.RECOVER, State.POPUP_DISMISS) and next_state is not self.ctx.interrupted:
            self.ctx.recover_counts.clear()
        log_state_event(self.logger, "enter", next_state.value, self.ctx.frame)
        if self.ctx.frame is not None:
            save_state_screenshot(self.ctx.frame, self.config.logging, next_state.value)

    def _save_failure(self, reason: str) -> None:
        """Save a screenshot of a failure when there is a frame."""
        if self.ctx.frame is not None:
            path = save_failure_screenshot(self.ctx.frame, self.config.logging, reason)
            self.logger.error("%s, screenshot: %s", reason, path)
        else:
            self.logger.error("%s, no frame to save", reason)

    def _fail(self, reason: str) -> None:
        """Record a failure and enter RECOVER, or HALT when recovering is pointless."""
        self._save_failure(reason)
        if self.state is State.RECOVER:
            self._halt(f"recovery failed: {reason}")
            return
        if self.state is State.RESET_ACCOUNT:
            self.ctx.reset_failures += 1
            limit = self.config.watchdog.max_consecutive_reset_failures
            if self.ctx.reset_failures >= limit:
                self._halt(f"{self.ctx.reset_failures} consecutive reset failures: {reason}")
                return
        self.ctx.interrupted = self.state
        self._enter(State.RECOVER)

    def _halt(self, reason: str) -> None:
        self.ctx.halt_reason = reason
        self._enter(State.HALT)

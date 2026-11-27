"""State machine: watchdog, transitions, popup preemption, recovery limits and stops."""

import logging
from pathlib import Path

import numpy as np
import pytest

from core.config import State, load_config
from core.manifest import load_manifest
from core.state import Context, StateMachine
from core.stop import BotStopped, StopWatcher
from core.vision import Match, Ocr, TemplateLibrary
from core.window import WindowLostError
from states import HANDLERS
from states.recover import recover

ROOT = Path(__file__).parent.parent
LOGGER = logging.getLogger("test_state")
FRAME = np.random.default_rng(0).integers(0, 255, (72, 128, 3), dtype=np.uint8)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeInput:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.keys: list[str] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def press(self, key: str) -> None:
        self.keys.append(key)


class FakeCapture:
    def __init__(self) -> None:
        self.error: Exception | None = None

    def grab(self, max_age_seconds: float = 0.0) -> np.ndarray:
        if self.error:
            raise self.error
        return FRAME


class FakeWindow:
    def __init__(self) -> None:
        self.foreground = True
        self.refocus = 0

    def is_foreground(self) -> bool:
        return self.foreground

    def ensure_foreground(self) -> bool:
        self.refocus += 1
        return True


class FakeSession:
    def __init__(self) -> None:
        self.capture = FakeCapture()
        self.input = FakeInput()
        self.window = FakeWindow()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeLibrary(TemplateLibrary):
    """A library where the visible templates are set by the test."""

    def __init__(self, config, logger) -> None:
        super().__init__(config, logger)
        self.visible: set[str] = set()

    def find(self, frame, name, region=None):
        return Match(name, 100, 100, 20, 10, 0.99, 1.0) if name in self.visible else None


@pytest.fixture
def config(tmp_path):
    config = load_config(ROOT / "config.yaml")
    return config.model_copy(
        update={"logging": config.logging.model_copy(update={"log_dir": tmp_path})}
    )


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def ctx(config, session) -> Context:
    return Context(
        config=config,
        logger=LOGGER,
        library=FakeLibrary(config.vision, LOGGER),
        ocr=Ocr(config.vision),
        open_session=lambda: session,
        session=session,
        sleep=lambda seconds: None,
    )


def machine(ctx, handlers=None, state=State.BOOT, clock=None) -> StateMachine:
    all_handlers = {**HANDLERS, **(handlers or {})}
    built = StateMachine(ctx, all_handlers, clock=clock or FakeClock())
    built.state = state
    return built


def failures(config) -> list[Path]:
    return list((config.logging.log_dir / "failures").glob("*.png"))


def test_every_transition_target_can_be_entered_from_boot_table(config):
    assert State.LAUNCH in config.transitions[State.BOOT]
    assert config.transitions[State.HALT] == []


def test_boot_goes_to_login_when_the_game_is_open(ctx):
    bot = machine(ctx)
    bot.tick()
    assert bot.state is State.LOGIN


def test_boot_goes_to_launch_without_a_game(ctx):
    ctx.session = None
    ctx.open_session = lambda: None
    bot = machine(ctx)
    bot.tick()
    assert bot.state is State.LAUNCH


def test_state_without_handler_halts_with_a_reason(ctx):
    bot = machine(ctx, state=State.COMBAT_IDLE)
    bot.tick()
    assert bot.state is State.HALT
    assert "no handler for state COMBAT_IDLE" in ctx.halt_reason


def test_returning_the_same_state_keeps_the_timer(ctx):
    clock = FakeClock()
    bot = machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN, clock)
    entered = bot.entered_at
    clock.now += 10
    bot.tick()
    assert bot.state is State.LOGIN and bot.entered_at == entered


def test_watchdog_fires_with_a_screenshot_and_enters_recover(ctx, config):
    clock = FakeClock()
    bot = machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN, clock)
    clock.now += config.watchdog.state_timeouts_seconds[State.LOGIN] + 1
    bot.tick()
    assert bot.state is State.RECOVER
    assert ctx.interrupted is State.LOGIN
    assert len(failures(config)) == 1


def test_watchdog_does_not_fire_before_the_limit(ctx, config):
    clock = FakeClock()
    bot = machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN, clock)
    clock.now += config.watchdog.state_timeouts_seconds[State.LOGIN] - 1
    bot.tick()
    assert bot.state is State.LOGIN


def test_unexpected_transition_is_a_failure(ctx, config):
    bot = machine(ctx, {State.LOGIN: lambda c: State.SUMMON}, State.LOGIN)
    bot.tick()
    assert bot.state is State.RECOVER
    assert len(failures(config)) == 1


def test_handler_exception_is_a_failure_not_a_crash(ctx, config):
    def broken(_):
        raise ValueError("boom")

    bot = machine(ctx, {State.LOGIN: broken}, State.LOGIN)
    bot.tick()
    assert bot.state is State.RECOVER
    assert len(failures(config)) == 1


def test_popup_is_dismissed_before_the_state_acts(ctx, session):
    acted = []
    ctx.library.visible = {"confirm"}
    bot = machine(ctx, {State.LOGIN: lambda c: acted.append(1) or State.LOGIN}, State.LOGIN)
    bot.tick()
    assert bot.state is State.POPUP_DISMISS and not acted
    ctx.library.visible = set()
    bot.tick()
    assert session.input.clicks == [(110, 105)]
    assert bot.state is State.LOGIN


def test_popup_does_not_restart_the_watchdog(ctx, config):
    clock = FakeClock()
    ctx.library.visible = {"confirm"}
    bot = machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN, clock)
    entered = bot.entered_at
    clock.now += 5
    bot.tick()
    bot.tick()
    assert bot.state is State.LOGIN and bot.entered_at == entered


def test_endless_popup_ends_in_the_watchdog(ctx, config):
    clock = FakeClock()
    ctx.library.visible = {"confirm"}
    bot = machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN, clock)
    limit = config.watchdog.state_timeouts_seconds[State.LOGIN]
    for _ in range(1000):
        clock.now += 1
        bot.tick()
        if bot.state is State.RECOVER:
            break
    assert bot.state is State.RECOVER
    assert clock.now - 1000.0 <= limit + 2


@pytest.mark.parametrize("state", [State.RESET_ACCOUNT, State.SUMMON])
def test_popups_are_never_clicked_in_sensitive_states(ctx, session, state):
    ctx.library.visible = {"confirm", "popup_close", "skip", "tap_continue"}
    bot = machine(ctx, {state: lambda c: state}, state)
    bot.tick()
    assert bot.state is state
    assert session.input.clicks == []


def test_recover_presses_esc_and_clicks_back_then_retries(ctx, session):
    ctx.library.visible = {"back_button"}
    ctx.frame = FRAME
    ctx.interrupted = State.LOGIN
    assert recover(ctx) is State.LOGIN
    assert session.input.keys == ["esc"]
    assert session.input.clicks == [(110, 105)]


def test_recover_gives_up_on_a_state_after_the_retries(ctx, config):
    ctx.interrupted = State.LOGIN
    limit = config.watchdog.recover_max_retries
    results = [recover(ctx) for _ in range(limit + 1)]
    assert results[:limit] == [State.LOGIN] * limit
    assert results[limit] is State.RESET_ACCOUNT


def test_retry_counter_clears_when_the_bot_moves_on(ctx):
    ctx.interrupted = State.LOGIN
    ctx.recover_counts[State.LOGIN] = 2
    bot = machine(ctx, {State.LOGIN: lambda c: State.STORY}, State.LOGIN)
    bot.tick()
    assert ctx.recover_counts == {}


def test_boot_starts_with_the_reset_when_one_is_pending(ctx):
    ctx.reset_pending = True
    bot = machine(ctx)
    bot.tick()
    assert bot.state is State.RESET_ACCOUNT


def test_state_changes_are_reported(ctx):
    seen = []
    bot = StateMachine(ctx, HANDLERS, clock=FakeClock(), on_state=seen.append)
    bot.tick()
    assert seen == [State.LOGIN]


def test_two_consecutive_reset_failures_halt(ctx, config):
    clock = FakeClock()
    limit = config.watchdog.state_timeouts_seconds[State.RESET_ACCOUNT]
    bot = machine(ctx, {State.RESET_ACCOUNT: lambda c: State.RESET_ACCOUNT}, State.RESET_ACCOUNT, clock)
    clock.now += limit + 1
    bot.tick()
    assert bot.state is State.RECOVER and ctx.reset_failures == 1
    bot.tick()
    assert bot.state is State.RESET_ACCOUNT
    clock.now += limit + 1
    bot.tick()
    assert bot.state is State.HALT
    assert "2 consecutive reset failures" in ctx.halt_reason


def test_successful_reset_clears_the_failure_count(ctx):
    ctx.reset_failures = 1
    bot = machine(ctx, {State.RESET_ACCOUNT: lambda c: State.LOGIN}, State.RESET_ACCOUNT)
    bot.tick()
    assert ctx.reset_failures == 0


def test_failing_recover_halts(ctx, config):
    clock = FakeClock()
    bot = machine(ctx, state=State.RECOVER, clock=clock)
    ctx.interrupted = State.LOGIN
    clock.now += config.watchdog.state_timeouts_seconds[State.RECOVER] + 1
    bot.tick()
    assert bot.state is State.HALT


def test_lost_window_goes_through_recover_back_to_boot(ctx, session):
    session.capture.error = WindowLostError("gone")
    bot = machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN)
    bot.tick()
    assert bot.state is State.RECOVER and ctx.session is None and session.closed
    bot.tick()
    assert bot.state is State.BOOT


def in_foreground_mode(ctx) -> None:
    ctx.config = ctx.config.model_copy(update={"input": ctx.config.input.model_copy(update={"background": False})})


def test_focus_is_taken_back_in_foreground_mode(ctx, session):
    in_foreground_mode(ctx)
    session.window.foreground = False
    machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN).tick()
    assert session.window.refocus == 1


def test_background_mode_leaves_the_focus_alone(ctx, session):
    session.window.foreground = False
    machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN).tick()
    assert session.window.refocus == 0


def test_dry_run_leaves_the_focus_alone(ctx, session):
    ctx.dry_run = True
    session.window.foreground = False
    machine(ctx, {State.LOGIN: lambda c: State.LOGIN}, State.LOGIN).tick()
    assert session.window.refocus == 0


def test_stop_event_halts_at_the_next_tick(ctx):
    ctx.stop_event.set()
    bot = machine(ctx, {State.LOGIN: lambda c: pytest.fail("the state ran")}, State.LOGIN)
    bot.tick()
    assert bot.state is State.HALT
    assert "stopped by the user" in ctx.halt_reason


def test_stop_raised_inside_a_handler_halts_without_a_failure(ctx, config):
    def stopped(_):
        raise BotStopped()

    bot = machine(ctx, {State.LOGIN: stopped}, State.LOGIN)
    bot.tick()
    assert bot.state is State.HALT
    assert failures(config) == []


def test_wait_is_cut_short_by_the_stop_event(config):
    context = Context(
        config=config,
        logger=LOGGER,
        library=TemplateLibrary(config.vision, LOGGER),
        ocr=Ocr(config.vision),
        open_session=lambda: None,
    )
    context.stop_event.set()
    with pytest.raises(BotStopped):
        context.wait(30)


def test_run_ends_at_halt_and_closes_the_session(ctx, session):
    assert "no handler" in machine(ctx, state=State.COMBAT_IDLE).run()
    assert session.closed


def test_manifest_has_the_back_button():
    assert "back_button" in load_manifest(ROOT / "templates" / "manifest.yaml").names()


def test_stop_watcher_sees_a_real_key_press():
    import win32api
    import win32con

    # F7: the real stop keys may be held by a bot running on this machine.
    watcher = StopWatcher(win32con.VK_F7, "F7")
    watcher.start()
    try:
        win32api.keybd_event(win32con.VK_F7, 0, 0, 0)
        win32api.keybd_event(win32con.VK_F7, 0, win32con.KEYEVENTF_KEYUP, 0)
        assert watcher.event.wait(2)
    finally:
        watcher.close()


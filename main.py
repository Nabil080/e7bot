"""Entrypoint: opens the bot window by default, --probe checks the game window. The window's Stop button stops the bot."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from core.window import (
    ELEVATED_FLAG,
    GameWindow,
    WindowError,
    WindowNotFoundError,
    enable_dpi_awareness,
    find_game_window,
    is_dpi_aware,
    is_elevated,
    list_windows,
    open_game_window,
    relaunch_as_admin,
    window_dpi,
)

# Before the imports below, because nothing may touch a window before DPI awareness is set.
enable_dpi_awareness()

import cv2
import numpy as np

from core.capture import Capture
from core.click_marker import ClickMarker
from core.config import AppConfig, ConfigError, State, load_config
from core.input import InputController
from core.logger import LOG_FILE_NAME, frame_hash, setup_logging
from core.manifest import load_manifest
from core.advisor import AdviceBoard, run_advisor
from core.panel import ADVISOR_GOAL, Panel
from core.panel_window import run_window, show_error
from core.settings import Settings, apply_settings, load_settings, settings_from_config
from core.awake import keep_awake
from core.stats import (
    load_route_pulls,
    load_route_step,
    load_stats,
    load_total_rerolls,
    save_route_pulls,
    save_route_step,
    save_stats,
    save_total_rerolls,
)
from core.state import Context, StateMachine
from core.status import BotStatus, LogFeed
from core.clients import CLIENTS, state_path, window_config_for
from core.vision import Ocr, OcrUnavailableError, TemplateLibrary
from states import HANDLERS
from tools.debug_overlay import WINDOW_NAME, draw_overlay, find_boxes

OVERLAY_SCALE = 0.5
SETTINGS_PATH = Path("settings.json")
# The window's page: HTML, CSS and JS, next to the .exe once packaged.
UI_DIR = Path("ui")
START_REROLL = "reroll"
START_CONTINUE = "continue"
GOAL_SUMMONS = "summons"
GOAL_STORY = "story"
GOAL_ABYSS = "abyss"
GOAL_CLIMB = "climb"
GOAL_THEATER = "theater"
GOAL_ARENA = "arena"
GOAL_SHOP = "shop"
GOAL_PENGUINS = "penguins"
GOAL_HUCHE = "huche"
GOAL_GEAR_ROLL = "gear_roll"
GOAL_GEAR_SORT = "gear_sort"
GOAL_GEAR_CRAFT = "gear_craft"
GOAL_GEAR_ACCOUNT = "gear_account"
GEAR_GOALS = (GOAL_GEAR_ROLL, GOAL_GEAR_SORT, GOAL_GEAR_CRAFT, GOAL_GEAR_ACCOUNT)
# These goals play an account the user kept: nothing may ever reset it.
KEPT_GOALS = (GOAL_STORY, GOAL_CLIMB, GOAL_THEATER, GOAL_ARENA, GOAL_SHOP, GOAL_PENGUINS, GOAL_HUCHE, *GEAR_GOALS)


@dataclass
class Session:
    """The live connection to the game window."""

    window: GameWindow
    capture: Capture
    input: InputController
    marker: ClickMarker | None = None

    def close(self) -> None:
        """Release the capture and the click marker."""
        self.capture.close()
        if self.marker is not None:
            self.marker.close()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Epic Seven reroll bot (official STOVE or Steam PC client)")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="config file path")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="find the game window, force its size and print its rects",
    )
    parser.add_argument("--dry-run", action="store_true", help="log actions without sending any input")
    parser.add_argument("--once", action="store_true", help="stop after one account")
    parser.add_argument("--debug-overlay", action="store_true", help="show the annotated capture")
    parser.add_argument("--headless", action="store_true", help="run without the bot window")
    parser.add_argument(
        "--start",
        choices=[START_REROLL, START_CONTINUE],
        help="start at once: reroll resets the account and starts over, continue carries on with it",
    )
    parser.add_argument(
        "--client",
        choices=sorted(CLIENTS),
        help="the official client to drive, instead of the one chosen in the bot window: stove or steam",
    )
    parser.add_argument(
        "--goal",
        choices=[GOAL_SUMMONS, GOAL_STORY, GOAL_ABYSS, GOAL_CLIMB, GOAL_THEATER, GOAL_ARENA, GOAL_SHOP, GOAL_PENGUINS, GOAL_HUCHE, *GEAR_GOALS, ADVISOR_GOAL],
        default=GOAL_SUMMONS,
        help="summons rerolls until the summons, abyss rerolls until abyss 45, story pushes the story, climb the Abyss, theater the Moonlight Theater, arena the NPC Challenge, shop refreshes the Secret Shop, gear_* the gear of a kept account, and gear_advise judges the gear on screen without a click",
    )
    # Set by the bot itself when it starts again as administrator: never ask twice.
    parser.add_argument(ELEVATED_FLAG, action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.goal in (*KEPT_GOALS, ADVISOR_GOAL) and args.start == START_REROLL:
        parser.error(f"--goal {args.goal} never erases an account: use --start continue")
    return args


def run_probe(config: AppConfig, dry_run: bool, logger: logging.Logger) -> int:
    """Log what the bot sees of the game window. Returns the process exit code."""
    logger.info("dpi aware (per-monitor v2): %s, administrator: %s", is_dpi_aware(), is_elevated())
    for window in list_windows():
        if window.process_name == config.window.process_name or window.title in config.window.titles:
            logger.info("matching window: %s", window.describe())

    info = find_game_window(config.window, logger)
    game = GameWindow(info, config.window, logger)
    logger.info("selected: %s", info.describe())
    logger.info("window dpi: %s", window_dpi(game.hwnd))
    logger.info("client on screen before: %s", game.client_rect_on_screen())
    if dry_run:
        logger.info("dry run: window left untouched")
        return 0

    logger.info("size forced in %s attempt(s)", game.apply_size())
    logger.info("client on screen after: %s", game.client_rect_on_screen())
    logger.info("foreground: %s", game.ensure_foreground())
    stable = game.verify()
    logger.info("client size stable: %s", stable)
    return 0 if stable else 1


def config_for_client(config: AppConfig, client: str) -> AppConfig:
    """Return the config aimed at one client's window."""
    return config.model_copy(update={"window": window_config_for(config.window, CLIENTS[client])})


def make_session_opener(config: AppConfig, logger: logging.Logger, dry_run: bool, stop_event: threading.Event):
    """Return the function that connects to the game, or returns None if it is not open."""

    def open_session() -> Session | None:
        try:
            window = open_game_window(config.window, logger, foreground=not config.input.background)
        except WindowNotFoundError:
            return None
        controller = InputController(window, config.input, logger, dry_run, stop_event)
        marker = None
        if config.input.click_marker and not dry_run:
            if config.input.background:
                marker = ClickMarker(window.hwnd, logger)
                controller.on_click = marker.show
            else:
                # In foreground mode the bot reads the screen, so the ring would end up in its images.
                logger.warning("the click marker only works in background mode: not showing it")
        return Session(window, Capture(window, background=config.input.background), controller, marker)

    return open_session


def make_overlay(library: TemplateLibrary):
    """Return the callback showing each frame with the current state and its matches."""

    def show(frame: np.ndarray, state: State) -> None:
        status = f"{state.value} | {frame_hash(frame)}"
        image = draw_overlay(frame, OVERLAY_SCALE, status, find_boxes(frame, library))
        cv2.imshow(WINDOW_NAME, image)
        cv2.waitKey(1)

    return show


def run_bot(
    config: AppConfig,
    args: argparse.Namespace,
    logger: logging.Logger,
    stop_event: threading.Event,
    state: Path,
    status: BotStatus | None = None,
    reset: bool = False,
    goal: str | None = None,
) -> str:
    """Run the state machine until HALT and return why it stopped."""
    # These goals play a kept account: they never reset one, whatever asks for it.
    reset = reset and (goal or args.goal) not in KEPT_GOALS
    library = TemplateLibrary(config.vision, logger)
    library.load(load_manifest(config.vision.manifest), config.vision.templates_dir)
    ocr = Ocr(config.vision)
    try:
        logger.info("tesseract %s", ocr.check())
    except OcrUnavailableError as error:
        logger.warning("%s", error)

    if status is None:
        status = BotStatus(load_total_rerolls(state), load_stats(state))

    def count_reroll() -> None:
        save_total_rerolls(status.add_reroll(), state)

    context = Context(
        config=config,
        logger=logger,
        library=library,
        ocr=ocr,
        open_session=make_session_opener(config, logger, args.dry_run, stop_event),
        stop_event=stop_event,
        dry_run=args.dry_run,
        once=args.once,
        story_only=(goal or args.goal) in (GOAL_STORY, GOAL_ABYSS, GOAL_CLIMB, GOAL_THEATER, GOAL_ARENA, GOAL_SHOP, GOAL_PENGUINS, GOAL_HUCHE, *GEAR_GOALS),
        goal=goal or args.goal,
        route_step=0 if reset else load_route_step(state),
        on_route_step=lambda step: save_route_step(step, state),
        on_route_pulls=lambda pulls: save_route_pulls(pulls, state),
        reset_pending=reset and not args.dry_run,
        # The long route picks up a fight in progress, rather than yielding it to reach the lobby.
        going_home=not reset and (goal or args.goal) not in (GOAL_ABYSS, GOAL_CLIMB, GOAL_THEATER, GOAL_ARENA, GOAL_SHOP, GOAL_PENGUINS, GOAL_HUCHE, *GEAR_GOALS),
        on_reset=count_reroll,
        on_stage=status.set_stage,
        on_result=status.set_result,
        on_cards=lambda cards: save_stats(status.add_cards(cards), state),
        on_run_done=lambda seconds: save_stats(status.add_run(seconds), state),
        on_story_won=lambda: save_stats(status.add_story(won=1), state),
        on_story_lost=lambda: save_stats(status.add_story(lost=1), state),
        on_story_revive=lambda: save_stats(status.add_story(revives=1), state),
        on_floor_won=lambda: save_stats(status.add_climb(won=1), state),
        on_floor_lost=lambda: save_stats(status.add_climb(lost=1), state),
        on_theater_act=lambda: save_stats(status.add_theater(acts=1), state),
        on_theater_ticket=lambda: save_stats(status.add_theater(tickets=1), state),
        on_arena_fight=lambda won, quick: save_stats(status.add_arena(won=won, quick=quick), state),
        on_arena_skystones=lambda spent: save_stats(status.add_arena_skystones(spent), state),
        on_shop_refresh=lambda: save_stats(status.add_shop(refreshes=1), state),
        on_penguin_buy=lambda stigma: save_stats(status.add_penguins(stigma), state),
        on_penguin_kinds=lambda kinds: save_stats(status.add_penguin_kinds(kinds), state),
        on_huche_buy=lambda kind, gold: save_stats(status.add_huche(kind, gold), state),
        on_shop_buy=lambda kind, amount, gold: save_stats(status.add_shop(**{kind: amount}, gold=gold), state),
        on_gear=lambda event, amount: save_stats(status.add_gear(event, amount), state),
        on_leaf=lambda: save_stats(status.add_leaf(), state),
    )
    if not reset and (goal or args.goal) == GOAL_ABYSS:
        # Pulls made before a restart still count for the keep decision.
        pulls = load_route_pulls(state)
        context.summon.cards_read = int(pulls.get("cards_read", 0))
        context.summon.moonlight_5star = list(pulls.get("moonlight_5star", []))
        context.summon.unsure = list(pulls.get("unsure", []))
    on_frame = make_overlay(library) if args.debug_overlay else None
    on_state = lambda state: status.set_state(state.value)
    started_at = time.monotonic()
    try:
        with keep_awake() as awake:
            if not awake:
                logger.warning("Windows refused to keep the screen on: the game stops when the display sleeps")
            return StateMachine(context, HANDLERS, on_frame=on_frame, on_state=on_state).run()
    finally:
        if context.goal == GOAL_STORY:
            save_stats(status.add_story(seconds=time.monotonic() - started_at), state)
        if context.goal == GOAL_CLIMB:
            save_stats(status.add_climb(seconds=time.monotonic() - started_at), state)
        if context.goal == GOAL_THEATER:
            save_stats(status.add_theater(seconds=time.monotonic() - started_at), state)
        if context.goal == GOAL_ARENA:
            save_stats(status.add_arena(seconds=time.monotonic() - started_at), state)
        if context.goal == GOAL_SHOP:
            save_stats(status.add_shop(seconds=time.monotonic() - started_at), state)
        if args.debug_overlay:
            cv2.destroyAllWindows()


def run_headless(config: AppConfig, args: argparse.Namespace, logger: logging.Logger, client: str) -> int:
    """Run the bot without the window, until it halts or Ctrl+C. Returns the process exit code.

    The settings saved from the window apply, as when the window starts the bot; --client already chose the client.
    """
    stop_event = threading.Event()
    config = apply_settings(config, load_settings(SETTINGS_PATH, config))
    try:
        if args.goal == ADVISOR_GOAL:
            result = run_advisor(config_for_client(config, client), logger, stop_event, AdviceBoard())
        else:
            result = run_bot(config_for_client(config, client), args, logger, stop_event, state_path(client), reset=args.start == START_REROLL)
        logger.info("stopped: %s", result)
    except KeyboardInterrupt:
        logger.info("stopped: Ctrl+C")
    return 0


def run_with_panel(config: AppConfig, args: argparse.Namespace, logger: logging.Logger, argv: list[str]) -> int:
    """Show the bot window, which starts and stops the bot. Returns the process exit code."""
    stop_event = threading.Event()
    settings = load_settings(SETTINGS_PATH, config)
    if args.client:
        settings = settings.model_copy(update={"client": args.client})
    status = BotStatus()
    board = AdviceBoard()
    feed = LogFeed()
    feed.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    feed.load_file(config.logging.log_dir / LOG_FILE_NAME)
    logger.addHandler(feed)

    def run_in_thread(settings: Settings, reset: bool, goal: str = GOAL_SUMMONS) -> None:
        result = "stopped by an error, see logs/bot.log"
        try:
            client = settings.client
            assert client is not None, "the window starts the bot only once a client is chosen"
            logger.info("client: %s (%s)", CLIENTS[client].label, CLIENTS[client].process_name)
            ready = config_for_client(apply_settings(config, settings), client)
            if goal == ADVISOR_GOAL:
                # It captures the game's window and never sends an input; it only keeps the game at 1280x720.
                result = run_advisor(ready, logger, stop_event, board)
            else:
                result = run_bot(ready, args, logger, stop_event, state_path(client), status, reset, goal)
            logger.info("stopped: %s", result)
        except Exception:
            logger.exception("the bot stopped on an error")
        finally:
            status.finished(result)

    panel = Panel(
        config.window, settings, SETTINGS_PATH, status, feed, stop_event, run_in_thread, logger, log_dir=config.logging.log_dir,
        is_admin=is_elevated, relaunch=lambda: relaunch_as_admin(argv, app_folder()), advice_board=board,
        defaults=settings_from_config(config),
    )
    return run_window(
        panel, UI_DIR, logger,
        always_on_top=not config.input.background,
        start_with_reset=None if args.start is None else args.start == START_REROLL,
        goal=args.goal,
    )


def app_folder() -> Path:
    """Return the folder holding config.yaml, templates and data: the .exe's once packaged."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def tell(text: str) -> None:
    """Say an error that stops the bot: on the console, or in a message box when it was started without one."""
    if sys.stderr is not None:
        print(text, file=sys.stderr)
    else:
        show_error(text)


def main(argv: list[str] | None = None) -> int:
    """Run the requested command and return the process exit code."""
    # An elevated launch can start in System32: relative config and log paths need the project root,
    # which for the packaged .exe is the folder it sits in.
    os.chdir(app_folder())
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        tell(str(error))
        return 2
    logger = setup_logging(config.logging)
    for warning in config.startup_warnings():
        logger.warning(warning)
    # The window asks for the client at the first launch; the commands without it need one already.
    client = args.client or load_settings(SETTINGS_PATH, config).client
    if (args.probe or args.headless or args.debug_overlay) and client is None:
        logger.error("no client chosen yet: open the bot window once, or pass --client stove or --client steam")
        return 2
    needs_admin = client is not None and CLIENTS[client].needs_admin and not is_elevated()
    if needs_admin and (args.probe or args.headless or args.debug_overlay):
        logger.error("%s runs the game as administrator: start the bot from an administrator terminal", CLIENTS[client].label)
        return 2
    # The client asked for on the command line goes on to the elevated bot, which would read the saved one otherwise.
    if needs_admin and not args.elevated and relaunch_as_admin(argv, app_folder(), args.client):
        logger.info("started again as administrator, for %s", CLIENTS[client].label)
        return 0
    try:
        if args.probe:
            logger.info("client: %s (%s)", CLIENTS[client].label, CLIENTS[client].process_name)
            return run_probe(config_for_client(config, client), args.dry_run, logger)
        if args.headless or args.debug_overlay:
            return run_headless(config, args, logger, client)
        return run_with_panel(config, args, logger, argv)
    except (WindowError, ConfigError) as error:
        logger.error("%s", error)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Without a console the traceback would vanish: the message box says where to look.
        logging.getLogger("e7bot").exception("the bot stopped on an error")
        if sys.stderr is None:
            show_error(f"The bot stopped on an error: {error}. The details are in logs/bot.log.")
        raise

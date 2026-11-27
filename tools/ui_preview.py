"""Open the real bot window with a pretend bot, to look at the window without the game. Optionally take a screenshot of each state.

    .venv\\Scripts\\python.exe tools\\ui_preview.py                  # open it and click around
    .venv\\Scripts\\python.exe tools\\ui_preview.py --shots shots    # tour every tab and state, save PNGs, close

It works in a temporary folder: the real settings.json and state files are never touched.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.window import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()

import mss  # noqa: E402
import mss.tools  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

from core.config import load_config  # noqa: E402
from core.panel import Panel  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.stats import Stats, save_stats, save_total_rerolls  # noqa: E402
from core.status import BotStatus, LogFeed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# A pretend abyss reroll: (seconds to wait, state, log message).
SCRIPT = [
    (0.3, "BOOT", "game window found"),
    (0.3, "STORY", "stage Episode 1 (1) 1-1"),
    (0.3, "STORY", "screen: dialogue"),
    (0.3, "STORY", "video skipped"),
    (0.3, "STORY", "screen: stage clear"),
    (0.3, "ROUTE", "route step 13 done"),
    (0.3, "ROUTE", "abyss: floor 11 cleared"),
    (0.3, "ROUTE", "abyss: floor 12 cleared"),
    (0.3, "ROUTE", "screen: fight"),
]
# The kept-account modes play their own lines, so their live step can be looked at.
SCRIPTS = {
    "shop": [
        (0.3, "ROUTE", "shop: 100 refreshes planned"),
        (0.3, "ROUTE", "screen: lobby"),
        (0.3, "ROUTE", "shop: refreshed for 3 skystones, 36 of 100"),
        (0.3, "ROUTE", "screen: shop list top"),
        (0.3, "ROUTE", "shop: Covenant Bookmarks for sale on the top page, buying"),
    ],
    "arena": [
        (0.3, "ROUTE", "screen: lobby"),
        (0.3, "ROUTE", "arena: fight won, 3 in this run"),
        (0.3, "ROUTE", "screen: arena npc challenge"),
    ],
    "theater": [
        (0.3, "ROUTE", "theater: act watched, 2 in this run"),
        (0.3, "ROUTE", "theater: act started, story 2 season 1"),
    ],
    "climb": [
        (0.3, "ROUTE", "abyss: floor 52 cleared"),
        (0.3, "ROUTE", "screen: abyss floors scrolling"),
    ],
}

# A pretend account evaluation, from a real run's log, slowed down.
ACCOUNT_SCRIPT = [
    (0.5, "ROUTE", "screen: lobby"),
    (0.5, "ROUTE", "account: opening the inventory"),
    (1.0, "ROUTE", "screen: gear inventory"),
    (0.3, "ROUTE", "account: setting the inventory filters: worn pieces shown, highest level first"),
    (2.0, "ROUTE", "account: weapon tab"),
    *[(0.25, "ROUTE", "account: epic weapon +15 critical set (level 88), main attack: crit_chance 13, crit_damage 22, speed 4, attack_percent 9")] * 8,
    (0.3, "ROUTE", "account: card 28 unread (line 4: crit_chance 0.54 vs health 0.30)"),
    (0.3, "ROUTE", "account: card 29 skipped (item level 70)"),
    (0.5, "ROUTE", "account: weapon tab done: 8 +15 pieces (under +15)"),
    (0.3, "ROUTE", "account: helmet tab"),
    *[(0.25, "ROUTE", "account: epic helmet +15 speed set, main health: speed 11, health_percent 14, defense_percent 8, effectiveness 9")] * 6,
]


def pretend_bot(status: BotStatus, logger: logging.Logger, stop: threading.Event, result: str, settings_folder: Path):
    """Return a run_bot that plays SCRIPT, then waits for Stop and ends with `result`."""

    def run(settings: Settings, reset: bool, goal: str) -> None:
        status.add_reroll()
        for wait, state, message in {**SCRIPTS, "gear_account": ACCOUNT_SCRIPT}.get(goal, SCRIPT):
            if stop.wait(wait):
                break
            status.set_state(state)
            logger.info(message)
        image = Path(settings_folder) / "pulls.png"
        if not image.exists():
            mss.tools.to_png(bytes([64, 96, 128]) * 64, (8, 8), output=str(image))
        status.set_result(21, 21, str(image))
        stop.wait()
        logger.info("stopped: %s", result)
        status.finished(result)

    return run


def own_window(title: str) -> int:
    """Return this preview's window with this title, not the real bot's window of the same name, or 0."""
    hwnd = win32gui.FindWindowEx(None, None, None, title)
    while hwnd:
        if win32process.GetWindowThreadProcessId(hwnd)[1] == os.getpid():
            return hwnd
        hwnd = win32gui.FindWindowEx(None, hwnd, None, title)
    return 0


def window_rect(title: str) -> tuple[int, int, int, int] | None:
    """Return the screen rect of this preview's window with this title."""
    hwnd = own_window(title)
    return win32gui.GetWindowRect(hwnd) if hwnd else None


def tour(window, panel: Panel, folder: Path, account_only: bool = False) -> None:
    """Show each tab and state, saving a screenshot of the window for each, then close it."""
    folder.mkdir(parents=True, exist_ok=True)

    def shot(name: str, settle: float = 0.8) -> None:
        time.sleep(settle)
        rect = window_rect(panel.title)
        if rect is None:
            return
        left, top, right, bottom = rect
        with mss.MSS() as screen:
            image = screen.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
            mss.tools.to_png(image.rgb, image.size, output=str(folder / f"{name}.png"))

    def focus_guard() -> str:
        hwnd = own_window(panel.title)
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        return "on" if style & win32con.WS_EX_NOACTIVATE else "off"

    def js(code: str) -> None:
        window.evaluate_js(code)

    def tab(name: str) -> None:
        js(f"selectTab('{name}')")

    time.sleep(4)
    if account_only:
        panel.pick_mode("gear_account")
        shot("30-account-ready")
        js("document.getElementById('account-outdated').scrollIntoView({block: 'end'})")
        shot("30b-account-kept")
        js("window.scrollTo(0, 0); document.querySelector('main').scrollTo(0, 0)")
        panel.start()
        for index in range(7):
            shot(f"31-account-running-{index}", 1.5)
        panel.stop()
        window.destroy()
        return
    if panel.client is None:
        shot("00-client")
        panel.choose_client("stove")
    shot("01-menu")
    panel.pick_mode("reroll_abyss")
    shot("02-mode-reroll")
    panel.erase()
    shot("03-erase-armed", 0.4)
    panel.erase_armed_at = None
    panel.start()
    shot("04-running", 3.5)
    print(f"running: the window cannot take the focus: {focus_guard()}")
    panel.back()
    shot("04b-running-menu")
    panel.pick_mode("theater")
    shot("04c-running-other-mode")
    panel.show_run()
    tab("Log")
    shot("05-log")
    tab("Stats")
    shot("06-stats")
    tab("Settings")
    shot("07-settings-running")
    panel.stop()
    time.sleep(0.6)
    panel.show_settings("Reroll")
    shot("08-settings-reroll")
    panel.show_settings("Gear")
    shot("09-settings-gear-rules")
    panel.show_gear_page("Run")
    shot("10-settings-gear-run")
    panel.show_settings("Advanced")
    shot("11-settings-advanced")
    tab("Home")
    shot("12-stopped")
    print(f"stopped: the window cannot take the focus: {focus_guard()}")
    panel.pick_mode("story")
    shot("13-mode-story")
    panel.pick_mode("theater")
    shot("14-mode-theater")
    panel.pick_mode("gear_craft")
    shot("15-mode-craft")
    panel.set_setting("gear_dry_run", True)
    panel.pick_mode("gear_sort")
    shot("16-mode-sort")
    panel.status.finished("")
    panel.start()
    shot("17-sort-dry-running", 2)
    panel.stop()
    time.sleep(0.6)
    panel.pick_mode("shop")
    panel.set_setting("shop_refreshes", 100)
    shot("18-mode-shop")
    # The cost follows the number while it is typed, before it is saved.
    js("const box = document.querySelector('#run-settings [data-goal=shop] input'); box.focus(); box.value = '250';")
    shot("18b-mode-shop-typing")
    js("document.activeElement.blur()")
    panel.status.finished("")
    panel.start()
    shot("19-shop-running", 2)
    panel.stop()
    time.sleep(0.6)
    panel.status.finished("Secret Shop: refreshes done, 100 refreshes (300 skystones), 15 bookmarks and 50 medals bought for 832,000 gold")
    shot("20-shop-done")
    tab("Stats")
    panel.show_stats("Shop")
    shot("21-stats-shop")
    for name in ("Reroll", "Story", "Theater", "Arena"):
        panel.show_stats(name)
        shot(f"22-stats-{name.lower()}")
    tab("Settings")
    panel.show_settings("Arena")
    shot("23-settings-arena")
    tab("Home")
    panel.pick_mode("arena")
    shot("24-mode-arena")
    window.destroy()


def demo_account(folder: Path) -> None:
    """Save an account evaluation for the page: the real one in logs/gear when there is one, with three pieces kept
    apart as locked or worn, one of them a junk ring the rules of now would sell."""
    from dataclasses import replace

    from core.account import evaluate, load_result, save_result
    from core.gear import Piece

    kept = [
        Piece("ring", "attack_percent", 15, {"defense": 30, "health": 160, "effect_resistance": 5, "defense_percent": 5}, "epic", "health", item_level=88),
        Piece("boots", "speed", 15, {"attack_percent": 21, "health_percent": 14, "defense_percent": 7, "crit_damage": 18}, "epic", "speed", item_level=88),
        Piece("necklace", "crit_damage", 15, {"attack": 40, "effect_resistance": 6, "health": 170, "defense": 33}, "heroic", "critical"),
    ]
    demo = evaluate(kept, kept={0: "worn", 1: "locked", 2: "locked"})
    real = load_result(ROOT / "logs" / "gear")
    save_result(replace(real, kept=demo.kept, kept_checked=True) if real is not None else demo, folder)


def main() -> int:
    """Open the window over a temporary folder with a pretend bot."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shots", type=Path, help="save a screenshot of each state in this folder, then close")
    parser.add_argument("--account", action="store_true", help="with --shots: only the account evaluation, while it runs")
    parser.add_argument("--no-client", action="store_true", help="start as on the first launch, with no client chosen")
    parser.add_argument("--result", default="account kept: Briar Witch Iseria ML 5*", help="why the pretend run stops")
    args = parser.parse_args()

    from core.panel_window import run_window
    import core.panel_window as panel_window

    folder = Path(tempfile.mkdtemp(prefix="e7bot-preview-"))
    config = load_config(ROOT / "config.yaml")
    logger = logging.getLogger("e7bot-preview")
    logger.setLevel(logging.INFO)
    feed = LogFeed()
    feed.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logger.addHandler(feed)
    for index in range(40):
        feed.emit(logging.LogRecord("e7bot", logging.INFO, "", 0, f"earlier line {index}: screen: lobby", None, None))
    status = BotStatus()
    stop = threading.Event()
    client = None if args.no_client else "stove"
    if client:
        state = folder / f"state_{client}.json"
        save_stats(Stats(summons=1386, heroes_5=31, moonlight_5=4, moonlight_4=22, normal_4=163, artifacts_5=18, runs=66, run_seconds=66 * 3170.0, story_stages_won=212, story_fights_lost=9, story_seconds=40000, leaves_used=3, shop_refreshes=140, shop_bookmarks=25, shop_medals=50, shop_gold=1_200_000, shop_seconds=760, theater_acts=41, theater_tickets=12, theater_seconds=9000, arena_fights=60, arena_wins=52, arena_quick=30, arena_skystones=90, arena_seconds=7200, story_revives=2), state)
        save_total_rerolls(66, state)
    panel = Panel(config.window, Settings(client=client), folder / "settings.json", status, feed, stop, pretend_bot(status, logger, stop, args.result, folder), logger, log_dir=folder)

    if args.account:
        demo_account(folder / "gear")
    if args.shots:
        original_start = panel_window.webview_problem

        def hook() -> str:
            import webview

            def on_start(window) -> None:
                tour(window, panel, args.shots.resolve(), args.account)

            real_start = webview.start
            webview.start = lambda **kwargs: real_start(func=lambda: on_start(webview.windows[0]), **kwargs)
            return original_start()

        panel_window.webview_problem = hook
    started = datetime.now()
    code = run_window(panel, ROOT / "ui", logger, always_on_top=True)
    print(f"window closed after {(datetime.now() - started).seconds} s, preview folder {folder}")
    return code


if __name__ == "__main__":
    sys.exit(main())

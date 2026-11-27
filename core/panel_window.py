"""The bot window itself: a pywebview window showing ui/index.html, wired to core.panel.Panel, placed beside the game."""

from __future__ import annotations

import ctypes
import logging
import threading
from pathlib import Path
from typing import Callable

import win32api
import win32con
import win32gui

from core.panel import GOALS, Panel, panel_position
from core.window import list_windows, select_game_window

FOLLOW_SECONDS = 1.0
BACKGROUND = "#0f1013"
# A desktop window the user sizes: room for the gear pages' charts and tables side by side.
WINDOW_WIDTH, WINDOW_HEIGHT = 1280, 800
MIN_SIZE = (900, 600)
# What the page may call. Everything else on Panel stays out of reach of the page.
API = (
    "options", "view", "log_lines", "pick_mode", "back", "start", "erase", "stop", "choose_client",
    "set_setting", "set_top_rule", "set_hero", "show_stats", "show_settings", "show_gear_page",
    "reset_stats", "reset_settings", "open_image", "open_gear_folder", "export_journal", "restart_as_admin", "show_run",
    "pick_piece", "set_role", "apply_account_scores", "clear_account_targets", "set_role_heroes",
)
# Asks Windows for a dark title bar, to match the page.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

Rect = tuple[int, int, int, int]

# Calls that make Windows send a message to the window's thread go through ctypes, which lets go of Python's lock
# while they wait. pywin32 keeps it, and the window's thread needs it to answer: the window froze.
USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
USER32.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
USER32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
USER32.SetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
USER32.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
USER32.LoadImageW.restype = ctypes.c_void_p
USER32.LoadImageW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
USER32.SendMessageW.restype = ctypes.c_ssize_t
USER32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p)
# The window's icon (ui/icon.ico, drawn by tools/build_icon.py), and the taskbar identity that shows it rather than
# Python's own.
ICON_FILE = "icon.ico"
APP_ID = "E7Bot.Window"
IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
ICON_SIZES = ((0, 16), (1, 32))


def show_error(text: str) -> None:
    """Tell the user why the window cannot open, in a plain Windows message box."""
    win32api.MessageBox(0, text, "E7 Bot", win32con.MB_OK | win32con.MB_ICONERROR)


def missing_assets(ui_dir: Path) -> list[str]:
    """Return the page's files that are not where the window looks for them."""
    return [name for name in ("index.html", "app.css", "app.js") if not (ui_dir / name).is_file()]


def webview_problem() -> str:
    """Return why pywebview cannot show the page with Edge WebView2 on this PC, or '' when it can."""
    try:
        from webview.platforms import winforms
    except Exception as error:  # pythonnet or .NET failing to load surfaces as many exception types
        return f"The window's engine could not load ({error}). The .NET Framework 4.6.2 or later is needed."
    if not winforms.is_chromium:
        return f"Microsoft Edge WebView2 Runtime is missing. Install it (free, from Microsoft: {WEBVIEW2_URL}), then start the bot again."
    return ""


def set_icon(hwnd: int, path: Path) -> None:
    """Put the icon on the window's title bar (small) and in the taskbar and Alt+Tab (big). A missing file is skipped."""
    if not path.is_file():
        return
    for which, size in ICON_SIZES:
        handle = USER32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if handle:
            USER32.SendMessageW(hwnd, WM_SETICON, which, handle)


def dark_title_bar(hwnd: int) -> None:
    """Paint the window's title bar dark, as the page is. Older Windows ignore it."""
    value = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))


class GameFollower:
    """Puts the window beside the game once, where it fits, and makes it unable to take focus while the bot plays.

    The window is big and the user moves and sizes it: it is placed when the game is first seen, never chased after.
    """

    def __init__(self, panel: Panel, logger: logging.Logger, hwnd: Callable[[], int | None], guard_focus: bool = True) -> None:
        """`hwnd` returns the window's handle once it exists. `guard_focus` keeps the window from taking the focus while
        the bot runs: only in foreground mode, where the game must keep it; in background mode the window stays normal,
        so a click still brings it above the game."""
        self.guard_focus = guard_focus
        self.panel = panel
        self.logger = logger
        self.hwnd = hwnd
        self.placed = False
        self.last_config = None
        self.no_activate: bool | None = None
        self.closed = threading.Event()

    def run(self) -> None:
        """Follow the game every second until the window closes."""
        while not self.closed.wait(FOLLOW_SECONDS):
            own = self.hwnd()
            if own is None:
                continue
            try:
                self.keep_focus_off(own)
                self.follow(own)
            except Exception:
                self.logger.debug("could not follow the game window", exc_info=True)

    def keep_focus_off(self, own: int) -> None:
        """While the bot runs, a click on the window must not take the focus from the game. Idle, it types normally."""
        running = self.guard_focus and self.panel.status.snapshot().running
        if running == self.no_activate:
            return
        style = USER32.GetWindowLongPtrW(own, win32con.GWL_EXSTYLE)
        style = style | win32con.WS_EX_NOACTIVATE if running else style & ~win32con.WS_EX_NOACTIVATE
        USER32.SetWindowLongPtrW(own, win32con.GWL_EXSTYLE, style)
        self.no_activate = running

    def follow(self, own: int) -> None:
        """Put the window next to the game the first time the game is seen, if it fits there: beside the game, else on
        another monitor. Without a client, there is nothing to look for."""
        if self.panel.client is None:
            return
        if self.panel.window_config is not self.last_config:
            self.last_config = self.panel.window_config
            self.placed = False
        if self.placed:
            return
        selected = select_game_window(list_windows(), self.panel.window_config)
        if selected is None:
            return
        self.placed = True
        rect = win32gui.GetWindowRect(selected.hwnd)
        monitor = win32api.MonitorFromWindow(selected.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        work = win32api.GetMonitorInfo(monitor)["Work"]
        others = [
            win32api.GetMonitorInfo(handle)["Work"]
            for handle, _, _ in win32api.EnumDisplayMonitors()
            if win32api.GetMonitorInfo(handle)["Work"] != work
        ]
        left, top, right, bottom = win32gui.GetWindowRect(own)
        width, height = right - left, bottom - top
        x, y = panel_position(rect, work, others, width=width, height=height)
        if not any(area[0] <= x and x + width <= area[2] and area[1] <= y and y + height <= area[3] for area in (work, *others)):
            # It fits nowhere beside the game: it stays where Windows put it, over or under the game.
            return
        flags = win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
        USER32.SetWindowPos(own, None, x, y, 0, 0, flags)


def run_window(
    panel: Panel,
    ui_dir: Path,
    logger: logging.Logger,
    always_on_top: bool = False,
    start_with_reset: bool | None = None,
    goal: str | None = None,
) -> int:
    """Show the window until it is closed. Returns the process exit code: 1 when it cannot open."""
    missing = missing_assets(ui_dir)
    if missing:
        text = f"The window's files are missing from {ui_dir.resolve()}: {', '.join(missing)}. Unzip the whole E7Bot folder again."
        logger.error("%s", text)
        show_error(text)
        return 1
    problem = webview_problem()
    if problem:
        logger.error("%s", problem)
        show_error(problem)
        return 1

    import webview

    try:
        # Its own taskbar button and icon, not grouped with other Python windows.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        logger.debug("no taskbar identity", exc_info=True)
    window = webview.create_window(
        panel.title, url=str((ui_dir / "index.html").resolve()),
        width=WINDOW_WIDTH, height=WINDOW_HEIGHT, min_size=MIN_SIZE, resizable=True,
        on_top=always_on_top, background_color=BACKGROUND, text_select=False,
    )
    window.expose(*(getattr(panel, name) for name in API))

    def ask_save_path(name: str) -> str | None:
        chosen = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name, file_types=("Text (*.txt)",))
        if not chosen:
            return None
        return chosen if isinstance(chosen, str) else chosen[0]

    def own_hwnd() -> int | None:
        native = window.native
        return None if native is None else int(native.Handle.ToInt64())

    panel.on_title = window.set_title
    panel.ask_save_path = ask_save_path
    panel.on_quit = window.destroy
    # On top and unable to take the focus while it runs: foreground mode only, where the bot needs the game focused.
    follower = GameFollower(panel, logger, own_hwnd, guard_focus=always_on_top)

    def on_shown() -> None:
        own = own_hwnd()
        if own is not None:
            try:
                dark_title_bar(own)
            except OSError:
                logger.debug("no dark title bar", exc_info=True)
            set_icon(own, ui_dir / ICON_FILE)
        threading.Thread(target=follower.run, name="follow-game", daemon=True).start()
        if goal is not None:
            panel.select_goal(goal)
        if start_with_reset is not None and panel.client is not None:
            # --start without a mode the window offers starts the first one, as before the main menu.
            if panel.chosen_goal() is None:
                panel.pick_mode(GOALS[0].key)
            panel.start(start_with_reset)

    def on_closing() -> None:
        follower.closed.set()
        panel.close()

    window.events.shown += on_shown
    window.events.closing += on_closing
    try:
        webview.start(gui="edgechromium", private_mode=True)
    except Exception as error:
        logger.exception("the window failed")
        show_error(f"The window failed: {error}. The details are in logs/bot.log.")
        panel.close()
        return 1
    return 0

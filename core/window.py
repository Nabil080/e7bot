"""DPI awareness, game window lookup, forced client size, per-frame check and focus."""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pywintypes
import win32api
import win32con
import win32gui
import win32process

from core.config import WindowConfig

DPI_CONTEXT_PER_MONITOR_V2 = ctypes.c_void_p(-4)
RESIZE_SETTLE_SECONDS = 0.05
FOREGROUND_WAIT_SECONDS = 1.0
TH32CS_SNAPPROCESS = 0x00000002
ERROR_ACCESS_DENIED = 5
ELEVATION_HINT = (
    "The STOVE client runs as administrator, so Windows blocks a normal process "
    "from controlling it: start the bot from an administrator terminal."
)
INVALID_HANDLE = ctypes.c_void_p(-1).value

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
_user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
_user32.GetDpiForWindow.argtypes = [wintypes.HWND]
_user32.GetDpiForWindow.restype = wintypes.UINT

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class WindowError(Exception):
    """Base class for window problems."""


class WindowNotFoundError(WindowError):
    """No window matches the config."""


class WindowResizeError(WindowError):
    """The client area could not be forced to the target size."""


class WindowLostError(WindowError):
    """The window handle is no longer valid, usually because the game closed."""


class _ProcessEntry(ctypes.Structure):
    """PROCESSENTRY32W from the Windows API."""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
_kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]


@dataclass(frozen=True)
class WindowInfo:
    """A visible top-level window and the process that owns it."""

    hwnd: int
    pid: int
    process_name: str
    class_name: str
    title: str
    client_width: int
    client_height: int

    def describe(self) -> str:
        """Return a one-line description for logs and probe output."""
        return (
            f"hwnd={self.hwnd} pid={self.pid} process={self.process_name!r} "
            f"class={self.class_name!r} title={self.title!r} "
            f"client={self.client_width}x{self.client_height}"
        )


@dataclass(frozen=True)
class ScreenRect:
    """A rectangle in true screen pixels. Left and top can be negative on multi-monitor setups."""

    left: int
    top: int
    width: int
    height: int


def enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware, so that coordinates are true pixels.

    Must run before any other window call. Raises WindowError if the process is
    stuck in another mode, because every coordinate would then be wrong.
    """
    if _user32.SetProcessDpiAwarenessContext(DPI_CONTEXT_PER_MONITOR_V2):
        return
    current = _user32.GetThreadDpiAwarenessContext()
    if not _user32.AreDpiAwarenessContextsEqual(current, DPI_CONTEXT_PER_MONITOR_V2):
        raise WindowError(
            "could not enable per-monitor DPI awareness: something changed the "
            "DPI mode before enable_dpi_awareness() ran"
        )


def is_dpi_aware() -> bool:
    """Return whether this thread uses per-monitor v2 DPI awareness."""
    current = _user32.GetThreadDpiAwarenessContext()
    return bool(_user32.AreDpiAwarenessContextsEqual(current, DPI_CONTEXT_PER_MONITOR_V2))


def is_elevated() -> bool:
    """Return whether this process runs as administrator."""
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


# Marks a launch that already went through the administrator prompt, so that it never asks twice.
ELEVATED_FLAG = "--elevated"


def relaunch_arguments(argv: list[str], client: str | None = None) -> list[str]:
    """Return the arguments to start the bot again as administrator, marked as already asked. The old --client
    is dropped: it would override the client just chosen in the window. `client`, when given, is passed on."""
    kept: list[str] = []
    skip = False
    for argument in argv:
        if skip:
            skip = False
        elif argument == "--client":
            skip = True
        elif not argument.startswith("--client=") and argument != ELEVATED_FLAG:
            kept.append(argument)
    if client is not None:
        kept += ["--client", client]
    return [*kept, ELEVATED_FLAG]


def windowed_python(executable: str | None = None) -> str:
    """Return the program that starts the bot again without a console: pythonw.exe beside python.exe from the
    sources (the restart only comes from the window), the .exe itself once packaged."""
    executable = executable or sys.executable
    if getattr(sys, "frozen", False):
        return executable
    windowed = Path(executable).with_name("pythonw.exe")
    return str(windowed) if windowed.is_file() else executable


def relaunch_as_admin(argv: list[str], folder: Path, client: str | None = None) -> bool:
    """Start the bot again as administrator, through the Windows prompt. Returns False if it was refused.

    The packaged .exe starts itself; from the sources, pythonw starts main.py: no console window, even when the bot
    was first started from a terminal.
    """
    if getattr(sys, "frozen", False):
        arguments = relaunch_arguments(argv, client)
    else:
        arguments = [str(folder / "main.py"), *relaunch_arguments(argv, client)]
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", windowed_python(), subprocess.list2cmdline(arguments), str(folder), win32con.SW_SHOWNORMAL)
    # Above 32 means started; a refused prompt gives an access error.
    return result > 32


def window_dpi(hwnd: int) -> int:
    """Return the DPI of the monitor showing a window (96 means 100% scaling)."""
    return _user32.GetDpiForWindow(hwnd)


def process_names_by_pid() -> dict[int, str]:
    """Map each running pid to its exe name. Also works for elevated processes."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, INVALID_HANDLE):
        raise WindowError(f"could not list processes: error {ctypes.get_last_error()}")
    names: dict[int, str] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            names[entry.th32ProcessID] = entry.szExeFile
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return names


def window_title(hwnd: int) -> str:
    """Return a window's title. Through ctypes, which lets go of Python's lock while the window answers:
    the bot window's thread needs that lock to answer, and pywin32 would keep it, freezing both."""
    buffer = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def list_windows() -> list[WindowInfo]:
    """Return every visible top-level window."""
    names = process_names_by_pid()
    windows: list[WindowInfo] = []

    def collect(hwnd: int, _: object) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            _, _, width, height = win32gui.GetClientRect(hwnd)
            windows.append(
                WindowInfo(
                    hwnd=hwnd,
                    pid=pid,
                    process_name=names.get(pid, ""),
                    class_name=win32gui.GetClassName(hwnd),
                    title=window_title(hwnd),
                    client_width=width,
                    client_height=height,
                )
            )
        return True

    win32gui.EnumWindows(collect, None)
    return windows


def select_game_window(windows: list[WindowInfo], config: WindowConfig) -> WindowInfo | None:
    """Pick the game window: by exact process name first, then by exact title and class.

    Excluded processes are removed before anything else, because the Steam build
    has the same title and class as the STOVE build. Returns None when nothing matches.
    """
    excluded = {name.casefold() for name in config.excluded_process_names}
    allowed = [w for w in windows if w.process_name.casefold() not in excluded]

    matches = [w for w in allowed if w.process_name.casefold() == config.process_name.casefold()]
    if not matches:
        titles = {title.casefold() for title in config.titles}
        matches = [
            w
            for w in allowed
            if w.title.casefold() in titles
            and (not config.class_names or w.class_name in config.class_names)
        ]
    if not matches:
        return None
    return max(matches, key=lambda w: w.client_width * w.client_height)


def find_game_window(config: WindowConfig, logger: logging.Logger) -> WindowInfo:
    """Find the game window, logging every candidate when the search fails."""
    windows = list_windows()
    selected = select_game_window(windows, config)
    if selected is None:
        titled = [w for w in windows if w.title]
        for window in titled:
            logger.error("candidate window: %s", window.describe())
        raise WindowNotFoundError(
            f"no window for process {config.process_name!r} or titles {config.titles}; "
            f"{len(titled)} candidates logged"
        )
    if selected.process_name.casefold() != config.process_name.casefold():
        logger.warning("matched by title and class, not process name: %s", selected.describe())
    return selected


def client_size(hwnd: int) -> tuple[int, int]:
    """Return the current client area size of a window."""
    try:
        _, _, width, height = win32gui.GetClientRect(hwnd)
    except pywintypes.error as error:
        raise WindowLostError(f"window {hwnd} is gone: {error}") from error
    return width, height


def outer_size_for_client(
    window_rect: tuple[int, int, int, int],
    current_client: tuple[int, int],
    target_client: tuple[int, int],
) -> tuple[int, int]:
    """Return the outer window size giving a target client size, keeping the current frame."""
    left, top, right, bottom = window_rect
    frame_width = (right - left) - current_client[0]
    frame_height = (bottom - top) - current_client[1]
    return target_client[0] + frame_width, target_client[1] + frame_height


def shift_into_area(
    client: ScreenRect, area: tuple[int, int, int, int]
) -> tuple[int, int]:
    """Return the (dx, dy) that brings a client rect fully inside an area. (0, 0) if it already is."""
    area_left, area_top, area_right, area_bottom = area
    dx = min(0, area_right - (client.left + client.width))
    if client.left + dx < area_left:
        dx = area_left - client.left
    dy = min(0, area_bottom - (client.top + client.height))
    if client.top + dy < area_top:
        dy = area_top - client.top
    return dx, dy


def client_rect_on_screen(hwnd: int) -> ScreenRect:
    """Return the client area in true screen pixels, wherever the window currently is."""
    width, height = client_size(hwnd)
    try:
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    except pywintypes.error as error:
        raise WindowLostError(f"window {hwnd} is gone: {error}") from error
    return ScreenRect(left, top, width, height)


def monitor_shift(hwnd: int) -> tuple[int, int]:
    """Return the move needed to fit the client area in its monitor's work area (no taskbar).

    Pixels outside it cannot be captured or clicked reliably.
    """
    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    return shift_into_area(client_rect_on_screen(hwnd), win32api.GetMonitorInfo(monitor)["Work"])


def force_client_size(hwnd: int, width: int, height: int, max_attempts: int) -> int:
    """Resize a window until its client area is exactly width x height and fits on a monitor.

    The window keeps its frame: measured on the STOVE client, removing the
    caption makes the game pick its own size (1536x864, then 1520x855 when asked
    for 1280x720), while a framed window takes the exact size on the first try.
    Returns the number of resize attempts used.
    """
    target = (width, height)
    try:
        is_maximized = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_MAXIMIZE
        if win32gui.IsIconic(hwnd) or is_maximized:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        flags = win32con.SWP_NOZORDER | win32con.SWP_NOMOVE | win32con.SWP_NOACTIVATE
        for attempt in range(1, max_attempts + 1):
            outer = outer_size_for_client(win32gui.GetWindowRect(hwnd), client_size(hwnd), target)
            win32gui.SetWindowPos(hwnd, 0, 0, 0, outer[0], outer[1], flags)
            time.sleep(RESIZE_SETTLE_SECONDS)
            if client_size(hwnd) == target:
                dx, dy = monitor_shift(hwnd)
                if (dx, dy) != (0, 0):
                    left, top, _, _ = win32gui.GetWindowRect(hwnd)
                    move_flags = win32con.SWP_NOZORDER | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                    win32gui.SetWindowPos(hwnd, 0, left + dx, top + dy, 0, 0, move_flags)
                return attempt
    except pywintypes.error as error:
        if not win32gui.IsWindow(hwnd):
            raise WindowLostError(f"window {hwnd} is gone: {error}") from error
        hint = f" {ELEVATION_HINT}" if error.winerror == ERROR_ACCESS_DENIED else ""
        raise WindowResizeError(f"Windows refused to resize window {hwnd}: {error}.{hint}") from error
    raise WindowResizeError(
        f"client size is {client_size(hwnd)} after {max_attempts} attempts, wanted {target}"
    )


class GameWindow:
    """The game window, kept at the configured client size."""

    def __init__(self, info: WindowInfo, config: WindowConfig, logger: logging.Logger) -> None:
        """Wrap a window found by find_game_window. Does not change the window yet."""
        self.info = info
        self.hwnd = info.hwnd
        self.target = (config.client_width, config.client_height)
        self.max_attempts = config.resize_max_attempts
        self.logger = logger

    def apply_size(self) -> int:
        """Force the client size now and return the number of attempts used."""
        return force_client_size(self.hwnd, *self.target, self.max_attempts)

    def verify(self) -> bool:
        """Check the client size and that it fits on a monitor, to be called every frame.

        Returns True if both are correct. On drift, logs a warning, re-applies them
        and returns False. Raises WindowLostError if the game has closed.
        """
        size = client_size(self.hwnd)
        if size == self.target and monitor_shift(self.hwnd) == (0, 0):
            return True
        self.logger.warning(
            "client drifted to %s, re-applying %s", client_rect_on_screen(self.hwnd), self.target
        )
        self.apply_size()
        return False

    def client_rect_on_screen(self) -> ScreenRect:
        """Return the client area in true screen pixels."""
        return client_rect_on_screen(self.hwnd)

    def is_foreground(self) -> bool:
        """Return whether the game has the input focus."""
        return win32gui.GetForegroundWindow() == self.hwnd

    def ensure_foreground(self) -> bool:
        """Bring the game to the foreground if needed. Returns whether it worked.

        Windows refuses SetForegroundWindow from a background process unless a
        key was just pressed, so a failed attempt is retried after an Alt tap.
        """
        if self.is_foreground():
            return True
        if not win32gui.IsWindow(self.hwnd):
            raise WindowLostError(f"window {self.hwnd} is gone")
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except pywintypes.error:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            try:
                win32gui.SetForegroundWindow(self.hwnd)
            except pywintypes.error as error:
                self.logger.warning("could not bring the game to the foreground: %s", error)
        # The focus change is asynchronous: measured at about 100 ms on the STOVE client.
        deadline = time.monotonic() + FOREGROUND_WAIT_SECONDS
        while not self.is_foreground() and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.is_foreground()


def open_game_window(config: WindowConfig, logger: logging.Logger, foreground: bool = True) -> GameWindow:
    """Find the game window, force its client size and bring it to the foreground if asked."""
    game = GameWindow(find_game_window(config, logger), config, logger)
    game.apply_size()
    if foreground and not game.ensure_foreground():
        logger.warning("the game is not in the foreground: what covers it will be captured")
    return game

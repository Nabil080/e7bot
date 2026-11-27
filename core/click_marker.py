"""A ring showing where the bot clicked, in a small see-through window owned by the game's.

Being owned, the window stays just above the game: what covers the game covers it too, and it
goes away with a minimized game. It takes no click and no focus. The bot reads the game's own
window, so it never sees the ring. In foreground mode it reads the screen, where the ring would
show up in its images, so the marker only exists in background mode.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass

import pywintypes
import win32api
import win32con
import win32gui

from core.coords import CoordMapper
from core.window import ScreenRect, client_rect_on_screen

_user32 = ctypes.windll.user32
_user32.SetTimer.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p)
_user32.KillTimer.argtypes = (ctypes.c_void_p, ctypes.c_size_t)

MARKER_SIZE = 64
FADE_SECONDS = 0.7
FRAME_MS = 30
FRAME_TIMER = 1
START_WAIT_SECONDS = 2.0
STOP_WAIT_SECONDS = 1.0
MAX_ALPHA = 230
RING_INSET = 6
DOT_INSET = 27
# Painted in this color, a pixel lets the game show through.
SEE_THROUGH = win32api.RGB(255, 0, 255)
RING = win32api.RGB(255, 210, 63)
RING_EDGE = win32api.RGB(20, 20, 20)
WM_SHOW_MARK = win32con.WM_APP + 1
EXTENDED_STYLE = (
    win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW
)
PLACE_FLAGS = win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW


def marker_alpha(elapsed: float) -> int:
    """Return how opaque the ring is this long after a click, 0 once it is gone."""
    if elapsed >= FADE_SECONDS:
        return 0
    return round(MAX_ALPHA * (1 - elapsed / FADE_SECONDS))


def marker_position(client: ScreenRect, x: float, y: float) -> tuple[int, int]:
    """Return where to put the window so that the ring is centered on a game point."""
    screen_x, screen_y = CoordMapper(client).game_to_screen(x, y)
    return screen_x - MARKER_SIZE // 2, screen_y - MARKER_SIZE // 2


@dataclass
class _Mark:
    """The click being shown."""

    x: float
    y: float
    shown_at: float


class ClickMarker:
    """Runs the window on its own thread, so that drawing never holds up the bot, nor the game."""

    def __init__(self, owner: int, logger: logging.Logger) -> None:
        """Create the window, hidden. If that fails the bot goes on without a marker."""
        self.owner = owner
        self.logger = logger
        self.hwnd = 0
        self.mark: _Mark | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="click-marker", daemon=True)
        self._thread.start()
        self._ready.wait(START_WAIT_SECONDS)

    def show(self, x: float, y: float) -> None:
        """Show the ring at a game point, in place of the one before. Never fails: it is only a display."""
        self.mark = _Mark(x, y, time.monotonic())
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, WM_SHOW_MARK, 0, 0)
            except pywintypes.error:
                self.logger.debug("the click marker is gone", exc_info=True)

    def close(self) -> None:
        """Destroy the window and end its thread."""
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except pywintypes.error:
                self.logger.debug("the click marker is already gone", exc_info=True)
        self._thread.join(STOP_WAIT_SECONDS)

    def _run(self) -> None:
        """Create the window and pump its messages until it closes."""
        class_name = f"E7BotClickMarker{id(self)}"
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.lpszClassName = class_name
        window_class.hInstance = instance
        window_class.lpfnWndProc = self._on_message
        try:
            win32gui.RegisterClass(window_class)
            self.hwnd = win32gui.CreateWindowEx(
                EXTENDED_STYLE, class_name, "", win32con.WS_POPUP, 0, 0, MARKER_SIZE, MARKER_SIZE, self.owner, 0, instance, None
            )
            win32gui.SetLayeredWindowAttributes(self.hwnd, SEE_THROUGH, MAX_ALPHA, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
        except pywintypes.error as error:
            self.logger.warning("no click marker: %s", error)
            self.hwnd = 0
            self._ready.set()
            return
        self._ready.set()
        win32gui.PumpMessages()
        self.hwnd = 0
        win32gui.UnregisterClass(class_name, instance)

    def _on_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        """Handle the window's messages."""
        if message == WM_SHOW_MARK:
            _user32.SetTimer(hwnd, FRAME_TIMER, FRAME_MS, None)
            self._draw_frame(hwnd)
            return 0
        if message == win32con.WM_TIMER:
            self._draw_frame(hwnd)
            return 0
        if message == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        if message == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _draw_frame(self, hwnd: int) -> None:
        """Update the fade, and the position in case the game moved."""
        mark = self.mark
        alpha = marker_alpha(time.monotonic() - mark.shown_at) if mark else 0
        try:
            if not alpha or not self._game_is_shown():
                _user32.KillTimer(hwnd, FRAME_TIMER)
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                return
            win32gui.SetLayeredWindowAttributes(hwnd, SEE_THROUGH, alpha, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
            x, y = marker_position(client_rect_on_screen(self.owner), mark.x, mark.y)
            win32gui.SetWindowPos(hwnd, self._just_above_the_game(hwnd), x, y, 0, 0, PLACE_FLAGS)
        except (pywintypes.error, ValueError):
            self.logger.debug("could not draw the click marker", exc_info=True)

    def _game_is_shown(self) -> bool:
        """Return whether the game is there to be marked. Showing the ring would otherwise ignore a minimized game."""
        return win32gui.IsWindow(self.owner) and win32gui.IsWindowVisible(self.owner) and not win32gui.IsIconic(self.owner)

    def _just_above_the_game(self, hwnd: int) -> int:
        """Return the window to go under: the one above the game, which keeps the ring out of other windows' way."""
        above = win32gui.GetWindow(self.owner, win32con.GW_HWNDPREV)
        if above == hwnd:
            above = win32gui.GetWindow(hwnd, win32con.GW_HWNDPREV)
        return above or win32con.HWND_TOP

    @staticmethod
    def _paint(hwnd: int) -> None:
        """Draw a ring with a dark edge, readable on any part of the game, and a dot at its center."""
        dc, paint = win32gui.BeginPaint(hwnd)
        background = win32gui.CreateSolidBrush(SEE_THROUGH)
        win32gui.FillRect(dc, (0, 0, MARKER_SIZE, MARKER_SIZE), background)
        win32gui.SelectObject(dc, win32gui.GetStockObject(win32con.NULL_BRUSH))
        pens = []
        for width, color in ((7, RING_EDGE), (3, RING)):
            pen = win32gui.CreatePen(win32con.PS_SOLID, width, color)
            pens.append(pen)
            win32gui.SelectObject(dc, pen)
            win32gui.Ellipse(dc, RING_INSET, RING_INSET, MARKER_SIZE - RING_INSET, MARKER_SIZE - RING_INSET)
        dot = win32gui.CreateSolidBrush(RING)
        win32gui.SelectObject(dc, dot)
        win32gui.Ellipse(dc, DOT_INSET, DOT_INSET, MARKER_SIZE - DOT_INSET, MARKER_SIZE - DOT_INSET)
        win32gui.SelectObject(dc, win32gui.GetStockObject(win32con.BLACK_PEN))
        win32gui.SelectObject(dc, win32gui.GetStockObject(win32con.NULL_BRUSH))
        for handle in (*pens, dot, background):
            win32gui.DeleteObject(handle)
        win32gui.EndPaint(hwnd, paint)

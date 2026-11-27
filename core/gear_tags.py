"""Verdict tags drawn over the game's gear cards, in a see-through window owned by the game's.

Like the click marker, the window is owned by the game: it stays just above it, goes away with a minimized game,
and takes no click and no focus (clicks go through to the game). It covers the game's client area and paints only
the tags; the advisor reads the game's own window, so it never sees them.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass

import pywintypes
import win32api
import win32con
import win32gui

from core.coords import CoordMapper
from core.window import client_rect_on_screen

_user32 = ctypes.windll.user32
_user32.SetTimer.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p)
_user32.KillTimer.argtypes = (ctypes.c_void_p, ctypes.c_size_t)

START_WAIT_SECONDS = 2.0
STOP_WAIT_SECONDS = 1.0
# The game can move: the window follows it this often while tags show.
FOLLOW_MS = 250
FOLLOW_TIMER = 1
ALPHA = 235
SEE_THROUGH = win32api.RGB(255, 0, 255)
WM_SHOW_TAGS = win32con.WM_APP + 2
EXTENDED_STYLE = (
    win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW
)
PLACE_FLAGS = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
# A tag: a pill on the bottom of the card's icon, over the game's own score number, in game pixels.
TAG_WIDTH, TAG_HEIGHT = 92, 20
TAG_FROM_CARD = (4, 92)
FONT_HEIGHT = 15
# The verdict colours of the bot window, the same as its cards: green to enhance, gold to keep or lock,
# coral to get rid of, graphite when unread.
COLORS = {
    "enhance": (win32api.RGB(60, 207, 142), win32api.RGB(4, 23, 14)),
    "speed": (win32api.RGB(60, 207, 142), win32api.RGB(4, 23, 14)),
    "keep": (win32api.RGB(233, 185, 73), win32api.RGB(29, 21, 3)),
    "lock": (win32api.RGB(233, 185, 73), win32api.RGB(29, 21, 3)),
    "sell": (win32api.RGB(255, 107, 87), win32api.RGB(33, 7, 3)),
    "extract": (win32api.RGB(255, 107, 87), win32api.RGB(33, 7, 3)),
    "": (win32api.RGB(44, 49, 57), win32api.RGB(139, 147, 161)),
}
EDGE = win32api.RGB(15, 16, 19)


@dataclass(frozen=True)
class Tag:
    """One tag: the card's area in the 1280x720 game space, the verdict's color key and its text."""

    box: tuple[int, int, int, int]
    action: str
    text: str


def tag_rect(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Return the tag's rectangle (left, top, right, bottom) in game pixels for a card's area."""
    left, top = box[0] + TAG_FROM_CARD[0], box[1] + TAG_FROM_CARD[1]
    return left, top, left + TAG_WIDTH, top + TAG_HEIGHT


class GearTags:
    """Runs the window on its own thread: drawing never holds up the advisor, nor the game."""

    def __init__(self, owner: int, logger: logging.Logger) -> None:
        """Create the window, hidden. If that fails the advisor goes on without tags."""
        self.owner = owner
        self.logger = logger
        self.hwnd = 0
        self.tags: tuple[Tag, ...] = ()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gear-tags", daemon=True)
        self._thread.start()
        self._ready.wait(START_WAIT_SECONDS)

    def show(self, tags: list[Tag] | tuple[Tag, ...]) -> None:
        """Show these tags in place of the ones before; none hides the window. Never fails: it is only a display."""
        self.tags = tuple(tags)
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, WM_SHOW_TAGS, 0, 0)
            except pywintypes.error:
                self.logger.debug("the gear tags are gone", exc_info=True)

    def close(self) -> None:
        """Destroy the window and end its thread."""
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except pywintypes.error:
                self.logger.debug("the gear tags are already gone", exc_info=True)
        self._thread.join(STOP_WAIT_SECONDS)

    def _run(self) -> None:
        """Create the window and pump its messages until it closes."""
        class_name = f"E7BotGearTags{id(self)}"
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.lpszClassName = class_name
        window_class.hInstance = instance
        window_class.lpfnWndProc = self._on_message
        try:
            win32gui.RegisterClass(window_class)
            self.hwnd = win32gui.CreateWindowEx(EXTENDED_STYLE, class_name, "", win32con.WS_POPUP, 0, 0, 1, 1, self.owner, 0, instance, None)
            win32gui.SetLayeredWindowAttributes(self.hwnd, SEE_THROUGH, ALPHA, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
        except pywintypes.error as error:
            self.logger.warning("no gear tags over the game: %s", error)
            self.hwnd = 0
            self._ready.set()
            return
        self._ready.set()
        win32gui.PumpMessages()
        self.hwnd = 0
        win32gui.UnregisterClass(class_name, instance)

    def _on_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        """Handle the window's messages."""
        if message in (WM_SHOW_TAGS, win32con.WM_TIMER):
            self._place(hwnd, repaint=message == WM_SHOW_TAGS)
            return 0
        if message == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        if message == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if message == win32con.WM_DESTROY:
            _user32.KillTimer(hwnd, FOLLOW_TIMER)
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _place(self, hwnd: int, repaint: bool) -> None:
        """Cover the game's client area just above it, or hide when there is nothing to show."""
        try:
            if not self.tags or not self._game_is_shown():
                _user32.KillTimer(hwnd, FOLLOW_TIMER)
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                return
            rect = client_rect_on_screen(self.owner)
            win32gui.SetWindowPos(hwnd, self._just_above_the_game(hwnd), rect.left, rect.top, rect.width, rect.height, PLACE_FLAGS)
            _user32.SetTimer(hwnd, FOLLOW_TIMER, FOLLOW_MS, None)
            if repaint:
                win32gui.InvalidateRect(hwnd, None, True)
        except (pywintypes.error, ValueError):
            self.logger.debug("could not place the gear tags", exc_info=True)

    def _game_is_shown(self) -> bool:
        """Return whether the game is there to draw on."""
        return win32gui.IsWindow(self.owner) and win32gui.IsWindowVisible(self.owner) and not win32gui.IsIconic(self.owner)

    def _just_above_the_game(self, hwnd: int) -> int:
        """Return the window to go under: the one above the game, which keeps the tags out of other windows' way."""
        above = win32gui.GetWindow(self.owner, win32con.GW_HWNDPREV)
        if above == hwnd:
            above = win32gui.GetWindow(hwnd, win32con.GW_HWNDPREV)
        return above or win32con.HWND_TOP

    def _paint(self, hwnd: int) -> None:
        """Paint the see-through ground, then each tag as a framed pill with its word."""
        dc, paint = win32gui.BeginPaint(hwnd)
        try:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            ground = win32gui.CreateSolidBrush(SEE_THROUGH)
            win32gui.FillRect(dc, (left, top, right, bottom), ground)
            win32gui.DeleteObject(ground)
            try:
                mapper = CoordMapper(client_rect_on_screen(self.owner))
            except (pywintypes.error, ValueError):
                return
            scale = mapper.client.width / 1280
            font = win32gui.CreateFontIndirect(_font(round(FONT_HEIGHT * scale)))
            old_font = win32gui.SelectObject(dc, font)
            win32gui.SetBkMode(dc, win32con.TRANSPARENT)
            pen = win32gui.CreatePen(win32con.PS_SOLID, 1, EDGE)
            old_pen = win32gui.SelectObject(dc, pen)
            for tag in self.tags:
                fill, ink = COLORS.get(tag.action, COLORS[""])
                x0, y0, x1, y1 = (round(value * scale) for value in tag_rect(tag.box))
                brush = win32gui.CreateSolidBrush(fill)
                old_brush = win32gui.SelectObject(dc, brush)
                win32gui.RoundRect(dc, x0, y0, x1, y1, 8, 8)
                win32gui.SelectObject(dc, old_brush)
                win32gui.DeleteObject(brush)
                win32gui.SetTextColor(dc, ink)
                win32gui.DrawText(dc, tag.text, -1, (x0, y0, x1, y1), win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
            win32gui.SelectObject(dc, old_pen)
            win32gui.DeleteObject(pen)
            win32gui.SelectObject(dc, old_font)
            win32gui.DeleteObject(font)
        finally:
            win32gui.EndPaint(hwnd, paint)


def _font(height: int):
    """Return a bold Segoe UI of this pixel height, without smoothing that would blend into the see-through color."""
    font = win32gui.LOGFONT()
    font.lfHeight = -height
    font.lfWeight = win32con.FW_BOLD
    font.lfFaceName = "Segoe UI"
    font.lfQuality = win32con.NONANTIALIASED_QUALITY
    return font

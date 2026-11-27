"""Mouse and keyboard in game coordinates, with randomized timing and click position."""

from __future__ import annotations

import ctypes
import logging
import random
import threading
import time
from ctypes import wintypes
from typing import Callable, Protocol

import pydirectinput
import win32api
import win32con
import win32gui

from core.config import InputConfig, Range
from core.coords import CoordMapper
from core.stop import BotStopped
from core.window import ScreenRect

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MOVE_SETTLE_SECONDS = 0.02
MAX_JITTER_DRAWS = 20
VIRTUAL_KEYS = {"esc": win32con.VK_ESCAPE, "enter": win32con.VK_RETURN}
KEY_PRESS_SECONDS = 0.08
DRAG_STEPS = 15
DRAG_SETTLE_SECONDS = 0.1
DRAG_STEP_SECONDS = 0.03
DRAG_HOLD_SECONDS = 0.3
# A quick drag, for the Inventory grids: fewer steps and a shorter hold scroll exactly as far (measured).
QUICK_DRAG = (8, 0.02, 0.15)

# The bot sets its own pauses, and an emergency stop must not depend on the cursor position.
pydirectinput.PAUSE = 0
pydirectinput.FAILSAFE = False


class _MouseInput(ctypes.Structure):

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):
    """KEYBDINPUT from the Windows API."""

    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    """The union inside INPUT. The mouse member is the largest."""

    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):

    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


class InputError(Exception):
    """Windows refused an input, or the game could not be focused."""


class InputWindow(Protocol):
    """What the controller needs from a window. GameWindow provides it."""

    def client_rect_on_screen(self) -> ScreenRect:
        ...

    def ensure_foreground(self) -> bool:
        """Bring the game to the foreground and return whether it worked."""
        ...


def _send(*inputs: _Input) -> None:
    """Send inputs to Windows, raising InputError if they are not all accepted."""
    array = (_Input * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), array, ctypes.sizeof(_Input))
    if sent != len(inputs):
        raise InputError(f"SendInput accepted {sent} of {len(inputs)} events: error {ctypes.get_last_error()}")


def _mouse_event(flags: int, dx: int = 0, dy: int = 0) -> _Input:
    event = _Input(type=INPUT_MOUSE)
    event.u.mi = _MouseInput(dx, dy, 0, flags, 0, 0)
    return event


def normalize_to_virtual_desktop(
    x: int, y: int, desktop_left: int, desktop_top: int, desktop_width: int, desktop_height: int
) -> tuple[int, int]:
    """Convert a screen pixel to the 0-65535 range SendInput uses across all monitors.

    Aims at the pixel's center, so that truncation inside Windows cannot land on its neighbor.
    """
    normalized_x = ((x - desktop_left) * 2 + 1) * 65536 // (desktop_width * 2)
    normalized_y = ((y - desktop_top) * 2 + 1) * 65536 // (desktop_height * 2)
    return normalized_x, normalized_y


def move_cursor(x: int, y: int) -> None:
    """Move the cursor to a screen pixel. Works on monitors with negative coordinates."""
    metrics = _user32.GetSystemMetrics
    dx, dy = normalize_to_virtual_desktop(
        x,
        y,
        metrics(SM_XVIRTUALSCREEN),
        metrics(SM_YVIRTUALSCREEN),
        metrics(SM_CXVIRTUALSCREEN),
        metrics(SM_CYVIRTUALSCREEN),
    )
    _send(_mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, dx, dy))


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        raise InputError(f"GetCursorPos failed: error {ctypes.get_last_error()}")
    return point.x, point.y


def jittered_point(
    x: float, y: float, jitter: int, last: tuple[int, int] | None, rng: random.Random
) -> tuple[int, int]:
    """Return a random point around (x, y), different from the last one whenever jitter allows."""
    point = (round(x), round(y))
    if jitter <= 0:
        return point
    for _ in range(MAX_JITTER_DRAWS):
        point = (round(x) + rng.randint(-jitter, jitter), round(y) + rng.randint(-jitter, jitter))
        if point != last:
            break
    return point


# A skill only activates when both clicks of a double click land quickly, on the same point.
DOUBLE_CLICK_HOLD_MS = Range(min=30, max=60)
# A tap is a short click without the pre-action delay, for text that only needs to move on.
TAP_HOLD_MS = Range(min=20, max=45)
DOUBLE_CLICK_GAP_MS = Range(min=50, max=100)


class InputController:
    """Clicks and types in game coordinates. In dry-run mode it only logs."""

    def __init__(
        self,
        window: InputWindow,
        config: InputConfig,
        logger: logging.Logger,
        dry_run: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Seed the random generator from the config: a fixed seed replays the same run."""
        self.stop_event = stop_event or threading.Event()
        self.window = window
        self.config = config
        self.logger = logger
        self.dry_run = dry_run
        self.rng = random.Random(config.seed)
        self._last_click: tuple[int, int] | None = None
        self.on_click: Callable[[int, int], None] | None = None

    def _duration(self, milliseconds: Range) -> float:
        """Draw a duration in seconds from a range in milliseconds."""
        return self.rng.uniform(milliseconds.min, milliseconds.max) / 1000

    def _prepare(self) -> None:
        """Wait the pre-action delay and make sure the game receives the input."""
        if self.stop_event.wait(self._duration(self.config.pre_action_delay_ms)):
            raise BotStopped()
        if not self.config.background and not self.window.ensure_foreground():
            raise InputError("the game is not in the foreground, refusing to send input")

    def click(self, x: float, y: float) -> tuple[int, int]:
        """Click a game point and return the game point actually used, after jitter."""
        return self._click(x, y, presses=1)

    def tap(self, x: float, y: float) -> tuple[int, int]:
        """Click a game point at once and briefly: for unskippable dialogue, where speed matters more."""
        return self._click(x, y, presses=1, fast=True)

    def double_click(self, x: float, y: float) -> tuple[int, int]:
        """Double click a game point: one delay and one jitter for both clicks, which come fast."""
        return self._click(x, y, presses=2)

    def _click(self, x: float, y: float, presses: int, fast: bool = False) -> tuple[int, int]:
        """Wait, then press the button at a jittered point. Returns the point used."""
        mapper = CoordMapper(self.window.client_rect_on_screen())
        point = jittered_point(x, y, self.config.jitter_px, self._last_click, self.rng)
        clamped = mapper.clamp(*point)
        point = (round(clamped[0]), round(clamped[1]))
        self._last_click = point
        screen = mapper.game_to_screen(*point)
        if self.dry_run:
            self.logger.info("dry run: click at game %s, screen %s", point, screen)
            return point

        if fast:
            if self.stop_event.is_set():
                raise BotStopped()
            if not self.config.background and not self.window.ensure_foreground():
                raise InputError("the game is not in the foreground, refusing to send input")
        else:
            self._prepare()
        # The window can move during the delay: use its position at the last moment.
        rect = self.window.client_rect_on_screen()
        if rect.width <= 0 or rect.height <= 0:
            # Minimized during the delay: the next frame puts it back, and the click is tried again then.
            self.logger.warning("the game window was minimized: click skipped")
            return point
        screen = CoordMapper(rect).game_to_screen(*point)
        self.logger.debug("%d click(s) at game %s, screen %s", presses, point, screen)
        send = self._click_pyautogui if self.config.mouse_backend == "pyautogui" else self._click_sendinput
        if self.config.background:
            send = self._post_click
        if self.on_click is not None:
            self.on_click(*point)
        if presses == 1:
            send(screen, self._duration(TAP_HOLD_MS if fast else self.config.click_hold_ms))
            return point
        for press in range(presses):
            if press:
                time.sleep(self._duration(DOUBLE_CLICK_GAP_MS))
            send(screen, self._duration(DOUBLE_CLICK_HOLD_MS))
        return point

    def _post_mouse(self, message: int, buttons: int, screen: tuple[int, int]) -> None:
        """Send a mouse message to the game's window. The real cursor does not move."""
        x, y = win32gui.ScreenToClient(self.window.hwnd, screen)
        win32gui.PostMessage(self.window.hwnd, message, buttons, (y << 16) | (x & 0xFFFF))

    def _post_click(self, screen: tuple[int, int], hold: float) -> None:
        """Click through the game's window, whatever covers it."""
        self._post_mouse(win32con.WM_MOUSEMOVE, 0, screen)
        time.sleep(MOVE_SETTLE_SECONDS)
        self._post_mouse(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, screen)
        time.sleep(hold)
        self._post_mouse(win32con.WM_LBUTTONUP, 0, screen)

    def _post_key(self, key: str, down: bool) -> None:
        """Send a key to the game's window. Bits 16 and up describe the key as Windows would."""
        virtual_key = VIRTUAL_KEYS[key]
        details = 1 | (win32api.MapVirtualKey(virtual_key, 0) << 16)
        message = win32con.WM_KEYDOWN if down else win32con.WM_KEYUP
        win32gui.PostMessage(self.window.hwnd, message, virtual_key, details if down else details | (3 << 30))

    def drag(self, x1: float, y1: float, x2: float, y2: float, quick: bool = False) -> None:
        """Press at a game point, slide to another one and let go, as to scroll a list. `quick` moves faster."""
        steps, step_seconds, hold = QUICK_DRAG if quick else (DRAG_STEPS, DRAG_STEP_SECONDS, DRAG_HOLD_SECONDS)
        if self.dry_run:
            self.logger.info("dry run: drag from game (%s, %s) to (%s, %s)", x1, y1, x2, y2)
            return
        self._prepare()
        mapper = CoordMapper(self.window.client_rect_on_screen())
        if self.config.background:
            self._post_drag(mapper, x1, y1, x2, y2, steps, step_seconds, hold)
            return
        move_cursor(*mapper.game_to_screen(x1, y1))
        time.sleep(DRAG_SETTLE_SECONDS)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        time.sleep(DRAG_SETTLE_SECONDS)
        for step in range(1, steps + 1):
            point = x1 + (x2 - x1) * step / steps, y1 + (y2 - y1) * step / steps
            move_cursor(*mapper.game_to_screen(*point))
            time.sleep(step_seconds)
        # Letting go while still moving would throw the list further than asked.
        time.sleep(hold)
        _send(_mouse_event(MOUSEEVENTF_LEFTUP))

    def _post_drag(
        self, mapper: CoordMapper, x1: float, y1: float, x2: float, y2: float,
        steps: int = DRAG_STEPS, step_seconds: float = DRAG_STEP_SECONDS, hold: float = DRAG_HOLD_SECONDS,
    ) -> None:
        """Drag through the game's window."""
        start = mapper.game_to_screen(x1, y1)
        self._post_mouse(win32con.WM_MOUSEMOVE, 0, start)
        time.sleep(DRAG_SETTLE_SECONDS)
        self._post_mouse(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, start)
        time.sleep(DRAG_SETTLE_SECONDS)
        point = start
        for step in range(1, steps + 1):
            point = mapper.game_to_screen(x1 + (x2 - x1) * step / steps, y1 + (y2 - y1) * step / steps)
            self._post_mouse(win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, point)
            time.sleep(step_seconds)
        time.sleep(hold)
        self._post_mouse(win32con.WM_LBUTTONUP, 0, point)

    @staticmethod
    def _click_sendinput(screen: tuple[int, int], hold: float) -> None:
        move_cursor(*screen)
        time.sleep(MOVE_SETTLE_SECONDS)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        time.sleep(hold)
        _send(_mouse_event(MOUSEEVENTF_LEFTUP))

    @staticmethod
    def _click_pyautogui(screen: tuple[int, int], hold: float) -> None:
        """Click through pyautogui. Imported here because the default backend does not need it."""
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
        pyautogui.moveTo(*screen)
        pyautogui.mouseDown()
        time.sleep(hold)
        pyautogui.mouseUp()

    def key_down(self, key: str) -> None:
        """Start holding a key. The caller must call key_up, even when something fails."""
        if self.dry_run:
            self.logger.info("dry run: hold %s", key)
            return
        self._prepare()
        self.logger.debug("key down %s", key)
        if self.config.background:
            self._post_key(key, down=True)
            return
        pydirectinput.keyDown(key)

    def key_up(self, key: str) -> None:
        """Release a key, without any delay: this must also work during a stop."""
        if self.dry_run:
            return
        self.logger.debug("key up %s", key)
        if self.config.background:
            self._post_key(key, down=False)
            return
        pydirectinput.keyUp(key)

    def press(self, key: str) -> None:
        """Press a named key such as 'esc' or 'enter'."""
        if self.dry_run:
            self.logger.info("dry run: press %s", key)
            return
        self._prepare()
        self.logger.debug("press %s", key)
        if self.config.background:
            self._post_key(key, down=True)
            time.sleep(KEY_PRESS_SECONDS)
            self._post_key(key, down=False)
            return
        pydirectinput.press(key)

    def type_text(self, text: str, secret: bool = False) -> None:
        """Type text as Unicode characters, which works with any keyboard layout.

        pydirectinput sends QWERTY scan codes, so text would come out wrong on AZERTY.
        A secret's content and length stay out of the logs.
        """
        label = "secret text" if secret else repr(text)
        if self.dry_run:
            self.logger.info("dry run: type %s", label)
            return
        self._prepare()
        self.logger.debug("type %s", label)
        for character in text:
            if self.stop_event.is_set():
                raise BotStopped()
            if self.config.background:
                win32gui.PostMessage(self.window.hwnd, win32con.WM_CHAR, ord(character), 1)
                time.sleep(self._duration(self.config.click_hold_ms))
                continue
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                event = _Input(type=INPUT_KEYBOARD)
                event.u.ki = _KeyboardInput(0, ord(character), flags, 0, 0)
                _send(event)
            time.sleep(self._duration(self.config.click_hold_ms))

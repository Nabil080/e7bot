"""Emergency stop: a global hotkey (F10 by default), watched in its own thread so it works at any moment."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import win32con

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 1
STOP_KEY_NAME = "F10"
STOP_KEY = win32con.VK_F10
START_TIMEOUT_SECONDS = 2.0

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class BotStopped(Exception):
    """The user asked the bot to stop."""


class StopWatcher:
    """Sets an event when the stop key is pressed, even while the game has the focus."""

    def __init__(self, key: int = STOP_KEY, key_name: str = STOP_KEY_NAME) -> None:
        """Prepare the watcher for one key. Call start() to register the hotkey."""
        self.key = key
        self.key_name = key_name
        self.event = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._started = threading.Event()
        self._error: str | None = None

    def start(self) -> None:
        """Register the hotkey, raising OSError if it is taken: the bot must not run without it."""
        self._thread = threading.Thread(target=self._watch, name="stop-watcher", daemon=True)
        self._thread.start()
        self._started.wait(START_TIMEOUT_SECONDS)
        if self._error or not self._started.is_set():
            raise OSError(self._error or "the stop key watcher did not start")

    def _watch(self) -> None:
        """Register the hotkey on this thread and wait for its messages."""
        self._thread_id = _kernel32.GetCurrentThreadId()
        if not _user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, self.key):
            self._error = f"{self.key_name} is already used by another program"
            self._started.set()
            return
        self._started.set()
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    self.event.set()
        finally:
            _user32.UnregisterHotKey(None, HOTKEY_ID)

    def close(self) -> None:
        """Release the hotkey and end the thread."""
        if self._thread is not None and self._thread.is_alive():
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(START_TIMEOUT_SECONDS)

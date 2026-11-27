"""Grabs the game's client area, from the screen or its own window, and caches the last frame."""

from __future__ import annotations

import ctypes
import time
from typing import Protocol

import mss
import numpy as np
import win32con
import win32gui
import win32ui

from core.coords import CoordMapper
from core.window import ScreenRect, WindowLostError

BLANK_FRAME_STD = 1.0
# PW_CLIENTONLY and PW_RENDERFULLCONTENT: without the latter, a 3D window comes out black.
PRINT_WINDOW_FLAGS = 3


class CapturableWindow(Protocol):
    """What Capture needs from a window. GameWindow provides it."""

    def verify(self) -> bool:
        """Check and repair the client size."""
        ...

    def client_rect_on_screen(self) -> ScreenRect:
        ...


def print_window(hwnd: int, width: int, height: int) -> np.ndarray:
    """Return the client area of a window as BGR, even when other windows cover it.

    A minimized window has nothing to copy: it gets restored, without taking the focus.
    """
    if not win32gui.IsWindow(hwnd):
        raise WindowLostError(f"window {hwnd} is gone")
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    window_dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(window_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    memory.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), PRINT_WINDOW_FLAGS):
            raise WindowLostError(f"could not copy window {hwnd}")
        pixels = np.frombuffer(bitmap.GetBitmapBits(True), np.uint8)
        return pixels.reshape(height, width, 4)[:, :, :3].copy()
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)


def is_blank(frame: np.ndarray) -> bool:
    """Return whether a frame has a single flat color, such as a black loading screen."""
    return float(frame.std()) < BLANK_FRAME_STD


class Capture:
    """Captures the client area as BGR frames.

    mss reads the screen, so a window covering the game ends up in the frame, and the
    caller must keep the game visible. In background mode, only the game's own window is
    read: whatever else is on the screen can never end up in a frame.
    """

    def __init__(self, window: CapturableWindow, background: bool = False) -> None:
        """Open the screen grabber. Close it with close() or a `with` block."""
        self.window = window
        self.background = background
        self._grabber = mss.MSS()
        self.last_frame: np.ndarray | None = None
        self.last_rect: ScreenRect | None = None
        self.last_grab_time = 0.0

    def __enter__(self) -> "Capture":
        """Support `with Capture(window) as capture`."""
        return self

    def __exit__(self, *_: object) -> None:
        """Release the screen grabber."""
        self.close()

    def close(self) -> None:
        """Release the screen grabber."""
        self._grabber.close()

    def grab(self, max_age_seconds: float = 0.0) -> np.ndarray:
        """Return a BGR frame of the client area, reusing the last one if it is recent enough.

        A real grab checks the window first, so that a resized window is repaired
        before the frame is taken. Raises WindowLostError if the game has closed.
        """
        is_recent = time.monotonic() - self.last_grab_time <= max_age_seconds
        if self.last_frame is not None and max_age_seconds > 0 and is_recent:
            return self.last_frame
        self.window.verify()
        rect = self.window.client_rect_on_screen()
        if self.background:
            self.last_frame = print_window(self.window.hwnd, rect.width, rect.height)
        else:
            shot = self._grabber.grab(
                {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
            )
            self.last_frame = np.array(shot)[:, :, :3]
        self.last_rect = rect
        self.last_grab_time = time.monotonic()
        return self.last_frame

    def mapper(self) -> CoordMapper:
        """Return a CoordMapper for the current client rect."""
        return CoordMapper(self.window.client_rect_on_screen())

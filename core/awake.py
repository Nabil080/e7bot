"""Keep the screen on while the bot runs: the game stops drawing and taking input once the display sleeps."""

from __future__ import annotations

import ctypes
from collections.abc import Iterator
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", _MouseInput)]


def wake_display() -> bool:
    """Wake a display that already went to sleep: a mouse move of zero pixels. The cursor does not move."""
    event = _Input(INPUT_MOUSE, _MouseInput(0, 0, 0, MOUSEEVENTF_MOVE, 0, 0))
    return bool(ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)))


def _set_state(flags: int) -> bool:
    """Ask Windows for a power state for the calling thread. Returns whether it accepted."""
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


@contextmanager
def keep_awake() -> Iterator[bool]:
    """Keep the display and the PC awake inside the block, as a video player does.

    It only resets Windows' idle timers: no input is sent and nothing takes the focus.
    Yields whether Windows accepted. The request ends with the block.
    """
    # A display already asleep stays asleep under the request alone: it is woken first.
    wake_display()
    accepted = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    try:
        yield accepted
    finally:
        _set_state(ES_CONTINUOUS)

"""The ring showing clicks: where it goes, how it fades, and how its window behaves."""

import logging
import threading
import time

import numpy as np
import pytest
import win32api
import win32con
import win32gui

from core.capture import print_window
from core.click_marker import EXTENDED_STYLE, FADE_SECONDS, MARKER_SIZE, ClickMarker, marker_alpha, marker_position
from core.config import load_config
from core.input import InputController
from core.settings import Settings, apply_settings, settings_from_config
from core.window import ScreenRect

LOGGER = logging.getLogger("test_click_marker")
CONFIG = load_config("config.yaml")


def test_the_ring_is_gone_in_less_than_a_second():
    assert FADE_SECONDS < 1
    assert marker_alpha(0) > marker_alpha(FADE_SECONDS / 2) > 0
    assert marker_alpha(FADE_SECONDS) == 0


def test_the_ring_is_centered_on_the_click_wherever_the_game_is():
    assert marker_position(ScreenRect(-1505, 222, 1280, 720), 640, 400) == (-1505 + 640 - MARKER_SIZE // 2, 222 + 400 - MARKER_SIZE // 2)


def test_the_option_is_off_by_default():
    assert not Settings().click_marker
    assert not CONFIG.input.click_marker
    assert not settings_from_config(CONFIG).click_marker


def test_the_setting_reaches_the_bot():
    assert apply_settings(CONFIG, Settings(click_marker=True)).input.click_marker


class Owner:
    """A real window standing in for the game, far off screen, with its own message loop."""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(2)

    def _run(self) -> None:
        window_class = win32gui.WNDCLASS()
        window_class.lpszClassName = f"E7BotTestOwner{id(self)}"
        window_class.lpfnWndProc = self._on_message
        window_class.hbrBackground = win32gui.GetStockObject(win32con.BLACK_BRUSH)
        win32gui.RegisterClass(window_class)
        self.hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE, window_class.lpszClassName, "", win32con.WS_POPUP,
            -30000, -30000, 300, 200, 0, 0, win32api.GetModuleHandle(None), None,
        )
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        self.ready.set()
        win32gui.PumpMessages()

    @staticmethod
    def _on_message(hwnd, message, wparam, lparam):
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def close(self) -> None:
        win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        self.thread.join(1)


@pytest.fixture
def owned():
    owner = Owner()
    marker = ClickMarker(owner.hwnd, LOGGER)
    yield owner, marker
    marker.close()
    owner.close()


def wait_until(condition, seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_window_is_owned_lets_clicks_through_and_is_not_topmost(owned):
    owner, marker = owned
    assert win32gui.GetWindow(marker.hwnd, win32con.GW_OWNER) == owner.hwnd
    style = win32gui.GetWindowLong(marker.hwnd, win32con.GWL_EXSTYLE)
    assert style & EXTENDED_STYLE == EXTENDED_STYLE
    assert not style & win32con.WS_EX_TOPMOST


def test_the_ring_shows_then_goes_away(owned):
    _, marker = owned
    assert not win32gui.IsWindowVisible(marker.hwnd)
    marker.show(100, 100)
    assert wait_until(lambda: win32gui.IsWindowVisible(marker.hwnd))
    assert wait_until(lambda: not win32gui.IsWindowVisible(marker.hwnd), FADE_SECONDS + 0.3)


def test_the_ring_never_takes_the_focus(owned):
    _, marker = owned
    before = win32gui.GetForegroundWindow()
    marker.show(100, 100)
    assert wait_until(lambda: win32gui.IsWindowVisible(marker.hwnd))
    assert win32gui.GetForegroundWindow() == before


def test_the_ring_is_not_in_what_the_bot_reads(owned):
    owner, marker = owned
    before = print_window(owner.hwnd, 300, 200)
    marker.show(100, 100)
    assert wait_until(lambda: win32gui.IsWindowVisible(marker.hwnd))
    assert np.array_equal(print_window(owner.hwnd, 300, 200), before)


def test_a_click_is_reported_to_the_marker(monkeypatch):
    import core.input as input_module

    class Window:
        hwnd = 1

        def client_rect_on_screen(self):
            return ScreenRect(0, 0, 1280, 720)

    monkeypatch.setattr(input_module.win32gui, "PostMessage", lambda *args: None)
    monkeypatch.setattr(input_module.win32gui, "ScreenToClient", lambda hwnd, point: point)
    config = CONFIG.input.model_copy(update={"background": True, "jitter_px": 0})
    controller = InputController(Window(), config, LOGGER)
    clicks = []
    controller.on_click = lambda x, y: clicks.append((x, y))
    assert controller.click(640, 400) == clicks[0]

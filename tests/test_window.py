"""Window lookup logic, plus resize and drift checks on a real hidden test window."""

import logging

import pytest
import pywintypes
import win32api
import win32con
import win32gui

from core.config import WindowConfig
from core.window import (
    GameWindow,
    ScreenRect,
    WindowInfo,
    WindowLostError,
    WindowResizeError,
    client_size,
    enable_dpi_awareness,
    find_game_window,
    force_client_size,
    is_dpi_aware,
    monitor_shift,
    outer_size_for_client,
    process_names_by_pid,
    select_game_window,
    shift_into_area,
)

LOGGER = logging.getLogger("test_window")
TEST_CLASS = "E7BotTestWindow"


@pytest.fixture
def config() -> WindowConfig:
    return WindowConfig(
        process_name="EpicSeven.exe",
        class_names=["GLFW30"],
        titles=["Epic Seven"],
        excluded_process_names=["STOVE.exe", "EpicSeven_Steam.exe"],
        client_width=640,
        client_height=360,
    )


def make_info(hwnd=1, process="EpicSeven.exe", cls="GLFW30", title="Epic Seven", width=1280, height=720) -> WindowInfo:
    return WindowInfo(hwnd, hwnd * 10, process, cls, title, width, height)


def test_process_name_wins_over_steam_build_with_same_title_and_class(config):
    steam = make_info(hwnd=1, process="EpicSeven_Steam.exe", width=1920, height=1080)
    stove = make_info(hwnd=2)
    assert select_game_window([steam, stove], config) == stove


def test_steam_build_alone_is_never_selected(config):
    assert select_game_window([make_info(process="EpicSeven_Steam.exe")], config) is None


def test_launcher_is_never_selected(config):
    assert select_game_window([make_info(process="STOVE.exe")], config) is None


def test_similar_process_name_does_not_match(config):
    assert select_game_window([make_info(process="EpicSeven2.exe", title="x")], config) is None


def test_fallback_needs_exact_title_and_class(config):
    fallback = make_info(process="renamed.exe")
    assert select_game_window([fallback], config) == fallback
    wrong_class = make_info(process="renamed.exe", cls="Chrome_WidgetWin_1")
    assert select_game_window([wrong_class], config) is None
    browser_tab = make_info(process="renamed.exe", title="Epic Seven wiki")
    assert select_game_window([browser_tab], config) is None


def test_largest_window_of_the_process_is_selected(config):
    splash = make_info(hwnd=1, width=300, height=200)
    main_window = make_info(hwnd=2)
    assert select_game_window([splash, main_window], config) == main_window


def test_not_found_logs_candidates(config, caplog, monkeypatch):
    monkeypatch.setattr("core.window.list_windows", lambda: [make_info(process="other.exe", title="Other", cls="x")])
    with caplog.at_level(logging.ERROR), pytest.raises(Exception, match="candidates logged"):
        find_game_window(config, LOGGER)
    assert "Other" in caplog.text


def test_outer_size_adds_the_current_frame():
    assert outer_size_for_client((100, 100, 1124, 739), (1000, 600), (1280, 720)) == (1304, 759)


def test_outer_size_works_with_negative_coordinates():
    assert outer_size_for_client((-1637, 173, -341, 932), (1280, 720), (1280, 720)) == (1296, 759)


def test_dpi_awareness_can_be_enabled_twice():
    enable_dpi_awareness()
    enable_dpi_awareness()
    assert is_dpi_aware()


def test_process_list_contains_this_python():
    assert process_names_by_pid()[win32api.GetCurrentProcessId()].lower().startswith("python")


@pytest.fixture(scope="module")
def window_class():
    enable_dpi_awareness()
    window_class = win32gui.WNDCLASS()
    window_class.lpszClassName = TEST_CLASS
    window_class.lpfnWndProc = {}
    atom = win32gui.RegisterClass(window_class)
    yield atom
    win32gui.UnregisterClass(TEST_CLASS, win32api.GetModuleHandle(None))


@pytest.fixture
def hwnd(window_class):
    handle = win32gui.CreateWindow(
        window_class, "test", win32con.WS_OVERLAPPEDWINDOW, -500, 120, 900, 700, 0, 0, 0, None
    )
    yield handle
    if win32gui.IsWindow(handle):
        win32gui.DestroyWindow(handle)


@pytest.fixture
def game(hwnd, config) -> GameWindow:
    info = WindowInfo(hwnd, 0, "EpicSeven.exe", TEST_CLASS, "test", 0, 0)
    return GameWindow(info, config, LOGGER)


def primary_work_area() -> tuple[int, int, int, int]:
    monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
    return win32api.GetMonitorInfo(monitor)["Work"]


def move_window(hwnd: int, left: int, top: int) -> None:
    win32gui.SetWindowPos(hwnd, 0, left, top, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)


def test_force_size_gives_exact_client_and_keeps_frame(hwnd):
    force_client_size(hwnd, 640, 360, 10)
    assert client_size(hwnd) == (640, 360)
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    assert style & win32con.WS_CAPTION
    assert style & win32con.WS_THICKFRAME


def test_force_size_does_not_move_a_window_that_fits(hwnd):
    area = primary_work_area()
    move_window(hwnd, area[0] + 50, area[1] + 50)
    before = win32gui.GetWindowRect(hwnd)
    force_client_size(hwnd, 640, 360, 10)
    assert win32gui.GetWindowRect(hwnd)[:2] == before[:2]


def test_force_size_brings_an_offscreen_client_onto_a_monitor(hwnd):
    move_window(hwnd, -20000, -20000)
    force_client_size(hwnd, 640, 360, 10)
    assert monitor_shift(hwnd) == (0, 0)


LEFT_MONITOR = (-1920, 0, 0, 1040)
RIGHT_MONITOR = (0, 0, 1920, 1040)


@pytest.mark.parametrize(
    ("client", "area", "expected"),
    [
        (ScreenRect(100, 100, 1280, 720), RIGHT_MONITOR, (0, 0)),
        (ScreenRect(-1926, 100, 1280, 720), LEFT_MONITOR, (6, 0)),
        (ScreenRect(1000, 100, 1280, 720), RIGHT_MONITOR, (-360, 0)),
        (ScreenRect(100, -5, 1280, 720), RIGHT_MONITOR, (0, 5)),
        (ScreenRect(100, 500, 1280, 720), RIGHT_MONITOR, (0, -180)),
    ],
)
def test_shift_into_area(client, area, expected):
    assert shift_into_area(client, area) == expected


def test_force_size_fails_loud_when_the_size_does_not_stick(hwnd, monkeypatch):
    monkeypatch.setattr("core.window.win32gui.SetWindowPos", lambda *args: None)
    with pytest.raises(WindowResizeError, match="after 3 attempts"):
        force_client_size(hwnd, 640, 360, 3)


def test_access_denied_explains_the_elevation_problem(hwnd, monkeypatch):
    def refuse(*args):
        raise pywintypes.error(5, "SetWindowPos", "Access is denied.")

    monkeypatch.setattr("core.window.win32gui.SetWindowPos", refuse)
    with pytest.raises(WindowResizeError, match="administrator terminal"):
        force_client_size(hwnd, 640, 360, 3)


def test_verify_is_true_when_size_is_right(game):
    game.apply_size()
    assert game.verify() is True


def test_verify_restores_size_after_drift(game, hwnd, caplog):
    game.apply_size()
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 500, 400, win32con.SWP_NOMOVE | win32con.SWP_NOZORDER)
    with caplog.at_level(logging.WARNING):
        assert game.verify() is False
    assert "drifted" in caplog.text
    assert client_size(hwnd) == (640, 360)
    assert game.verify() is True


def test_verify_brings_back_a_client_moved_offscreen(game, hwnd, caplog):
    game.apply_size()
    assert game.verify() is True
    move_window(hwnd, -20000, -20000)
    with caplog.at_level(logging.WARNING):
        assert game.verify() is False
    assert "drifted" in caplog.text
    assert game.verify() is True


def test_client_rect_follows_the_window_and_handles_negative_x(game, hwnd):
    game.apply_size()
    win32gui.SetWindowPos(hwnd, 0, -700, 50, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)
    first = game.client_rect_on_screen()
    assert first.left < 0
    assert (first.width, first.height) == (640, 360)
    win32gui.SetWindowPos(hwnd, 0, 200, 90, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)
    second = game.client_rect_on_screen()
    assert (second.left - first.left, second.top - first.top) == (900, 40)


def test_closed_window_raises_window_lost(game, hwnd):
    win32gui.DestroyWindow(hwnd)
    with pytest.raises(WindowLostError):
        game.verify()
    with pytest.raises(WindowLostError):
        game.client_rect_on_screen()
    with pytest.raises(WindowLostError):
        game.ensure_foreground()

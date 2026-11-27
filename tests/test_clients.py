from __future__ import annotations

import win32con

from core.clients import CLIENTS, STEAM, STOVE, state_path, window_config_for
from core.settings import Settings
from core.config import WindowConfig
from core.stop import StopWatcher
from core.window import WindowInfo, select_game_window
from main import parse_args


def base_config() -> WindowConfig:
    return WindowConfig(
        process_name="EpicSeven.exe",
        class_names=["GLFW30"],
        titles=["Epic Seven"],
        excluded_process_names=["STOVE.exe", "EpicSeven_Steam.exe"],
        client_width=1280,
        client_height=720,
    )


def window(hwnd: int, process: str) -> WindowInfo:
    return WindowInfo(hwnd, hwnd * 10, process, "GLFW30", "Epic Seven", 1280, 720)


STOVE_WINDOW = window(1, "EpicSeven.exe")
STEAM_WINDOW = window(2, "EpicSeven_Steam.exe")


def test_stove_bot_picks_stove_with_both_clients_open():
    config = window_config_for(base_config(), CLIENTS[STOVE])
    assert select_game_window([STEAM_WINDOW, STOVE_WINDOW], config) == STOVE_WINDOW


def test_steam_bot_picks_steam_with_both_clients_open():
    config = window_config_for(base_config(), CLIENTS[STEAM])
    assert select_game_window([STOVE_WINDOW, STEAM_WINDOW], config) == STEAM_WINDOW


def test_steam_bot_never_falls_back_to_stove_by_title():
    config = window_config_for(base_config(), CLIENTS[STEAM])
    assert select_game_window([STOVE_WINDOW], config) is None


def test_stove_bot_never_falls_back_to_steam_by_title():
    config = window_config_for(base_config(), CLIENTS[STOVE])
    assert select_game_window([STEAM_WINDOW], config) is None


def test_steam_config_keeps_the_launcher_excluded():
    config = window_config_for(base_config(), CLIENTS[STEAM])
    assert "STOVE.exe" in config.excluded_process_names
    assert "EpicSeven_Steam.exe" not in config.excluded_process_names


def test_each_client_keeps_its_own_state(tmp_path):
    # The state.json of the versions with one client per folder goes to the first client chosen.
    (tmp_path / "state.json").write_text('{"total_rerolls": 7}', encoding="utf-8")
    steam = state_path(STEAM, tmp_path)
    assert steam == tmp_path / "state_steam.json" and steam.exists() and not (tmp_path / "state.json").exists()
    stove = state_path(STOVE, tmp_path)
    assert stove == tmp_path / "state_stove.json" and not stove.exists()


def test_no_client_is_chosen_by_default():
    assert Settings().client is None


def test_watcher_registers_the_key_it_is_given():
    # F7 and F8: the real stop keys may be held by a bot running on this machine.
    first = StopWatcher(win32con.VK_F7, "F7")
    second = StopWatcher(win32con.VK_F8, "F8")
    first.start()
    try:
        second.start()
        second.close()
    finally:
        first.close()
    assert (first.key, first.key_name) == (win32con.VK_F7, "F7")


def test_the_client_can_be_given_on_the_command_line():
    assert parse_args([]).client is None
    assert parse_args(["--client", "steam"]).client == STEAM

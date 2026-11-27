"""Config schema: the shipped file loads, and bad values fail loud."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from core.config import AppConfig, ConfigError, State, load_config, transition_problems

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


@pytest.fixture
def raw() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def write_and_load(tmp_path: Path, data: dict) -> AppConfig:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_config(path)


def test_shipped_config_is_valid():
    config = load_config(CONFIG_PATH)
    assert config.window.process_name == "EpicSeven.exe"
    assert config.vision.default_threshold == 0.86
    assert config.input.jitter_px == 3


def test_shipped_config_warns_about_what_the_user_must_fill():
    warnings = " ".join(load_config(CONFIG_PATH).startup_warnings())
    assert "route" not in warnings
    assert "summon" not in warnings


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")


def test_broken_yaml_raises_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("window: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_unknown_key_is_rejected(tmp_path, raw):
    raw["window"]["proces_name"] = "typo.exe"
    with pytest.raises(ConfigError, match="window.proces_name"):
        write_and_load(tmp_path, raw)


def test_threshold_out_of_range_is_rejected(tmp_path, raw):
    raw["vision"]["template_thresholds"] = {"confirm": 1.5}
    with pytest.raises(ConfigError, match="confirm"):
        write_and_load(tmp_path, raw)


def test_inverted_range_is_rejected(tmp_path, raw):
    raw["input"]["click_hold_ms"] = {"min": 120, "max": 40}
    with pytest.raises(ConfigError, match="greater than max"):
        write_and_load(tmp_path, raw)


def test_missing_state_timeout_is_rejected(tmp_path, raw):
    del raw["watchdog"]["state_timeouts_seconds"]["LOGIN"]
    with pytest.raises(ConfigError, match="LOGIN"):
        write_and_load(tmp_path, raw)


def test_unknown_transition_target_is_rejected(tmp_path, raw):
    raw["transitions"]["BOOT"].append("NOT_A_STATE")
    with pytest.raises(ConfigError, match="transitions"):
        write_and_load(tmp_path, raw)


def parsed_transitions(raw: dict) -> dict[State, list[State]]:
    return {State(key): [State(t) for t in targets] for key, targets in deepcopy(raw["transitions"]).items()}


def test_shipped_transitions_have_no_problem(raw):
    assert transition_problems(parsed_transitions(raw)) == []


def test_state_without_recover_is_a_problem(raw):
    table = parsed_transitions(raw)
    table[State.LOGIN].remove(State.RECOVER)
    assert any("LOGIN cannot reach RECOVER" in p for p in transition_problems(table))


def test_missing_entry_is_a_problem(raw):
    table = parsed_transitions(raw)
    del table[State.LOGIN]
    assert any("no entry for LOGIN" in p for p in transition_problems(table))


def test_halt_with_exit_is_a_problem(raw):
    table = parsed_transitions(raw)
    table[State.HALT] = [State.BOOT]
    assert any("HALT" in p for p in transition_problems(table))


def test_unreachable_state_is_a_problem(raw):
    table = parsed_transitions(raw)
    for state, targets in table.items():
        table[state] = [t for t in targets if t is not State.SUMMON]
    assert any("SUMMON" in p for p in transition_problems(table))


def test_enabled_reset_needs_an_allowed_server(tmp_path, raw):
    raw["reset"]["enabled"] = True
    raw["reset"]["allowed_servers"] = []
    with pytest.raises(ConfigError, match="allowed_servers"):
        write_and_load(tmp_path, raw)


def test_a_headless_run_uses_the_settings_saved_from_the_window(tmp_path, monkeypatch):
    import argparse
    import logging

    import main
    from core.settings import Settings, save_settings

    path = tmp_path / "settings.json"
    save_settings(Settings(client="steam", gear_efficiency=72, gear_roles=["damage"]), path)
    monkeypatch.setattr(main, "SETTINGS_PATH", path)
    seen = []
    monkeypatch.setattr(main, "run_bot", lambda config, *args, **kwargs: seen.append(config) or "done")
    args = argparse.Namespace(goal="gear_sort", start="continue")
    main.run_headless(load_config(Path(__file__).parent.parent / "config.yaml"), args, logging.getLogger("test"), "steam")
    assert seen[0].gear.efficiency == 72 and seen[0].gear.roles == ["damage"]

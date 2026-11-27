"""Logger: file and console levels, password redaction, hashes and screenshots."""

import logging

import cv2
import numpy as np
import pytest

from core import logger as logger_module
from core.config import LoggingConfig
from core.logger import (
    frame_hash,
    log_state_event,
    redact,
    register_secret,
    save_failure_screenshot,
    save_state_screenshot,
    setup_logging,
    shutdown_logging,
)


@pytest.fixture
def config(tmp_path) -> LoggingConfig:
    return LoggingConfig(log_dir=tmp_path / "logs", max_bytes=2000, backup_count=2)


@pytest.fixture
def bot_logger(config):
    logger_module._secrets.clear()
    yield setup_logging(config)
    shutdown_logging()
    logger_module._secrets.clear()


def read_log(config: LoggingConfig) -> str:
    for handler in logging.getLogger(logger_module.LOGGER_NAME).handlers:
        handler.flush()
    return (config.log_dir / "bot.log").read_text(encoding="utf-8")


def test_debug_goes_to_file_only(bot_logger, config, capsys):
    bot_logger.debug("debug line")
    bot_logger.info("info line")
    log_text = read_log(config)
    console = capsys.readouterr().err
    assert "debug line" in log_text and "info line" in log_text
    # The console handler holds the stderr captured at setup, so check its level.
    console_handler = next(h for h in bot_logger.handlers if type(h) is logging.StreamHandler)
    assert console_handler.level == logging.INFO
    assert "debug line" not in console


def test_setup_twice_does_not_duplicate_lines(config, bot_logger):
    logger = setup_logging(config)
    logger.info("only once")
    assert read_log(config).count("only once") == 1


def test_log_file_rotates(bot_logger, config):
    for index in range(100):
        bot_logger.info("filler line %d with enough text to reach the limit", index)
    assert (config.log_dir / "bot.log.1").exists()
    assert not (config.log_dir / "bot.log.3").exists()


def test_registered_secret_is_masked_in_message(bot_logger, config):
    register_secret("hunter2-secret")
    bot_logger.info("typed %s into the field", "hunter2-secret")
    log_text = read_log(config)
    assert "hunter2-secret" not in log_text
    assert "typed *** into the field" in log_text


def test_registered_secret_is_masked_in_traceback(bot_logger, config):
    register_secret("hunter2-secret")
    try:
        raise ValueError("login failed with hunter2-secret")
    except ValueError:
        bot_logger.exception("failure")
    log_text = read_log(config)
    assert "hunter2-secret" not in log_text
    assert "login failed with ***" in log_text


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("password=abc123", "abc123"),
        ("PASSWORD: abc123", "abc123"),
        ("{'email': 'a@b.c', 'password': 'abc123'}", "abc123"),
        ('{"pwd": "abc123"}', "abc123"),
    ],
)
def test_password_looking_values_are_masked(text, leaked):
    assert leaked not in redact(text)


def test_redact_keeps_normal_text():
    assert redact("state enter LOGIN frame=abcd") == "state enter LOGIN frame=abcd"


def test_frame_hash_is_stable_and_detects_changes():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    changed = frame.copy()
    changed[0, 0, 0] = 1
    assert frame_hash(frame) == frame_hash(frame.copy())
    assert frame_hash(frame) != frame_hash(changed)


def test_frame_hash_depends_on_shape():
    assert frame_hash(np.zeros((2, 8), dtype=np.uint8)) != frame_hash(np.zeros((4, 4), dtype=np.uint8))


def test_state_event_logs_frame_hash(bot_logger, config):
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    log_state_event(bot_logger, "enter", "LOGIN", frame)
    log_state_event(bot_logger, "exit", "LOGIN", None)
    log_text = read_log(config)
    assert f"state enter LOGIN frame={frame_hash(frame)}" in log_text
    assert "state exit LOGIN frame=no-frame" in log_text


def test_failure_screenshot_round_trip(config):
    frame = np.full((20, 30, 3), 127, dtype=np.uint8)
    path = save_failure_screenshot(frame, config, "watchdog: LOGIN/expired")
    assert path.parent == config.log_dir / "failures"
    assert path.name.endswith("_watchdog_LOGIN_expired.png")
    assert np.array_equal(cv2.imread(str(path)), frame)


def test_state_screenshot_is_opt_in(config):
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    assert save_state_screenshot(frame, config, "LOGIN") is None
    enabled = config.model_copy(update={"state_screenshots": True})
    path = save_state_screenshot(frame, enabled, "LOGIN")
    assert path is not None and path.parent == config.log_dir / "states" / "LOGIN"

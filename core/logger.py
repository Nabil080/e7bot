"""Rotating file + console logging, password redaction, and frame screenshots."""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import cv2
import numpy as np

from core.config import LoggingConfig

LOGGER_NAME = "e7bot"
LOG_FILE_NAME = "bot.log"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
REDACTED = "***"

_PASSWORD_PATTERN = re.compile(r"(?i)(password|passwd|pwd)(['\"]?\s*[:=]\s*)(['\"]?)[^\s,'\"}]+")
_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Remember a value so that it is masked in every log line, tracebacks included."""
    if value:
        _secrets.add(value)


def redact(text: str) -> str:
    """Mask registered secrets and anything that looks like `password=value`."""
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return _PASSWORD_PATTERN.sub(rf"\1\2\3{REDACTED}", text)


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the final output, so tracebacks are covered too."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record, then mask secrets in the result."""
        return redact(super().format(record))


def setup_logging(config: LoggingConfig) -> logging.Logger:
    """Configure the bot logger. Safe to call twice: old handlers are replaced."""
    config.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    shutdown_logging()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = RedactingFormatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        config.log_dir / LOG_FILE_NAME,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(config.file_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Started without a console (pythonw), there is nowhere to print: the log file is all.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(config.console_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger


def shutdown_logging() -> None:
    """Close and remove the bot logger's handlers, releasing bot.log."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def frame_hash(frame: np.ndarray) -> str:
    """Return a short hash of a frame, to spot identical frames in the logs."""
    digest = hashlib.blake2b(digest_size=8)
    digest.update(str(frame.shape).encode())
    digest.update(frame.tobytes())
    return digest.hexdigest()


def log_state_event(logger: logging.Logger, event: str, state: str, frame: np.ndarray | None) -> None:
    """Log a state entry or exit along with the current frame hash."""
    hash_text = frame_hash(frame) if frame is not None else "no-frame"
    logger.info("state %s %s frame=%s", event, state, hash_text)


def save_screenshot(frame: np.ndarray, directory: Path, label: str) -> Path:
    """Write a frame as PNG under a timestamped name and return its path.

    Uses imencode rather than imwrite: imwrite fails silently on non-ASCII
    Windows paths.
    """
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_") or "frame"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}_{safe_label}.png"
    encoded, buffer = cv2.imencode(".png", frame)
    if not encoded:
        raise ValueError(f"could not encode frame with shape {frame.shape} as PNG")
    path.write_bytes(buffer.tobytes())
    return path


def save_failure_screenshot(frame: np.ndarray, config: LoggingConfig, reason: str) -> Path:
    """Save a frame into logs/failures/ after a failure or an unexpected transition."""
    return save_screenshot(frame, config.log_dir / "failures", reason)


def save_state_screenshot(frame: np.ndarray, config: LoggingConfig, state: str) -> Path | None:
    """Save a frame into logs/states/<state>/ if state screenshots are enabled."""
    if not config.state_screenshots:
        return None
    return save_screenshot(frame, config.log_dir / "states" / state, state)

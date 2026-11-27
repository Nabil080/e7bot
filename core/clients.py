"""The two official PC clients the bot can drive, what tells them apart, and where each keeps its state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import WindowConfig


@dataclass(frozen=True)
class Client:
    """One official client: its name and its window's process."""

    label: str
    process_name: str
    # The game runs as administrator: Windows lets only an administrator program send it clicks and keys.
    needs_admin: bool = False


STOVE = "stove"
STEAM = "steam"
CLIENTS = {
    STOVE: Client("STOVE", "EpicSeven.exe", needs_admin=True),
    STEAM: Client("Steam", "EpicSeven_Steam.exe"),
}
# The state file of the versions that drove one client per folder. It goes to the first client chosen.
LEGACY_STATE_PATH = Path("state.json")


def window_config_for(config: WindowConfig, client: Client) -> WindowConfig:
    """Return the window config aimed at one client, with every other client's exe excluded.

    Both builds share the window title and class, so the title fallback must never reach the other one.
    """
    others = [c.process_name for c in CLIENTS.values() if c.process_name != client.process_name]
    excluded = [name for name in config.excluded_process_names if name.casefold() != client.process_name.casefold()]
    excluded += [name for name in others if name not in excluded]
    return config.model_copy(update={"process_name": client.process_name, "excluded_process_names": excluded})


def state_path(client: str, folder: Path = Path(".")) -> Path:
    """Return the state file of one client: its accounts, route and stats never mix with the other's.

    An older state.json, from when a folder drove a single client, becomes the file of the first client chosen.
    """
    path = folder / f"state_{client}.json"
    legacy = folder / LEGACY_STATE_PATH
    if not path.exists() and legacy.exists():
        try:
            legacy.rename(path)
        except OSError:
            # Left in place: the stats start over rather than stopping the bot.
            pass
    return path

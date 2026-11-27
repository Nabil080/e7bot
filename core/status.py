"""BotStatus: what the bot thread shows to the window, safe to read from another thread."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

from core.stats import CardLike, Stats

MAX_LOG_LINES = 5000
LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (?:INFO|WARNING|ERROR|CRITICAL)\s+\S+: (.*)$")


@dataclass(frozen=True)
class StatusSnapshot:
    """A consistent copy of the status at one moment."""

    running: bool
    state: str
    stage: str
    last_result: str
    session_rerolls: int
    total_rerolls: int
    elapsed_seconds: float
    pulls_read: int = 0
    pulls_expected: int = 0
    result_image: str = ""
    stats: Stats = field(default_factory=Stats)
    # Wall clock time the current account began at, known only for an account started in this run.
    account_started_at: float | None = None


class BotStatus:
    """Shared between the bot thread, which writes, and the window, which reads."""

    def __init__(self, total_rerolls: int = 0, stats: Stats | None = None) -> None:
        """Start idle with the lasting counts."""
        self._stats = stats or Stats()
        self._lock = threading.Lock()
        self._running = False
        self._state = "-"
        self._stage = ""
        self._last_result = ""
        self._session_rerolls = 0
        self._total_rerolls = total_rerolls
        self._started_at: float | None = None
        self._stopped_elapsed = 0.0
        self._pulls_read = 0
        self._pulls_expected = 0
        self._result_image = ""
        self._account_started_at: float | None = None

    def reload(self, total_rerolls: int, stats: Stats) -> None:
        """Replace the lasting counts, when the window switches to the other client's."""
        with self._lock:
            self._total_rerolls = total_rerolls
            self._stats = stats

    def started(self) -> None:
        with self._lock:
            self._running = True
            self._state = "BOOT"
            self._stage = ""
            self._last_result = ""
            self._session_rerolls = 0
            self._pulls_read = 0
            self._pulls_expected = 0
            self._result_image = ""
            self._account_started_at = None
            self._started_at = time.monotonic()

    def finished(self, result: str) -> None:
        """Mark the end of a run and why it ended."""
        with self._lock:
            self._running = False
            self._last_result = result
            if self._started_at is not None:
                self._stopped_elapsed = time.monotonic() - self._started_at
            self._started_at = None

    def set_state(self, state: str) -> None:
        with self._lock:
            self._state = state

    def set_stage(self, stage: str) -> None:
        """Record the stage selected in the story."""
        with self._lock:
            self._stage = stage

    def set_result(self, read: int, expected: int, image: str) -> None:
        """Record how many summon results were read, and the picture of the last one."""
        with self._lock:
            self._pulls_read = read
            self._pulls_expected = expected
            self._result_image = image

    def add_cards(self, cards: list[CardLike]) -> Stats:
        """Count the results of one pull and return a copy of the statistics."""
        with self._lock:
            self._stats.add_cards(cards)
            return replace(self._stats)

    def add_run(self, seconds: float) -> Stats:
        """Count one finished account and return a copy of the statistics."""
        with self._lock:
            self._stats.add_run(seconds)
            return replace(self._stats)

    def add_story(self, won: int = 0, lost: int = 0, seconds: float = 0.0, revives: int = 0) -> Stats:
        """Count story stages won, fights lost, skystone revives and time spent pushing the story, and return a copy of the statistics."""
        with self._lock:
            self._stats.story_stages_won += won
            self._stats.story_fights_lost += lost
            self._stats.story_revives += revives
            self._stats.story_seconds += seconds
            return replace(self._stats)

    def add_climb(self, won: int = 0, lost: int = 0, seconds: float = 0.0) -> Stats:
        """Count Abyss floors won, fights lost and time spent climbing a kept account, and return a copy of the statistics."""
        with self._lock:
            self._stats.abyss_floors_won += won
            self._stats.abyss_fights_lost += lost
            self._stats.abyss_seconds += seconds
            return replace(self._stats)

    def add_theater(self, acts: int = 0, tickets: int = 0, seconds: float = 0.0) -> Stats:
        """Count Moonlight Theater acts watched, tickets bought and time spent, and return a copy of the statistics."""
        with self._lock:
            self._stats.theater_acts += acts
            self._stats.theater_tickets += tickets
            self._stats.theater_seconds += seconds
            return replace(self._stats)

    def add_arena(self, won: bool | None = None, quick: bool = False, seconds: float = 0.0) -> Stats:
        """Count one NPC Challenge fight when won is given, or time spent, and return a copy of the statistics."""
        with self._lock:
            if won is not None:
                self._stats.arena_fights += 1
                self._stats.arena_wins += won
                self._stats.arena_quick += quick
            self._stats.arena_seconds += seconds
            return replace(self._stats)

    def add_gear(self, event: str, amount: int = 1) -> Stats:
        """Count a gear event (judged, enhanced, sold, extracted, locked, crafted, gold), and return a copy of the statistics."""
        with self._lock:
            name = f"gear_{event}"
            if hasattr(self._stats, name):
                setattr(self._stats, name, getattr(self._stats, name) + amount)
            return replace(self._stats)

    def add_shop(self, refreshes: int = 0, bookmarks: int = 0, medals: int = 0, gold: int = 0, seconds: float = 0.0) -> Stats:
        """Count Secret Shop refreshes, bookmarks and medals bought, gold and time spent, and return a copy of the statistics."""
        with self._lock:
            self._stats.shop_refreshes += refreshes
            self._stats.shop_bookmarks += bookmarks
            self._stats.shop_medals += medals
            self._stats.shop_gold += gold
            self._stats.shop_seconds += seconds
            return replace(self._stats)

    def add_penguins(self, stigma: int) -> Stats:
        """Count one purchase of 50 penguins and the stigma it cost, and return a copy of the statistics."""
        with self._lock:
            self._stats.penguin_purchases += 1
            self._stats.penguin_stigma += stigma
            return replace(self._stats)

    def add_penguin_kinds(self, kinds: dict[str, int]) -> Stats:
        """Count the penguins of each kind a purchase gave, and return a copy of the statistics."""
        with self._lock:
            self._stats.penguin_newbie += kinds.get("newbie", 0)
            self._stats.penguin_experienced += kinds.get("experienced", 0)
            self._stats.penguin_epic += kinds.get("epic", 0)
            return replace(self._stats)

    def add_huche(self, kind: str, gold: int) -> Stats:
        """Count one charm bought from Huche, of its kind, and the gold it cost, and return a copy of the statistics."""
        with self._lock:
            self._stats.huche_purchases += 1
            self._stats.huche_gold += gold
            name = f"huche_{kind}"
            if hasattr(self._stats, name):
                setattr(self._stats, name, getattr(self._stats, name) + 1)
            return replace(self._stats)

    def add_arena_skystones(self, skystones: int) -> Stats:
        """Count skystones spent on arena flags, and return a copy of the statistics."""
        with self._lock:
            self._stats.arena_skystones += skystones
            return replace(self._stats)

    def reset_stats(self) -> Stats:
        """Forget every statistic and the reroll count, and return the empty statistics."""
        with self._lock:
            self._stats = Stats()
            self._total_rerolls = 0
            return replace(self._stats)

    def add_leaf(self) -> Stats:
        """Count one leaf spent on energy, and return a copy of the statistics."""
        with self._lock:
            self._stats.leaves_used += 1
            return replace(self._stats)

    def add_reroll(self) -> int:
        """Count one reroll, which starts a new account, and return the new total."""
        with self._lock:
            self._stage = ""
            self._pulls_read = 0
            self._pulls_expected = 0
            self._result_image = ""
            self._account_started_at = time.time()
            self._session_rerolls += 1
            self._total_rerolls += 1
            return self._total_rerolls

    def snapshot(self) -> StatusSnapshot:
        with self._lock:
            elapsed = self._stopped_elapsed
            if self._started_at is not None:
                elapsed = time.monotonic() - self._started_at
            return StatusSnapshot(
                self._running,
                self._state,
                self._stage,
                self._last_result,
                self._session_rerolls,
                self._total_rerolls,
                elapsed,
                self._pulls_read,
                self._pulls_expected,
                self._result_image,
                replace(self._stats),
                self._account_started_at,
            )


class LogFeed(logging.Handler):
    """Keeps the latest log lines for the window. Thread safe, as logging handlers are."""

    def __init__(self) -> None:
        """Start with an empty feed."""
        super().__init__(level=logging.INFO)
        self.lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.added = 0

    def load_file(self, path: Path) -> None:
        """Start with what the log file already holds, so that the journal does not begin at this launch."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            found = LOG_LINE.match(line)
            if found:
                self.lines.append(f"{found.group(1)} {found.group(2)}")
                self.added += 1

    def emit(self, record: logging.LogRecord) -> None:
        """Store a formatted line. The bot logger's formatter hides secrets on the way."""
        self.lines.append(self.format(record))
        self.added += 1

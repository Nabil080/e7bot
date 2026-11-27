"""The 5-star Moonlight heroes, from a local file, and matching their names as read by OCR."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

HEROES_PATH = Path("data/moonlight_heroes.json")
# A ranking of the 5-star Moonlight heroes, best first. A hero missing from it, or not
# recognized, ranks 0, above them all: it may be new and worth a look.
UNRANKED = 0
# A chest shows a short name that two Moonlight heroes share: their element, as the user gave it, tells them apart.
SHARED_NAME_ELEMENTS = {
    "Monarch of the Sword Iseria": "light",
    "Briar Witch Iseria": "dark",
    "Witch of the Mere Tenebria": "light",
    "Specter Tenebria": "dark",
}
RANKING_PATH = Path("data/ml5_ranking.json")
# High enough that two heroes sharing a word stay apart, as Dark Corvus and Corvus would.
MATCH_CUTOFF = 0.8


def load_moonlight_heroes(path: Path = HEROES_PATH) -> list[str]:
    """Return the sorted hero names, empty if the file is missing or broken."""
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted({name for name in names if isinstance(name, str)}) if isinstance(names, list) else []


def identify_hero(reading: str, names: list[str]) -> str | None:
    """Return the hero an OCR reading is, or None if it cannot be told for sure.

    A reading can have artwork noise after the name, and a few wrong letters.
    """
    words = reading.split()
    candidates = [" ".join(words[:count]) for count in range(len(words), 0, -1)]
    best: tuple[float, str] | None = None
    for candidate in candidates:
        for name in names:
            score = difflib.SequenceMatcher(None, candidate.casefold(), name.casefold()).ratio()
            if score >= MATCH_CUTOFF and (best is None or score > best[0]):
                best = (score, name)
    if best:
        return best[1]
    return _unique_prefix(reading, names)


# A name cut by the edge of what is read: its start must be this long and fit one hero only.
MIN_PREFIX_LETTERS = 6


def _unique_prefix(reading: str, names: list[str]) -> str | None:
    """Return the only hero whose name starts with the reading, once the noise at its end is cut."""
    clean = ""
    for char in reading:
        if not (char.isalpha() or char == " "):
            break
        clean += char
    clean = " ".join(clean.split())
    # The last word may itself be cut: keep it as a start of word.
    if sum(char.isalpha() for char in clean) < MIN_PREFIX_LETTERS:
        return None
    matches = [name for name in names if name.casefold().startswith(clean.casefold())]
    return matches[0] if len(matches) == 1 else None


def load_ranking(path: Path = RANKING_PATH) -> list[str]:
    """Return the ranked hero names, best first, empty if the file is missing or broken."""
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else []


def rank_of(name: str, ranking: list[str]) -> int | None:
    """Return a hero's place in the ranking, 1 for the best, or None if it is not ranked."""
    return ranking.index(name) + 1 if name in ranking else None


def chest_rank(short_name: str, ranking: list[str], element: str | None = None) -> int | None:
    """Return the place of the ranked hero a chest's short name stands for, such as Charles for Closer Charles.

    A chest only shows the hero's own name. Two Moonlight heroes can share it (Tenebria, Iseria): the element
    read on the card ("light" or "dark") tells them apart. Without it the worse one counts, so that a hero is
    never taken for better than it may be.
    """
    wanted = " ".join(short_name.split()).casefold()
    if not wanted:
        return None
    places = [
        index + 1 for index, name in enumerate(ranking)
        if (name.casefold() == wanted or name.casefold().endswith(" " + wanted))
        and (element is None or SHARED_NAME_ELEMENTS.get(name, element) == element)
    ]
    return max(places) if places else None


def ranked_heroes(names: list[str], ranking: list[str]) -> list[str]:
    """Return the heroes in the order of the ranking, those it does not hold after, in alphabetical order."""
    known = set(names)
    return [name for name in ranking if name in known] + sorted(known - set(ranking))

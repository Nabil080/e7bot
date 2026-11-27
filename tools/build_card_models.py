"""Build the small-card reader's templates from cards whose detail panel was read (scratch collections of
card_*.png + truth.json), and write them to templates/cards/models.npz.

Usage: python tools/build_card_models.py DIR [DIR...]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cards import GLYPH_MAX_HEIGHT, GLYPH_MIN_HEIGHT, LINE_CENTERS, MODELS_PATH, STAT_ICON, art_features, badge_patch, glyph_features, grade_features, icon_patch, main_features, value_groups, value_region


def unit(vectors: list[np.ndarray]) -> np.ndarray:
    """Return the normalised mean of some vectors."""
    mean = np.mean(vectors, axis=0)
    mean = mean - mean.mean()
    return mean / (np.linalg.norm(mean) or 1)


def main() -> None:
    stats: dict[str, list[np.ndarray]] = defaultdict(list)
    sets: dict[str, list[np.ndarray]] = defaultdict(list)
    grades: dict[str, list[np.ndarray]] = defaultdict(list)
    digits: dict[str, list[np.ndarray]] = defaultdict(list)
    art_vectors: list[np.ndarray] = []
    art_slots: list[str] = []
    main_vectors: list[np.ndarray] = []
    main_keys: list[str] = []
    for folder in map(Path, sys.argv[1:]):
        for row in json.loads((folder / "truth.json").read_text(encoding="utf-8")):
            truth = row["truth"]
            if truth is None:
                continue
            card = cv2.imread(str(folder / row["image"]))
            gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
            names = [truth["main"]] + [name for name, _ in truth["subs"]]
            for line, name in enumerate(names):
                stats[STAT_ICON[name]].append(icon_patch(gray, LINE_CENTERS[line]))
            # The substats' values, as the panel wrote them, label the characters of the card's values.
            for line, (_, value) in enumerate(truth["subs"], start=1):
                text = str(int(value))
                region_gray, _, groups = value_groups(value_region(card, LINE_CENTERS[line]))
                tall = [g for g in groups if GLYPH_MIN_HEIGHT <= g[3] - g[2] <= GLYPH_MAX_HEIGHT]
                if len(tall) >= len(text):
                    for (x0, x1, y0, y1), char in zip(tall, text):
                        digits[char].append(glyph_features(region_gray[y0:y1, x0:x1]))
            if truth["set"]:
                sets[truth["set"]].append(badge_patch(gray))
            grades[truth["grade"]].append(grade_features(card))
            art_vectors.append(art_features(card))
            if truth["level"] == 0:
                main_vectors.append(main_features(card, LINE_CENTERS[0]))
                main_keys.append(truth["main"])
            art_slots.append(truth["slot"])
    arrays = {f"stat_{name}": unit(values) for name, values in stats.items()}
    arrays.update({f"digit_{char}": unit(values) for char, values in digits.items()})
    arrays.update({f"set_{name}": unit(values) for name, values in sets.items()})
    arrays.update({f"grade_{name}": np.mean(values, axis=0) for name, values in grades.items()})
    arrays["art_vectors"] = np.array(art_vectors)
    arrays["art_slots"] = np.array(art_slots)
    arrays["main_vectors"] = np.array(main_vectors)
    arrays["main_keys"] = np.array(main_keys)
    MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(MODELS_PATH, **arrays)
    print(f"{MODELS_PATH}: {len(digits)} digits, {len(stats)} stat icons, {len(sets)} sets, {len(grades)} grades, {len(art_slots)} artworks")


if __name__ == "__main__":
    main()

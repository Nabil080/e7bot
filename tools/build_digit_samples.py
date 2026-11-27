"""Build the value reader's digit examples from cards whose detail panel was read (scratch collections of
card_*.png + truth.json), and write them to templates/cards/digits.npz. Each digit of a card is compared with every
example, which copes with the font's small rendering differences better than one mean template per digit.

Usage: python tools/build_digit_samples.py DIR [DIR...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cards import DIGIT_SAMPLES_PATH, GLYPH_MAX_HEIGHT, GLYPH_MIN_HEIGHT, LINE_CENTERS, glyph_features, value_groups, value_region


# Two examples this alike are the same glyph.
DUPLICATE = 0.99


def main() -> None:
    features, labels = [], []
    for folder in map(Path, sys.argv[1:]):
        for row in json.loads((folder / "truth.json").read_text(encoding="utf-8")):
            truth = row["truth"]
            if truth is None:
                continue
            card = cv2.imread(str(folder / row["image"]))
            # The substats' values, as the panel wrote them, label the characters of the card's values.
            for line, (_, value) in enumerate(truth["subs"], start=1):
                text = str(int(value))
                gray, _, groups = value_groups(value_region(card, LINE_CENTERS[line]))
                tall = [g for g in groups if GLYPH_MIN_HEIGHT <= g[3] - g[2] <= GLYPH_MAX_HEIGHT]
                if len(tall) >= len(text):
                    for (x0, x1, y0, y1), char in zip(tall, text):
                        features.append(glyph_features(gray[y0:y1, x0:x1]))
                        labels.append(char)
    features, labels = np.array(features, np.float32), np.array(labels)
    # Many examples are the same glyph drawn again: one kept per look (similarity under DUPLICATE) reads as well,
    # 27 times faster.
    kept = []
    for char in sorted(set(labels.tolist())):
        same: list[int] = []
        for index in np.nonzero(labels == char)[0]:
            if not same or float((features[same] @ features[index]).max()) < DUPLICATE:
                same.append(int(index))
        kept += same
    np.savez_compressed(ROOT / DIGIT_SAMPLES_PATH, features=features[kept].astype(np.float16), labels=labels[kept])
    print(f"{len(labels)} digits, {len(kept)} kept")


if __name__ == "__main__":
    main()

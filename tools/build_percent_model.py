"""Build the template of the "%" that follows a card's % substats, from cards whose detail panel was read
(scratch collections of card_*.png + truth.json), and write it to templates/cards/percent.npy.

Usage: python tools/build_percent_model.py DIR [DIR...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cards import (
    ALWAYS_PERCENT, GLYPH_MAX_HEIGHT, GLYPH_MIN_HEIGHT, LINE_CENTERS, PERCENT_MODEL_PATH, after_digits, value_groups, value_region,
)


def main() -> None:
    patches = []
    for folder in map(Path, sys.argv[1:]):
        for row in json.loads((folder / "truth.json").read_text(encoding="utf-8")):
            truth = row["truth"]
            if truth is None or truth["level"] != 0:
                continue
            card = cv2.imread(str(folder / row["image"]))
            for line, (name, value) in enumerate(truth["subs"], start=1):
                if not (name.endswith("percent") or name in ALWAYS_PERCENT):
                    continue
                gray, ink, groups = value_groups(value_region(card, LINE_CENTERS[line]))
                tall = [g for g in groups if GLYPH_MIN_HEIGHT <= g[3] - g[2] <= GLYPH_MAX_HEIGHT]
                digits = len(str(int(value)))
                if ink is None or len(tall) <= digits:
                    continue
                patch = after_digits(gray, ink, tall[digits - 1][1])
                if patch is not None:
                    patches.append(patch)
    mean = np.mean(patches, axis=0)
    mean = mean - mean.mean()
    np.save(ROOT / PERCENT_MODEL_PATH, mean / (np.linalg.norm(mean) or 1))
    print(f"{len(patches)} '%' signs")


if __name__ == "__main__":
    main()

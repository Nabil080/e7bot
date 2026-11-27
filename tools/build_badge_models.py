"""Build the +N badge reader's character templates from labelled badges, and write templates/cards/badges.npz.

The badges are templates/cards/badges/<level>_<n>.png, crops of the badge box of cards whose level was checked by eye.
Usage: python tools/build_badge_models.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cards import BADGE_MODELS_PATH, badge_glyphs


def main() -> None:
    glyphs: dict[str, list[np.ndarray]] = defaultdict(list)
    skipped = 0
    for image in sorted((ROOT / "templates" / "cards" / "badges").glob("*.png")):
        text = "+" + image.stem.split("_")[0]
        found = badge_glyphs(cv2.imread(str(image)))
        if len(found) != len(text):
            skipped += 1
            continue
        for char, glyph in zip(text, found):
            glyphs[char].append(glyph)
    arrays = {}
    for char, vectors in glyphs.items():
        mean = np.mean(vectors, axis=0)
        mean = mean - mean.mean()
        arrays[char] = mean / (np.linalg.norm(mean) or 1)
    np.savez_compressed(ROOT / BADGE_MODELS_PATH, **arrays)
    print(f"{len(arrays)} characters ({''.join(sorted(arrays))}), {skipped} badges skipped")


if __name__ == "__main__":
    main()

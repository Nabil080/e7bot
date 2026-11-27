"""Build the item level reader's digit templates (the yellow "85" on a card's icon) from labelled examples, and write
templates/cards/levels.npz.

The examples are templates/cards/levels/<level>_<n>.png: the top left corner of cards whose level was read by eye.
Usage: python tools/build_level_models.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cards import LEVEL_MODELS_PATH, level_glyphs


def main() -> None:
    glyphs: dict[str, list[np.ndarray]] = defaultdict(list)
    skipped = 0
    for image in sorted((ROOT / "templates" / "cards" / "levels").glob("*.png")):
        text = image.stem.split("_")[0]
        found = level_glyphs(cv2.imread(str(image)))
        if found is None:
            skipped += 1
            continue
        for position, (char, glyph) in enumerate(zip(text, found)):
            glyphs[f"{position}{char}"].append(glyph)
    arrays = {}
    for key, vectors in glyphs.items():
        mean = np.mean(vectors, axis=0)
        mean = mean - mean.mean()
        arrays[key] = mean / (np.linalg.norm(mean) or 1)
    np.savez_compressed(ROOT / LEVEL_MODELS_PATH, **arrays)
    print(f"{len(arrays)} digit templates ({', '.join(sorted(arrays))}), {skipped} examples skipped")


if __name__ == "__main__":
    main()

"""Build the slot reader's level 88/90 artwork examples, and write templates/cards/art.npz.

The examples are templates/cards/art/<slot>_<n>.png: the art box of cards whose slot is known (the Inventory tab
they were seen on, a sure reading, or by eye). Level 85 artworks are in models.npz already.
Usage: python tools/build_art_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cards import ART_SAMPLES_PATH, SLOTS_OF_MAIN, art_without_portrait

SLOTS = {slot for slots in SLOTS_OF_MAIN.values() for slot in slots}


def main() -> None:
    vectors: list[np.ndarray] = []
    slots: list[str] = []
    for image in sorted((ROOT / "templates" / "cards" / "art").glob("*.png")):
        slot = image.stem.split("_")[0]
        if slot not in SLOTS:
            raise SystemExit(f"{image.name}: unknown slot {slot!r}")
        vectors.append(art_without_portrait(cv2.imread(str(image))))
        slots.append(slot)
    if not vectors:
        raise SystemExit("no examples in templates/cards/art")
    np.savez_compressed(ROOT / ART_SAMPLES_PATH, vectors=np.array(vectors, dtype=np.float32), slots=np.array(slots))
    counts = {slot: slots.count(slot) for slot in sorted(set(slots))}
    print(f"{ART_SAMPLES_PATH}: {len(slots)} artworks {counts}")


if __name__ == "__main__":
    main()

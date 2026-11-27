"""Cut the game's stat icons from the gear screenshots of the tests into ui/icons/<stat>.png, for the advisor's window.

Each icon is taken from a card's main stat line, where the game draws it white. Usage: python tools/export_stat_icons.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.advisor import Advisor  # noqa: E402
from core.cards import ICON_HALF, ICON_X0, ICON_X1, LINE_CENTERS, CardModels  # noqa: E402
from core.config import load_config  # noqa: E402
from core.gear import GearPolicy  # noqa: E402
from core.vision import Ocr  # noqa: E402

SCREENS = ("grid_two_worn.png", "grid_otherworldly_steam.png", "arrange_one_ticked.png", "grid_a.png")
STATS = ("attack", "defense", "health", "speed", "crit_chance", "crit_damage", "effectiveness", "effect_resistance")
# Drawn bigger, and with a margin so that the icon is not cut.
SCALE = 2
MARGIN = 2


def main() -> None:
    models = CardModels.load(ROOT / "templates" / "cards" / "models.npz")
    advisor = Advisor(Ocr(load_config(ROOT / "config.yaml").vision), models, GearPolicy())
    found: dict[str, np.ndarray] = {}
    for name in SCREENS:
        frame = cv2.imread(str(ROOT / "tests" / "fixtures" / "gear" / name))
        for card in advisor.look(frame).cards:
            if not card.read or card.box is None:
                continue
            key = card.main_stat.removesuffix("_percent")
            if key in found:
                continue
            x, y = card.box[0], card.box[1]
            line = y + LINE_CENTERS[0]
            found[key] = frame[line - ICON_HALF - MARGIN : line + ICON_HALF + 1 + MARGIN, x + ICON_X0 - MARGIN : x + ICON_X1 + MARGIN]
    folder = ROOT / "ui" / "icons"
    folder.mkdir(exist_ok=True)
    for key, patch in found.items():
        big = cv2.resize(patch, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(str(folder / f"{key}.png"), big)
    missing = [key for key in STATS if key not in found]
    print(f"{len(found)} icons written to {folder}" + (f"; missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()

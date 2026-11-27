"""The Select Supporter screen some stages show between the stage list and the team."""

from pathlib import Path

import cv2

from core.config import load_config
from core.vision import Ocr
from states.story import SELECT_TEAM_POINT, SELECT_TEAM_REGION, SUPPORTER_TITLE_REGION

ROOT = Path(__file__).parent.parent


def test_the_select_supporter_screen_is_seen_and_its_button_found():
    ocr = Ocr(load_config(ROOT / "config.yaml").vision)
    frame = cv2.imread(str(ROOT / "tests" / "fixtures" / "story_select_supporter.png"))
    assert "supporter" in ocr.read(frame, SUPPORTER_TITLE_REGION).casefold()
    assert "select team" in ocr.read(frame, SELECT_TEAM_REGION).casefold()
    x, y, width, height = SELECT_TEAM_REGION
    assert x <= SELECT_TEAM_POINT[0] <= x + width and y <= SELECT_TEAM_POINT[1] <= y + height
    # The stage list's Ready screen and the team screen are not taken for it.
    for name in ("arena_npc.png",):
        other = cv2.imread(str(ROOT / "tests" / "fixtures" / name))
        assert "select team" not in ocr.read(other, SELECT_TEAM_REGION).casefold()


def test_the_team_screen_of_a_stage_played_before_shows_start():
    from states.story import START_POINT, START_TEXT_REGION, TEAM_CP_REGION
    ocr = Ocr(load_config(ROOT / "config.yaml").vision)
    frame = cv2.imread(str(ROOT / "tests" / "fixtures" / "story_team_start.png"))
    assert "team cp" in ocr.read(frame, TEAM_CP_REGION).casefold()
    assert ocr.read(frame, START_TEXT_REGION).strip().casefold() == "start"
    x, y, width, height = START_TEXT_REGION
    assert x <= START_POINT[0] <= x + width and y <= START_POINT[1] <= y + height

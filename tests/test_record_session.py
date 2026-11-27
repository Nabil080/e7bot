"""The recorder keeps clicks inside the game, skips still frames and logs usable events."""

import json

import cv2
import numpy as np

from core.coords import CoordMapper
from core.window import ScreenRect
from tools.record_session import Recording, click_in_game, worth_saving

MAPPER = CoordMapper(ScreenRect(-1920, 92, 1280, 720))


def test_click_is_converted_to_game_coordinates():
    assert click_in_game((-1280, 452), MAPPER) == (640, 360)


def test_click_outside_the_game_is_ignored():
    assert click_in_game((100, 100), MAPPER) is None
    assert click_in_game((-1280, 50), MAPPER) is None


def test_only_changed_frames_are_worth_saving():
    black = np.zeros((10, 10, 3), dtype=np.uint8)
    assert worth_saving(black, None)
    assert not worth_saving(black, black)
    assert worth_saving(np.full((10, 10, 3), 90, dtype=np.uint8), black)


def test_release_names_keep_the_duration(tmp_path):
    recording = Recording(tmp_path / "session")
    recording.save(np.full((36, 64, 3), 120, dtype=np.uint8), "release_2.4s")
    assert (tmp_path / "session" / "00001_release_2.4s.jpg").exists()


def test_recording_writes_frames_and_events(tmp_path):
    recording = Recording(tmp_path / "session")
    frame = np.full((36, 64, 3), 120, dtype=np.uint8)
    recording.save(frame, "auto")
    recording.save(frame, "click", (10, 20))
    lines = (tmp_path / "session" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["kind"] for event in events] == ["auto", "click"]
    assert events[1]["point"] == [10, 20]
    assert events[1]["file"] == "00002_click_10_20.jpg"
    assert cv2.imread(str(tmp_path / "session" / events[1]["file"])).shape == (36, 64, 3)

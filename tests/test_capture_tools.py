"""CoordMapper math, capture caching, the manifest, and the template and overlay helpers."""

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from core.capture import Capture, is_blank
from core.config import ConfigError, load_config
from core.coords import CoordMapper
from core.manifest import Manifest, TemplateEntry, load_manifest, save_manifest
from core.window import ScreenRect, WindowLostError
from tools.capture_template import clean_name, crop_frame, resolve_name, save_template
from tools.debug_overlay import OverlayBox, draw_overlay

ROOT = Path(__file__).parent.parent


def test_native_size_on_the_left_monitor():
    mapper = CoordMapper(ScreenRect(-1920, 92, 1280, 720))
    assert mapper.game_to_client(640, 360) == (640, 360)
    assert mapper.game_to_screen(640, 360) == (-1280, 452)
    assert mapper.screen_to_game(-1280, 452) == (640, 360)


def test_scaled_client():
    mapper = CoordMapper(ScreenRect(100, 50, 1920, 1080))
    assert mapper.game_to_client(640, 360) == (960, 540)
    assert mapper.game_to_screen(1279, 719) == (100 + 1918, 50 + 1078)
    assert mapper.client_to_game(960, 540) == (640, 360)


@pytest.mark.parametrize("x, y", [(0, 0), (1, 1), (333, 77), (1279, 719)])
@pytest.mark.parametrize("client", [ScreenRect(-1920, 92, 1280, 720), ScreenRect(5, 5, 1600, 900)])
def test_round_trip_stays_within_one_game_pixel(client, x, y):
    mapper = CoordMapper(client)
    back_x, back_y = mapper.screen_to_game(*mapper.game_to_screen(x, y))
    assert abs(back_x - x) <= 1 and abs(back_y - y) <= 1


def test_bounds_and_clamp():
    mapper = CoordMapper(ScreenRect(0, 0, 1280, 720))
    assert mapper.in_game_bounds(0, 0) and mapper.in_game_bounds(1279, 719)
    assert not mapper.in_game_bounds(1280, 0) and not mapper.in_game_bounds(0, -1)
    assert mapper.clamp(-5, 800) == (0, 719)


def test_minimized_client_is_rejected():
    with pytest.raises(ValueError, match="no area"):
        CoordMapper(ScreenRect(-32000, -32000, 0, 0))


class FakeWindow:
    """A window standing still on the top-left corner of the primary monitor."""

    def __init__(self) -> None:
        self.verify_count = 0
        self.lost = False

    def verify(self) -> bool:
        self.verify_count += 1
        if self.lost:
            raise WindowLostError("gone")
        return True

    def client_rect_on_screen(self) -> ScreenRect:
        return ScreenRect(0, 0, 64, 36)


def test_grab_returns_bgr_frame_of_the_client_size():
    with Capture(FakeWindow()) as capture:
        frame = capture.grab()
    assert frame.shape == (36, 64, 3)
    assert frame.dtype == np.uint8


def test_each_real_grab_verifies_the_window():
    window = FakeWindow()
    with Capture(window) as capture:
        capture.grab()
        capture.grab()
    assert window.verify_count == 2


def test_recent_frame_is_reused_without_a_grab():
    window = FakeWindow()
    with Capture(window) as capture:
        first = capture.grab()
        assert capture.grab(max_age_seconds=60) is first
    assert window.verify_count == 1


def test_lost_window_propagates():
    window = FakeWindow()
    window.lost = True
    with Capture(window) as capture, pytest.raises(WindowLostError):
        capture.grab()


def test_is_blank():
    assert is_blank(np.zeros((10, 10, 3), dtype=np.uint8))
    assert is_blank(np.full((10, 10, 3), 200, dtype=np.uint8))
    assert not is_blank(np.random.default_rng(0).integers(0, 255, (10, 10, 3), dtype=np.uint8))


def test_shipped_manifest_covers_the_popup_templates():
    config = load_config(ROOT / "config.yaml")
    names = load_manifest(ROOT / config.vision.manifest).names()
    assert set(config.popups.preempt_templates) <= set(names)
    assert "summon_results" in names


def test_manifest_missing_follows_the_files(tmp_path):
    manifest = Manifest(templates=[TemplateEntry(name="a"), TemplateEntry(name="b")])
    (tmp_path / "a.png").write_bytes(b"")
    assert manifest.missing(tmp_path) == ["b"]


def test_manifest_region_round_trip(tmp_path):
    path = tmp_path / "manifest.yaml"
    manifest = Manifest(templates=[TemplateEntry(name="a")])
    manifest.set_region("a", (1, 2, 3, 4))
    manifest.set_region("new", (5, 6, 7, 8))
    save_manifest(manifest, path)
    loaded = load_manifest(path)
    assert loaded.templates[0].region == (1, 2, 3, 4)
    assert loaded.names() == ["a", "new"]


def test_manifest_rejects_duplicates_and_unknown_states(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"templates": [{"name": "a"}, {"name": "a"}]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_manifest(path)
    path.write_text(yaml.safe_dump({"templates": [{"name": "a", "state": "NOPE"}]}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_manifest(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("confirm", "confirm"), (" Popup_Close.png ", "popup_close"), ("../evil", None), ("a b", None), ("", None)],
)
def test_clean_name(text, expected):
    assert clean_name(text) == expected


def test_resolve_name_accepts_a_number_or_a_name():
    assert resolve_name("2", ["a", "b"]) == "b"
    assert resolve_name("3", ["a", "b"]) is None
    assert resolve_name("0", ["a", "b"]) is None
    assert resolve_name("new_one", ["a", "b"]) == "new_one"


def test_crop_and_save_template(tmp_path):
    frame = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    crop = crop_frame(frame, (1, 2, 3, 2))
    assert crop.shape == (2, 3, 3)
    path = save_template(crop, "button", tmp_path)
    assert np.array_equal(cv2.imread(str(path)), crop)


def test_draw_overlay_scales_and_leaves_the_frame_untouched():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    image = draw_overlay(frame, 0.5, "status", [OverlayBox("confirm", 100, 100, 50, 20)], (10, 10))
    assert image.shape == (360, 640, 3)
    assert image.any()
    assert not frame.any()

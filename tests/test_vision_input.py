"""Template matching on synthetic frames, loading detection, OCR and input math."""

import logging
import random
import shutil
import threading

import cv2
import numpy as np
import pytest

from core.config import InputConfig, Range, VisionConfig
from core.input import InputController, InputError, jittered_point, normalize_to_virtual_desktop
from core.manifest import Manifest, TemplateEntry
from core.stop import BotStopped
from core.vision import (
    LoadingDetector,
    Ocr,
    OcrUnavailableError,
    TemplateLibrary,
    best_score,
    find_all,
    frame_difference,
    match_template,
    prepare_for_ocr,
    scale_values,
)
from core.window import ScreenRect

LOGGER = logging.getLogger("test_vision_input")
SCALES = scale_values(0.9, 1.1, 0.05)


def textured(height: int, width: int, seed: int = 1) -> np.ndarray:
    """Return a smooth random image, closer to game art than per-pixel noise."""
    noise = np.random.default_rng(seed).integers(0, 255, (height // 8 + 1, width // 8 + 1, 3), dtype=np.uint8)
    return cv2.resize(noise, (width, height), interpolation=cv2.INTER_CUBIC)


@pytest.fixture
def frame() -> np.ndarray:
    return textured(720, 1280)


def vision_config(tmp_path, **overrides) -> VisionConfig:
    return VisionConfig(templates_dir=tmp_path, manifest=tmp_path / "manifest.yaml", **overrides)


def test_scales_try_native_size_first():
    assert SCALES[0] == 1.0
    assert sorted(SCALES) == [0.9, 0.95, 1.0, 1.05, 1.1]


def test_exact_crop_is_found_at_its_position(frame):
    template = frame[300:360, 500:620].copy()
    match = match_template(frame, template, 0.86, SCALES, "button")
    assert match is not None
    assert (match.x, match.y, match.width, match.height) == (500, 300, 120, 60)
    assert match.center == (560, 330)
    assert match.scale == 1.0
    assert match.score > 0.99


@pytest.mark.parametrize("scale", [0.9, 1.1])
def test_scaled_element_is_found(frame, scale):
    template = frame[300:400, 500:680].copy()
    scene = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    match = match_template(scene, template, 0.86, SCALES)
    assert match is not None
    assert match.scale == scale
    assert abs(match.x - 500 * scale) <= 2
    assert abs(match.y - 300 * scale) <= 2


def test_absent_template_does_not_match(frame):
    assert match_template(frame, textured(60, 120, seed=99), 0.86, SCALES) is None


def test_flat_template_does_not_match(frame):
    assert match_template(frame, np.full((40, 40, 3), 128, dtype=np.uint8), 0.5, SCALES) is None


@pytest.mark.parametrize("level", [0, 128, 255])
def test_nothing_matches_on_a_flat_loading_screen(level):
    screen = np.full((720, 1280, 3), level, dtype=np.uint8)
    assert match_template(screen, textured(60, 120), 0.86, SCALES) is None


def test_template_larger_than_the_frame_does_not_match():
    assert match_template(textured(50, 50), textured(100, 100), 0.5, SCALES) is None


def test_region_limits_the_search_and_keeps_frame_coordinates(frame):
    template = frame[300:360, 500:620].copy()
    found = match_template(frame, template, 0.86, SCALES, region=(450, 250, 300, 200))
    assert found is not None and (found.x, found.y) == (500, 300)
    assert match_template(frame, template, 0.86, SCALES, region=(0, 0, 300, 200)) is None


def test_best_score_reports_near_misses(frame):
    template = frame[300:360, 500:620].copy()
    assert best_score(frame, template, SCALES) > 0.99
    assert best_score(frame, textured(60, 120, seed=99), SCALES) < 0.86


def test_find_all_returns_each_copy_left_to_right():
    scene = textured(300, 800)
    icon = textured(40, 40, seed=5)
    for x in (600, 100, 350):
        scene[50:90, x : x + 40] = icon
    matches = find_all(scene, icon, 0.9, "enemy")
    assert [m.x for m in matches] == [100, 350, 600]


@pytest.fixture
def library(tmp_path, frame) -> TemplateLibrary:
    cv2.imwrite(str(tmp_path / "button.png"), frame[300:360, 500:620])
    cv2.imwrite(str(tmp_path / "flat.png"), np.full((20, 20, 3), 90, dtype=np.uint8))
    (tmp_path / "broken.png").write_bytes(b"not a png")
    return TemplateLibrary(vision_config(tmp_path, template_thresholds={"button": 0.95}), LOGGER)


def manifest_of(*names: str) -> Manifest:
    return Manifest(templates=[TemplateEntry(name=name) for name in names])


def test_missing_flat_and_broken_templates_warn_without_crashing(library, tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        missing = library.load(manifest_of("button", "flat", "broken", "absent"), tmp_path)
    assert missing == ["flat", "broken", "absent"]
    assert "3 of 4 templates" in caplog.text


def test_unloaded_template_never_matches(library, tmp_path, frame):
    library.load(manifest_of("button", "absent"), tmp_path)
    assert library.find(frame, "absent") is None
    assert library.find_every(frame, "absent") == []
    assert library.find(frame, "button") is not None


def test_per_template_threshold_overrides_the_default(library):
    assert library.threshold("button") == 0.95
    assert library.threshold("other") == 0.86


def test_find_first_follows_the_given_order(library, tmp_path, frame):
    library.load(manifest_of("button"), tmp_path)
    match = library.find_first(frame, ["absent", "button"])
    assert match is not None and match.name == "button"


def test_frame_difference():
    black = np.zeros((10, 10, 3), dtype=np.uint8)
    assert frame_difference(black, black) == 0
    assert frame_difference(black, np.full((10, 10, 3), 40, dtype=np.uint8)) == 40
    assert frame_difference(black, np.zeros((5, 5, 3), dtype=np.uint8)) == 255


def test_loading_needs_both_no_match_and_a_still_picture():
    detector = LoadingDetector(no_match_seconds=1.5, frame_diff_threshold=2.0)
    still = np.zeros((10, 10, 3), dtype=np.uint8)
    moving = np.full((10, 10, 3), 90, dtype=np.uint8)
    detector.update(still, True, now=100.0)
    assert detector.update(still, False, now=101.0) is False, "too soon"
    assert detector.update(still, False, now=102.0) is True
    assert detector.update(moving, False, now=103.0) is False, "picture changed"
    assert detector.update(still, True, now=104.0) is False, "template matched"
    assert detector.update(still, False, now=104.5) is False, "the wait restarted"


def test_prepare_for_ocr_gives_dark_text_on_light():
    light_on_dark = np.zeros((20, 60), dtype=np.uint8)
    light_on_dark[8:12, 10:50] = 255
    prepared = prepare_for_ocr(light_on_dark)
    assert prepared.shape == (60, 180)
    assert np.count_nonzero(prepared) > prepared.size / 2


def test_missing_tesseract_gives_a_clear_error(tmp_path, frame):
    ocr = Ocr(vision_config(tmp_path, tesseract_cmd=str(tmp_path / "nope.exe")))
    with pytest.raises(OcrUnavailableError, match="tesseract_cmd"):
        ocr.check()
    with pytest.raises(OcrUnavailableError):
        ocr.read(frame, (0, 0, 100, 40))


def installed_ocr(tmp_path) -> Ocr:
    command = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    command = command if shutil.which(command) else None
    ocr = Ocr(vision_config(tmp_path, tesseract_cmd=command))
    try:
        ocr.check()
    except OcrUnavailableError:
        pytest.skip("Tesseract is not installed")
    return ocr


@pytest.mark.parametrize("colors", [((255, 255, 255), (20, 20, 20)), ((30, 30, 30), (230, 230, 230))])
def test_ocr_reads_rendered_text(tmp_path, colors):
    text_color, background = colors
    image = np.full((60, 400, 3), background, dtype=np.uint8)
    cv2.putText(image, "Victory", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.2, text_color, 2, cv2.LINE_AA)
    ocr = installed_ocr(tmp_path)
    assert "victory" in ocr.read(image).lower()
    assert ocr.contains(image, "VICTORY")
    assert not ocr.contains(image, "Defeat")


def test_virtual_desktop_ends_stay_in_range():
    assert normalize_to_virtual_desktop(-1920, 0, -1920, 0, 3840, 1080)[:2] == (8, 30)
    x, y = normalize_to_virtual_desktop(1919, 1079, -1920, 0, 3840, 1080)
    assert x <= 65535 and y <= 65535


@pytest.mark.parametrize("screen_x", [-1920, -1280, -1, 0, 1, 1919])
def test_normalized_x_maps_back_to_the_same_pixel(screen_x):
    x, _ = normalize_to_virtual_desktop(screen_x, 0, -1920, 0, 3840, 1080)
    assert x * 3840 // 65536 - 1920 == screen_x


def test_jitter_stays_in_range_and_never_repeats():
    rng = random.Random(1)
    last = None
    for _ in range(500):
        point = jittered_point(100, 200, 3, last, rng)
        assert abs(point[0] - 100) <= 3 and abs(point[1] - 200) <= 3
        assert point != last
        last = point


def test_no_jitter_gives_the_exact_point():
    assert jittered_point(100.4, 200.6, 0, (100, 201), random.Random(1)) == (100, 201)


class FakeWindow:
    """A window that is always ready, or never in the foreground."""

    def __init__(self, foreground: bool = True) -> None:
        self.foreground = foreground

    def client_rect_on_screen(self) -> ScreenRect:
        return ScreenRect(-1920, 92, 1280, 720)

    def ensure_foreground(self) -> bool:
        return self.foreground


def input_config(**overrides) -> InputConfig:
    values = {"click_hold_ms": Range(min=0, max=0), "pre_action_delay_ms": Range(min=0, max=0), "seed": 7}
    return InputConfig(**{**values, **overrides})


def test_dry_run_logs_clicks_without_touching_the_mouse(monkeypatch, caplog):
    monkeypatch.setattr("core.input._send", lambda *events: pytest.fail("input was sent"))
    controller = InputController(FakeWindow(foreground=False), input_config(), LOGGER, dry_run=True)
    with caplog.at_level(logging.INFO):
        controller.click(640, 360)
        controller.press("esc")
        controller.type_text("hello")
    assert "dry run: click" in caplog.text


def test_same_seed_gives_the_same_clicks():
    first = InputController(FakeWindow(), input_config(), LOGGER, dry_run=True)
    second = InputController(FakeWindow(), input_config(), LOGGER, dry_run=True)
    assert [first.click(640, 360) for _ in range(5)] == [second.click(640, 360) for _ in range(5)]


def test_clicks_stay_inside_the_game_near_an_edge():
    controller = InputController(FakeWindow(), input_config(), LOGGER, dry_run=True)
    for _ in range(50):
        x, y = controller.click(1279, 0)
        assert 0 <= x <= 1279 and 0 <= y <= 719


def test_no_input_is_sent_without_focus(monkeypatch):
    monkeypatch.setattr("core.input._send", lambda *events: pytest.fail("input was sent"))
    controller = InputController(FakeWindow(foreground=False), input_config(), LOGGER)
    with pytest.raises(InputError, match="foreground"):
        controller.click(640, 360)


def test_click_sends_move_down_up(monkeypatch):
    events = []
    monkeypatch.setattr("core.input._send", lambda *inputs: events.append(inputs[0].u.mi.dwFlags))
    InputController(FakeWindow(), input_config(), LOGGER).click(640, 360)
    assert events[1:] == [0x0002, 0x0004]
    assert events[0] & 0x8000


def test_key_hold_goes_down_then_up(monkeypatch):
    events = []
    monkeypatch.setattr("core.input.pydirectinput.keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr("core.input.pydirectinput.keyUp", lambda key: events.append(("up", key)))
    controller = InputController(FakeWindow(), input_config(), LOGGER)
    controller.key_down("esc")
    controller.key_up("esc")
    assert events == [("down", "esc"), ("up", "esc")]


def test_dry_run_does_not_hold_a_key(monkeypatch):
    monkeypatch.setattr("core.input.pydirectinput.keyDown", lambda key: pytest.fail("key sent"))
    monkeypatch.setattr("core.input.pydirectinput.keyUp", lambda key: pytest.fail("key sent"))
    controller = InputController(FakeWindow(foreground=False), input_config(), LOGGER, dry_run=True)
    controller.key_down("esc")
    controller.key_up("esc")


def test_key_hold_is_refused_without_focus(monkeypatch):
    monkeypatch.setattr("core.input.pydirectinput.keyDown", lambda key: pytest.fail("key sent"))
    with pytest.raises(InputError):
        InputController(FakeWindow(foreground=False), input_config(), LOGGER).key_down("esc")


def test_stop_event_prevents_the_click(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr("core.input._send", lambda *events: pytest.fail("input was sent"))
    controller = InputController(FakeWindow(), input_config(), LOGGER, stop_event=stop)
    with pytest.raises(BotStopped):
        controller.click(640, 360)


def test_secret_text_stays_out_of_the_logs(monkeypatch, caplog):
    monkeypatch.setattr("core.input._send", lambda *events: None)
    controller = InputController(FakeWindow(), input_config(), LOGGER)
    with caplog.at_level(logging.DEBUG):
        controller.type_text("hunter2", secret=True)
    assert "hunter2" not in caplog.text


def test_double_click_presses_twice_on_one_point_and_fast(monkeypatch):
    sleeps = []
    moves = []
    buttons = []
    monkeypatch.setattr("core.input.move_cursor", lambda x, y: moves.append((x, y)))
    monkeypatch.setattr("core.input._send", lambda *inputs: buttons.append(inputs[0].u.mi.dwFlags))
    monkeypatch.setattr("core.input.time.sleep", sleeps.append)
    InputController(FakeWindow(), input_config(), LOGGER).double_click(640, 360)
    assert len(moves) == 2 and moves[0] == moves[1]
    assert buttons == [0x0002, 0x0004, 0x0002, 0x0004]
    assert sum(sleeps) < 0.3


class BackgroundWindow:
    """A game window behind others: it never has the focus, and asking for it would be a bug."""

    hwnd = 1234

    def client_rect_on_screen(self):
        return ScreenRect(100, 50, 1280, 720)

    def ensure_foreground(self):
        raise AssertionError("background mode must not take the focus")


@pytest.fixture
def posted(monkeypatch):
    import core.input as input_module

    messages = []
    monkeypatch.setattr(input_module.win32gui, "PostMessage", lambda hwnd, message, wparam, lparam: messages.append((hwnd, message, wparam, lparam)))
    monkeypatch.setattr(input_module.win32gui, "ScreenToClient", lambda hwnd, point: (point[0] - 100, point[1] - 50))
    monkeypatch.setattr(input_module, "move_cursor", lambda x, y: pytest.fail("the real cursor must not move"))
    monkeypatch.setattr(input_module, "_send", lambda event: pytest.fail("no global input in background mode"))
    return messages


def background_controller():
    config = input_config().model_copy(update={"background": True, "jitter_px": 0})
    return InputController(BackgroundWindow(), config, LOGGER)


def test_background_click_goes_to_the_window(posted):
    x, y = background_controller().click(640, 400)
    assert [message[1] for message in posted] == [0x0200, 0x0201, 0x0202]
    assert all(message[0] == 1234 for message in posted)
    assert posted[1][3] == (y << 16) | x


def test_background_key_goes_to_the_window(posted, monkeypatch):
    import core.input as input_module

    monkeypatch.setattr(input_module.pydirectinput, "press", lambda key: pytest.fail("no global key"))
    background_controller().press("esc")
    assert [message[1:3] for message in posted] == [(0x0100, 0x1B), (0x0101, 0x1B)]


def test_background_text_goes_to_the_window(posted):
    background_controller().type_text("ab")
    assert [message[1:3] for message in posted] == [(0x0102, ord("a")), (0x0102, ord("b"))]


def test_background_drag_holds_the_button_while_moving(posted):
    background_controller().drag(120, 620, 120, 200)
    assert posted[1][1] == 0x0201
    assert all(message[2] == 1 for message in posted[2:-1])
    assert posted[-1][1] == 0x0202


def test_shipped_config_runs_in_the_background():
    from pathlib import Path

    from core.config import load_config

    assert load_config(Path(__file__).parent.parent / "config.yaml").input.background


class MinimizedDuringDelay(FakeWindow):
    """A window minimized between the first look and the click."""

    def __init__(self) -> None:
        super().__init__()
        self.looks = 0

    def client_rect_on_screen(self) -> ScreenRect:
        self.looks += 1
        return ScreenRect(-1920, 92, 1280, 720) if self.looks == 1 else ScreenRect(-32000, -32000, 0, 0)


def test_a_click_is_skipped_when_the_game_gets_minimized(monkeypatch, caplog):
    monkeypatch.setattr("core.input._send", lambda *events: pytest.fail("input was sent"))
    controller = InputController(MinimizedDuringDelay(), input_config(), LOGGER)
    with caplog.at_level(logging.WARNING):
        controller.click(640, 360)
    assert "minimized: click skipped" in caplog.text


def test_a_tap_skips_the_pre_action_delay(posted):
    import time

    config = input_config(pre_action_delay_ms=Range(min=2000, max=2000)).model_copy(update={"background": True, "jitter_px": 0})
    started = time.monotonic()
    InputController(BackgroundWindow(), config, LOGGER).tap(640, 400)
    assert time.monotonic() - started < 0.5
    assert [message[1] for message in posted] == [0x0200, 0x0201, 0x0202]

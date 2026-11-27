"""Multi-scale template matching, template library, OCR helpers and loading detection."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract

from core.config import VisionConfig
from core.manifest import Manifest

Region = tuple[int, int, int, int]
FLAT_TEMPLATE_STD = 1.0
# Normalized correlation stretches any noise: a nearly black corner scored 0.99 against the pause
# icon. A real match keeps some of the template's contrast, even dimmed behind a popup.
MIN_CONTRAST_RATIO = 0.1


class OcrUnavailableError(Exception):
    """The Tesseract program cannot be run."""


@dataclass(frozen=True)
class Match:
    """A template found in a frame, in frame coordinates."""

    name: str
    x: int
    y: int
    width: int
    height: int
    score: float
    scale: float

    @property
    def center(self) -> tuple[int, int]:
        """Return the center of the match, the usual click target."""
        return self.x + self.width // 2, self.y + self.height // 2


def scale_values(scale_min: float, scale_max: float, scale_step: float) -> list[float]:
    """Return the scales to try, native size first: the window is forced to the native size."""
    count = round((scale_max - scale_min) / scale_step) + 1
    scales = [round(scale_min + index * scale_step, 4) for index in range(count)]
    return sorted(scales, key=lambda scale: (abs(scale - 1.0), scale))


def score_map(frame: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """Return the TM_CCOEFF_NORMED scores, or None if the template does not fit in the frame.

    OpenCV scores a flat template 1.0 everywhere, so one is refused here.
    """
    if template.shape[0] > frame.shape[0] or template.shape[1] > frame.shape[1]:
        return None
    if float(template.std()) < FLAT_TEMPLATE_STD:
        return None
    scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    return np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)


def _resized(template: np.ndarray, scale: float) -> np.ndarray | None:
    """Return a template at a scale, or None if nothing is left of it."""
    if scale == 1.0:
        return template
    width = round(template.shape[1] * scale)
    height = round(template.shape[0] * scale)
    if width < 1 or height < 1:
        return None
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(template, (width, height), interpolation=interpolation)


def _crop_to_region(frame: np.ndarray, region: Region | None) -> tuple[np.ndarray, int, int]:
    """Return the part of a frame to search and its offset."""
    if region is None:
        return frame, 0, 0
    x, y, width, height = region
    x, y = max(x, 0), max(y, 0)
    return frame[y : y + height, x : x + width], x, y


def match_template(
    frame: np.ndarray,
    template: np.ndarray,
    threshold: float,
    scales: list[float],
    name: str = "",
    region: Region | None = None,
) -> Match | None:
    """Return the best match over all scales, or None if none reaches the threshold.

    Stops at the first scale that reaches the threshold, so a native-size match is fast.
    """
    area, offset_x, offset_y = _crop_to_region(frame, region)
    for scale in scales:
        scaled = _resized(template, scale)
        if scaled is None:
            continue
        scores = score_map(area, scaled)
        if scores is None:
            continue
        _, best, _, location = cv2.minMaxLoc(scores)
        patch = area[location[1] : location[1] + scaled.shape[0], location[0] : location[0] + scaled.shape[1]]
        if best >= threshold and float(patch.std()) >= MIN_CONTRAST_RATIO * float(scaled.std()):
            return Match(
                name,
                location[0] + offset_x,
                location[1] + offset_y,
                scaled.shape[1],
                scaled.shape[0],
                float(best),
                scale,
            )
    return None


def best_score(frame: np.ndarray, template: np.ndarray, scales: list[float]) -> float:
    """Return the highest score over all scales, to tune thresholds during calibration."""
    best = -1.0
    for scale in scales:
        scaled = _resized(template, scale)
        scores = score_map(frame, scaled) if scaled is not None else None
        if scores is not None:
            best = max(best, float(scores.max()))
    return best


def find_all(
    frame: np.ndarray,
    template: np.ndarray,
    threshold: float,
    name: str = "",
    region: Region | None = None,
) -> list[Match]:
    """Return every separate native-size match, left to right then top to bottom.

    Used for repeated elements such as enemy portraits. Overlapping hits count once.
    """
    area, offset_x, offset_y = _crop_to_region(frame, region)
    scores = score_map(area, template)
    if scores is None:
        return []
    height, width = template.shape[:2]
    candidates = sorted(
        zip(*np.where(scores >= threshold)), key=lambda point: float(scores[point]), reverse=True
    )
    kept: list[Match] = []
    for y, x in candidates:
        px, py = int(x) + offset_x, int(y) + offset_y
        if all(abs(px - m.x) >= width / 2 or abs(py - m.y) >= height / 2 for m in kept):
            kept.append(Match(name, px, py, width, height, float(scores[y, x]), 1.0))
    return sorted(kept, key=lambda m: (m.x, m.y))


class TemplateLibrary:
    """The template images on disk. A missing image is a warning, never a crash."""

    def __init__(self, config: VisionConfig, logger: logging.Logger) -> None:
        """Prepare an empty library. Call load() to read the images."""
        self.config = config
        self.logger = logger
        self.scales = scale_values(config.scale_min, config.scale_max, config.scale_step)
        self.templates: dict[str, np.ndarray] = {}

    def load(self, manifest: Manifest, templates_dir: Path) -> list[str]:
        """Load every manifest template that has an image and return the names of the others."""
        self.templates = {}
        missing = []
        for name in manifest.names():
            path = templates_dir / f"{name}.png"
            image = None
            if path.is_file():
                # imdecode rather than imread: imread fails on non-ASCII Windows paths.
                image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                missing.append(name)
            elif float(image.std()) < FLAT_TEMPLATE_STD:
                self.logger.warning("template %s is a flat color and can never match", name)
                missing.append(name)
            else:
                self.templates[name] = image
        if missing:
            self.logger.warning(
                "%d of %d templates are not captured yet: %s",
                len(missing),
                len(manifest.names()),
                ", ".join(missing),
            )
        return missing

    def threshold(self, name: str) -> float:
        """Return a template's threshold: its own if configured, otherwise the default."""
        return self.config.template_thresholds.get(name, self.config.default_threshold)

    def find(self, frame: np.ndarray, name: str, region: Region | None = None) -> Match | None:
        """Look for one template. A template that is not loaded simply does not match."""
        template = self.templates.get(name)
        if template is None:
            return None
        return match_template(frame, template, self.threshold(name), self.scales, name, region)

    def find_first(self, frame: np.ndarray, names: list[str]) -> Match | None:
        """Return the match of the first template found, in the given order."""
        for name in names:
            match = self.find(frame, name)
            if match is not None:
                return match
        return None

    def find_every(self, frame: np.ndarray, name: str, region: Region | None = None) -> list[Match]:
        """Return every separate match of a repeated element."""
        template = self.templates.get(name)
        if template is None:
            return []
        return find_all(frame, template, self.threshold(name), name, region)


def prepare_for_ocr(image: np.ndarray, white_level: int | None = None) -> np.ndarray:
    """Return a black and white, enlarged image: Tesseract reads small game text badly as is.

    With `white_level`, only pixels above it count as text, for white text over artwork.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    enlarged = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    if white_level is not None:
        return cv2.threshold(enlarged, white_level, 255, cv2.THRESH_BINARY_INV)[1]
    _, binary = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Tesseract expects dark text on a light background.
    if np.count_nonzero(binary) < binary.size / 2:
        binary = cv2.bitwise_not(binary)
    return binary


BUNDLED_TESSERACT = Path("tesseract/tesseract.exe")


class Ocr:
    """Text gates read with Tesseract."""

    def __init__(self, config: VisionConfig) -> None:
        """Point pytesseract at the configured program."""
        self.language = config.ocr_language
        command = config.tesseract_cmd
        # The packaged bot ships its own copy, used when Tesseract is not installed where the config says.
        if (not command or not Path(command).exists()) and BUNDLED_TESSERACT.exists():
            command = str(BUNDLED_TESSERACT.resolve())
        if command:
            pytesseract.pytesseract.tesseract_cmd = command

    def check(self) -> str:
        """Return the Tesseract version, raising OcrUnavailableError if it cannot run."""
        try:
            return str(pytesseract.get_tesseract_version())
        except (pytesseract.TesseractNotFoundError, OSError) as error:
            raise OcrUnavailableError(
                f"Tesseract cannot run ({error}): set vision.tesseract_cmd in config.yaml"
            ) from error

    def read(
        self,
        frame: np.ndarray,
        region: Region | None = None,
        single_line: bool = True,
        whitelist: str | None = None,
        white_level: int | None = None,
    ) -> str:
        area, _, _ = _crop_to_region(frame, region)
        options = f"--psm {7 if single_line else 6}"
        if whitelist:
            options += f" -c tessedit_char_whitelist={whitelist}"
        try:
            text = pytesseract.image_to_string(prepare_for_ocr(area, white_level), lang=self.language, config=options)
        except (pytesseract.TesseractNotFoundError, OSError) as error:
            raise OcrUnavailableError(f"Tesseract cannot run: {error}") from error
        return text.strip()

    def contains(self, frame: np.ndarray, words: str, region: Region | None = None) -> bool:
        """Return whether a region contains some text, ignoring case."""
        return words.casefold() in self.read(frame, region, single_line=False).casefold()


def frame_difference(first: np.ndarray, second: np.ndarray) -> float:
    """Return the mean absolute pixel difference between two frames, from 0 to 255."""
    if first.shape != second.shape:
        return 255.0
    return float(cv2.absdiff(first, second).mean())


class LoadingDetector:
    """Spots loading: no template matched for a while, and the picture has stopped changing."""

    def __init__(self, no_match_seconds: float, frame_diff_threshold: float) -> None:
        """Store the two limits from the config."""
        self.no_match_seconds = no_match_seconds
        self.frame_diff_threshold = frame_diff_threshold
        self._last_match_time = time.monotonic()
        self._last_frame: np.ndarray | None = None

    def update(self, frame: np.ndarray, any_match: bool, now: float | None = None) -> bool:
        """Feed the current frame and return whether the game looks like it is loading."""
        now = time.monotonic() if now is None else now
        if any_match:
            self._last_match_time = now
        previous = self._last_frame
        self._last_frame = frame
        if any_match or previous is None:
            return False
        is_still = frame_difference(previous, frame) < self.frame_diff_threshold
        return is_still and now - self._last_match_time > self.no_match_seconds

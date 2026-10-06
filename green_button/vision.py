"""Пиксельный детектор зелёной заливки; не определяет наличие обработчика нажатия."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from cv2.typing import MatLike
from numpy.typing import NDArray

from .errors import ScreenshotError
from .models import Bounds, Candidate, Detection

type Image = NDArray[np.uint8]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MIN_PNG_HEADER = 24
_MAX_PNG_BYTES = 32 * 1024 * 1024
_MAX_PIXELS = 20_000_000
_MIN_SIDE_PIXELS = 12
_MIN_SIDE_FRACTION = 0.018
_MIN_ASPECT = 0.7
_MAX_ASPECT = 14.0


@dataclass(frozen=True, slots=True)
class DetectionSettings:
    """Начальные пороги для залитых кнопок; калибровка на реальных экранах отдельно."""

    hue_low: int = 38
    hue_high: int = 88
    saturation_low: int = 70
    value_low: int = 45
    min_area_fraction: float = 0.0004
    max_area_fraction: float = 0.25
    min_green_fraction: float = 0.58
    min_solidity: float = 0.9
    min_extent: float = 0.68


def decode_png(data: bytes) -> Image:
    """Проверить PNG-заголовок и декодировать снимок без исправления бинарных байтов.

    Raises:
        ScreenshotError: Данные повреждены или превышают ограничения размера.
    """
    width, height = _png_size(data)
    return _decoded_frame(data, width, height)


def _png_size(data: bytes) -> tuple[int, int]:
    """Прочитать ширину и высоту из IHDR."""
    _require_png_header(data)
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    _require_png_size(width, height)
    return width, height


def _require_png_header(data: bytes) -> None:
    """Отклонить пустой буфер и данные без сигнатуры PNG."""
    if not _MIN_PNG_HEADER <= len(data) <= _MAX_PNG_BYTES:
        msg = "Пустой, слишком короткий или слишком большой PNG."
        raise ScreenshotError(msg)
    _require_png_signature(data)


def _require_png_signature(data: bytes) -> None:
    """Проверить сигнатуру и чанк IHDR."""
    if data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR":
        msg = "ADB вернул данные, не являющиеся PNG-скриншотом."
        raise ScreenshotError(msg)


def _require_png_size(width: int, height: int) -> None:
    """Отклонить пустой и чрезмерно большой кадр."""
    if not _positive_sides(width, height) or width * height > _MAX_PIXELS:
        msg = "Неподдерживаемый размер скриншота."
        raise ScreenshotError(msg)


def _positive_sides(width: int, height: int) -> bool:
    """Проверить, что обе стороны изображения больше нуля."""
    return width > 0 and height > 0


def _decoded_frame(data: bytes, width: int, height: int) -> Image:
    """Декодировать кадр и сверить его с размером из заголовка."""
    frame = _imdecode(data)
    if not _frame_matches(frame, width, height):
        msg = "PNG-скриншот повреждён."
        raise ScreenshotError(msg)
    return cast(Image, frame)


def _imdecode(data: bytes) -> MatLike | None:
    """Декодировать байты снимка в цветовой кадр."""
    try:
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as error:
        msg = "Не удалось декодировать PNG-скриншот."
        raise ScreenshotError(msg) from error


def _frame_matches(frame: MatLike | None, width: int, height: int) -> bool:
    """Сверить форму и тип декодированного кадра с заголовком PNG."""
    if frame is None:
        return False
    return bool(frame.shape == (height, width, 3) and frame.dtype == np.uint8)


class GreenButtonDetector:
    """Чистая обработка изображения без команд ADB, ожиданий и записи файлов."""

    def __init__(self, settings: DetectionSettings | None = None) -> None:
        """Сохранить пороги детектора без разделяемого изменяемого состояния."""
        self._settings = settings or DetectionSettings()

    def detect(
        self, png: bytes, *, excluded_regions: Sequence[Bounds] = ()
    ) -> Detection:
        """Найти и детерминированно упорядочить кандидатов в исходных координатах."""
        frame = decode_png(png)
        height, width = frame.shape[:2]
        settings = self._settings
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array(
                [settings.hue_low, settings.saturation_low, settings.value_low],
                dtype=np.uint8,
            ),
            np.array([settings.hue_high, 255, 255], dtype=np.uint8),
        )
        kernel = np.ones((3, 3), dtype=np.uint8)
        clean = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        clean = cv2.morphologyEx(clean, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(
            clean,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        candidates = []
        for contour in contours:
            candidate = self._candidate(contour, mask)
            if candidate is not None and not self._excluded(
                candidate,
                excluded_regions,
            ):
                candidates.append(candidate)
        candidates.sort(
            key=lambda candidate: (
                -candidate.score,
                -candidate.bounds.area,
                candidate.bounds.top,
                candidate.bounds.left,
            )
        )
        return Detection(width, height, tuple(candidates))

    @staticmethod
    def _excluded(candidate: Candidate, regions: Sequence[Bounds]) -> bool:
        """Не превращать системную панель в кандидата даже после очистки маски."""
        return any(candidate.bounds.intersection_area(region) > 0 for region in regions)

    def _candidate(self, contour: MatLike, mask: MatLike) -> Candidate | None:
        """Проверить размер, форму и исходную долю зелёного внутри кандидата."""
        bounds = _contour_bounds(contour)
        if not self._shape_ok(bounds, mask):
            return None
        return self._filled_candidate(contour, mask, bounds)

    def _shape_ok(self, bounds: Bounds, mask: MatLike) -> bool:
        """Отсечь слишком мелкие, вытянутые и огромные области."""
        screen_height, screen_width = mask.shape[:2]
        relative_area = bounds.area / (screen_width * screen_height)
        return _side_ok(bounds, screen_width, screen_height) and _area_ok(
            relative_area, self._settings
        )

    def _filled_candidate(
        self, contour: MatLike, mask: MatLike, bounds: Bounds
    ) -> Candidate | None:
        """Принять только залитую область, центр которой лежит внутри контура."""
        extent, solidity, green_fraction = _fill_metrics(contour, mask, bounds)
        if not _fill_ok(extent, solidity, green_fraction, self._settings):
            return None
        if not _center_inside(contour, bounds):
            return None
        score = (extent + solidity + green_fraction) / 3
        return Candidate(bounds, score, green_fraction, solidity)


def _contour_bounds(contour: MatLike) -> Bounds:
    """Получить ограничивающий прямоугольник контура."""
    x, y, width, height = cv2.boundingRect(contour)
    return Bounds(x, y, x + width, y + height)


def _side_ok(bounds: Bounds, screen_width: int, screen_height: int) -> bool:
    """Проверить минимальную сторону и отношение ширины к высоте."""
    min_side = max(
        _MIN_SIDE_PIXELS,
        min(screen_width, screen_height) * _MIN_SIDE_FRACTION,
    )
    return min(bounds.width, bounds.height) >= min_side and (
        _MIN_ASPECT <= bounds.width / bounds.height <= _MAX_ASPECT
    )


def _area_ok(relative_area: float, settings: DetectionSettings) -> bool:
    """Проверить долю площади кандидата на экране."""
    low, high = settings.min_area_fraction, settings.max_area_fraction
    return low <= relative_area <= high


def _fill_metrics(
    contour: MatLike, mask: MatLike, bounds: Bounds
) -> tuple[float, float, float]:
    """Посчитать заполненность, выпуклость и долю зелёного в прямоугольнике."""
    area = float(cv2.contourArea(contour))
    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    solidity = area / hull_area if hull_area else 0.0
    green = cv2.countNonZero(
        mask[bounds.top : bounds.bottom, bounds.left : bounds.right]
    )
    return area / bounds.area, solidity, green / bounds.area


def _fill_ok(
    extent: float, solidity: float, green_fraction: float, settings: DetectionSettings
) -> bool:
    """Проверить, что область похожа на залитую кнопку, а не на рамку или линию."""
    return (
        extent >= settings.min_extent
        and solidity >= settings.min_solidity
        and green_fraction >= settings.min_green_fraction
    )


def _center_inside(contour: MatLike, bounds: Bounds) -> bool:
    """Проверить, что центр прямоугольника лежит внутри контура."""
    center = bounds.center
    measure_distance = False
    distance = cv2.pointPolygonTest(
        contour,
        (float(center.x), float(center.y)),
        measure_distance,
    )
    return float(distance) >= 0

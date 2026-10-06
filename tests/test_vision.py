"""Реальный OpenCV: цвет, геометрия, отрицательные примеры и координаты."""

import cv2
import numpy as np
import pytest
from green_button.errors import ScreenshotError
from green_button.models import Bounds
from green_button.vision import GreenButtonDetector, Image, decode_png

from .helpers import blank_image, button_image, encode_image


def test_filled_button_coordinates() -> None:
    """Точка нажатия остаётся в исходных координатах PNG."""
    found = GreenButtonDetector().detect(button_image())
    assert (found.width, found.height) == (480, 800)
    assert len(found.candidates) == 1
    assert found.candidates[0].bounds.center.x == 220
    assert found.candidates[0].bounds.center.y == 305


@pytest.mark.parametrize("hue", [40, 50, 60, 75, 85])
def test_green_hues(hue: int) -> None:
    """Несколько оттенков зелёного проходят один фиксированный диапазон."""
    image = blank_image()
    hsv = np.full((70, 300, 3), (hue, 200, 190), dtype=np.uint8)
    image[200:270, 60:360] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    assert len(GreenButtonDetector().detect(encode_image(image)).candidates) == 1


@pytest.mark.parametrize("hue", [0, 15, 25, 100, 120, 150])
def test_other_colors(hue: int) -> None:
    """Красный, жёлтый, голубой, синий и пурпурный не считаются зелёным."""
    image = blank_image()
    hsv = np.full((70, 300, 3), (hue, 200, 190), dtype=np.uint8)
    image[200:270, 60:360] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    assert not GreenButtonDetector().detect(encode_image(image)).candidates


@pytest.mark.parametrize(
    ("saturation", "value"), [(0, 255), (30, 200), (200, 20), (0, 0)]
)
def test_muted_or_dark_colors(saturation: int, value: int) -> None:
    """Почти серые и очень тёмные области не дают цветового кандидата."""
    image = blank_image()
    hsv = np.full((70, 300, 3), (60, saturation, value), dtype=np.uint8)
    image[200:270, 60:360] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    assert not GreenButtonDetector().detect(encode_image(image)).candidates


@pytest.mark.parametrize("shape", ["circle", "rounded", "text", "wide"])
def test_supported_shapes(shape: str) -> None:
    """Круг, скруглённая кнопка, подпись и кнопка во всю ширину поддерживаются."""
    image = blank_image()
    green = (55, 175, 60)
    if shape == "circle":
        cv2.circle(image, (240, 400), 45, green, -1)
    elif shape == "rounded":
        cv2.rectangle(image, (100, 270), (370, 340), green, -1)
        cv2.circle(image, (100, 305), 35, green, -1)
        cv2.circle(image, (370, 305), 35, green, -1)
    else:
        left, right = (0, 479) if shape == "wide" else (60, 420)
        cv2.rectangle(image, (left, 270), (right, 340), green, -1)
        cv2.putText(
            image,
            "OK",
            (210, 320),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (255, 255, 255),
            3,
        )
    assert len(GreenButtonDetector().detect(encode_image(image)).candidates) == 1


@pytest.mark.parametrize(
    "shape",
    ["empty", "background", "outline", "line", "indicator", "text"],
)
def test_negative_images(shape: str) -> None:
    """Шум, зелёный экран, рамки, линии и отдельные буквы не вызывают нажатие."""
    image = blank_image()
    _paint_negative(image, shape)
    assert not GreenButtonDetector().detect(encode_image(image)).candidates


def _paint_negative(image: Image, shape: str) -> None:
    """Нарисовать форму, которая не должна считаться кнопкой."""
    painter = _NEGATIVE_PAINTERS.get(shape)
    if painter is not None:
        painter(image)


def _paint_background(image: Image) -> None:
    """Залить кадр зелёным."""
    image[:] = _GREEN


def _paint_outline(image: Image) -> None:
    """Нарисовать зелёную рамку."""
    cv2.rectangle(image, (60, 270), (420, 340), _GREEN, 2)


def _paint_line(image: Image) -> None:
    """Нарисовать тонкую зелёную полосу."""
    cv2.rectangle(image, (60, 270), (420, 273), _GREEN, -1)


def _paint_indicator(image: Image) -> None:
    """Нарисовать маленький зелёный индикатор."""
    cv2.rectangle(image, (440, 10), (458, 18), _GREEN, -1)


def _paint_text(image: Image) -> None:
    """Нарисовать зелёную надпись."""
    cv2.putText(image, "HELLO", (60, 300), cv2.FONT_HERSHEY_SIMPLEX, 1, _GREEN, 2)


_GREEN = (55, 175, 60)
_NEGATIVE_PAINTERS = {
    "background": _paint_background,
    "outline": _paint_outline,
    "line": _paint_line,
    "indicator": _paint_indicator,
    "text": _paint_text,
}


def test_system_bar_exclusion() -> None:
    """Системная область исключается по InsetsSource, а не фиксированным отступам."""
    image = blank_image()
    cv2.rectangle(image, (50, 0), (250, 40), (55, 175, 60), -1)
    png = encode_image(image)
    detector = GreenButtonDetector()
    assert detector.detect(png).candidates
    assert not detector.detect(png, excluded_regions=[Bounds(0, 0, 480, 50)]).candidates


def test_two_buttons_stay_separate() -> None:
    """Морфология не объединяет соседние кнопки с нормальным зазором."""
    image = blank_image()
    cv2.rectangle(image, (50, 270), (220, 340), (55, 175, 60), -1)
    cv2.rectangle(image, (235, 270), (405, 340), (55, 175, 60), -1)
    candidates = GreenButtonDetector().detect(encode_image(image)).candidates
    assert len(candidates) == 2
    assert candidates[0].bounds.left < candidates[1].bounds.left


@pytest.mark.parametrize(
    ("width", "height"),
    [(480, 800), (800, 480), (1080, 2400), (2160, 3840)],
)
def test_resolution_and_orientation(width: int, height: int) -> None:
    """Координаты верны в портрете, альбоме и на больших исходных снимках."""
    image = blank_image(width=width, height=height)
    x, y = width // 5, height // 3
    w, h = width // 2, max(40, height // 12)
    cv2.rectangle(image, (x, y), (x + w, y + h), (55, 175, 60), -1)
    candidate = GreenButtonDetector().detect(encode_image(image)).candidates[0]
    assert candidate.bounds.center.x == x + (w + 1) // 2
    assert candidate.bounds.center.y == y + (h + 1) // 2


@pytest.mark.parametrize(
    "data",
    [b"", b"not a PNG" * 10, b"\x89PNG\r\n\x1a\n" + b"x" * 32],
)
def test_invalid_png(data: bytes) -> None:
    """Неверные ответы не превращаются в корректный пустой экран."""
    with pytest.raises(ScreenshotError):
        decode_png(data)


def test_corrupt_png() -> None:
    """Правильная сигнатура не отменяет проверку декодирования."""
    with pytest.raises(ScreenshotError):
        decode_png(button_image()[:40])


def test_png_bomb_header() -> None:
    """Завышенные размеры отвергаются до выделения памяти декодером."""
    data = bytearray(button_image())
    data[16:20] = (100_000).to_bytes(4, "big")
    data[20:24] = (100_000).to_bytes(4, "big")
    with pytest.raises(ScreenshotError, match="размер"):
        decode_png(bytes(data))


def test_detection_does_not_change_png() -> None:
    """Обработка не модифицирует исходный материал для диагностики."""
    png = button_image()
    original = png
    detector = GreenButtonDetector()
    assert detector.detect(png) == detector.detect(png)
    assert png == original

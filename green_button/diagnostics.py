"""Диагностика после сценария; сохранение файлов не повторяет нажатие."""

import json
from dataclasses import asdict
from pathlib import Path

import cv2

from .models import Candidate, RunTrace
from .vision import Image, decode_png


def save_trace(directory: Path, trace: RunTrace, *, outcome: str, code: str) -> None:
    """Сохранить последний PNG, размеченный кадр и JSON без серийного номера телефона.

    Args:
        directory: Каталог, явно выбранный пользователем.
        trace: Наблюдения текущего запуска.
        outcome: Итоговое состояние, включая ошибку или отмену.
        code: Машинный код результата.

    Raises:
        OSError: Каталог или файл недоступен для записи.
    """
    directory.mkdir(parents=True, exist_ok=True)
    report = {
        "outcome": outcome,
        "code": code,
        "stage": trace.stage,
        "elapsed_seconds": round(trace.elapsed_seconds, 6),
        "frames": trace.frames,
        "tap_attempted": trace.tap_attempted,
        "selected": asdict(trace.selected) if trace.selected is not None else None,
        "candidates": [asdict(candidate) for candidate in trace.candidates],
    }
    (directory / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if trace.last_png is not None:
        (directory / "last.png").write_bytes(trace.last_png)
        _save_annotation(directory / "annotated.png", trace)


def _save_annotation(path: Path, trace: RunTrace) -> None:
    """Нанести границы, не изменяя исходный снимок; поддержать Unicode-пути Windows."""
    if trace.last_png is None:
        return
    image = decode_png(trace.last_png)
    _draw_candidates(image, trace)
    _write_png(path, image)


def _draw_candidates(image: Image, trace: RunTrace) -> None:
    """Обвести найденные области и отметить выбранную."""
    for candidate in trace.candidates:
        _draw_candidate(image, candidate, selected=candidate == trace.selected)


def _mark_color(*, selected: bool) -> tuple[int, int, int]:
    """Выбрать цвет рамки: выбранный кандидат отличается от остальных."""
    if selected:
        return (0, 0, 255)
    return (255, 0, 0)


def _draw_candidate(image: Image, candidate: Candidate, *, selected: bool) -> None:
    """Нарисовать прямоугольник и центр одного кандидата."""
    bounds = candidate.bounds
    color = _mark_color(selected=selected)
    cv2.rectangle(
        image,
        (bounds.left, bounds.top),
        (bounds.right - 1, bounds.bottom - 1),
        color,
        2,
    )
    cv2.drawMarker(
        image,
        (bounds.center.x, bounds.center.y),
        color,
        cv2.MARKER_CROSS,
        14,
        2,
    )


def _write_png(path: Path, image: Image) -> None:
    """Записать размеченный кадр в PNG."""
    encoded, data = cv2.imencode(".png", image)
    if not encoded:
        msg = "Не удалось закодировать диагностическое изображение."
        raise OSError(msg)
    path.write_bytes(data.tobytes())

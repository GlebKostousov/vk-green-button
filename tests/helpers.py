"""Типизированные часы, устройство и изображения для воспроизводимых тестов."""

from collections import deque
from collections.abc import Sequence

import cv2
import numpy as np
from green_button.deadline import Deadline
from green_button.errors import DeadlineError, GreenButtonError, TapUnknownError
from green_button.models import Point, WindowState
from green_button.process import CommandResult
from green_button.vision import Image

PACKAGE = "com.example.app"


class FakeClock:
    """Управляемое время без реальных sleep."""

    def __init__(self) -> None:
        """Начать отсчёт с ненулевой отметки."""
        self.value = 100.0

    def now(self) -> float:
        """Получить текущую отметку."""
        return self.value

    def sleep(self, seconds: float) -> None:
        """Переместить часы на заданную длительность."""
        self.value += seconds


def blank_image(*, width: int = 480, height: int = 800) -> Image:
    """Создать светлый экран известного разрешения."""
    return np.full((height, width, 3), 245, dtype=np.uint8)


def encode_image(image: Image) -> bytes:
    """Закодировать реальный PNG, не подменяя декодер OpenCV."""
    valid, buffer = cv2.imencode(".png", image)
    assert valid
    return buffer.tobytes()


def button_image(*, left: int = 70, top: int = 270) -> bytes:
    """Нарисовать залитую зелёную кнопку на исходном экране."""
    image = blank_image()
    cv2.rectangle(image, (left, top), (left + 300, top + 70), (55, 175, 60), -1)
    return encode_image(image)


class FakeDevice:
    """Телефон с очередями кадров и фокуса; последний ответ повторяется."""

    def __init__(self, clock: FakeClock, frames: Sequence[bytes]) -> None:
        """Сохранить сценарий и журнал реально вызванных действий."""
        self.clock = clock
        self.frames = deque(frames)
        self.windows = deque([WindowState(PACKAGE, rotation=0)])
        self.calls: list[str] = []
        self.taps: list[Point] = []
        self.durations: dict[str, float] = {
            "select": 0.05,
            "launch": 0.15,
            "window": 0.04,
            "screenshot": 0.04,
            "tap": 0.04,
        }
        self.errors: dict[str, GreenButtonError] = {}

    def _spend(self, action: str, deadline: Deadline) -> None:
        """Учесть длительность команды в том же бюджете, что использует workflow."""
        deadline.require(action)
        self.calls.append(action)
        self.clock.sleep(min(self.durations[action], deadline.remaining))
        if action in self.errors:
            raise self.errors[action]
        deadline.require(action)

    def select(self, deadline: Deadline) -> None:
        """Воспроизвести выбор устройства."""
        self._spend("select", deadline)

    def launch(self, package: str, deadline: Deadline) -> None:
        """Воспроизвести запуск запрошенного пакета."""
        assert package == PACKAGE
        self._spend("launch", deadline)

    def window(self, deadline: Deadline) -> WindowState:
        """Получить следующее состояние фокуса."""
        self._spend("window", deadline)
        if len(self.windows) > 1:
            return self.windows.popleft()
        return self.windows[0]

    def screenshot(self, deadline: Deadline) -> bytes:
        """Получить следующий снимок известной последовательности."""
        self._spend("screenshot", deadline)
        if len(self.frames) > 1:
            return self.frames.popleft()
        return self.frames[0]

    def tap(self, point: Point, deadline: Deadline) -> None:
        """Записать попытку до ожидания ответа, как реальный побочный эффект."""
        deadline.require("tap")
        self.taps.append(point)
        try:
            self._spend("tap", deadline)
        except DeadlineError as error:
            msg = "Результат нажатия неизвестен."
            raise TapUnknownError(msg) from error


class RecordingRunner:
    """Очередь ответов subprocess с проверяемыми аргументами и таймаутами."""

    def __init__(self, replies: Sequence[CommandResult | GreenButtonError]) -> None:
        """Подготовить ответы и пустой журнал вызовов."""
        self.replies = deque(replies)
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(self, arguments: Sequence[str], *, timeout: float) -> CommandResult:
        """Вернуть очередной ответ или выбросить заданную ошибку транспорта."""
        self.calls.append((tuple(arguments), timeout))
        reply = self.replies.popleft()
        if isinstance(reply, GreenButtonError):
            raise reply
        return reply

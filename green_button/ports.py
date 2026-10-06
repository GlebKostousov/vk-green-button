"""Контракт единственной внешней системы — Android-устройства."""

from typing import Protocol

from .deadline import Deadline
from .models import Point, WindowState


class AndroidDevice(Protocol):
    """Операции устройства без подробностей команд и формата dumpsys."""

    def select(self, deadline: Deadline) -> None:
        """Выбрать одно готовое устройство или сообщить об ошибке."""
        ...

    def launch(self, package: str, deadline: Deadline) -> None:
        """Запустить launcher activity запрошенного пакета."""
        ...

    def window(self, deadline: Deadline) -> WindowState:
        """Прочитать активное окно и доступные системные области."""
        ...

    def screenshot(self, deadline: Deadline) -> bytes:
        """Получить PNG исходного разрешения без текстовых преобразований."""
        ...

    def tap(self, point: Point, deadline: Deadline) -> None:
        """Отправить одну команду нажатия без автоматического повтора."""
        ...

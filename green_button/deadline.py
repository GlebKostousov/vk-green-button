"""Единый монотонный дедлайн, включая резерв на финальную проверку и tap."""

import math
import time
from dataclasses import dataclass
from typing import Protocol

from .errors import ConfigurationError, DeadlineError


class Clock(Protocol):
    """Часы, которые можно заменить без реального ожидания в тестах."""

    def now(self) -> float:
        """Получить монотонное время в секундах."""
        ...

    def sleep(self, seconds: float) -> None:
        """Приостановить наблюдение на указанное время."""
        ...


class SystemClock:
    """Монотонные часы процесса."""

    def now(self) -> float:
        """Получить время, не зависящее от перевода системных часов."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Выполнить ограниченную паузу между наблюдениями."""
        time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class Deadline:
    """Абсолютный момент окончания, а не новый таймаут каждой команды."""

    started_at: float
    expires_at: float
    clock: Clock

    @classmethod
    def start(cls, seconds: float, *, clock: Clock) -> "Deadline":
        """Начать бюджет; ноль, NaN и бесконечность запрещены."""
        if not math.isfinite(seconds) or seconds <= 0:
            msg = "Таймаут должен быть конечным и положительным."
            raise ConfigurationError(msg)
        started_at = clock.now()
        return cls(started_at, started_at + seconds, clock)

    @property
    def remaining(self) -> float:
        """Получить неотрицательный остаток бюджета."""
        return max(0.0, self.expires_at - self.clock.now())

    @property
    def elapsed(self) -> float:
        """Получить время от начала общего сценария."""
        return max(0.0, self.clock.now() - self.started_at)

    def require(self, operation: str) -> float:
        """Вернуть таймаут следующего действия или запретить его запуск.

        Raises:
            DeadlineError: Времени на новое действие больше нет.
        """
        remaining = self.remaining
        if remaining <= 0:
            msg = f"Истёк общий таймаут: {operation}."
            raise DeadlineError(msg)
        return remaining

    def reserve(self, seconds: float) -> "Deadline":
        """Ограничить поиск тем же дедлайном, оставив время на нажатие."""
        if not math.isfinite(seconds) or seconds < 0:
            msg = "Резерв должен быть конечным и неотрицательным."
            raise ConfigurationError(msg)
        return Deadline(self.started_at, self.expires_at - seconds, self.clock)

    def pause(self, seconds: float) -> None:
        """Не переносить обычную паузу за границу доступного бюджета."""
        duration = min(max(0.0, seconds), self.remaining)
        if duration > 0:
            self.clock.sleep(duration)

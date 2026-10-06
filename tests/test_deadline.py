"""Единый бюджет, резерв и граничные значения времени."""

import pytest
from green_button.deadline import Deadline
from green_button.errors import ConfigurationError, DeadlineError
from green_button.models import Bounds, Point

from .helpers import FakeClock


def test_deadline_does_not_reset() -> None:
    """Остаток уменьшается между действиями, а не начинается заново."""
    clock = FakeClock()
    deadline = Deadline.start(10, clock=clock)
    clock.sleep(7)
    assert deadline.require("операция") == 3
    assert deadline.elapsed == 7
    clock.sleep(3)
    with pytest.raises(DeadlineError):
        deadline.require("tap")


def test_reserve_uses_same_origin() -> None:
    """Поиск заканчивается раньше, но оба дедлайна имеют одни часы и начало."""
    clock = FakeClock()
    deadline = Deadline.start(10, clock=clock)
    search = deadline.reserve(0.5)
    clock.sleep(9.5)
    assert search.remaining == 0
    assert deadline.remaining == 0.5
    assert search.elapsed == deadline.elapsed


def test_sleep_is_bounded() -> None:
    """Пауза не расходует больше доступного остатка."""
    clock = FakeClock()
    deadline = Deadline.start(1, clock=clock)
    deadline.pause(100)
    assert deadline.elapsed == 1
    deadline.pause(100)
    assert deadline.elapsed == 1


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_deadline(seconds: float) -> None:
    """Неконечные и неположительные бюджеты запрещены."""
    with pytest.raises(ConfigurationError):
        Deadline.start(seconds, clock=FakeClock())


@pytest.mark.parametrize("reserve", [-1, float("nan"), float("inf")])
def test_invalid_reserve(reserve: float) -> None:
    """Неверный резерв не увеличивает и не ломает бюджет."""
    with pytest.raises(ConfigurationError):
        Deadline.start(10, clock=FakeClock()).reserve(reserve)


def test_bounds_and_iou() -> None:
    """Геометрия использует полуоткрытые границы без деления на ноль."""
    bounds = Bounds(0, 0, 20, 10)
    assert bounds.center == Point(10, 5)
    assert bounds.iou(bounds) == 1
    assert bounds.intersection_area(Bounds(20, 0, 30, 10)) == 0
    with pytest.raises(ValueError, match="непустой прямоугольник"):
        Bounds(0, 0, 0, 1)

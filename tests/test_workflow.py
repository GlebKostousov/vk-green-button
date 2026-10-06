"""Сценарии времени и побочных эффектов на реальных синтетических PNG."""

from collections import deque

import pytest
from green_button.errors import (
    AdbCommandError,
    ConfigurationError,
    DeadlineError,
    ScreenshotError,
    TapUnknownError,
    WindowError,
)
from green_button.models import Bounds, Outcome, RunTrace, WindowState
from green_button.workflow import ButtonWorkflow, WorkflowSettings

from .helpers import (
    PACKAGE,
    FakeClock,
    FakeDevice,
    blank_image,
    button_image,
    encode_image,
)


def test_success_requires_two_frames_and_one_tap() -> None:
    """Нажатие происходит только после двух наблюдений и финальной проверки."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    trace = RunTrace()
    result = ButtonWorkflow(device, clock=clock).run(PACKAGE, trace=trace)
    assert result.outcome is Outcome.TAPPED
    assert result.frames == 2
    assert len(device.taps) == 1
    assert device.calls == [
        "select",
        "launch",
        "window",
        "screenshot",
        "window",
        "screenshot",
        "window",
        "tap",
    ]
    assert trace.tap_attempted
    assert trace.elapsed_seconds == result.elapsed_seconds


def test_delayed_button() -> None:
    """Отсутствие кандидата на ранних кадрах — ожидание, а не ошибка."""
    clock = FakeClock()
    empty = encode_image(blank_image())
    device = FakeDevice(clock, [empty, empty, empty, button_image()])
    result = ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert result.outcome is Outcome.TAPPED
    assert result.frames == 5
    assert len(device.taps) == 1


def test_not_found_within_total_budget() -> None:
    """Пустой экран завершается без tap, включая время запуска в общий лимит."""
    clock = FakeClock()
    device = FakeDevice(clock, [encode_image(blank_image())])
    result = ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert result.outcome is Outcome.NOT_FOUND
    assert 9 <= result.elapsed_seconds <= 10
    assert not device.taps


def test_transient_candidate_is_not_clicked() -> None:
    """Кнопка, присутствовавшая на одном кадре, не подтверждается."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image(), encode_image(blank_image())])
    assert ButtonWorkflow(device, clock=clock).run(PACKAGE).outcome is Outcome.NOT_FOUND
    assert not device.taps


def test_moving_candidate_is_not_clicked() -> None:
    """Чередование двух позиций не проходит выбранный порог стабильности."""
    clock = FakeClock()
    frames = [button_image(left=40), button_image(left=100)] * 100
    device = FakeDevice(clock, frames)
    assert ButtonWorkflow(device, clock=clock).run(PACKAGE).outcome is Outcome.NOT_FOUND
    assert not device.taps


def test_foreign_foreground_never_captured() -> None:
    """Зелёный элемент лаунчера не анализируется как кнопка приложения."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.windows = deque([WindowState("com.android.launcher")])
    with pytest.raises(WindowError):
        ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert "screenshot" not in device.calls
    assert not device.taps


def test_focus_loss_before_tap_resets_confirmation() -> None:
    """После чужого финального окна нужны заново два безопасных наблюдения."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    target = WindowState(PACKAGE, rotation=0)
    device.windows = deque(
        [target, target, WindowState("com.android.settings"), target],
    )
    result = ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert result.frames == 4
    assert len(device.taps) == 1


def test_rotation_resets_confirmation() -> None:
    """Смена ориентации сбрасывает сопоставление даже при одинаковых размерах PNG."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.windows = deque(
        [WindowState(PACKAGE, rotation=0), WindowState(PACKAGE, rotation=1)],
    )
    result = ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert result.frames == 3


def test_system_bar_appearing_before_tap_blocks_it() -> None:
    """Новая системная область проверяется после второго кадра."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    target = WindowState(PACKAGE, rotation=0)
    blocked = WindowState(PACKAGE, (Bounds(0, 250, 480, 360),), rotation=0)
    device.windows = deque([target, target, blocked])
    assert ButtonWorkflow(device, clock=clock).run(PACKAGE).outcome is Outcome.NOT_FOUND
    assert not device.taps


@pytest.mark.parametrize(
    "state",
    [
        WindowState(None),
        WindowState(PACKAGE, display_id=1),
        WindowState(PACKAGE, keyboard_visible=True),
    ],
)
def test_unsafe_window(state: WindowState) -> None:
    """Неизвестный фокус, другой дисплей и клавиатура запрещают tap."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.windows = deque([state])
    with pytest.raises(WindowError):
        ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert not device.taps


def test_stale_coordinates_are_not_used() -> None:
    """Медленные захват и финальная проверка делают координаты устаревшими."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.durations["window"] = 0.7
    device.durations["screenshot"] = 0.7
    assert ButtonWorkflow(device, clock=clock).run(PACKAGE).outcome is Outcome.NOT_FOUND
    assert not device.taps


def test_launch_consumes_deadline() -> None:
    """Зависание запуска не оставляет ещё десять секунд на поиск."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.durations["launch"] = 12
    trace = RunTrace()
    with pytest.raises(DeadlineError):
        ButtonWorkflow(device, clock=clock).run(PACKAGE, trace=trace)
    assert trace.elapsed_seconds == pytest.approx(10)
    assert device.calls == ["select", "launch"]


def test_transport_failure_is_not_not_found() -> None:
    """Потеря устройства остаётся ошибкой транспорта."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.errors["screenshot"] = AdbCommandError("Устройство отключено")
    with pytest.raises(AdbCommandError):
        ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert not device.taps


def test_corrupt_capture_is_error() -> None:
    """Повреждённый снимок не выглядит пустым успешным наблюдением."""
    clock = FakeClock()
    device = FakeDevice(clock, [b"broken"])
    with pytest.raises(ScreenshotError):
        ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert not device.taps


def test_unknown_tap_is_not_retried() -> None:
    """Ошибка подтверждения нажатия выходит наружу после первой попытки."""
    clock = FakeClock()
    device = FakeDevice(clock, [button_image()])
    device.errors["tap"] = TapUnknownError("Ответ потерян")
    with pytest.raises(TapUnknownError):
        ButtonWorkflow(device, clock=clock).run(PACKAGE)
    assert len(device.taps) == 1
    assert device.calls[-1] == "tap"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_workflow_timeout(timeout: float) -> None:
    """Некорректные внутренние настройки не создают бесконечного запуска."""
    with pytest.raises(ConfigurationError):
        WorkflowSettings(timeout_seconds=timeout)

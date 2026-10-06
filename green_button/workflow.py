"""Запустить приложение, подтвердить визуального кандидата, отправить один tap."""

import logging
import math
from dataclasses import dataclass
from typing import Never

from .deadline import Clock, Deadline, SystemClock
from .errors import CommandTimeoutError, ConfigurationError, DeadlineError, WindowError
from .models import Candidate, Detection, Outcome, RunResult, RunTrace, WindowState
from .ports import AndroidDevice
from .validation import validate_package
from .vision import GreenButtonDetector

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkflowSettings:
    """Бюджет сценария и параметры подтверждения; CLI использует лимит 10 секунд."""

    timeout_seconds: float = 10.0
    poll_seconds: float = 0.12
    final_action_reserve: float = 0.5
    max_snapshot_age: float = 1.2
    stable_iou: float = 0.9

    def __post_init__(self) -> None:
        """Запретить бесконечный бюджет, busy loop и резерв больше таймаута."""
        self._check_times()
        self._check_reserve()
        self._check_iou()

    def _check_times(self) -> None:
        """Потребовать конечные положительные интервалы."""
        values = (
            self.timeout_seconds,
            self.poll_seconds,
            self.final_action_reserve,
            self.max_snapshot_age,
        )
        if any(not math.isfinite(value) or value <= 0 for value in values):
            msg = "Временные параметры должны быть конечными и положительными."
            raise ConfigurationError(msg)

    def _check_reserve(self) -> None:
        """Оставить время на последнее действие внутри общего лимита."""
        if self.final_action_reserve >= self.timeout_seconds:
            msg = "Резерв должен быть меньше общего таймаута."
            raise ConfigurationError(msg)

    def _check_iou(self) -> None:
        """Принять только осмысленный порог совпадения двух кадров."""
        if not 0 < self.stable_iou <= 1:
            msg = "IoU должен находиться в диапазоне (0, 1]."
            raise ConfigurationError(msg)


@dataclass(frozen=True, slots=True)
class Observation:
    """Кандидаты кадра вместе с консервативным временем начала его получения."""

    captured_at: float
    detection: Detection
    window: WindowState


def _stable_candidate(
    previous: Observation | None, current: Observation, threshold: float
) -> Candidate | None:
    """Сопоставить два кадра только при неизменных размере и ориентации."""
    if previous is None:
        return None
    old = previous.detection
    new = current.detection
    if (old.width, old.height, previous.window.rotation) != (
        new.width,
        new.height,
        current.window.rotation,
    ):
        return None
    return next(
        (
            candidate
            for candidate in new.candidates
            if any(
                candidate.bounds.iou(prior.bounds) >= threshold
                for prior in old.candidates
            )
        ),
        None,
    )


class ButtonWorkflow:
    """Один последовательный сценарий с явным владельцем часов и устройства."""

    def __init__(
        self,
        device: AndroidDevice,
        *,
        detector: GreenButtonDetector | None = None,
        clock: Clock | None = None,
        settings: WorkflowSettings | None = None,
    ) -> None:
        """Передать внешние зависимости без глобального состояния."""
        self._device = device
        self._detector = detector or GreenButtonDetector()
        self._clock = clock or SystemClock()
        self._settings = settings or WorkflowSettings()

    def run(self, package: str, *, trace: RunTrace | None = None) -> RunResult:
        """Выполнить сценарий; сбой инфраструктуры не означает отсутствие кнопки."""
        validate_package(package)
        trace = trace if trace is not None else RunTrace()
        deadline = Deadline.start(self._settings.timeout_seconds, clock=self._clock)
        try:
            trace.stage = "device"
            self._device.select(deadline)
            trace.stage = "launch"
            self._device.launch(package, deadline)
            return self._search(package, deadline, trace)
        finally:
            trace.elapsed_seconds = deadline.elapsed

    def _observe(
        self, package: str, deadline: Deadline, trace: RunTrace
    ) -> Observation | None:
        """Получить кадр только подтверждённого активного приложения."""
        trace.stage = "foreground"
        window = self._device.window(deadline)
        if not window.permits(package):
            return None
        trace.stage = "screenshot"
        captured_at = self._clock.now()
        png = self._device.screenshot(deadline)
        trace.last_png = png
        trace.stage = "detection"
        detection = self._detector.detect(png, excluded_regions=window.excluded_regions)
        trace.frames += 1
        trace.candidates = detection.candidates
        deadline.require("анализ кадра")
        logger.debug(
            "frame.observed frames=%d candidates=%d",
            trace.frames,
            len(detection.candidates),
        )
        return Observation(captured_at, detection, window)

    def _search(self, package: str, deadline: Deadline, trace: RunTrace) -> RunResult:
        """Наблюдать до конца поискового бюджета, не повторяя побочные эффекты."""
        search = deadline.reserve(self._settings.final_action_reserve)
        previous: Observation | None = None
        while search.remaining > 0:
            finished, previous = self._search_pass(
                package, search, deadline, trace, previous
            )
            if finished is not None:
                return finished
            search.pause(self._settings.poll_seconds)
        return self._button_absent(trace, deadline)

    def _search_pass(
        self,
        package: str,
        search: Deadline,
        deadline: Deadline,
        trace: RunTrace,
        previous: Observation | None,
    ) -> tuple[RunResult | None, Observation | None]:
        """Сделать один шаг поиска: кадр, совпадение или конец бюджета."""
        try:
            current = self._observe(package, search, trace)
        except (DeadlineError, CommandTimeoutError) as error:
            return self._stop_after_timeout(error, search, trace, deadline), None
        return self._consider(package, previous, current, deadline, trace)

    def _stop_after_timeout(
        self,
        error: DeadlineError | CommandTimeoutError,
        search: Deadline,
        trace: RunTrace,
        deadline: Deadline,
    ) -> RunResult:
        """Не выдавать обрыв связи за отсутствие кнопки, пока бюджет ещё есть."""
        if search.remaining > 0:
            raise error
        if trace.frames == 0:
            self._raise_without_frames(error, trace)
        return RunResult(Outcome.NOT_FOUND, deadline.elapsed, trace.frames)

    def _raise_without_frames(
        self, error: DeadlineError | CommandTimeoutError, trace: RunTrace
    ) -> Never:
        """Сообщить, что окно так и не подтвердилось, либо вернуть исходный сбой."""
        if trace.stage == "foreground":
            msg = "За отведённое время не подтверждено активное окно приложения."
            raise WindowError(msg) from error
        raise error

    def _consider(
        self,
        package: str,
        previous: Observation | None,
        current: Observation | None,
        deadline: Deadline,
        trace: RunTrace,
    ) -> tuple[RunResult | None, Observation | None]:
        """Нажать устойчивого кандидата или запомнить кадр для следующего сравнения."""
        if current is None:
            return None, None
        candidate = _stable_candidate(previous, current, self._settings.stable_iou)
        if candidate is None:
            return None, current
        return self._tap_or_drop(package, current, candidate, deadline, trace)

    def _tap_or_drop(
        self,
        package: str,
        current: Observation,
        candidate: Candidate,
        deadline: Deadline,
        trace: RunTrace,
    ) -> tuple[RunResult | None, Observation | None]:
        """Нажать один раз либо сбросить пару кадров, если финальная проверка не прошла."""
        if self._can_tap(package, current, candidate, deadline, trace):
            return self._tap(candidate, deadline, trace), None
        return None, None

    def _button_absent(self, trace: RunTrace, deadline: Deadline) -> RunResult:
        """Отличить пустой поиск окна от поиска, где кнопка так и не появилась."""
        if trace.frames == 0:
            msg = "За отведённое время не подтверждено активное окно приложения."
            raise WindowError(msg)
        return RunResult(Outcome.NOT_FOUND, deadline.elapsed, trace.frames)

    def _can_tap(
        self,
        package: str,
        observation: Observation,
        candidate: Candidate,
        deadline: Deadline,
        trace: RunTrace,
    ) -> bool:
        """Повторно проверить фокус, системные области и возраст координат."""
        trace.stage = "final_check"
        window = self._device.window(deadline)
        same_window = (
            window.permits(package) and window.rotation == observation.window.rotation
        )
        overlaps_bar = any(
            candidate.bounds.intersection_area(region) > 0
            for region in window.excluded_regions
        )
        age = self._clock.now() - observation.captured_at
        fresh = age <= self._settings.max_snapshot_age
        return same_window and not overlaps_bar and fresh

    def _tap(
        self, candidate: Candidate, deadline: Deadline, trace: RunTrace
    ) -> RunResult:
        """Один побочный эффект: исключения уходят наружу, а не обратно в цикл."""
        deadline.require("нажатие")
        trace.stage = "tap"
        trace.selected = candidate
        trace.tap_attempted = True
        self._device.tap(candidate.bounds.center, deadline)
        logger.info(
            "button.tapped x=%d y=%d",
            candidate.bounds.center.x,
            candidate.bounds.center.y,
        )
        return RunResult(Outcome.TAPPED, deadline.elapsed, trace.frames, candidate)

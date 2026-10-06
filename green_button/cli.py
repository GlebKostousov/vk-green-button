"""CLI: сборка зависимостей, сообщения, коды выхода и необязательная диагностика."""

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2

from .diagnostics import save_trace
from .errors import (
    ConfigurationError,
    GreenButtonError,
    ScreenshotError,
    TapUnknownError,
)
from .gateways.adb import AdbGateway
from .models import Outcome, Point, RunResult, RunTrace
from .process import CommandRunner, run_command
from .validation import validate_package
from .workflow import ButtonWorkflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Arguments:
    """Проверенные аргументы вместо распространения Namespace по приложению."""

    package: str
    serial: str | None
    adb: str
    debug_dir: Path | None
    verbose: bool


def _package_argument(value: str) -> str:
    """Перевести ошибку имени пакета в стандартную ошибку argparse."""
    try:
        return validate_package(value)
    except ConfigurationError as error:
        raise argparse.ArgumentTypeError(error.public_message) from error


def parse_arguments(argv: Sequence[str] | None = None) -> Arguments:
    """Разобрать CLI; пакет обязателен, общий лимит фиксирован на 10 секундах."""
    parser = argparse.ArgumentParser(
        description="Найти и нажать зелёную кнопку в Android. Лимит: 10 секунд."
    )
    parser.add_argument(
        "package",
        type=_package_argument,
        help="Имя пакета: com.example.app",
    )
    parser.add_argument("--serial", help="Устройство из adb devices -l")
    parser.add_argument(
        "--adb",
        default="adb",
        help="Бинарник adb или полный путь к нему",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        help="Сохранить последний кадр и result.json",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Диагностические сообщения в stderr",
    )
    parsed = parser.parse_args(argv)
    # argparse валидирует указанные type; Namespace остаётся только на границе CLI.
    return Arguments(
        parsed.package,
        parsed.serial,
        parsed.adb,
        parsed.debug_dir,
        parsed.verbose,
    )


def main(
    argv: Sequence[str] | None = None, *, runner: CommandRunner = run_command
) -> int:
    """Выполнить один запуск и вернуть документированный код завершения."""
    arguments = parse_arguments(argv)
    _configure_logging(verbose=arguments.verbose)
    trace = RunTrace()
    outcome, code = "error", "execution_failed"
    try:
        outcome, code, status = _execute(arguments, runner, trace)
    except KeyboardInterrupt:
        outcome, code, status = _cancelled(trace)
    finally:
        if arguments.debug_dir is not None:
            _export_diagnostics(arguments.debug_dir, trace, outcome=outcome, code=code)
    return status


def _configure_logging(*, verbose: bool) -> None:
    """Писать служебный журнал в stderr, не смешивая его с ответом сценария."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _execute(
    arguments: Arguments, runner: CommandRunner, trace: RunTrace
) -> tuple[str, str, int]:
    """Вернуть исход, код и статус после одного прохода сценария."""
    try:
        return _report(_workflow_result(arguments, runner, trace))
    except TapUnknownError as error:
        print(error.public_message, file=sys.stderr)
        return "unknown", error.code, 3
    except GreenButtonError as error:
        print(f"Ошибка [{error.code}]: {error.public_message}", file=sys.stderr)
        logger.debug("run.failed code=%s", error.code)
        return "error", error.code, 2


def _workflow_result(
    arguments: Arguments, runner: CommandRunner, trace: RunTrace
) -> RunResult:
    """Собрать шлюз и выполнить сценарий один раз."""
    device = AdbGateway(
        executable=arguments.adb,
        serial=arguments.serial,
        runner=runner,
    )
    return ButtonWorkflow(device).run(arguments.package, trace=trace)


def _report(result: RunResult) -> tuple[str, str, int]:
    """Напечатать короткий ответ и сопоставить его коду завершения."""
    if result.outcome is Outcome.NOT_FOUND:
        print("Зелёная кнопка не обнаружена за отведённое время (лимит 10 секунд).")
        return result.outcome.value, result.outcome.value, 1
    point = _selected_point(result)
    print(
        "Зелёная кнопка обнаружена. Команда нажатия выполнена: "
        f"({point.x}, {point.y}).",
    )
    return result.outcome.value, result.outcome.value, 0


def _selected_point(result: RunResult) -> Point:
    """Вернуть центр нажатой кнопки."""
    if result.selected is None:
        msg = "Результат TAPPED должен содержать выбранного кандидата."
        raise RuntimeError(msg)
    return result.selected.bounds.center


def _cancelled(trace: RunTrace) -> tuple[str, str, int]:
    """Отличить отмену до нажатия от нажатия с неизвестным итогом."""
    if trace.tap_attempted:
        print(
            "Нажатие прервано; результат неизвестен. Повтор не выполнялся.",
            file=sys.stderr,
        )
        return "unknown", "tap_interrupted", 3
    print("Выполнение прервано пользователем.", file=sys.stderr)
    return "cancelled", "interrupted", 130


def _export_diagnostics(
    directory: Path, trace: RunTrace, *, outcome: str, code: str
) -> None:
    """Не подменять результат сценария ошибкой необязательного экспорта."""
    try:
        save_trace(directory, trace, outcome=outcome, code=code)
    except (OSError, ScreenshotError, cv2.error) as error:
        logger.warning("diagnostics.failed code=%s", type(error).__name__)

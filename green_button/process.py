"""Запуск локального процесса: бинарные потоки, конечный таймаут, cleanup."""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import CommandStartError, CommandTimeoutError


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Результат процесса без преобразования stdout в текст."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class CommandRunner(Protocol):
    """Заменяемая граница subprocess для проверки точных аргументов."""

    def __call__(self, arguments: Sequence[str], *, timeout: float) -> CommandResult:
        """Выполнить команду с указанным остатком бюджета."""
        ...


def run_command(arguments: Sequence[str], *, timeout: float) -> CommandResult:
    """Запустить процесс без локального shell и дождаться его завершения.

    Raises:
        CommandStartError: Процесс не удалось создать.
        CommandTimeoutError: Процесс не ответил; subprocess остановил его.
    """
    try:
        completed = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        msg = "Команда ADB не ответила в пределах оставшегося времени."
        raise CommandTimeoutError(msg) from error
    except OSError as error:
        msg = "Не удалось запустить ADB. Проверьте Platform Tools и путь --adb."
        raise CommandStartError(
            msg,
            detail=str(error),
        ) from error
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)

"""ADB gateway: запуск приложения, снимок, фокус и одно нажатие."""

import shlex
import shutil
from collections.abc import Sequence
from pathlib import Path

from green_button.deadline import Deadline
from green_button.errors import (
    AdbCommandError,
    CommandStartError,
    CommandTimeoutError,
    ConfigurationError,
    DeadlineError,
    TapUnknownError,
)
from green_button.models import Point, WindowState
from green_button.process import CommandResult, CommandRunner, run_command
from green_button.validation import validate_package

from ._parsing import (
    check_launch,
    choose_device,
    parse_component,
    parse_devices,
    parse_window,
)


def _require_adb_success(result: CommandResult, operation: str) -> None:
    """Превратить ненулевой код ADB в ошибку транспорта, не в «кнопки нет»."""
    if result.returncode != 0:
        msg = f"Ошибка ADB: {operation}."
        raise AdbCommandError(
            msg,
            detail=result.stderr.decode("utf-8", errors="replace"),
        )


class AdbGateway:
    """Состояние одного выбранного устройства и инжектируемый subprocess."""

    def __init__(
        self,
        *,
        executable: str = "adb",
        serial: str | None = None,
        runner: CommandRunner = run_command,
    ) -> None:
        """Настроить транспорт; обнаружение бинарника выполняется внутри бюджета."""
        self._requested_executable = executable
        self._requested_serial = serial
        self._executable: str | None = None
        self._serial: str | None = None
        self._runner = runner

    def _execute(
        self,
        arguments: Sequence[str],
        deadline: Deadline,
        *,
        operation: str,
        device: bool = True,
    ) -> CommandResult:
        """Передать команде остаток времени; не продлевать истёкший бюджет."""
        timeout = deadline.require(operation)
        result = self._runner(self._command(arguments, device=device), timeout=timeout)
        _require_adb_success(result, operation)
        deadline.require(operation)
        return result

    def _command(self, arguments: Sequence[str], *, device: bool) -> list[str]:
        """Собрать argv: глобальные команды не получают -s."""
        executable = self._required_executable()
        if not device:
            return [executable, *arguments]
        return [executable, "-s", self._required_serial(), *arguments]

    def _required_executable(self) -> str:
        """Вернуть уже найденный бинарник ADB."""
        if self._executable is None:
            msg = "ADB ещё не подготовлен: сначала вызовите select."
            raise ConfigurationError(msg)
        return self._executable

    def _required_serial(self) -> str:
        """Вернуть уже выбранный серийный номер."""
        if self._serial is None:
            msg = "Устройство ещё не выбрано."
            raise ConfigurationError(msg)
        return self._serial

    def _shell(
        self, arguments: Sequence[str], deadline: Deadline, *, operation: str
    ) -> str:
        """Экранировать удалённый shell; локальный shell не запускается."""
        result = self._execute(
            ("shell", shlex.join(arguments)), deadline, operation=operation
        )
        return (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace")

    def select(self, deadline: Deadline) -> None:
        """Найти ADB и выбрать конкретное готовое устройство."""
        deadline.require("подготовка ADB")
        path = shutil.which(self._requested_executable)
        if path is None:
            msg = "ADB не найден. Установите Platform Tools или задайте --adb."
            raise CommandStartError(
                msg,
            )
        if Path(path).suffix.lower() in {".bat", ".cmd"}:
            msg = "Укажите бинарник adb, а не командный файл .bat/.cmd."
            raise ConfigurationError(
                msg,
            )
        self._executable = path
        output = self._execute(
            ("devices", "-l"), deadline, operation="список устройств", device=False
        )
        devices = parse_devices(output.stdout.decode("utf-8", errors="replace"))
        self._serial = choose_device(devices, self._requested_serial)

    def launch(self, package: str, deadline: Deadline) -> None:
        """Определить launcher Activity и запустить её без monkey и force-stop."""
        validate_package(package)
        output = self._shell(
            (
                "cmd",
                "package",
                "resolve-activity",
                "--components",
                "--user",
                "current",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
                "-p",
                package,
            ),
            deadline,
            operation="определение стартовой Activity",
        )
        component = parse_component(output, package)
        output = self._shell(
            (
                "am",
                "start",
                "-W",
                "--user",
                "current",
                "-n",
                component,
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
            ),
            deadline,
            operation="запуск приложения",
        )
        check_launch(output)

    def window(self, deadline: Deadline) -> WindowState:
        """Прочитать системную диагностику, не используя дерево UI-элементов."""
        output = self._shell(("dumpsys", "window"), deadline, operation="проверка окна")
        return parse_window(output)

    def screenshot(self, deadline: Deadline) -> bytes:
        """Получить исходный PNG напрямую в память через exec-out."""
        return self._execute(
            ("exec-out", "screencap", "-p"), deadline, operation="получение скриншота"
        ).stdout

    def tap(self, point: Point, deadline: Deadline) -> None:
        """Выполнить одну попытку tap; неопределённый результат не повторять.

        Raises:
            TapUnknownError: Команда уже могла дойти, но подтверждения в бюджете нет.
            DeadlineError: Бюджет истёк до начала попытки.
        """
        if point.x < 0 or point.y < 0:
            msg = "Координаты нажатия не могут быть отрицательными."
            raise ConfigurationError(msg)
        deadline.require("отправка нажатия")
        try:
            self._shell(
                ("input", "tap", str(point.x), str(point.y)),
                deadline,
                operation="отправка нажатия",
            )
        except (AdbCommandError, CommandTimeoutError, DeadlineError) as error:
            msg = (
                "Результат нажатия неизвестен. Команда могла дойти до телефона; "
                "повтор не выполнялся."
            )
            raise TapUnknownError(
                msg,
                detail=error.code,
            ) from error

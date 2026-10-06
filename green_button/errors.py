"""Ошибки сценария с публичным сообщением и отдельной диагностикой."""


class GreenButtonError(Exception):
    """Базовая ошибка с безопасным сообщением для командной строки."""

    code = "execution_failed"

    def __init__(self, public_message: str, *, detail: str = "") -> None:
        """Сохранить сообщение и ограниченную техническую диагностику."""
        super().__init__(public_message)
        self.public_message = public_message
        self.detail = detail[:1000]


class ConfigurationError(GreenButtonError):
    """Неверные аргументы или настройки."""

    code = "invalid_configuration"


class DeadlineError(GreenButtonError):
    """Общий бюджет времени исчерпан до следующего действия."""

    code = "deadline_exceeded"


class CommandStartError(GreenButtonError):
    """Локальный процесс не удалось запустить."""

    code = "adb_unavailable"


class CommandTimeoutError(GreenButtonError):
    """Запущенная команда не ответила в пределах своего бюджета."""

    code = "adb_timeout"


class AdbCommandError(GreenButtonError):
    """ADB или системная команда Android завершилась с ошибкой."""

    code = "adb_command_failed"


class DeviceError(GreenButtonError):
    """Нельзя однозначно выбрать готовое устройство."""

    code = "device_unavailable"


class LaunchError(GreenButtonError):
    """Указанный пакет нельзя запустить как приложение."""

    code = "launch_failed"


class WindowError(GreenButtonError):
    """Нельзя безопасно установить принадлежность активного окна."""

    code = "foreground_unconfirmed"


class ScreenshotError(GreenButtonError):
    """Ответ телефона не является пригодным снимком экрана."""

    code = "invalid_screenshot"


class TapUnknownError(GreenButtonError):
    """Нажатие могло дойти до телефона; повторять команду нельзя."""

    code = "tap_outcome_unknown"

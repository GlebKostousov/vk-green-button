"""Валидация параметров сценария до обращения к внешней системе."""

import re

from .errors import ConfigurationError

PACKAGE_PATTERN = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+"
_MAX_PACKAGE_LENGTH = 255


def validate_package(package: str) -> str:
    """Принять только имя из Android-сегментов, а не текст shell-команды.

    Raises:
        ConfigurationError: Имя содержит недопустимые символы или слишком длинное.
    """
    if (
        len(package) > _MAX_PACKAGE_LENGTH
        or re.fullmatch(PACKAGE_PATTERN, package) is None
    ):
        msg = "Некорректное имя пакета. Пример: com.example.app."
        raise ConfigurationError(msg)
    return package

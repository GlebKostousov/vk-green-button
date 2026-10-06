"""Разбор текстовых контрактов Android без запуска команд."""

import re

from green_button.errors import DeviceError, LaunchError, WindowError
from green_button.models import Bounds, WindowState
from green_button.validation import PACKAGE_PATTERN

_COMPONENT = re.compile(rf"(?P<package>{PACKAGE_PATTERN})/[A-Za-z0-9_.$]+")
_FOCUS = re.compile(r"^\s*mCurrentFocus=(.+)$", re.MULTILINE)
_FRAME = re.compile(r"(?:mFrame|frame)=\[(-?\d+),\s*(-?\d+)\]\[(-?\d+),\s*(-?\d+)\]")
_RECT = re.compile(
    r"(?:mFrame|frame)=Rect\((-?\d+),\s*(-?\d+)\s*-\s*(-?\d+),\s*(-?\d+)\)"
)
_BAR_TYPE = re.compile(
    r"\b(?:mType|type)=(statusBars|navigationBars|"
    r"ITYPE_STATUS_BAR|ITYPE_NAVIGATION_BAR)\b"
)
_VISIBLE = re.compile(r"\b(?:mVisible|visible)=true\b")
_IME_TYPE = re.compile(r"\b(?:mType|type)=(?:ime|ITYPE_IME)\b")


_DEVICE_HEADER = "List of devices attached"
_MIN_DEVICE_COLUMNS = 2


def parse_devices(output: str) -> dict[str, str]:
    """Вернуть состояния устройств, не путая отсутствие с неверным ответом."""
    lines = output.strip().splitlines()
    _require_device_header(lines)
    return _device_states(lines[1:])


def _require_device_header(lines: list[str]) -> None:
    """Потребовать штатный заголовок adb devices."""
    if not lines or lines[0].strip() != _DEVICE_HEADER:
        msg = "ADB вернул неизвестный формат списка устройств."
        raise DeviceError(msg)


def _device_states(lines: list[str]) -> dict[str, str]:
    """Собрать серийные номера и их состояния."""
    devices: dict[str, str] = {}
    for line in lines:
        row = _device_row(line)
        if row is not None:
            serial, state = row
            devices[serial] = state
    return devices


def _device_row(line: str) -> tuple[str, str] | None:
    """Вернуть серийный номер и состояние одной строки списка."""
    parts = line.split()
    if len(parts) < _MIN_DEVICE_COLUMNS:
        return None
    return parts[0], parts[1]


def choose_device(devices: dict[str, str], serial: str | None) -> str:
    """Выбрать единственный device; unauthorized и offline не готовы."""
    if serial is not None:
        return _named_device(devices, serial)
    return _only_ready_device(devices)


def _named_device(devices: dict[str, str], serial: str) -> str:
    """Принять только явно запрошенное готовое устройство."""
    if devices.get(serial) != "device":
        msg = "Выбранное устройство не готово. Проверьте adb devices -l."
        raise DeviceError(msg)
    return serial


def _only_ready_device(devices: dict[str, str]) -> str:
    """Взять единственное готовое устройство либо объяснить, почему его нет."""
    ready = [name for name, state in devices.items() if state == "device"]
    if len(ready) == 1:
        return ready[0]
    return _missing_ready_device(devices, ready)


def _missing_ready_device(devices: dict[str, str], ready: list[str]) -> str:
    """Различить несколько телефонов и отсутствие готового."""
    if ready:
        msg = "Подключено несколько устройств. Укажите --serial."
        raise DeviceError(msg)
    return _unavailable_device(devices)


def _unavailable_device(devices: dict[str, str]) -> str:
    """Подсказать про ключ или про подключение, если готовых устройств нет."""
    if "unauthorized" in devices.values():
        msg = "Подтвердите RSA-ключ компьютера на разблокированном телефоне."
        raise DeviceError(msg)
    msg = "Нет готового устройства. Проверьте USB и adb devices -l."
    raise DeviceError(msg)


def parse_component(output: str, package: str) -> str:
    """Принять ровно одну launcher Activity из запрошенного пакета."""
    components = [line.strip() for line in output.splitlines()]
    valid = [value for value in components if _COMPONENT.fullmatch(value)]
    if len(valid) != 1 or valid[0].split("/", 1)[0] != package:
        msg = "Не найдена однозначная стартовая Activity указанного пакета."
        raise LaunchError(
            msg,
            detail=output,
        )
    return valid[0]


def check_launch(output: str) -> None:
    """Проверить Status у am start -W, включая ошибки с кодом возврата 0."""
    has_error = re.search(
        r"^\s*(?:Error|Exception|SecurityException)\b",
        output,
        re.MULTILINE,
    )
    status = re.search(r"^\s*Status:\s*(\S+)", output, re.MULTILINE)
    if has_error or status is None or status.group(1) != "ok":
        msg = "Android не подтвердил запуск приложения."
        raise LaunchError(msg, detail=output)


def _visible_regions(output: str) -> tuple[Bounds, ...]:
    """Собрать только явно видимые status/navigation InsetsSource."""
    regions = _bar_regions(output.splitlines())
    return tuple(sorted(regions, key=lambda region: (region.top, region.left)))


def _bar_regions(lines: list[str]) -> set[Bounds]:
    """Собрать прямоугольники видимых системных панелей."""
    regions: set[Bounds] = set()
    for line in lines:
        region = _bar_region(line)
        if region is not None:
            regions.add(region)
    return regions


def _bar_region(line: str) -> Bounds | None:
    """Вернуть рамку строки, если это видимая системная панель."""
    if not _is_visible_bar(line):
        return None
    return _frame_bounds(line)


def _is_visible_bar(line: str) -> bool:
    """Проверить тип панели и признак видимости."""
    return _BAR_TYPE.search(line) is not None and _VISIBLE.search(line) is not None


def _frame_bounds(line: str) -> Bounds | None:
    """Прочитать прямоугольник из mFrame или Rect."""
    match = _FRAME.search(line) or _RECT.search(line)
    if match is None:
        return None
    return _positive_bounds(match)


def _positive_bounds(match: re.Match[str]) -> Bounds | None:
    """Принять только непустой прямоугольник."""
    left, top, right, bottom = (int(value) for value in match.groups())
    if right <= left or bottom <= top:
        return None
    return Bounds(left, top, right, bottom)


def parse_window(output: str) -> WindowState:
    """Прочитать фокус, не подменяя mCurrentFocus запасным mFocusedApp.

    Системный диалог может перекрыть приложение, пока mFocusedApp всё ещё
    указывает на него. Неизвестный формат фокуса — ошибка, null — переход.

    Raises:
        WindowError: В ответе отсутствует однозначная запись mCurrentFocus.
    """
    focuses = set(_FOCUS.findall(output))
    if len(focuses) != 1:
        msg = "Не удалось однозначно прочитать активное окно Android."
        raise WindowError(msg)
    component = _COMPONENT.search(focuses.pop())
    display = re.search(r"\bmTopFocusedDisplayId=(\d+)", output)
    rotation = re.search(r"\bmRotation=(?:ROTATION_)?(\d+)\b", output)
    keyboard = any(
        _IME_TYPE.search(line) and _VISIBLE.search(line) for line in output.splitlines()
    )
    return WindowState(
        package=component.group("package") if component is not None else None,
        excluded_regions=_visible_regions(output),
        rotation=int(rotation.group(1)) if rotation is not None else None,
        display_id=int(display.group(1)) if display is not None else 0,
        keyboard_visible=keyboard,
    )

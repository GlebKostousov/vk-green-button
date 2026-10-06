"""Подставной ADB со стандартными потоками и журналом команд; Android не нужен."""

import json
import os
import shlex
import sys
from pathlib import Path


def respond(arguments: list[str], directory: Path, scenario: str) -> int:
    """Обработать только команды, предусмотренные контрактом gateway."""
    if arguments == ["devices", "-l"]:
        print("List of devices attached\nSERIAL\tdevice product:test")
        return 0
    if arguments[:2] != ["-s", "SERIAL"]:
        print("device selection required", file=sys.stderr)
        return 1
    return _device_command(arguments[2:], directory, scenario)


def _device_command(command: list[str], directory: Path, scenario: str) -> int:
    """Ответить на команду уже выбранного устройства."""
    if command == ["exec-out", "screencap", "-p"]:
        _write_screenshot(directory, scenario)
        return 0
    return _shell_or_reject(command, directory, scenario)


def _write_screenshot(directory: Path, scenario: str) -> None:
    """Отдать заранее подготовленный PNG."""
    filename = _screenshot_name(scenario)
    sys.stdout.buffer.write((directory / filename).read_bytes())


def _screenshot_name(scenario: str) -> str:
    """Выбрать пустой кадр или кадр с кнопкой."""
    if scenario == "none":
        return "empty.png"
    return "button.png"


def _shell_or_reject(command: list[str], directory: Path, scenario: str) -> int:
    """Передать shell-команду или отклонить неизвестный вызов."""
    if command[:1] == ["shell"]:
        return shell_response(shlex.split(command[1]), directory, scenario)
    print("unexpected command", file=sys.stderr)
    return 1


def shell_response(arguments: list[str], directory: Path, scenario: str) -> int:
    """Вернуть управляемые launcher/focus/tap ответы без чтения UI-дерева."""
    launch = _launch_response(arguments, scenario)
    if launch is not None:
        return launch
    return _input_response(arguments, directory, scenario)


def _launch_response(arguments: list[str], scenario: str) -> int | None:
    """Ответить на resolve-activity и am start."""
    if arguments[:3] == ["cmd", "package", "resolve-activity"]:
        print(_activity_name(scenario))
        return 0
    if arguments[:2] == ["am", "start"]:
        print("Status: ok\nActivity: com.example.app/.Main\nComplete")
        return 0
    return None


def _activity_name(scenario: str) -> str:
    """Вернуть стартовую Activity или сообщение об её отсутствии."""
    if scenario == "missing":
        return "No activity found"
    return "com.example.app/.Main"


def _input_response(arguments: list[str], directory: Path, scenario: str) -> int:
    """Ответить на dumpsys и input tap."""
    if arguments == ["dumpsys", "window"]:
        print("mCurrentFocus=Window{abc u0 com.example.app/.Main}\nmRotation=0")
        return 0
    if arguments[:2] == ["input", "tap"]:
        return _record_tap(arguments, directory, scenario)
    print("unexpected shell command", file=sys.stderr)
    return 1


def _record_tap(arguments: list[str], directory: Path, scenario: str) -> int:
    """Записать координаты нажатия и при необходимости оборвать подтверждение."""
    with (directory / "taps.txt").open("a", encoding="utf-8") as stream:
        stream.write(" ".join(arguments[2:]) + "\n")
    if scenario == "tap_error":
        print("connection lost after submission", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Прочитать тестовое окружение, записать argv и выдать один ответ."""
    directory = Path(os.environ["FAKE_ADB_DIR"])
    arguments = sys.argv[1:]
    with (directory / "commands.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(arguments) + "\n")
    return respond(arguments, directory, os.environ["FAKE_ADB_SCENARIO"])


if __name__ == "__main__":
    raise SystemExit(main())

"""Контракты ADB: состояние, launcher, focus, аргументы и неопределённый tap."""

import shlex
import sys

import pytest
from green_button.deadline import Deadline
from green_button.errors import (
    AdbCommandError,
    CommandStartError,
    CommandTimeoutError,
    ConfigurationError,
    DeadlineError,
    DeviceError,
    LaunchError,
    TapUnknownError,
    WindowError,
)
from green_button.gateways._parsing import (
    check_launch,
    choose_device,
    parse_component,
    parse_devices,
    parse_window,
)
from green_button.gateways.adb import AdbGateway
from green_button.models import Bounds, Point
from green_button.process import CommandResult
from green_button.validation import validate_package

from .helpers import PACKAGE, FakeClock, RecordingRunner, button_image

DEVICES = CommandResult(0, b"List of devices attached\nSERIAL\tdevice product:demo\n")


def test_parse_devices() -> None:
    """offline и unauthorized сохраняются отдельно от готового device."""
    devices = parse_devices(
        "List of devices attached\na device model:foo\nb offline\nc unauthorized\n",
    )
    assert devices == {"a": "device", "b": "offline", "c": "unauthorized"}
    assert choose_device(devices, None) == "a"
    assert choose_device(devices, "a") == "a"


@pytest.mark.parametrize("output", ["", "error: adb not found", "unrecognized output"])
def test_malformed_device_list(output: str) -> None:
    """Неизвестный ответ списка устройств не считается корректным пустым списком."""
    with pytest.raises(DeviceError):
        parse_devices(output)


@pytest.mark.parametrize(
    ("devices", "serial"),
    [
        ({}, None),
        ({"a": "unauthorized"}, None),
        ({"a": "offline"}, None),
        ({"a": "device", "b": "device"}, None),
        ({"a": "device"}, "b"),
    ],
)
def test_device_selection_errors(devices: dict[str, str], serial: str | None) -> None:
    """Ошибки выбора не приводят к команде на случайный телефон."""
    with pytest.raises(DeviceError):
        choose_device(devices, serial)


@pytest.mark.parametrize("package", ["com.example.app", "org.Example.app_2", "a.b"])
def test_valid_packages(package: str) -> None:
    """Допустимые имена не ограничены одним примером из README."""
    assert validate_package(package) == package


@pytest.mark.parametrize(
    "package",
    [
        "",
        "com.example;reboot",
        "com.x\nreboot",
        "com.$(id)",
        "--help",
        "com..app",
        "com.x/app",
        "a." + "b" * 254,
    ],
)
def test_shell_payloads_rejected(package: str) -> None:
    """Параметр пакета не превращается в произвольную удалённую команду."""
    with pytest.raises(ConfigurationError):
        validate_package(package)


@pytest.mark.parametrize(
    "component",
    ["com.example.app/.Main", "com.example.app/com.example.app.Main$Inner"],
)
def test_launcher_component(component: str) -> None:
    """Полные и сокращённые Activity принадлежат указанному пакету."""
    assert parse_component(component + "\n", PACKAGE) == component


@pytest.mark.parametrize(
    "output",
    [
        "No activity found",
        "",
        "com.other.app/.Main",
        "com.example.app/.Main;reboot",
        "com.example.app/.A\ncom.example.app/.B",
    ],
)
def test_bad_launcher_results(output: str) -> None:
    """Даже returncode=0 не подтверждает отсутствие или однозначность Activity."""
    with pytest.raises(LaunchError):
        parse_component(output, PACKAGE)


@pytest.mark.parametrize(
    "output",
    ["Status: ok\nComplete", "Warning: Activity not started\nStatus: ok\nComplete"],
)
def test_successful_launch_status(output: str) -> None:
    """Повторная доставка intent уже открытой Activity допустима."""
    check_launch(output)


@pytest.mark.parametrize(
    "output",
    [
        "Error: Activity not found\nStatus: ok",
        "Status: timeout",
        "Starting: Intent {}",
        "SecurityException: denied",
    ],
)
def test_launch_output_errors(output: str) -> None:
    """Ошибка am start не маскируется успешным кодом завершения процесса."""
    with pytest.raises(LaunchError):
        check_launch(output)


def test_parse_window_and_bars() -> None:
    """Поддерживаемый InsetsSource задаёт реальные видимые системные области."""
    state = parse_window("""
      mCurrentFocus=Window{abc u0 com.example.app/.Main}
      mTopFocusedDisplayId=0
      mRotation=ROTATION_90
      InsetsSource: {type=statusBars frame=[0,0][480,30] visible=true}
      InsetsSource: {type=navigationBars frame=[0,760][480,800] visible=true}
      InsetsSource: {type=statusBars frame=[0,0][480,90] visible=false}
    """)
    assert state.package == PACKAGE
    assert state.rotation == 90
    assert state.excluded_regions == (Bounds(0, 0, 480, 30), Bounds(0, 760, 480, 800))


def test_legacy_inset_frame() -> None:
    """Старый формат mType/mFrame разбирается без зависимости от Android grep."""
    state = parse_window("""
    mCurrentFocus=Window{abc u0 com.example.app/.Main}
    InsetsSource mType=ITYPE_STATUS_BAR mFrame=Rect(0, 0 - 480, 30) mVisible=true
    """)
    assert state.excluded_regions == (Bounds(0, 0, 480, 30),)


def test_focused_app_does_not_override_system_dialog() -> None:
    """mFocusedApp не разрешает tap за перекрывающим системным окном."""
    state = parse_window("""
    mCurrentFocus=Window{abc u0 NotificationShade}
    mFocusedApp=ActivityRecord{123 u0 com.example.app/.Main t1}
    """)
    assert state.package is None
    assert not state.permits(PACKAGE)


def test_null_focus_is_transient() -> None:
    """Переход без активного окна можно наблюдать дальше, но нажимать нельзя."""
    assert parse_window("mCurrentFocus=null\n").package is None


def test_unknown_focus_format_is_error() -> None:
    """Неизвестный формат не становится разрешением на нажатие."""
    with pytest.raises(WindowError):
        parse_window("mFocusedApp=ActivityRecord{123 com.example.app/.Main}")


def test_visible_keyboard_is_blocking() -> None:
    """Активная IME исключает ошибочное нажатие по перекрытому приложению."""
    state = parse_window(
        "mCurrentFocus=Window{abc u0 com.example.app/.Main}\n"
        "InsetsSource: {type=ime frame=[0,400][480,800] visible=true}",
    )
    assert state.keyboard_visible
    assert not state.permits(PACKAGE)


def test_adb_arguments_and_remaining_budget() -> None:
    """Каждая команда закреплена за serial и получает актуальный остаток времени."""
    runner = RecordingRunner(
        [
            DEVICES,
            CommandResult(0, b"com.example.app/.Main\n"),
            CommandResult(0, b"Status: ok\n"),
            CommandResult(0, button_image()),
            CommandResult(0),
        ],
    )
    clock = FakeClock()
    deadline = Deadline.start(10, clock=clock)
    gateway = AdbGateway(executable=sys.executable, runner=runner)
    gateway.select(deadline)
    clock.sleep(2)
    gateway.launch(PACKAGE, deadline)
    gateway.screenshot(deadline)
    gateway.tap(Point(123, 456), deadline)
    assert runner.calls[0][0][1:] == ("devices", "-l")
    assert all(call[0][1:3] == ("-s", "SERIAL") for call in runner.calls[1:])
    assert runner.calls[0][1] == 10
    assert all(call[1] == 8 for call in runner.calls[1:])
    resolve = shlex.split(runner.calls[1][0][-1])
    assert resolve[:3] == ["cmd", "package", "resolve-activity"]
    assert resolve[-2:] == ["-p", PACKAGE]
    assert shlex.split(runner.calls[-1][0][-1]) == ["input", "tap", "123", "456"]
    assert runner.calls[-2][0][-3:] == ("exec-out", "screencap", "-p")


@pytest.mark.parametrize(
    "failure",
    [CommandTimeoutError("timeout"), AdbCommandError("device lost")],
)
def test_tap_failure_is_unknown(failure: CommandTimeoutError | AdbCommandError) -> None:
    """Уже отправленный tap не повторяется при потере подтверждения."""
    runner = RecordingRunner([DEVICES, failure])
    deadline = Deadline.start(10, clock=FakeClock())
    gateway = AdbGateway(executable=sys.executable, runner=runner)
    gateway.select(deadline)
    with pytest.raises(TapUnknownError) as caught:
        gateway.tap(Point(1, 2), deadline)
    assert caught.value.__cause__ is failure
    assert len(runner.calls) == 2


def test_expired_deadline_prevents_tap_process() -> None:
    """При уже истёкшем бюджете subprocess для tap вообще не создаётся."""
    runner = RecordingRunner([DEVICES])
    clock = FakeClock()
    deadline = Deadline.start(10, clock=clock)
    gateway = AdbGateway(executable=sys.executable, runner=runner)
    gateway.select(deadline)
    clock.sleep(10)
    with pytest.raises(DeadlineError):
        gateway.tap(Point(1, 2), deadline)
    assert len(runner.calls) == 1


def test_nonzero_return_code() -> None:
    """stderr не теряется, но не подменяет понятное публичное сообщение."""
    runner = RecordingRunner([CommandResult(1, stderr=b"device offline")])
    gateway = AdbGateway(executable=sys.executable, runner=runner)
    with pytest.raises(AdbCommandError) as caught:
        gateway.select(Deadline.start(10, clock=FakeClock()))
    assert caught.value.detail == "device offline"


def test_missing_executable() -> None:
    """Отсутствие Platform Tools выявляется до первого внешнего вызова."""
    with pytest.raises(CommandStartError):
        AdbGateway(executable="missing-adb-7a263ad02").select(
            Deadline.start(10, clock=FakeClock()),
        )

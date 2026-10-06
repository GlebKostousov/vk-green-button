"""Пользовательские сообщения, exit codes и необязательный экспорт."""

import json
import sys
from pathlib import Path

import pytest
from green_button.cli import main, parse_arguments
from green_button.diagnostics import save_trace
from green_button.errors import CommandTimeoutError
from green_button.models import RunTrace
from green_button.process import CommandResult
from green_button.workflow import ButtonWorkflow

from .helpers import PACKAGE, RecordingRunner, button_image
from .test_adb import DEVICES

FOCUS = CommandResult(0, b"mCurrentFocus=Window{abc u0 com.example.app/.Main}\n")


def _success_replies() -> list[CommandResult]:
    """Собрать последовательность ответов для двух кадров и одного tap."""
    image = CommandResult(0, button_image())
    return [
        DEVICES,
        CommandResult(0, b"com.example.app/.Main\n"),
        CommandResult(0, b"Status: ok\n"),
        FOCUS,
        image,
        FOCUS,
        image,
        FOCUS,
        CommandResult(0),
    ]


def test_parse_options(tmp_path: Path) -> None:
    """CLI передаёт в сценарий типизированные значения, а не Namespace."""
    arguments = parse_arguments(
        [PACKAGE, "--serial", "s1", "--debug-dir", str(tmp_path)],
    )
    assert arguments.package == PACKAGE
    assert arguments.serial == "s1"
    assert arguments.debug_dir == tmp_path


def test_invalid_cli_package(capsys: pytest.CaptureFixture[str]) -> None:
    """Недопустимый пакет получает стандартный код ошибки argparse."""
    with pytest.raises(SystemExit) as caught:
        parse_arguments(["com.x;reboot"])
    assert caught.value.code == 2
    assert "Некорректное" in capsys.readouterr().err


def test_success_cli(capsys: pytest.CaptureFixture[str]) -> None:
    """Код 0 означает завершённую команду tap, а не обещание реакции приложения."""
    runner = RecordingRunner(_success_replies())
    assert main([PACKAGE, "--adb", sys.executable], runner=runner) == 0
    captured = capsys.readouterr()
    assert "(220, 305)" in captured.out
    assert not captured.err


def test_unknown_cli(capsys: pytest.CaptureFixture[str]) -> None:
    """Неопределённое нажатие имеет отдельный код и не переходит в retry."""
    replies = _success_replies()[:-1]
    runner = RecordingRunner([*replies, CommandTimeoutError("lost confirmation")])
    assert main([PACKAGE, "--adb", sys.executable], runner=runner) == 3
    assert "неизвестен" in capsys.readouterr().err
    assert len(runner.calls) == 9


def test_cli_environment_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Отсутствие ADB — ошибка, а не сообщение об отсутствии кнопки."""
    assert main([PACKAGE, "--adb", "missing-adb-7a263ad02"]) == 2
    assert "ADB не найден" in capsys.readouterr().err


def test_debug_directory_and_unicode_path(tmp_path: Path) -> None:
    """Диагностика сохраняется и в каталог с кириллицей, включая выбранную область."""
    output = tmp_path / "проверка кнопки"
    runner = RecordingRunner(_success_replies())
    assert (
        main(
            [PACKAGE, "--adb", sys.executable, "--debug-dir", str(output)],
            runner=runner,
        )
        == 0
    )
    report: object = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert isinstance(report, dict)
    assert report["outcome"] == "tapped"
    assert report["tap_attempted"] is True
    assert (output / "last.png").exists()
    assert (output / "annotated.png").exists()
    assert "SERIAL" not in (output / "result.json").read_text(encoding="utf-8")


def test_debug_write_error_does_not_retry_tap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ошибка необязательных файлов не меняет успешный tap и не повторяет его."""
    output = tmp_path / "occupied"
    output.write_text("not a directory", encoding="utf-8")
    runner = RecordingRunner(_success_replies())
    assert (
        main(
            [PACKAGE, "--adb", sys.executable, "--debug-dir", str(output)],
            runner=runner,
        )
        == 0
    )
    assert len(runner.calls) == 9
    captured = capsys.readouterr()
    assert "diagnostics.failed code=FileExistsError" in captured.err
    assert str(output) not in captured.err


def test_verbose_hides_raw_command_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Сырой stderr команды остаётся внутри ошибки и не попадает в журнал."""
    marker = "device offline raw-metadata-9f3c"
    runner = RecordingRunner([CommandResult(1, stderr=marker.encode())])
    assert (
        main(
            [PACKAGE, "--adb", sys.executable, "--verbose"],
            runner=runner,
        )
        == 2
    )
    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert "adb_command_failed" in captured.err
    assert "список устройств" in captured.err


def test_no_debug_files_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без явного флага снимки телефона не записываются на диск."""
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [PACKAGE, "--adb", sys.executable],
            runner=RecordingRunner(_success_replies()),
        )
        == 0
    )
    assert not list(tmp_path.iterdir())


def test_error_trace_without_frame(tmp_path: Path) -> None:
    """Ошибка до снимка оставляет JSON, но не выдуманное изображение."""
    trace = RunTrace(stage="launch")
    save_trace(tmp_path, trace, outcome="error", code="launch_failed")
    assert (tmp_path / "result.json").exists()
    assert not (tmp_path / "last.png").exists()


@pytest.mark.parametrize(("tap_attempted", "expected"), [(False, 130), (True, 3)])
def test_interrupt_result(
    tap_attempted: bool, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+C до tap — отмена; после начала попытки результат неопределён."""

    def interrupted(
        _self: ButtonWorkflow, _package: str, *, trace: RunTrace | None = None
    ) -> None:
        """Прервать сценарий в явно выбранной тестом фазе."""
        assert trace is not None
        trace.tap_attempted = tap_attempted
        raise KeyboardInterrupt

    monkeypatch.setattr(ButtonWorkflow, "run", interrupted)
    assert main([PACKAGE, "--adb", sys.executable]) == expected

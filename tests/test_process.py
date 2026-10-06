"""Проверки настоящего локального subprocess, включая остановку зависания."""

import os
import sys
from pathlib import Path

import pytest
from green_button.errors import CommandStartError, CommandTimeoutError
from green_button.process import run_command

pytestmark = pytest.mark.integration


def test_binary_stdout_and_stderr_are_separate() -> None:
    """stdout не проходит текстовое преобразование и не смешивается с ошибками."""
    script = (
        "import sys; sys.stdout.buffer.write(bytes(range(256))); "
        "sys.stderr.write('detail')"
    )
    result = run_command([sys.executable, "-S", "-c", script], timeout=3)
    assert result.returncode == 0
    assert result.stdout == bytes(range(256))
    assert result.stderr == b"detail"


def test_nonzero_exit_is_returned() -> None:
    """Низкоуровневый runner оставляет интерпретацию кода завершения gateway."""
    result = run_command(
        [sys.executable, "-S", "-c", "raise SystemExit(17)"],
        timeout=3,
    )
    assert result.returncode == 17


def test_missing_command_has_specific_error() -> None:
    """Ошибка создания процесса отличается от истечения его времени."""
    with pytest.raises(CommandStartError) as caught:
        run_command(["definitely-missing-adb-1ce7"], timeout=1)
    assert isinstance(caught.value.__cause__, OSError)


def test_hanging_child_is_killed_and_reaped(tmp_path: Path) -> None:
    """Таймаут реально останавливает дочерний процесс; на POSIX не остаётся zombie."""
    pid_file = tmp_path / "child pid.txt"
    script = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(30)"
    )
    with pytest.raises(CommandTimeoutError):
        run_command([sys.executable, "-S", "-c", script, str(pid_file)], timeout=2)
    assert pid_file.exists()
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text(encoding="utf-8")), 0)


def test_arguments_are_not_local_shell_commands() -> None:
    """Shell-метасимволы остаются обычным аргументом локального процесса."""
    payload = "hello; echo injected && $(whoami)"
    result = run_command(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; print(sys.argv[1])",
            payload,
        ],
        timeout=3,
    )
    assert result.stdout.decode().strip() == payload

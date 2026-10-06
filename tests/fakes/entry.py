"""CLI в отдельном процессе с явно подменённым транспортом ADB."""

import sys
from collections.abc import Sequence
from pathlib import Path

from green_button.cli import main
from green_button.process import CommandResult, run_command


def fake_runner(arguments: Sequence[str], *, timeout: float) -> CommandResult:
    """Выполнить тестовый ADB как настоящий подпроцесс на Linux и Windows."""
    script = Path(__file__).with_name("adb_process.py")
    return run_command(
        [sys.executable, "-S", str(script), *arguments[1:]],
        timeout=timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main(runner=fake_runner))

"""CLI → настоящий subprocess → подставной ADB → реальное распознавание PNG."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .helpers import PACKAGE, blank_image, button_image, encode_image

pytestmark = pytest.mark.integration


def _run_scenario(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[bytes]:
    """Запустить CLI и подставные процессы ADB с сохранением диагностики."""
    (tmp_path / "button.png").write_bytes(button_image())
    (tmp_path / "empty.png").write_bytes(encode_image(blank_image()))
    environment = {
        **os.environ,
        "FAKE_ADB_DIR": str(tmp_path),
        "FAKE_ADB_SCENARIO": scenario,
        "PYTHONUTF8": "1",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.fakes.entry",
            PACKAGE,
            "--adb",
            sys.executable,
            "--debug-dir",
            str(tmp_path / "diagnostics"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        check=False,
        timeout=15,
    )


def test_complete_subprocess_scenario(tmp_path: Path) -> None:
    """Полная цепочка выдаёт исходные координаты и ровно одну команду tap."""
    completed = _run_scenario(tmp_path, "success")
    assert completed.returncode == 0, completed.stderr.decode("utf-8")
    assert "(220, 305)" in completed.stdout.decode("utf-8")
    assert (tmp_path / "taps.txt").read_text(encoding="utf-8").splitlines() == [
        "220 305"
    ]
    assert (tmp_path / "diagnostics" / "annotated.png").exists()
    commands = (tmp_path / "commands.jsonl").read_text(encoding="utf-8")
    assert "monkey" not in commands
    assert "uiautomator" not in commands


def test_tap_disconnect_is_not_repeated(tmp_path: Path) -> None:
    """Потеря соединения после отправки tap не вызывает второй subprocess tap."""
    completed = _run_scenario(tmp_path, "tap_error")
    assert completed.returncode == 3
    assert "неизвестен" in completed.stderr.decode("utf-8")
    assert (tmp_path / "taps.txt").read_text(encoding="utf-8").splitlines() == [
        "220 305"
    ]


def test_missing_activity_with_successful_adb_exit(tmp_path: Path) -> None:
    """No activity found при returncode=0 остаётся ошибкой запуска."""
    completed = _run_scenario(tmp_path, "missing")
    assert completed.returncode == 2
    assert "launch_failed" in completed.stderr.decode("utf-8")
    assert not (tmp_path / "taps.txt").exists()


def test_absence_with_real_clock_and_processes(tmp_path: Path) -> None:
    """Пустой экран проверяется с настоящими часами; процессов tap не появляется."""
    started = time.monotonic()
    completed = _run_scenario(tmp_path, "none")
    elapsed = time.monotonic() - started
    assert completed.returncode == 1, completed.stderr.decode("utf-8")
    assert "не обнаружена" in completed.stdout.decode("utf-8")
    assert 9 <= elapsed < 13
    assert not (tmp_path / "taps.txt").exists()

"""Небольшие стражи архитектуры и совместимости синтаксиса с Python 3.12."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "green_button"


def test_python_312_syntax() -> None:
    """Все исходники разбираются грамматикой 3.12 даже при запуске теста на 3.13."""
    for path in SOURCE.rglob("*.py"):
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 12),
        )


def test_workflow_does_not_depend_on_adb_gateway() -> None:
    """Сценарий работает через порт и не разбирает системные ответы Android."""
    tree = ast.parse((SOURCE / "workflow.py").read_text(encoding="utf-8"))
    assert not _imports_gateway(tree)


def test_detector_has_no_io_dependencies() -> None:
    """Детектор не запускает команды, не спит и не сохраняет файлы."""
    forbidden = {"subprocess", "time", "pathlib", "os", "adb", "appium", "uiautomator"}
    tree = ast.parse((SOURCE / "vision.py").read_text(encoding="utf-8"))
    assert not forbidden.intersection(_import_roots(tree))


def test_no_future_annotations_or_any_imports() -> None:
    """Новый код не размывает типы и сохраняет соглашение об аннотациях проекта."""
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not _imports_future_or_any(tree)


def _imports_gateway(tree: ast.AST) -> bool:
    """Проверить, что модуль импортирует gateway."""
    return any(_is_gateway_import(node) for node in ast.walk(tree))


def _is_gateway_import(node: ast.AST) -> bool:
    """Узнать импорт из модуля gateway."""
    if not isinstance(node, ast.ImportFrom):
        return False
    return "gateway" in (node.module or "")


def _import_roots(tree: ast.AST) -> set[str]:
    """Собрать корневые имена импортов."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        roots.update(_import_root(node))
    return roots


def _import_root(node: ast.AST) -> set[str]:
    """Вернуть корневое имя одного узла импорта."""
    if isinstance(node, ast.Import):
        return {alias.name.split(".")[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {(node.module or "").split(".")[0]}
    return set()


def _imports_future_or_any(tree: ast.AST) -> bool:
    """Найти запрещённые импорты аннотаций и Any."""
    return any(_is_future_or_any(node) for node in ast.walk(tree))


def _is_future_or_any(node: ast.AST) -> bool:
    """Узнать запрещённый ImportFrom."""
    if not isinstance(node, ast.ImportFrom):
        return False
    return _future_or_any(node)


def _future_or_any(node: ast.ImportFrom) -> bool:
    """Проверить модуль __future__ и имя Any."""
    if node.module == "__future__":
        return True
    names = {alias.name for alias in node.names}
    return node.module == "typing" and "Any" in names

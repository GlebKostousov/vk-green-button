"""Нейтральные типы: координаты, наблюдения и результат сценария."""

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Point:
    """Точка в пикселях исходного снимка основного дисплея."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Bounds:
    """Прямоугольник с невключёнными правой и нижней границами."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        """Не допускать пустую или перевёрнутую геометрию."""
        if self.right <= self.left or self.bottom <= self.top:
            msg = "Границы должны задавать непустой прямоугольник."
            raise ValueError(msg)

    @property
    def width(self) -> int:
        """Получить ширину в пикселях."""
        return self.right - self.left

    @property
    def height(self) -> int:
        """Получить высоту в пикселях."""
        return self.bottom - self.top

    @property
    def area(self) -> int:
        """Получить площадь прямоугольника."""
        return self.width * self.height

    @property
    def center(self) -> Point:
        """Получить целочисленную точку внутри прямоугольника."""
        return Point(self.left + self.width // 2, self.top + self.height // 2)

    def intersection_area(self, other: "Bounds") -> int:
        """Измерить общую площадь двух прямоугольников."""
        width = max(0, min(self.right, other.right) - max(self.left, other.left))
        height = max(0, min(self.bottom, other.bottom) - max(self.top, other.top))
        return width * height

    def iou(self, other: "Bounds") -> float:
        """Сравнить геометрию через intersection over union."""
        common = self.intersection_area(other)
        return common / (self.area + other.area - common)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Визуальный кандидат; score не является вероятностью кликабельности."""

    bounds: Bounds
    score: float
    green_fraction: float
    solidity: float


@dataclass(frozen=True, slots=True)
class Detection:
    """Кандидаты, упорядоченные по оценке, и размер исходного изображения."""

    width: int
    height: int
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class WindowState:
    """Снимок фокуса и доступной геометрии системных панелей."""

    package: str | None
    excluded_regions: tuple[Bounds, ...] = ()
    rotation: int | None = None
    display_id: int = 0
    keyboard_visible: bool = False

    def permits(self, package: str) -> bool:
        """Разрешить работу только с целевым окном основного дисплея."""
        return (
            self.package == package
            and self.display_id == 0
            and not self.keyboard_visible
        )


class Outcome(StrEnum):
    """Штатные результаты; сбои передаются прикладными исключениями."""

    TAPPED = "tapped"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class RunResult:
    """Результат наблюдения, а не подтверждение бизнес-действия приложения."""

    outcome: Outcome
    elapsed_seconds: float
    frames: int
    selected: Candidate | None = None


@dataclass(slots=True)
class RunTrace:
    """Диагностика одного запуска; снимок остаётся в памяти до явного экспорта."""

    stage: str = "initialization"
    elapsed_seconds: float = 0.0
    frames: int = 0
    last_png: bytes | None = field(default=None, repr=False)
    candidates: tuple[Candidate, ...] = ()
    selected: Candidate | None = None
    tap_attempted: bool = False

"""
Общие фикстуры для тестов Phototrichogram.

Синтетические изображения (make_test_image, make_ruler_image) заменяют
реальные снимки с микроскопа и калибровочной шкалы — тесты детерминированы
и не зависят от файлов на диске или подключённой камеры.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest

from phototrichogram.data.database import Database
from phototrichogram.utils.config import AnalysisConfig

# ---------------------------------------------------------------------------
# Фабрики синтетических изображений
# ---------------------------------------------------------------------------


def make_test_image(
    width: int = 640,
    height: int = 480,
    n_thick: int = 3,
    n_thin: int = 2,
    bg_color: int = 220,
) -> np.ndarray:
    """Создаёт синтетический трихоскопический снимок с 'волосами'.

    Толстые линии (4px, цвет 30) устойчиво проходят через пайплайн
    сегментации (core/segmentation.py) и распознаются как отдельные
    контуры волос. Тонкие линии (1px, цвет 50) добавлены для
    реалистичности фона: контур линии шириной в 1px имеет площадь ~0
    (cv2.contourArea вырождается для линий толщиной в один пиксель),
    поэтому такие линии, как правило, НЕ формируют отдельных волос —
    это подтверждено в tests/test_segmentation.py.

    Args:
        width: Ширина изображения в пикселях.
        height: Высота изображения в пикселях.
        n_thick: Количество толстых линий.
        n_thin: Количество тонких линий.
        bg_color: Яркость фона (0-255).

    Returns:
        BGR изображение (uint8) размером (height, width, 3).
    """
    img = np.ones((height, width, 3), dtype=np.uint8) * bg_color

    for i in range(n_thick):
        x = 80 + i * 120
        cv2.line(img, (x, 30), (x + 10, height - 30), (30, 30, 30), 4)

    for i in range(n_thin):
        x = 140 + i * 120
        cv2.line(img, (x, 60), (x + 5, height - 60), (50, 50, 50), 1)

    return img


def make_ruler_image(
    width: int = 400,
    height: int = 100,
    spacing_px: int = 20,
    line_width: int = 2,
    bg: int = 255,
    fg: int = 0,
    margin: int = 10,
) -> np.ndarray:
    """Создаёт синтетический снимок микрометрической шкалы.

    Вертикальные тёмные полосы через равные интервалы имитируют деления
    калибровочной линейки. Расстояние между центрами соседних полос
    равно spacing_px — используется в tests/test_calibration.py как
    известное эталонное значение для проверки автокалибровки.

    Args:
        width: Ширина изображения.
        height: Высота изображения.
        spacing_px: Расстояние между делениями в пикселях.
        line_width: Ширина каждого деления в пикселях.
        bg: Яркость фона.
        fg: Яркость деления.
        margin: Отступ от левого края до первого деления.

    Returns:
        Grayscale изображение (uint8) размером (height, width).
    """
    img = np.ones((height, width), dtype=np.uint8) * bg
    x = margin
    while x + line_width <= width:
        img[:, x : x + line_width] = fg
        x += spacing_px
    return img


# ---------------------------------------------------------------------------
# Фикстуры изображений
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_image() -> np.ndarray:
    """Синтетический снимок с 'волосами' — параметры по умолчанию."""
    return make_test_image()


@pytest.fixture
def blank_image() -> np.ndarray:
    """Светлый снимок без волос — граничный случай (пустой кадр)."""
    return np.ones((480, 640, 3), dtype=np.uint8) * 255


@pytest.fixture
def ruler_image() -> np.ndarray:
    """Синтетический снимок калибровочной шкалы — параметры по умолчанию."""
    return make_ruler_image()


# ---------------------------------------------------------------------------
# Фикстуры конфигурации
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> AnalysisConfig:
    """Конфиг анализа с дефолтными параметрами (METHODOLOGY.md §7.1)."""
    return AnalysisConfig()


# ---------------------------------------------------------------------------
# Фикстуры базы данных
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path: Path) -> Iterator[Database]:
    """Временная SQLite БД в изолированной директории pytest tmp_path."""
    db = Database(tmp_path / "test.db")
    yield db
    db.close()

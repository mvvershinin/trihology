"""
Измерение параметров отдельного волоса по контуру.

Основной метод — MinFeret через MinAreaRect (METHODOLOGY.md §4.5):
- MinFeret (меньшая сторона) = толщина волоса
- MaxFeret (большая сторона) = длина волоса в кадре
- Угол наклона, площадь, периметр, центр масс

Все пересчёты пиксели → микроны через config.um_per_px.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from phototrichogram.utils.config import AnalysisConfig

logger = logging.getLogger(__name__)


@dataclass
class HairMeasurement:
    """Результат измерения одного волоса.

    Attributes:
        thickness_px: Толщина волоса в пикселях (MinFeret).
        thickness_um: Толщина волоса в микронах.
        length_px: Длина волоса в кадре в пикселях (MaxFeret).
        length_um: Длина волоса в кадре в микронах.
        area: Площадь контура в px^2.
        perimeter: Периметр контура в px.
        circularity: Форм-фактор (4*pi*area/perimeter^2). Круг=1, волос->0.
        center: Координаты центра (x, y) в пикселях.
        angle: Угол наклона волоса в градусах.
        contour: Массив точек контура для визуализации.
        classification: Тип волоса после классификации (заполняется позже).
    """

    thickness_px: float
    thickness_um: float
    length_px: float
    length_um: float
    area: float
    perimeter: float
    circularity: float
    center: tuple[float, float]
    angle: float
    contour: NDArray[np.int32]
    classification: str = ""


def measure_hair(
    contour: NDArray[np.int32], config: AnalysisConfig
) -> HairMeasurement | None:
    """Измерение параметров одного волоса по контуру.

    Алгоритм (METHODOLOGY.md §4.5):
    1. MinAreaRect — минимальный описанный повёрнутый прямоугольник
    2. MinFeret = min(width, height) — толщина
    3. MaxFeret = max(width, height) — длина в кадре
    4. Пересчёт px → мкм через config.um_per_px

    Args:
        contour: Контур волоса (массив точек от cv2.findContours).
        config: Параметры анализа (um_per_px для пересчёта).

    Returns:
        HairMeasurement с заполненными полями или None если контур невалиден
        (слишком мало точек или нулевой периметр).
    """
    if len(contour) < 5:
        logger.debug("Контур отброшен: слишком мало точек (%d)", len(contour))
        return None

    # MinAreaRect → повёрнутый прямоугольник
    rect = cv2.minAreaRect(contour)
    (cx, cy), (w, h), angle = rect

    # MinFeret = меньшая сторона, MaxFeret = большая
    min_feret = min(w, h)
    max_feret = max(w, h)

    if min_feret <= 0:
        logger.debug("Контур отброшен: MinFeret=0 в точке (%.0f, %.0f)", cx, cy)
        return None

    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, True)

    if perimeter <= 0:
        return None

    circularity = 4.0 * np.pi * area / (perimeter * perimeter)

    # Пересчёт в микроны
    thickness_um = min_feret * config.um_per_px
    length_um = max_feret * config.um_per_px

    return HairMeasurement(
        thickness_px=min_feret,
        thickness_um=thickness_um,
        length_px=max_feret,
        length_um=length_um,
        area=area,
        perimeter=perimeter,
        circularity=circularity,
        center=(cx, cy),
        angle=angle,
        contour=contour,
    )


def measure_all(
    contours: list[NDArray[np.int32]], config: AnalysisConfig
) -> list[HairMeasurement]:
    """Измерение параметров всех волос из списка контуров.

    Args:
        contours: Список контуров от find_hair_contours().
        config: Параметры анализа.

    Returns:
        Список HairMeasurement (только валидные, None отфильтрованы).
    """
    measurements: list[HairMeasurement] = []
    for contour in contours:
        m = measure_hair(contour, config)
        if m is not None:
            measurements.append(m)

    logger.debug(
        "Измерено %d из %d контуров",
        len(measurements),
        len(contours),
    )
    return measurements


def split_thick_bundles(
    measurements: list[HairMeasurement], config: AnalysisConfig
) -> tuple[list[HairMeasurement], list[HairMeasurement]]:
    """Разделение измерений на реальные волосы и слипшиеся пучки по толщине.

    Физический фильтр невозможных толщин: один волос не может быть толще
    config.max_hair_thickness_um. Контуры толще — это слипшиеся пучки соседних
    волос, которые сегментация объединила в один объект (METHODOLOGY.md §9,
    ограничение 3). Такие пучки завышают счёт, среднюю толщину и плотность,
    поэтому исключаются из всех дальнейших расчётов.

    Порог берётся из конфига (config.max_hair_thickness_um) — не захардкожен.
    Чтобы отключить фильтр, достаточно задать большой порог.

    Args:
        measurements: Список измерений от measure_all().
        config: Параметры анализа (max_hair_thickness_um — порог в мкм).

    Returns:
        Кортеж (hairs, bundles):
        - hairs — измерения с толщиной <= порога (реальные волосы);
        - bundles — измерения толще порога (слипшиеся пучки, исключаются).
    """
    threshold_um = config.max_hair_thickness_um
    hairs: list[HairMeasurement] = []
    bundles: list[HairMeasurement] = []

    for m in measurements:
        if m.thickness_um > threshold_um:
            bundles.append(m)
        else:
            hairs.append(m)

    if bundles:
        logger.debug(
            "Разделение по толщине: волос=%d, пучков>%.0f мкм=%d",
            len(hairs),
            threshold_um,
            len(bundles),
        )
    return hairs, bundles

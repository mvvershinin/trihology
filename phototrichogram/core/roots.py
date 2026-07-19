"""
Детекция «растущих» волос — подсчёт от корней (METHODOLOGY.md §4, план v1.4).

Плотность завышается транзитными волосами: волос лежит поперёк кадра, входит
и выходит через границы (растёт вне снятого участка), но считается наравне с
растущими здесь. Кадр — единицы мм² (при 5.9 мкм/px ≈ 3.8×2.8 мм), поэтому
таких «прохожих» много.

Признак растущего волоса: у контура есть конец, лежащий ВНУТРИ кадра (не
упирается в границу). Волос классифицируется как:
- ``rooted``  — хотя бы один конец дальше config.root_border_margin_px от всех
  четырёх краёв (виден корневой конец → волос растёт на участке);
- ``transit`` — ОБА конца у границы кадра (волос проходит насквозь).

Метод поиска концов: две крайние точки контура по наибольшему размаху —
пара точек выпуклой оболочки с максимальным попарным расстоянием (диаметр
множества). Это реальные точки контура (а не синтетические углы прямоугольника),
устойчивые к изгибу волоса: для вытянутого объекта диаметр всегда реализуется
на двух его концах. Клиппинг кадром работает в нашу пользу — cv2.findContours
обводит обрезанный волос вдоль края изображения, поэтому концы транзитного
волоса гарантированно лежат на границе (x=0/x=W-1 или y=0/y=H-1).

Все пороги — из AnalysisConfig, ничего не захардкожено. Модуль не зависит от
GUI или Data (только cv2/numpy/utils.config).
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
from numpy.typing import NDArray

from phototrichogram.core.measurement import HairMeasurement
from phototrichogram.utils.config import AnalysisConfig

logger = logging.getLogger(__name__)


def contour_endpoints(
    contour: NDArray[np.int32],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Две крайние точки контура по наибольшему размаху (диаметр).

    Возвращает пару точек контура с максимальным попарным евклидовым
    расстоянием — концы вытянутого волоса. Поиск идёт по вершинам выпуклой
    оболочки (диаметр множества всегда реализуется на них), что дёшево:
    вершин у оболочки волоса единицы-десятки. Расстояния считаются
    векторизованно (без циклов по точкам).

    Args:
        contour: Контур волоса (массив точек (N, 1, 2) от cv2.findContours).

    Returns:
        Кортеж ((x1, y1), (x2, y2)) — координаты двух концов в пикселях.
        Для вырожденного контура (< 2 точек) обе точки совпадают.
    """
    pts = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if pts.shape[0] < 2:
        single = (float(pts[0, 0]), float(pts[0, 1])) if pts.shape[0] == 1 else (0.0, 0.0)
        return single, single

    # Диаметр множества реализуется на вершинах выпуклой оболочки — берём их,
    # чтобы попарная матрица расстояний была маленькой.
    hull = cv2.convexHull(contour)
    hull_pts = np.asarray(hull, dtype=np.float64).reshape(-1, 2)
    if hull_pts.shape[0] >= 2:
        pts = hull_pts

    # Попарные квадраты расстояний: (K, K, 2) -> (K, K). Векторизованно.
    diff = pts[:, None, :] - pts[None, :, :]
    dist_sq = (diff * diff).sum(axis=2)

    flat_idx = int(np.argmax(dist_sq))
    idx = np.unravel_index(flat_idx, dist_sq.shape)
    i = int(idx[0])
    j = int(idx[1])

    end1 = (float(pts[i, 0]), float(pts[i, 1]))
    end2 = (float(pts[j, 0]), float(pts[j, 1]))
    return end1, end2


def _point_is_interior(
    x: float, y: float, height: int, width: int, margin: int
) -> bool:
    """Лежит ли точка внутри кадра (дальше margin от всех четырёх краёв).

    Args:
        x: Координата X точки (пиксели).
        y: Координата Y точки (пиксели).
        height: Высота кадра (пиксели).
        width: Ширина кадра (пиксели).
        margin: Отступ от края (пиксели); точка ближе margin к любому краю —
            «на границе», не внутренняя.

    Returns:
        True, если точка дальше margin от каждого из четырёх краёв.
    """
    return (margin <= x <= width - 1 - margin) and (margin <= y <= height - 1 - margin)


def is_rooted(
    contour: NDArray[np.int32],
    image_shape: tuple[int, int],
    config: AnalysisConfig,
) -> bool:
    """Растёт ли волос на участке — есть ли у контура конец внутри кадра.

    Волос считается растущим (rooted), если ХОТЯ БЫ один из двух концов лежит
    внутри кадра (дальше config.root_border_margin_px от всех краёв). Если ОБА
    конца упираются в границу — это транзитный волос (проходит через кадр
    насквозь, растёт вне участка).

    Args:
        contour: Контур волоса.
        image_shape: Размер кадра (height, width) — как numpy image.shape[:2].
        config: Параметры анализа (root_border_margin_px — отступ от края).

    Returns:
        True — волос растущий (rooted); False — транзитный (transit).
    """
    height, width = image_shape
    margin = config.root_border_margin_px
    end1, end2 = contour_endpoints(contour)
    return _point_is_interior(end1[0], end1[1], height, width, margin) or _point_is_interior(
        end2[0], end2[1], height, width, margin
    )


def classify_rooting(
    measurements: list[HairMeasurement],
    image_shape: tuple[int, int],
    config: AnalysisConfig,
) -> tuple[list[HairMeasurement], list[HairMeasurement]]:
    """Разделение измерений на растущие (rooted) и транзитные (transit).

    По аналогии со split_thick_bundles (core/measurement.py): транзитные волосы
    исключаются из счёта, плотности, соотношений и статистики толщин, но
    сохраняются отдельно — для аннотации (обводятся серым, чтобы оператор видел,
    что отброшено). Порог отступа берётся из config.root_border_margin_px.

    Args:
        measurements: Список измерений от measure_all() (после split_thick_bundles).
        image_shape: Размер кадра (height, width).
        config: Параметры анализа (root_border_margin_px).

    Returns:
        Кортеж (rooted, transit):
        - rooted — волосы с корневым концом в кадре (идут в подсчёт);
        - transit — проходят через кадр насквозь (исключаются из подсчёта).
    """
    rooted: list[HairMeasurement] = []
    transit: list[HairMeasurement] = []

    for m in measurements:
        if is_rooted(m.contour, image_shape, config):
            rooted.append(m)
        else:
            transit.append(m)

    if transit:
        logger.debug(
            "Детекция корней: rooted=%d, transit=%d (margin=%d px)",
            len(rooted),
            len(transit),
            config.root_border_margin_px,
        )
    return rooted, transit

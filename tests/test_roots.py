"""
Тесты детекции «растущих» волос — подсчёт от корней (core/roots.py).

Покрывает:
- contour_endpoints: поиск двух концов контура по наибольшему размаху
  (вертикаль/горизонталь/диагональ, вырожденный контур)
- is_rooted: rooted (есть конец в кадре) / transit (оба конца у границы),
  влияние параметра root_border_margin_px
- classify_rooting: разделение списка измерений на rooted/transit,
  пустой вход, сохранение объектов
"""

from __future__ import annotations

import cv2
import numpy as np

from phototrichogram.core.measurement import HairMeasurement, measure_hair
from phototrichogram.core.roots import (
    classify_rooting,
    contour_endpoints,
    is_rooted,
)
from phototrichogram.utils.config import AnalysisConfig

# Размер синтетического кадра (h, w) — как реальный кадр микроскопа 640x480.
_SHAPE: tuple[int, int] = (480, 640)


# ---------------------------------------------------------------------------
# Фабрики контуров с известной геометрией
# ---------------------------------------------------------------------------


def _line_contour(
    p1: tuple[int, int],
    p2: tuple[int, int],
    thickness: int = 8,
    shape: tuple[int, int] = _SHAPE,
) -> np.ndarray:
    """Контур отрезка p1->p2 заданной толщины (CHAIN_APPROX_NONE — все точки).

    CHAIN_APPROX_NONE сохраняет все точки границы: измерение требует >= 5
    точек, а осевой прямоугольник сжимается SIMPLE до 4 углов.
    """
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.line(mask, p1, p2, 255, thickness)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours[0]


def _measurement(
    p1: tuple[int, int], p2: tuple[int, int], thickness: int = 8
) -> HairMeasurement:
    """HairMeasurement для отрезка p1->p2 (контур сохраняется в поле contour)."""
    contour = _line_contour(p1, p2, thickness)
    m = measure_hair(contour, AnalysisConfig(um_per_px=1.0))
    assert m is not None
    return m


# ---------------------------------------------------------------------------
# contour_endpoints
# ---------------------------------------------------------------------------


class TestContourEndpoints:
    """Поиск двух крайних точек контура по наибольшему размаху."""

    def test_vertical_line_endpoints_near_top_and_bottom(self) -> None:
        """Концы вертикального отрезка близки к его началу и концу по Y."""
        contour = _line_contour((300, 50), (300, 430))

        end1, end2 = contour_endpoints(contour)

        ys = sorted([end1[1], end2[1]])
        assert abs(ys[0] - 50) <= 5
        assert abs(ys[1] - 430) <= 5

    def test_horizontal_line_endpoints_near_left_and_right(self) -> None:
        """Концы горизонтального отрезка близки к его началу и концу по X."""
        contour = _line_contour((40, 240), (600, 240))

        end1, end2 = contour_endpoints(contour)

        xs = sorted([end1[0], end2[0]])
        assert abs(xs[0] - 40) <= 5
        assert abs(xs[1] - 600) <= 5

    def test_endpoints_span_full_length(self) -> None:
        """Расстояние между концами близко к длине волоса (диаметр множества)."""
        contour = _line_contour((100, 100), (400, 400))

        end1, end2 = contour_endpoints(contour)

        span = ((end1[0] - end2[0]) ** 2 + (end1[1] - end2[1]) ** 2) ** 0.5
        expected = ((400 - 100) ** 2 + (400 - 100) ** 2) ** 0.5
        assert abs(span - expected) <= 8

    def test_returns_two_coordinate_pairs(self) -> None:
        """Результат — две точки, каждая из двух float-координат."""
        contour = _line_contour((300, 50), (300, 430))

        end1, end2 = contour_endpoints(contour)

        assert len(end1) == 2
        assert len(end2) == 2
        assert all(isinstance(v, float) for v in (*end1, *end2))

    def test_single_point_contour_returns_same_point(self) -> None:
        """Вырожденный контур из одной точки не приводит к падению."""
        single = np.array([[[10, 20]]], dtype=np.int32)

        end1, end2 = contour_endpoints(single)

        assert end1 == end2 == (10.0, 20.0)


# ---------------------------------------------------------------------------
# is_rooted
# ---------------------------------------------------------------------------


class TestIsRooted:
    """Классификация одного контура: rooted / transit."""

    def test_tip_in_center_is_rooted(self) -> None:
        """Волос от верхней границы с концом в центре кадра — растущий."""
        contour = _line_contour((300, 0), (300, 240))

        assert is_rooted(contour, _SHAPE, AnalysisConfig()) is True

    def test_crossing_frame_is_transit(self) -> None:
        """Волос через весь кадр (оба конца у левой/правой границы) — транзит."""
        contour = _line_contour((0, 120), (639, 380))

        assert is_rooted(contour, _SHAPE, AnalysisConfig()) is False

    def test_edge_to_center_diagonal_is_rooted(self) -> None:
        """Волос от угла с концом в центре — растущий (один конец внутри)."""
        contour = _line_contour((0, 0), (320, 240))

        assert is_rooted(contour, _SHAPE, AnalysisConfig()) is True

    def test_fully_interior_is_rooted(self) -> None:
        """Волос целиком внутри кадра — растущий (оба конца внутри)."""
        contour = _line_contour((200, 150), (400, 350))

        assert is_rooted(contour, _SHAPE, AnalysisConfig()) is True

    def test_vertical_spanning_full_height_is_transit(self) -> None:
        """Вертикаль от верхней до нижней границы — оба конца у края → транзит."""
        contour = _line_contour((300, 0), (300, 479))

        assert is_rooted(contour, _SHAPE, AnalysisConfig()) is False

    def test_larger_margin_turns_rooted_into_transit(self) -> None:
        """Больший root_border_margin_px превращает близкий к краю конец в границу.

        Волос от верхней границы (y=0) до y≈100: при margin=6 нижний конец
        внутри → rooted; при margin=150 нижний конец (y≈100 < 150) считается у
        границы, оба конца у края → transit.
        """
        contour = _line_contour((300, 0), (300, 100), thickness=4)

        assert is_rooted(contour, _SHAPE, AnalysisConfig(root_border_margin_px=6)) is True
        assert (
            is_rooted(contour, _SHAPE, AnalysisConfig(root_border_margin_px=150)) is False
        )

    def test_margin_zero_treats_edge_pixel_as_interior(self) -> None:
        """При margin=0 конец ровно на краю (y=0) уже считается внутри кадра."""
        # Горизонталь у самого верха: оба конца на границе по X, но margin=0
        # делает крайние пиксели «внутренними», поэтому волос считается rooted.
        contour = _line_contour((0, 120), (639, 380))

        rooted_zero = is_rooted(contour, _SHAPE, AnalysisConfig(root_border_margin_px=0))
        rooted_default = is_rooted(contour, _SHAPE, AnalysisConfig(root_border_margin_px=6))

        assert rooted_zero is True
        assert rooted_default is False


# ---------------------------------------------------------------------------
# classify_rooting
# ---------------------------------------------------------------------------


class TestClassifyRooting:
    """Разделение списка измерений на растущие и транзитные."""

    def test_empty_list_returns_two_empty_lists(self) -> None:
        """Пустой вход → две пустые группы."""
        rooted, transit = classify_rooting([], _SHAPE, AnalysisConfig())

        assert rooted == []
        assert transit == []

    def test_partitions_rooted_and_transit(self) -> None:
        """Смешанный список делится: растущий в rooted, сквозной в transit."""
        rooted_hair = _measurement((300, 0), (300, 240))   # конец в центре
        transit_hair = _measurement((0, 120), (639, 380))  # через весь кадр

        rooted, transit = classify_rooting(
            [rooted_hair, transit_hair], _SHAPE, AnalysisConfig()
        )

        assert rooted == [rooted_hair]
        assert transit == [transit_hair]

    def test_all_rooted_yields_empty_transit(self) -> None:
        """Список из одних растущих волос → transit пуст, ни один не потерян."""
        hairs = [
            _measurement((200, 150), (400, 350)),
            _measurement((300, 0), (300, 240)),
            _measurement((100, 100), (100, 300)),
        ]

        rooted, transit = classify_rooting(hairs, _SHAPE, AnalysisConfig())

        assert len(rooted) == 3
        assert transit == []

    def test_partition_preserves_all_measurements(self) -> None:
        """Ни одно измерение не теряется: |rooted| + |transit| == вход."""
        hairs = [
            _measurement((300, 0), (300, 240)),
            _measurement((0, 120), (639, 380)),
            _measurement((200, 150), (400, 350)),
        ]

        rooted, transit = classify_rooting(hairs, _SHAPE, AnalysisConfig())

        assert len(rooted) + len(transit) == len(hairs)

    def test_margin_affects_partition(self) -> None:
        """Параметр root_border_margin_px влияет на разбиение.

        Волос от верхней границы к y≈100: при большом margin становится
        транзитным и уходит из rooted в transit.
        """
        near_edge = _measurement((300, 0), (300, 100), thickness=4)

        rooted_small, transit_small = classify_rooting(
            [near_edge], _SHAPE, AnalysisConfig(root_border_margin_px=6)
        )
        rooted_large, transit_large = classify_rooting(
            [near_edge], _SHAPE, AnalysisConfig(root_border_margin_px=150)
        )

        assert rooted_small == [near_edge]
        assert transit_small == []
        assert rooted_large == []
        assert transit_large == [near_edge]

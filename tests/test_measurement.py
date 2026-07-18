"""
Тесты модуля измерения параметров волоса (core/measurement.py).

Покрывает:
- measure_hair: MinFeret/MaxFeret/площадь/периметр на фигурах с известной
  геометрией (прямоугольник, линия, окружность), пересчёт px -> мкм,
  граничные случаи (мало точек, вырожденный контур)
- measure_all: фильтрация невалидных контуров, пустой вход
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from phototrichogram.core.measurement import HairMeasurement, measure_all, measure_hair
from phototrichogram.utils.config import AnalysisConfig

# ---------------------------------------------------------------------------
# Фабрики контуров с известной геометрией
# ---------------------------------------------------------------------------


def _rectangle_contour(x: int, y: int, w: int, h: int, canvas: int = 400) -> np.ndarray:
    """Контур прямоугольника w x h с полным числом точек (CHAIN_APPROX_NONE).

    CHAIN_APPROX_SIMPLE сжимает контур оси-выровненного прямоугольника до
    4 угловых точек, что меньше минимума в 5 точек, требуемого measure_hair.
    CHAIN_APPROX_NONE сохраняет все точки границы.
    """
    mask = np.zeros((canvas, canvas), dtype=np.uint8)
    cv2.rectangle(mask, (x, y), (x + w - 1, y + h - 1), 255, -1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours[0]


def _line_contour(
    x: int, y1: int, y2: int, thickness: int, canvas: int = 400
) -> np.ndarray:
    """Контур вертикальной линии заданной толщины и длины."""
    mask = np.zeros((canvas, canvas), dtype=np.uint8)
    cv2.line(mask, (x, y1), (x, y2), 255, thickness)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours[0]


def _circle_contour(cx: int, cy: int, radius: int, canvas: int = 400) -> np.ndarray:
    """Контур окружности заданного радиуса."""
    mask = np.zeros((canvas, canvas), dtype=np.uint8)
    cv2.circle(mask, (cx, cy), radius, 255, -1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours[0]


# ---------------------------------------------------------------------------
# measure_hair -- известная геометрия
# ---------------------------------------------------------------------------


class TestMeasureHairKnownGeometry:
    """Тесты измерения на фигурах с заранее известными размерами."""

    def test_rectangle_min_feret_close_to_width(self) -> None:
        """MinFeret прямоугольника 20x200 близок к меньшей стороне (20px)."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert abs(m.thickness_px - 20) <= 2

    def test_rectangle_max_feret_close_to_height(self) -> None:
        """MaxFeret прямоугольника 20x200 близок к большей стороне (200px)."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert abs(m.length_px - 200) <= 2

    def test_rectangle_area_close_to_expected(self) -> None:
        """Площадь контура прямоугольника 20x200 близка к 4000 px^2."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert abs(m.area - 4000) / 4000 < 0.1

    def test_rectangle_perimeter_close_to_expected(self) -> None:
        """Периметр контура прямоугольника 20x200 близок к 2*(20+200)=440 px."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert abs(m.perimeter - 440) / 440 < 0.1

    def test_rectangle_is_elongated_low_circularity(self) -> None:
        """Вытянутый прямоугольник имеет низкую циркулярность (не круг)."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert m.circularity < 0.3

    def test_thickness_um_conversion_uses_calibration(self) -> None:
        """thickness_um = thickness_px * um_per_px (точный пересчёт)."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=2.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert m.thickness_um == pytest.approx(m.thickness_px * 2.0)

    def test_length_um_conversion_uses_calibration(self) -> None:
        """length_um = length_px * um_per_px (точный пересчёт)."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=3.5)

        m = measure_hair(contour, config)

        assert m is not None
        assert m.length_um == pytest.approx(m.length_px * 3.5)

    def test_line_thickness_matches_drawn_width(self) -> None:
        """MinFeret линии близок к заданной толщине рисования (8px)."""
        contour = _line_contour(x=50, y1=20, y2=219, thickness=8)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert abs(m.thickness_px - 8) <= 2

    def test_line_length_matches_drawn_length(self) -> None:
        """MaxFeret линии близок к заданной длине рисования (~200px)."""
        contour = _line_contour(x=50, y1=20, y2=219, thickness=8)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert abs(m.length_px - 200) <= 10

    def test_circle_has_high_circularity(self) -> None:
        """Окружность имеет циркулярность, близкую к 1 (в разы выше волоса)."""
        contour = _circle_contour(cx=100, cy=100, radius=30)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert m.circularity > 0.7

    def test_center_and_angle_are_populated(self) -> None:
        """Центр -- кортеж из двух координат, угол -- число."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert len(m.center) == 2
        assert isinstance(m.angle, float)

    def test_classification_field_initially_empty(self) -> None:
        """До вызова classify_hair поле classification -- пустая строка."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert m.classification == ""

    def test_contour_field_preserved(self) -> None:
        """Поле contour сохраняет исходный массив точек контура."""
        contour = _rectangle_contour(x=50, y=20, w=20, h=200)
        config = AnalysisConfig(um_per_px=1.0)

        m = measure_hair(contour, config)

        assert m is not None
        assert np.array_equal(m.contour, contour)


# ---------------------------------------------------------------------------
# measure_hair -- граничные случаи
# ---------------------------------------------------------------------------


class TestMeasureHairEdgeCases:
    """Тесты вырожденных и невалидных контуров."""

    def test_too_few_points_returns_none(self) -> None:
        """Контур из < 5 точек отбрасывается."""
        tiny = np.array([[[0, 0]], [[1, 0]], [[1, 1]], [[0, 1]]], dtype=np.int32)
        config = AnalysisConfig()

        assert measure_hair(tiny, config) is None

    def test_two_points_returns_none(self) -> None:
        """Контур из 2 точек отбрасывается."""
        tiny = np.array([[[0, 0]], [[1, 0]]], dtype=np.int32)
        config = AnalysisConfig()

        assert measure_hair(tiny, config) is None

    def test_zero_feret_returns_none(self) -> None:
        """Вырожденный контур (все точки совпадают) -> MinFeret=0 -> None."""
        degenerate = np.array(
            [[[5, 5]], [[5, 5]], [[5, 5]], [[5, 5]], [[5, 5]]], dtype=np.int32
        )
        config = AnalysisConfig()

        assert measure_hair(degenerate, config) is None

    def test_empty_contour_array_returns_none(self) -> None:
        """Контур с нулевым числом точек не приводит к падению."""
        empty = np.empty((0, 1, 2), dtype=np.int32)
        config = AnalysisConfig()

        assert measure_hair(empty, config) is None


# ---------------------------------------------------------------------------
# measure_all
# ---------------------------------------------------------------------------


class TestMeasureAll:
    """Тесты пакетного измерения списка контуров."""

    def test_empty_list_returns_empty_list(self) -> None:
        """Пустой список контуров -> пустой список измерений."""
        config = AnalysisConfig()

        assert measure_all([], config) == []

    def test_filters_out_invalid_contours(self) -> None:
        """Невалидные контуры (мало точек) отфильтровываются, валидные -- нет."""
        valid = _rectangle_contour(x=50, y=20, w=20, h=200)
        invalid = np.array([[[0, 0]], [[1, 0]]], dtype=np.int32)
        config = AnalysisConfig(um_per_px=1.0)

        result = measure_all([valid, invalid, valid], config)

        assert len(result) == 2
        assert all(isinstance(m, HairMeasurement) for m in result)

    def test_all_valid_contours_preserved(self) -> None:
        """Все валидные контуры возвращаются, ни один не потерян."""
        rect = _rectangle_contour(x=50, y=20, w=20, h=200)
        line = _line_contour(x=150, y1=20, y2=219, thickness=8)
        config = AnalysisConfig(um_per_px=1.0)

        result = measure_all([rect, line], config)

        assert len(result) == 2

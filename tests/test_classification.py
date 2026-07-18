"""
Тесты модуля классификации волос по толщине (core/classification.py).

Покрывает:
- classify_hair: пороги Vellus/Intermediate/Terminal, граничные значения
  (ровно на пороге, чуть ниже/выше), пользовательские пороги в конфиге
- classify_all: заполнение поля classification, порядок и количество,
  пустой список
"""

from __future__ import annotations

import numpy as np
import pytest

from phototrichogram.core.classification import HairType, classify_all, classify_hair
from phototrichogram.core.measurement import HairMeasurement
from phototrichogram.utils.config import AnalysisConfig

# ---------------------------------------------------------------------------
# Фабрика измерений с заданной толщиной
# ---------------------------------------------------------------------------


def _make_measurement(thickness_um: float) -> HairMeasurement:
    """Создаёт HairMeasurement с заданной толщиной, остальные поля -- заглушки."""
    return HairMeasurement(
        thickness_px=thickness_um / 5.9,
        thickness_um=thickness_um,
        length_px=100.0,
        length_um=590.0,
        area=500.0,
        perimeter=200.0,
        circularity=0.1,
        center=(50.0, 50.0),
        angle=0.0,
        contour=np.zeros((5, 1, 2), dtype=np.int32),
    )


# ---------------------------------------------------------------------------
# classify_hair -- базовые пороги
# ---------------------------------------------------------------------------


class TestClassifyHair:
    """Тесты классификации одного волоса по толщине."""

    def test_vellus_below_threshold(self) -> None:
        """Толщина 20 мкм (< 30) -> VELLUS."""
        m = _make_measurement(20.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.VELLUS

    def test_intermediate_between_thresholds(self) -> None:
        """Толщина 35 мкм (30 <= x < 40) -> INTERMEDIATE."""
        m = _make_measurement(35.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.INTERMEDIATE

    def test_terminal_above_threshold(self) -> None:
        """Толщина 50 мкм (>= 40) -> TERMINAL."""
        m = _make_measurement(50.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.TERMINAL

    # --- Граничные значения ---

    def test_boundary_exactly_vellus_threshold_is_intermediate(self) -> None:
        """Толщина ровно 30.0 мкм -> INTERMEDIATE (порог не включён в Vellus)."""
        m = _make_measurement(30.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.INTERMEDIATE

    def test_boundary_exactly_intermediate_threshold_is_terminal(self) -> None:
        """Толщина ровно 40.0 мкм -> TERMINAL (порог не включён в Intermediate)."""
        m = _make_measurement(40.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.TERMINAL

    def test_just_below_vellus_threshold(self) -> None:
        """Толщина 29.99 мкм -> VELLUS."""
        m = _make_measurement(29.99)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.VELLUS

    def test_just_below_intermediate_threshold(self) -> None:
        """Толщина 39.99 мкм -> INTERMEDIATE."""
        m = _make_measurement(39.99)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.INTERMEDIATE

    def test_zero_thickness_is_vellus(self) -> None:
        """Нулевая толщина -> VELLUS (граница снизу)."""
        m = _make_measurement(0.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.VELLUS

    def test_very_large_thickness_is_terminal(self) -> None:
        """Очень большая толщина (100 мкм) -> TERMINAL."""
        m = _make_measurement(100.0)
        config = AnalysisConfig()

        assert classify_hair(m, config) == HairType.TERMINAL

    # --- Пользовательские пороги ---

    def test_custom_thresholds_change_classification(self) -> None:
        """Пользовательские пороги в конфиге изменяют результат классификации.

        Толщина 36 мкм: с дефолтными порогами (30/40) -> INTERMEDIATE,
        с пониженными порогами (25/35) -> TERMINAL.
        """
        m = _make_measurement(36.0)
        default_config = AnalysisConfig()
        custom_config = AnalysisConfig(vellus_threshold_um=25.0, intermediate_threshold_um=35.0)

        assert classify_hair(m, default_config) == HairType.INTERMEDIATE
        assert classify_hair(m, custom_config) == HairType.TERMINAL

    def test_custom_vellus_threshold_only(self) -> None:
        """Изменение только vellus_threshold_um влияет на границу Vellus."""
        m = _make_measurement(22.0)
        strict_config = AnalysisConfig(vellus_threshold_um=20.0)
        loose_config = AnalysisConfig(vellus_threshold_um=25.0)

        assert classify_hair(m, strict_config) == HairType.INTERMEDIATE
        assert classify_hair(m, loose_config) == HairType.VELLUS


# ---------------------------------------------------------------------------
# HairType enum
# ---------------------------------------------------------------------------


class TestHairTypeEnum:
    """Проверка значений перечисления HairType."""

    @pytest.mark.parametrize(
        ("member", "expected_value"),
        [
            (HairType.VELLUS, "vellus"),
            (HairType.INTERMEDIATE, "intermediate"),
            (HairType.TERMINAL, "terminal"),
        ],
    )
    def test_enum_string_values(self, member: HairType, expected_value: str) -> None:
        """Строковые значения enum используются в stats.py и в БД."""
        assert member.value == expected_value


# ---------------------------------------------------------------------------
# classify_all
# ---------------------------------------------------------------------------


class TestClassifyAll:
    """Тесты пакетной классификации списка измерений."""

    def test_empty_list_returns_empty_list(self) -> None:
        """Пустой список измерений -> пустой список на выходе."""
        config = AnalysisConfig()

        assert classify_all([], config) == []

    def test_fills_classification_field_in_place(self) -> None:
        """Поле classification заполняется для каждого измерения."""
        measurements = [_make_measurement(20.0), _make_measurement(35.0), _make_measurement(50.0)]
        config = AnalysisConfig()

        classify_all(measurements, config)

        assert measurements[0].classification == HairType.VELLUS.value
        assert measurements[1].classification == HairType.INTERMEDIATE.value
        assert measurements[2].classification == HairType.TERMINAL.value

    def test_returns_same_list_object(self) -> None:
        """classify_all возвращает тот же список (модификация in-place)."""
        measurements = [_make_measurement(20.0)]
        config = AnalysisConfig()

        result = classify_all(measurements, config)

        assert result is measurements

    def test_preserves_order_and_count(self) -> None:
        """Порядок и количество измерений не меняются после классификации."""
        thicknesses = [10.0, 25.0, 32.0, 41.0, 90.0]
        measurements = [_make_measurement(t) for t in thicknesses]
        config = AnalysisConfig()

        result = classify_all(measurements, config)

        assert len(result) == len(thicknesses)
        assert [m.thickness_um for m in result] == thicknesses

    def test_mixed_batch_all_three_categories_present(self) -> None:
        """Смешанная партия классифицируется по всем трём категориям."""
        measurements = [
            _make_measurement(10.0),  # vellus
            _make_measurement(15.0),  # vellus
            _make_measurement(33.0),  # intermediate
            _make_measurement(60.0),  # terminal
            _make_measurement(80.0),  # terminal
        ]
        config = AnalysisConfig()

        classify_all(measurements, config)
        labels = {m.classification for m in measurements}

        assert labels == {
            HairType.VELLUS.value,
            HairType.INTERMEDIATE.value,
            HairType.TERMINAL.value,
        }

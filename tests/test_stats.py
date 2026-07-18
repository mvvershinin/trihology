"""
Тесты модуля статистического анализа (core/stats.py).

Покрывает:
- calculate_density: формула плотности, деление на ноль/отрицательную площадь
- calculate_ratios: подсчёт долей Terminal/Vellus/Intermediate, T/V ratio,
  граница при vellus=0, пустой список
- thickness_statistics: mean/median/std/min/max, пустой список, один элемент
- make_zone_stats: агрегация статистики зоны
- compare_zones: сравнение с контролем, отсутствие контрольной зоны,
  граничные значения оценки (_assess_zone) через публичный API
"""

from __future__ import annotations

import numpy as np
import pytest

from phototrichogram.core.measurement import HairMeasurement
from phototrichogram.core.stats import (
    HairRatios,
    ThicknessStats,
    ZoneStats,
    calculate_density,
    calculate_ratios,
    compare_zones,
    make_zone_stats,
    thickness_statistics,
)

# ---------------------------------------------------------------------------
# Фабрики
# ---------------------------------------------------------------------------


def _make_measurement(thickness_um: float, classification: str) -> HairMeasurement:
    """HairMeasurement с заданной толщиной и уже проставленной классификацией."""
    return HairMeasurement(
        thickness_px=thickness_um / 5.9,
        thickness_um=thickness_um,
        length_px=100.0,
        length_um=590.0,
        area=500.0,
        perimeter=200.0,
        circularity=0.1,
        center=(0.0, 0.0),
        angle=0.0,
        contour=np.zeros((5, 1, 2), dtype=np.int32),
        classification=classification,
    )


def _make_zone_stats(zone_code: str, density: float, vellus_pct: float) -> ZoneStats:
    """ZoneStats с заданной плотностью и % vellus, остальное -- заглушки."""
    ratios = HairRatios(
        terminal_count=80,
        vellus_count=15,
        intermediate_count=5,
        total_count=100,
        terminal_pct=100.0 - vellus_pct - 5.0,
        vellus_pct=vellus_pct,
        intermediate_pct=5.0,
        tv_ratio=5.0,
    )
    thickness = ThicknessStats(mean=45.0, median=45.0, std=5.0, min=20.0, max=70.0, count=100)
    return ZoneStats(
        zone_code=zone_code,
        hair_count=100,
        density_per_cm2=density,
        thickness=thickness,
        ratios=ratios,
    )


# ---------------------------------------------------------------------------
# calculate_density
# ---------------------------------------------------------------------------


class TestCalculateDensity:
    """Тесты расчёта плотности волос на см²."""

    def test_basic_formula(self) -> None:
        """density = count / area(мм²) * 100."""
        density = calculate_density(hair_count=100, fov_area_mm2=10.6)

        assert density == pytest.approx(100 / 10.6 * 100.0, abs=0.1)

    def test_zero_hair_count(self) -> None:
        """Ноль волос -> плотность 0."""
        assert calculate_density(hair_count=0, fov_area_mm2=10.6) == 0.0

    def test_zero_area_returns_zero(self) -> None:
        """Нулевая площадь поля зрения -> плотность 0 (без деления на ноль)."""
        assert calculate_density(hair_count=50, fov_area_mm2=0.0) == 0.0

    def test_negative_area_returns_zero(self) -> None:
        """Отрицательная площадь (некорректный вход) -> плотность 0."""
        assert calculate_density(hair_count=50, fov_area_mm2=-5.0) == 0.0

    def test_result_rounded_to_one_decimal(self) -> None:
        """Результат округляется до 1 знака после запятой."""
        density = calculate_density(hair_count=33, fov_area_mm2=7.0)

        assert density == round(density, 1)


# ---------------------------------------------------------------------------
# calculate_ratios
# ---------------------------------------------------------------------------


class TestCalculateRatios:
    """Тесты подсчёта соотношений типов волос."""

    def test_empty_list_all_zero(self) -> None:
        """Пустой список -> все поля нулевые."""
        ratios = calculate_ratios([])

        assert ratios.total_count == 0
        assert ratios.terminal_count == 0
        assert ratios.vellus_count == 0
        assert ratios.intermediate_count == 0
        assert ratios.terminal_pct == 0.0
        assert ratios.vellus_pct == 0.0
        assert ratios.intermediate_pct == 0.0
        assert ratios.tv_ratio == 0.0

    def test_counts_by_classification(self) -> None:
        """Подсчёт количества по каждой категории."""
        measurements = (
            [_make_measurement(50.0, "terminal")] * 8
            + [_make_measurement(20.0, "vellus")] * 1
            + [_make_measurement(35.0, "intermediate")] * 1
        )

        ratios = calculate_ratios(measurements)

        assert ratios.total_count == 10
        assert ratios.terminal_count == 8
        assert ratios.vellus_count == 1
        assert ratios.intermediate_count == 1

    def test_percentages_computed_correctly(self) -> None:
        """Проценты вычисляются относительно общего количества."""
        measurements = (
            [_make_measurement(50.0, "terminal")] * 8
            + [_make_measurement(20.0, "vellus")] * 1
            + [_make_measurement(35.0, "intermediate")] * 1
        )

        ratios = calculate_ratios(measurements)

        assert ratios.terminal_pct == pytest.approx(80.0)
        assert ratios.vellus_pct == pytest.approx(10.0)
        assert ratios.intermediate_pct == pytest.approx(10.0)

    def test_percentages_sum_to_100(self) -> None:
        """Сумма всех процентов равна 100 (с учётом округления)."""
        measurements = (
            [_make_measurement(50.0, "terminal")] * 7
            + [_make_measurement(20.0, "vellus")] * 2
            + [_make_measurement(35.0, "intermediate")] * 1
        )

        ratios = calculate_ratios(measurements)
        total_pct = ratios.terminal_pct + ratios.vellus_pct + ratios.intermediate_pct

        assert total_pct == pytest.approx(100.0, abs=0.2)

    def test_tv_ratio_normal_case(self) -> None:
        """T/V = terminal_count / vellus_count."""
        measurements = [_make_measurement(50.0, "terminal")] * 8 + [
            _make_measurement(20.0, "vellus")
        ] * 2

        ratios = calculate_ratios(measurements)

        assert ratios.tv_ratio == pytest.approx(4.0)

    def test_tv_ratio_capped_when_no_vellus(self) -> None:
        """При отсутствии Vellus T/V ограничен значением 99.9 (не inf)."""
        measurements = [_make_measurement(50.0, "terminal")] * 5

        ratios = calculate_ratios(measurements)

        assert ratios.tv_ratio == 99.9

    def test_all_vellus_zero_terminal(self) -> None:
        """Все волосы -- Vellus: terminal_count=0, tv_ratio=0."""
        measurements = [_make_measurement(15.0, "vellus")] * 5

        ratios = calculate_ratios(measurements)

        assert ratios.terminal_count == 0
        assert ratios.vellus_count == 5
        assert ratios.tv_ratio == 0.0


# ---------------------------------------------------------------------------
# thickness_statistics
# ---------------------------------------------------------------------------


class TestThicknessStatistics:
    """Тесты статистики толщины волос."""

    def test_empty_list_all_zero(self) -> None:
        """Пустой список -> все поля нулевые, count=0."""
        stats = thickness_statistics([])

        assert stats.mean == 0.0
        assert stats.median == 0.0
        assert stats.std == 0.0
        assert stats.min == 0.0
        assert stats.max == 0.0
        assert stats.count == 0

    def test_known_values(self) -> None:
        """Известный набор [10, 20, 30, 40, 50] -> точные mean/median/std/min/max."""
        measurements = [_make_measurement(t, "terminal") for t in [10.0, 20.0, 30.0, 40.0, 50.0]]

        stats = thickness_statistics(measurements)

        assert stats.mean == pytest.approx(30.0)
        assert stats.median == pytest.approx(30.0)
        assert stats.std == pytest.approx(14.1, abs=0.05)
        assert stats.min == pytest.approx(10.0)
        assert stats.max == pytest.approx(50.0)
        assert stats.count == 5

    def test_single_value(self) -> None:
        """Один элемент -> mean=median=значение, std=0."""
        stats = thickness_statistics([_make_measurement(25.0, "vellus")])

        assert stats.mean == pytest.approx(25.0)
        assert stats.median == pytest.approx(25.0)
        assert stats.std == 0.0
        assert stats.min == pytest.approx(25.0)
        assert stats.max == pytest.approx(25.0)
        assert stats.count == 1

    def test_count_matches_input_length(self) -> None:
        """count равен числу переданных измерений."""
        measurements = [_make_measurement(30.0, "intermediate") for _ in range(7)]

        stats = thickness_statistics(measurements)

        assert stats.count == 7


# ---------------------------------------------------------------------------
# make_zone_stats
# ---------------------------------------------------------------------------


class TestMakeZoneStats:
    """Тесты агрегации полной статистики по зоне."""

    def test_aggregates_all_components(self) -> None:
        """ZoneStats объединяет hair_count, density, thickness, ratios."""
        measurements = [_make_measurement(50.0, "terminal")] * 8 + [
            _make_measurement(20.0, "vellus")
        ] * 2

        zone = make_zone_stats("F", measurements, fov_area_mm2=10.6)

        assert zone.zone_code == "F"
        assert zone.hair_count == 10
        assert zone.density_per_cm2 == pytest.approx(10 / 10.6 * 100.0, abs=0.1)
        assert zone.ratios.total_count == 10
        assert zone.thickness.count == 10

    def test_empty_measurements(self) -> None:
        """Пустой список измерений -> нулевая статистика без падения."""
        zone = make_zone_stats("O", [], fov_area_mm2=10.6)

        assert zone.hair_count == 0
        assert zone.density_per_cm2 == 0.0
        assert zone.ratios.total_count == 0
        assert zone.thickness.count == 0


# ---------------------------------------------------------------------------
# compare_zones
# ---------------------------------------------------------------------------


class TestCompareZones:
    """Тесты сравнения зон с контрольной (затылочной)."""

    def test_missing_control_zone_returns_empty(self) -> None:
        """Отсутствие контрольной зоны 'O' -> пустой список сравнений."""
        zones = {"F": _make_zone_stats("F", density=100.0, vellus_pct=25.0)}

        comparisons = compare_zones(zones, control_code="O")

        assert comparisons == []

    def test_control_zone_excluded_from_results(self) -> None:
        """Сама контрольная зона не попадает в список сравнений."""
        zones = {
            "F": _make_zone_stats("F", density=100.0, vellus_pct=25.0),
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)
        codes = {c.zone_code for c in comparisons}

        assert "O" not in codes
        assert codes == {"F"}

    def test_density_diff_pct_formula(self) -> None:
        """density_diff_pct = (zone - control) / control * 100."""
        zones = {
            "F": _make_zone_stats("F", density=130.0, vellus_pct=10.0),
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].density_diff_pct == pytest.approx(-35.0)

    def test_vellus_diff_pp_formula(self) -> None:
        """vellus_diff_pp = zone.vellus_pct - control.vellus_pct."""
        zones = {
            "F": _make_zone_stats("F", density=200.0, vellus_pct=25.0),
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].vellus_diff_pp == pytest.approx(15.0)

    def test_control_zero_density_no_division_by_zero(self) -> None:
        """Нулевая плотность контроля -> density_diff_pct=0 (без деления на ноль)."""
        zones = {
            "F": _make_zone_stats("F", density=100.0, vellus_pct=15.0),
            "O": _make_zone_stats("O", density=0.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].density_diff_pct == 0.0

    def test_custom_control_code(self) -> None:
        """Нестандартный код контрольной зоны работает так же, как 'O'."""
        zones = {
            "F": _make_zone_stats("F", density=100.0, vellus_pct=25.0),
            "P": _make_zone_stats("P", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones, control_code="P")

        assert len(comparisons) == 1
        assert comparisons[0].zone_code == "F"

    # --- Диагностическая оценка (_assess_zone через публичный API) ---

    def test_assessment_severe_miniaturization(self) -> None:
        """density_diff < -30% И vellus_diff > 10пп -> 'Выраженная миниатюризация'."""
        zones = {
            "F": _make_zone_stats("F", density=130.0, vellus_pct=25.0),  # -35%, +15пп
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].assessment == "Выраженная миниатюризация"

    def test_assessment_mild_miniaturization_by_density_only(self) -> None:
        """density_diff < -15% (но не < -30%) -> 'Начальная миниатюризация'."""
        zones = {
            "F": _make_zone_stats("F", density=160.0, vellus_pct=10.0),  # -20%, 0пп
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].assessment == "Начальная миниатюризация"

    def test_assessment_mild_miniaturization_by_vellus_only(self) -> None:
        """vellus_diff > 5пп (при density_diff в норме) -> 'Начальная миниатюризация'."""
        zones = {
            "F": _make_zone_stats("F", density=195.0, vellus_pct=17.0),  # -2.5%, +7пп
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].assessment == "Начальная миниатюризация"

    def test_assessment_within_normal_range(self) -> None:
        """Малые отклонения -> 'В пределах нормы'."""
        zones = {
            "F": _make_zone_stats("F", density=195.0, vellus_pct=11.0),  # -2.5%, +1пп
            "O": _make_zone_stats("O", density=200.0, vellus_pct=10.0),
        }

        comparisons = compare_zones(zones)

        assert comparisons[0].assessment == "В пределах нормы"

    def test_multiple_zones_all_compared(self) -> None:
        """Несколько зон сравниваются одновременно с контролем."""
        zones = {
            "F": _make_zone_stats("F", density=100.0, vellus_pct=25.0),
            "P": _make_zone_stats("P", density=90.0, vellus_pct=30.0),
            "T": _make_zone_stats("T", density=150.0, vellus_pct=18.0),
            "O": _make_zone_stats("O", density=220.0, vellus_pct=8.0),
        }

        comparisons = compare_zones(zones)

        assert {c.zone_code for c in comparisons} == {"F", "P", "T"}

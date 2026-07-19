"""
Тесты усреднения серии снимков зоны (gui/series.py, план v1.4).

Проверяют математику агрегации метрик серии — то, что явно отмечено как
рискованное в плане:
- T/V считается из СУММ terminal/vellus по серии, а НЕ как среднее отношений;
- семантика «нет пушковых» (vellus_sum == 0 → TV_RATIO_MAX) и пустой серии;
- density_std (разброс плотности) и n_snapshots;
- проценты и толщины — среднее по снимкам;
- обратная совместимость: серия из одного снимка тождественна исходному
  результату (n_snapshots=1, density_std=None).

Модуль gui/series.py не тянет tkinter/matplotlib (только core + numpy),
поэтому тест работает без дисплея.
"""

from __future__ import annotations

import numpy as np
import pytest

from phototrichogram.core.analyzer import AnalysisResult
from phototrichogram.core.measurement import HairMeasurement
from phototrichogram.core.stats import (
    TV_RATIO_MAX,
    calculate_ratios,
    thickness_statistics,
)
from phototrichogram.gui.series import aggregate_series

# Толщины по типу волоса (мкм) — по умолчанию для фабрики измерений.
_THICKNESS_BY_TYPE = {"terminal": 55.0, "intermediate": 35.0, "vellus": 20.0}

# ---------------------------------------------------------------------------
# Фабрики
# ---------------------------------------------------------------------------


def _make_measurement(classification: str, thickness_um: float) -> HairMeasurement:
    """HairMeasurement заданного типа и толщины для наполнения списка измерений."""
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


def _make_result(
    *,
    density: float,
    terminal: int,
    vellus: int,
    intermediate: int = 0,
    thickness_mean: float | None = None,
    fov_area_mm2: float = 10.6,
) -> AnalysisResult:
    """AnalysisResult, повторяющий то, что вернуло бы ядро для одного снимка.

    Измерения строятся согласованно с запрошенными counts (по одному
    HairMeasurement на волос нужного типа), а ratios/thickness ВЫЧИСЛЯЮТСЯ из
    них через core.stats — ровно как в реальном пайплайне. Это гарантирует, что
    measurements и агрегаты не расходятся (иначе усреднение по пулу вскроет
    несогласованность, которой в бою не бывает).

    thickness_mean, если задан, применяется к терминальным волосам (для
    сценариев, проверяющих статистику толщин).
    """
    term_thick = thickness_mean if thickness_mean is not None else _THICKNESS_BY_TYPE["terminal"]
    measurements = (
        [_make_measurement("terminal", term_thick) for _ in range(terminal)]
        + [_make_measurement("vellus", _THICKNESS_BY_TYPE["vellus"]) for _ in range(vellus)]
        + [
            _make_measurement("intermediate", _THICKNESS_BY_TYPE["intermediate"])
            for _ in range(intermediate)
        ]
    )
    ratios = calculate_ratios(measurements)
    thickness = thickness_statistics(measurements)
    return AnalysisResult(
        measurements=measurements,
        hair_count=terminal + vellus + intermediate,
        density_per_cm2=density,
        ratios=ratios,
        thickness=thickness,
        mask=np.zeros((10, 10), dtype=np.uint8),
        annotated_image=None,
        fov_area_mm2=fov_area_mm2,
    )


# ---------------------------------------------------------------------------
# Обратная совместимость: один снимок
# ---------------------------------------------------------------------------


def test_single_snapshot_is_identity() -> None:
    """Серия из одного снимка не меняет метрики (n=1, std=None)."""
    r = _make_result(density=232.7, terminal=80, vellus=15, intermediate=5,
                     thickness_mean=44.3)
    agg = aggregate_series([r])

    assert agg.n_snapshots == 1
    assert agg.density_std is None
    assert agg.result.density_per_cm2 == r.density_per_cm2
    assert agg.result.hair_count == r.hair_count
    # HairRatios и ThicknessStats — frozen dataclass'ы без numpy-полей, == корректно.
    assert agg.result.ratios == r.ratios
    assert agg.result.thickness == r.thickness
    # Измерения — те же объекты (пул из одного снимка).
    assert len(agg.result.measurements) == len(r.measurements)
    assert agg.result.mask is r.mask


def test_single_snapshot_no_vellus_identity() -> None:
    """Один снимок без пушковых: T/V = TV_RATIO_MAX сохраняется."""
    r = _make_result(density=210.0, terminal=50, vellus=0, intermediate=3)
    agg = aggregate_series([r])
    assert agg.result.ratios.tv_ratio == TV_RATIO_MAX


# ---------------------------------------------------------------------------
# Плотность и разброс
# ---------------------------------------------------------------------------


def test_density_mean_and_std() -> None:
    """Плотность — среднее по серии, density_std — np.std (population)."""
    results = [
        _make_result(density=200.0, terminal=40, vellus=10),
        _make_result(density=260.0, terminal=40, vellus=10),
    ]
    agg = aggregate_series(results)

    assert agg.n_snapshots == 2
    assert agg.result.density_per_cm2 == 230.0
    # np.std([200, 260]) == 30.0 (population, ddof=0)
    assert agg.density_std == 30.0


def test_density_std_three_snapshots() -> None:
    """density_std для трёх снимков совпадает с np.std."""
    densities = [180.0, 240.0, 210.0]
    results = [_make_result(density=d, terminal=30, vellus=5) for d in densities]
    agg = aggregate_series(results)

    expected = round(float(np.std(np.asarray(densities))), 1)
    assert agg.density_std == expected
    assert agg.result.density_per_cm2 == 210.0


# ---------------------------------------------------------------------------
# T/V — из СУММ, а не среднее отношений
# ---------------------------------------------------------------------------


def test_tv_ratio_from_sums_not_mean_of_ratios() -> None:
    """T/V серии = ΣT/ΣV, а НЕ среднее (Tᵢ/Vᵢ)."""
    # Снимок A: T/V = 10/1 = 10.0; Снимок B: T/V = 1/10 = 0.1.
    a = _make_result(density=200.0, terminal=10, vellus=1)
    b = _make_result(density=200.0, terminal=1, vellus=10)

    agg = aggregate_series([a, b])

    # Среднее отношений было бы (10.0 + 0.1) / 2 = 5.05 — НЕВЕРНО.
    # Из сумм: (10 + 1) / (1 + 10) = 11 / 11 = 1.0.
    assert agg.result.ratios.tv_ratio == 1.0


def test_tv_ratio_no_vellus_in_series() -> None:
    """Нет пушковых во всей серии (ΣV == 0, ΣT > 0) → TV_RATIO_MAX."""
    results = [
        _make_result(density=210.0, terminal=30, vellus=0),
        _make_result(density=230.0, terminal=40, vellus=0),
    ]
    agg = aggregate_series(results)
    assert agg.result.ratios.tv_ratio == TV_RATIO_MAX


def test_tv_ratio_all_empty_series_is_zero() -> None:
    """Совсем нет волос во всей серии → T/V = 0.0 (как в core для пустого)."""
    results = [
        _make_result(density=0.0, terminal=0, vellus=0),
        _make_result(density=0.0, terminal=0, vellus=0),
    ]
    agg = aggregate_series(results)
    assert agg.result.ratios.tv_ratio == 0.0


# ---------------------------------------------------------------------------
# Проценты, толщины, пул измерений
# ---------------------------------------------------------------------------


def test_percentages_are_hair_weighted_not_snapshot_averaged() -> None:
    """Проценты — по ПУЛУ волос (взвешенно), а не простым средним по снимкам.

    Плохой снимок с малым числом волос НЕ должен перевешивать полноценный.
    Это защита от бага, когда равновзвешенное среднее процентов искажало
    vellus% и могло перекрасить норму вопреки T/V на той же карточке.
    """
    # A (плохой): 4 волоса, 2T + 2V → vellus_pct снимка 50%.
    # B (хороший): 96 волос, 90T + 6V → vellus_pct снимка 6.25%.
    a = _make_result(density=200.0, terminal=2, vellus=2)
    b = _make_result(density=200.0, terminal=90, vellus=6)
    agg = aggregate_series([a, b])

    # Пул: 92T + 8V из 100 → vellus 8% (а не равновзвешенное (50+6.25)/2 ≈ 28%).
    assert agg.result.ratios.vellus_pct == 8.0
    assert agg.result.ratios.terminal_pct == 92.0


def test_thickness_from_pool() -> None:
    """Средняя толщина — по пулу всех волос серии (согласуется с гистограммой)."""
    a = _make_result(density=200.0, terminal=50, vellus=0, thickness_mean=40.0)
    b = _make_result(density=200.0, terminal=50, vellus=0, thickness_mean=50.0)
    agg = aggregate_series([a, b])
    # Пул: 50 волос@40 + 50@50 → mean 45.
    assert agg.result.thickness.mean == 45.0


def test_measurements_are_pooled() -> None:
    """Измерения для гистограммы — объединение волос всех снимков серии."""
    a = _make_result(density=200.0, terminal=3, vellus=2)  # 5 измерений
    b = _make_result(density=200.0, terminal=4, vellus=4)  # 8 измерений
    agg = aggregate_series([a, b])
    assert len(agg.result.measurements) == 13


def test_counts_are_per_snapshot_average() -> None:
    """Счётчики типов — среднее число волос на снимок (из пула / n)."""
    a = _make_result(density=200.0, terminal=40, vellus=10)
    b = _make_result(density=200.0, terminal=60, vellus=20)
    agg = aggregate_series([a, b])
    # Пул: 100T, 30V; на снимок: round(100/2)=50, round(30/2)=15.
    assert agg.result.ratios.terminal_count == 50
    assert agg.result.ratios.vellus_count == 15


def test_empty_series_raises() -> None:
    """Пустая серия — ошибка (вызывающий обязан отсеять зоны без снимков)."""
    with pytest.raises(ValueError, match="пуст"):
        aggregate_series([])

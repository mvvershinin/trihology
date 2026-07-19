"""
Усреднение серии снимков одной зоны (план v1.4, Phase 3).

Кадр микроскопа — единицы мм² (при 5.9 мкм/px ≈ 3.8×2.8 мм), поэтому один
снимок шумный: случайные транзитные волосы, неоднородность участка. Оператор
делает до 5 контрольных снимков на зону, слегка смещая поле; итоговые метрики
зоны = среднее по серии, с показом разброса плотности.

Модуль живёт в слое ``gui`` (не в ``core``): ядро анализа готово и не трогается,
а усреднение — это агрегация результатов на стороне интерфейса. GUI-слой вправе
импортировать ``core`` (см. import-linter контракт), поэтому здесь строится
синтетический :class:`AnalysisResult` из усреднённых значений — карточки зон,
графики и детали продолжают работать с тем же типом, что и раньше.

Правила усреднения (см. также docstring :func:`aggregate_series`):
- плотность, толщины, проценты типов — СРЕДНЕЕ по снимкам серии;
- T/V — из СУММ terminal/vellus по снимкам (среднее отношений математически
  некорректно), с той же семантикой «нет пушковых», что и ``core.stats``;
- ``density_std`` — стандартное отклонение плотности по серии (None для одного
  снимка).

Серия из одного снимка полностью повторяет прежнее поведение: усреднение —
тождественная операция, ``n_snapshots == 1``, ``density_std is None``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from phototrichogram.core.analyzer import AnalysisResult
from phototrichogram.core.measurement import HairMeasurement
from phototrichogram.core.stats import (
    HairRatios,
    ThicknessStats,
    calculate_ratios,
    thickness_statistics,
)


@dataclass(frozen=True)
class ZoneSeriesResult:
    """Агрегированный результат серии снимков одной зоны.

    Обёртка вокруг усреднённого :class:`AnalysisResult` плюс метаданные серии.
    ``AnalysisResult`` — тип из ``core`` — не расширяется (ядро не трогаем);
    число снимков и разброс плотности живут здесь, в GUI-слое.

    Attributes:
        result: Усреднённый результат анализа (плотность, соотношения,
            статистика толщин — как среднее по серии). Для одиночного снимка
            равен исходному ``AnalysisResult`` без изменений.
        n_snapshots: Количество снимков в серии (1 — одиночный снимок, прежнее
            поведение; 2–5 — усреднённая серия).
        density_std: Стандартное отклонение плотности (волос/см²) по серии.
            None для одиночного снимка (n_snapshots == 1), где разброс не
            определён.
    """

    result: AnalysisResult
    n_snapshots: int
    density_std: float | None


def _aggregate_ratios(pooled: list[HairMeasurement], n: int) -> HairRatios:
    """Соотношения типов волос по ПУЛУ всех волос серии.

    Проценты и T/V считаются по объединённому списку волос всех снимков
    (взвешенно по волосам). T/V — из СУММ terminal/vellus (``ΣTᵢ / ΣVᵢ``, а не
    среднее отношений ``mean(Tᵢ/Vᵢ)``). Семантика вырожденных случаев — как в
    ``core.stats.calculate_ratios`` (нет волос → 0.0, нет пушковых →
    ``TV_RATIO_MAX``), что обеспечивает тождественность для серии из 1 снимка.

    Счётчики типов для показа — среднее число волос на снимок (деление на n
    не меняет проценты, поэтому счётчики остаются согласованными с процентами).

    Args:
        pooled: Объединённый список волос всех снимков серии.
        n: Число снимков серии.

    Returns:
        Агрегированный :class:`HairRatios`.
    """
    # Проценты и T/V считаются по ПУЛУ всех волос серии (взвешенно по волосам),
    # а не простым средним по снимкам: иначе снимок с малым числом волос имел бы
    # такой же вес, как полноценный, и мог бы исказить vellus% и перекрасить
    # норму, противореча T/V (тоже взвешенному по волосам) на той же карточке.
    # calculate_ratios даёт ту же семантику вырожденных случаев (нет волос → 0.0,
    # Σvellus==0 → TV_RATIO_MAX), поэтому для серии из 1 снимка результат
    # тождественен исходному AnalysisResult.ratios.
    base = calculate_ratios(pooled)
    if n == 1:
        return base

    # Счётчики для показа — среднее число волос на снимок («типичный кадр»).
    # Деление на n не меняет проценты (они из пула), поэтому *_count/total_count
    # остаются согласованными с *_pct.
    terminal_count = round(base.terminal_count / n)
    vellus_count = round(base.vellus_count / n)
    intermediate_count = round(base.intermediate_count / n)

    return HairRatios(
        terminal_count=terminal_count,
        vellus_count=vellus_count,
        intermediate_count=intermediate_count,
        total_count=terminal_count + vellus_count + intermediate_count,
        terminal_pct=base.terminal_pct,
        vellus_pct=base.vellus_pct,
        intermediate_pct=base.intermediate_pct,
        tv_ratio=base.tv_ratio,
    )


def _aggregate_thickness(pooled: list[HairMeasurement], hair_count: int) -> ThicknessStats:
    """Статистика толщин по ПУЛУ всех волос серии.

    Считается по объединённому списку волос всех снимков (взвешенно по волосам),
    поэтому min/max/std — настоящие по всей серии и согласуются с гистограммой
    (она строится по тому же пулу). Простое среднее по-снимочных min/max/std было
    бы статистически некорректным (среднее минимумов ≠ минимум, среднее std
    занижает реальный разброс). Поле ``count`` — усреднённое число волос зоны
    (для показанного ``hair_count``); в интерфейсе не отображается.

    Args:
        pooled: Объединённый список волос всех снимков серии.
        hair_count: Усреднённое число волос зоны (для поля ``count``).

    Returns:
        Агрегированный :class:`ThicknessStats`.
    """
    base = thickness_statistics(pooled)
    return ThicknessStats(
        mean=base.mean,
        median=base.median,
        std=base.std,
        min=base.min,
        max=base.max,
        count=hair_count,
    )


def aggregate_series(results: list[AnalysisResult]) -> ZoneSeriesResult:
    """Свести серию результатов анализа одной зоны в единый усреднённый результат.

    Правила усреднения:
    - **плотность** — среднее по снимкам; **разброс** ``density_std`` —
      стандартное отклонение плотности по серии (population/ddof=0, как
      ``np.std`` в ``core.stats``), либо None для одиночного снимка;
    - **проценты и толщины** — среднее по снимкам (равный вес каждому снимку);
    - **T/V** — из СУММ terminal/vellus по всей серии (см. ``_aggregate_ratios``);
    - **измерения для гистограммы** — объединение (пул) волос всех снимков серии,
      чтобы распределение толщин строилось по всем данным; ``mask`` и
      ``annotated_image`` берутся с последнего снимка серии (он же — миниатюра
      карточки зоны).

    Обратная совместимость: для серии из одного снимка все операции усреднения
    тождественны, поэтому результат численно совпадает с исходным
    ``AnalysisResult`` (n_snapshots=1, density_std=None).

    Args:
        results: Непустой список ``AnalysisResult`` — по одному на снимок серии
            (уже без None: невалидные снимки отсеивает вызывающая сторона).

    Returns:
        :class:`ZoneSeriesResult` с усреднённым ``AnalysisResult``, числом
        снимков и разбросом плотности.

    Raises:
        ValueError: Если ``results`` пуст (усреднять нечего — вызывающий код
            обязан отсеять зоны без валидных снимков).
    """
    if not results:
        raise ValueError("Нельзя усреднить пустую серию снимков")

    n = len(results)

    densities = np.asarray([r.density_per_cm2 for r in results], dtype=np.float64)
    density_mean = round(float(np.mean(densities)), 1)
    density_std = round(float(np.std(densities)), 1) if n > 1 else None

    hair_count = round(float(np.mean([r.hair_count for r in results])))

    # Пул всех волос серии — единая основа для процентов, T/V, толщин и
    # гистограммы (взвешенно по волосам, всё согласовано между собой).
    pooled: list[HairMeasurement] = [m for r in results for m in r.measurements]

    ratios = _aggregate_ratios(pooled, n)
    thickness = _aggregate_thickness(pooled, hair_count)

    last = results[-1]
    aggregated = AnalysisResult(
        measurements=pooled,
        hair_count=hair_count,
        density_per_cm2=density_mean,
        ratios=ratios,
        thickness=thickness,
        mask=last.mask,
        annotated_image=last.annotated_image,
        fov_area_mm2=results[0].fov_area_mm2,
    )

    return ZoneSeriesResult(
        result=aggregated,
        n_snapshots=n,
        density_std=density_std,
    )

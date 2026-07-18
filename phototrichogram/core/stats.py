"""
Статистический анализ результатов трихоскопии.

Модуль рассчитывает:
- Плотность волос (волос/см²) — METHODOLOGY.md §4.1 шаг 6
- Соотношения Terminal/Vellus/Intermediate
- Статистику толщин (mean, median, std, min, max)
- Сравнение зон (F/P/T vs O) — METHODOLOGY.md §4.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from phototrichogram.core.classification import HairType
from phototrichogram.core.measurement import HairMeasurement

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThicknessStats:
    """Статистика толщины волос.

    Attributes:
        mean: Средняя толщина (мкм).
        median: Медианная толщина (мкм).
        std: Стандартное отклонение (мкм).
        min: Минимальная толщина (мкм).
        max: Максимальная толщина (мкм).
        count: Количество волос.
    """

    mean: float
    median: float
    std: float
    min: float
    max: float
    count: int


@dataclass(frozen=True)
class HairRatios:
    """Соотношения типов волос.

    Attributes:
        terminal_count: Количество терминальных волос.
        vellus_count: Количество пушковых волос.
        intermediate_count: Количество промежуточных волос.
        total_count: Общее количество волос.
        terminal_pct: Процент терминальных.
        vellus_pct: Процент пушковых.
        intermediate_pct: Процент промежуточных.
        tv_ratio: Соотношение Terminal/Vellus (норма > 4:1).
    """

    terminal_count: int
    vellus_count: int
    intermediate_count: int
    total_count: int
    terminal_pct: float
    vellus_pct: float
    intermediate_pct: float
    tv_ratio: float


@dataclass(frozen=True)
class ZoneStats:
    """Статистика одной зоны скальпа.

    Attributes:
        zone_code: Код зоны (F/P/T/O).
        hair_count: Количество волос.
        density_per_cm2: Плотность (волос/см²).
        thickness: Статистика толщины.
        ratios: Соотношения типов.
    """

    zone_code: str
    hair_count: int
    density_per_cm2: float
    thickness: ThicknessStats
    ratios: HairRatios


@dataclass(frozen=True)
class ZoneComparison:
    """Результат сравнения зоны с контрольной (затылочной).

    METHODOLOGY.md §4.8: затылок (O) — эталон, устойчив к DHT.

    Attributes:
        zone_code: Код сравниваемой зоны (F/P/T).
        zone_stats: Статистика зоны.
        control_stats: Статистика контрольной зоны (O).
        density_diff_pct: Разница плотности в % относительно контроля.
        vellus_diff_pp: Разница % vellus в процентных пунктах.
        assessment: Текстовая оценка состояния.
    """

    zone_code: str
    zone_stats: ZoneStats
    control_stats: ZoneStats
    density_diff_pct: float
    vellus_diff_pp: float
    assessment: str


def calculate_density(hair_count: int, fov_area_mm2: float) -> float:
    """Расчёт плотности волос на см².

    Формула (METHODOLOGY.md §4.1 шаг 6):
        плотность = количество / площадь_поля(мм²) * 100

    Args:
        hair_count: Количество обнаруженных волос.
        fov_area_mm2: Площадь поля зрения в мм².

    Returns:
        Плотность волос на 1 см² (1 см² = 100 мм²).
    """
    if fov_area_mm2 <= 0:
        logger.warning("Площадь поля зрения <= 0: %.4f мм²", fov_area_mm2)
        return 0.0

    density = hair_count / fov_area_mm2 * 100.0
    return round(density, 1)


def calculate_ratios(measurements: list[HairMeasurement]) -> HairRatios:
    """Подсчёт соотношений типов волос.

    Args:
        measurements: Список измерений с заполненным полем classification.

    Returns:
        HairRatios с количествами, процентами и T/V соотношением.
    """
    total = len(measurements)
    if total == 0:
        return HairRatios(
            terminal_count=0,
            vellus_count=0,
            intermediate_count=0,
            total_count=0,
            terminal_pct=0.0,
            vellus_pct=0.0,
            intermediate_pct=0.0,
            tv_ratio=0.0,
        )

    terminal = sum(1 for m in measurements if m.classification == HairType.TERMINAL.value)
    vellus = sum(1 for m in measurements if m.classification == HairType.VELLUS.value)
    intermediate = sum(
        1 for m in measurements if m.classification == HairType.INTERMEDIATE.value
    )

    terminal_pct = terminal / total * 100.0
    vellus_pct = vellus / total * 100.0
    intermediate_pct = intermediate / total * 100.0

    # T/V соотношение — vellus=0 → бесконечность, ограничиваем 99.9
    tv_ratio = terminal / vellus if vellus > 0 else 99.9

    return HairRatios(
        terminal_count=terminal,
        vellus_count=vellus,
        intermediate_count=intermediate,
        total_count=total,
        terminal_pct=round(terminal_pct, 1),
        vellus_pct=round(vellus_pct, 1),
        intermediate_pct=round(intermediate_pct, 1),
        tv_ratio=round(tv_ratio, 1),
    )


def thickness_statistics(measurements: list[HairMeasurement]) -> ThicknessStats:
    """Статистика толщины волос.

    Args:
        measurements: Список измерений.

    Returns:
        ThicknessStats с mean, median, std, min, max.
    """
    if len(measurements) == 0:
        return ThicknessStats(
            mean=0.0,
            median=0.0,
            std=0.0,
            min=0.0,
            max=0.0,
            count=0,
        )

    thicknesses = np.array([m.thickness_um for m in measurements])

    return ThicknessStats(
        mean=round(float(np.mean(thicknesses)), 1),
        median=round(float(np.median(thicknesses)), 1),
        std=round(float(np.std(thicknesses)), 1),
        min=round(float(np.min(thicknesses)), 1),
        max=round(float(np.max(thicknesses)), 1),
        count=len(measurements),
    )


def _assess_zone(density_diff_pct: float, vellus_diff_pp: float) -> str:
    """Диагностическая оценка зоны по сравнению с контролем.

    Критерии (METHODOLOGY.md §4.8):
    - density_diff < -30% И vellus_diff > 10 пп → «Выраженная миниатюризация»
    - density_diff < -15% ИЛИ vellus_diff > 5 пп → «Начальная миниатюризация»
    - Иначе → «В пределах нормы»

    Args:
        density_diff_pct: Разница плотности в % (отрицательная = хуже).
        vellus_diff_pp: Разница % vellus в процентных пунктах (положительная = хуже).

    Returns:
        Текстовая оценка на русском языке.
    """
    if density_diff_pct < -30.0 and vellus_diff_pp > 10.0:
        return "Выраженная миниатюризация"
    elif density_diff_pct < -15.0 or vellus_diff_pp > 5.0:
        return "Начальная миниатюризация"
    else:
        return "В пределах нормы"


def make_zone_stats(
    zone_code: str,
    measurements: list[HairMeasurement],
    fov_area_mm2: float,
) -> ZoneStats:
    """Формирование полной статистики по одной зоне.

    Args:
        zone_code: Код зоны (F/P/T/O).
        measurements: Измерения волос в этой зоне.
        fov_area_mm2: Площадь поля зрения в мм².

    Returns:
        ZoneStats со всеми рассчитанными показателями.
    """
    hair_count = len(measurements)
    density = calculate_density(hair_count, fov_area_mm2)
    thickness = thickness_statistics(measurements)
    ratios = calculate_ratios(measurements)

    return ZoneStats(
        zone_code=zone_code,
        hair_count=hair_count,
        density_per_cm2=density,
        thickness=thickness,
        ratios=ratios,
    )


def compare_zones(
    zones: dict[str, ZoneStats],
    control_code: str = "O",
) -> list[ZoneComparison]:
    """Сравнение зон с контрольной (затылочной).

    METHODOLOGY.md §4.8: затылок (O) генетически устойчив к DHT,
    используется как базовое значение для сравнения.

    Args:
        zones: Словарь {код_зоны: ZoneStats}.
        control_code: Код контрольной зоны (по умолчанию "O").

    Returns:
        Список ZoneComparison для каждой зоны (кроме контрольной).
        Пустой список если контрольная зона отсутствует.
    """
    if control_code not in zones:
        logger.warning(
            "Контрольная зона '%s' отсутствует, сравнение невозможно",
            control_code,
        )
        return []

    control = zones[control_code]
    comparisons: list[ZoneComparison] = []

    for code, zone in zones.items():
        if code == control_code:
            continue

        # Разница плотности в %
        if control.density_per_cm2 > 0:
            density_diff = (
                (zone.density_per_cm2 - control.density_per_cm2)
                / control.density_per_cm2
                * 100.0
            )
        else:
            density_diff = 0.0

        # Разница vellus в процентных пунктах
        vellus_diff = zone.ratios.vellus_pct - control.ratios.vellus_pct

        assessment = _assess_zone(density_diff, vellus_diff)

        comparisons.append(ZoneComparison(
            zone_code=code,
            zone_stats=zone,
            control_stats=control,
            density_diff_pct=round(density_diff, 1),
            vellus_diff_pp=round(vellus_diff, 1),
            assessment=assessment,
        ))

    logger.debug("Сравнение зон: %d зон vs контроль '%s'", len(comparisons), control_code)
    return comparisons

"""
Классификация волос по толщине.

Три категории (METHODOLOGY.md §4.6):
- Vellus: < 30 мкм (пушковый, тонкий)
- Intermediate: 30–40 мкм (промежуточный, ранняя миниатюризация)
- Terminal: > 40 мкм (толстый, пигментированный)

Пороги настраиваются через AnalysisConfig.
"""

from __future__ import annotations

import logging
from enum import Enum

from phototrichogram.core.measurement import HairMeasurement
from phototrichogram.utils.config import AnalysisConfig

logger = logging.getLogger(__name__)


class HairType(Enum):
    """Тип волоса по толщине."""

    VELLUS = "vellus"
    INTERMEDIATE = "intermediate"
    TERMINAL = "terminal"


def classify_hair(
    measurement: HairMeasurement, config: AnalysisConfig
) -> HairType:
    """Классификация одного волоса по толщине.

    Пороги (METHODOLOGY.md §4.6, §7.1):
    - thickness_um < vellus_threshold_um (30 мкм) → VELLUS
    - vellus_threshold_um <= thickness_um < intermediate_threshold_um (40 мкм) → INTERMEDIATE
    - thickness_um >= intermediate_threshold_um → TERMINAL

    Args:
        measurement: Измерения волоса (нужно поле thickness_um).
        config: Параметры анализа (vellus_threshold_um, intermediate_threshold_um).

    Returns:
        HairType — тип волоса.
    """
    thickness = measurement.thickness_um

    if thickness < config.vellus_threshold_um:
        return HairType.VELLUS
    elif thickness < config.intermediate_threshold_um:
        return HairType.INTERMEDIATE
    else:
        return HairType.TERMINAL


def classify_all(
    measurements: list[HairMeasurement], config: AnalysisConfig
) -> list[HairMeasurement]:
    """Классификация всех волос — заполняет поле classification.

    Модифицирует поле measurement.classification in-place
    и возвращает тот же список.

    Args:
        measurements: Список измерений от measure_all().
        config: Параметры анализа.

    Returns:
        Тот же список с заполненным полем classification.
    """
    counts = {t: 0 for t in HairType}

    for m in measurements:
        hair_type = classify_hair(m, config)
        m.classification = hair_type.value
        counts[hair_type] += 1

    logger.debug(
        "Классификация: terminal=%d, intermediate=%d, vellus=%d",
        counts[HairType.TERMINAL],
        counts[HairType.INTERMEDIATE],
        counts[HairType.VELLUS],
    )
    return measurements

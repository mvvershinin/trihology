"""
Диагностические выводы по результатам трихоскопического анализа.

На основе результатов анализа зон формирует текстовые заключения на русском:
- Статус каждой зоны (norm/attention/problem)
- Определение паттерна алопеции (AGA, диффузная, локальная)
- Сравнение зон с контрольной (затылочной)
- Общее заключение и рекомендации

Клинические нормы из METHODOLOGY.md §2.2:
- Плотность: 150-300 волос/см² (норма)
- Vellus: <20% (норма)
- T/V ratio: >4:1 (норма)
- Anagen: 80-90% (норма)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from phototrichogram.core.analyzer import AnalysisResult

logger = logging.getLogger(__name__)

# Названия зон на русском (METHODOLOGY.md §2.3)
ZONE_NAMES: dict[str, str] = {
    "F": "Лобная",
    "P": "Теменная",
    "T": "Височная",
    "O": "Затылочная",
}

# Предложный падеж для текстовых заключений ("в ... зоне")
_ZONE_NAMES_PREP: dict[str, str] = {
    "F": "лобной",
    "P": "теменной",
    "T": "височной",
    "O": "затылочной",
}

# Дефолтные клинические пороги (METHODOLOGY.md §2.2)
# Все передаются как параметры функций, здесь только дефолты.
_DEFAULT_DENSITY_NORMAL = 150.0          # волос/см², нижняя граница нормы
_DEFAULT_DENSITY_PROBLEM = 100.0         # волос/см², граница патологии
_DEFAULT_VELLUS_NORMAL_PCT = 20.0        # % vellus, верхняя граница нормы
_DEFAULT_VELLUS_PROBLEM_PCT = 30.0       # % vellus, граница патологии
_DEFAULT_TV_NORMAL = 4.0                 # T/V ratio, нижняя граница нормы
_DEFAULT_TV_PROBLEM = 2.0               # T/V ratio, граница патологии
_DEFAULT_DENSITY_DIFF_SIGNIFICANT = 20.0  # %, значимая разница с контролем
_DEFAULT_VELLUS_DIFF_SIGNIFICANT = 10.0   # п.п., значимая разница с контролем


@dataclass
class ZoneDiagnosis:
    """Диагностика одной зоны.

    Attributes:
        zone: Код зоны ('F', 'P', 'T', 'O').
        zone_name: Полное название зоны на русском.
        density_per_cm2: Плотность волос (волос/см²).
        terminal_pct: Процент терминальных волос.
        vellus_pct: Процент пушковых волос.
        tv_ratio: Соотношение Terminal/Vellus.
        mean_thickness_um: Средняя толщина волос (мкм).
        status: Статус зоны ('normal', 'attention', 'problem').
        findings: Список находок на русском языке.
    """

    zone: str
    zone_name: str
    density_per_cm2: float
    terminal_pct: float
    vellus_pct: float
    tv_ratio: float
    mean_thickness_um: float
    status: str
    findings: list[str] = field(default_factory=list)


@dataclass
class DiagnosisReport:
    """Полный диагностический отчёт.

    Attributes:
        zones: Диагностика по зонам {код_зоны: ZoneDiagnosis}.
        comparisons: Текстовые сравнения зон (F vs O, P vs O и т.д.).
        pattern: Код паттерна ('normal', 'aga', 'diffuse', 'localized', 'unknown').
        pattern_name: Название паттерна на русском.
        conclusion: Общее текстовое заключение (2-3 предложения).
        recommendations: Список рекомендаций на русском.
        severity: Код тяжести ('normal', 'mild', 'moderate', 'severe').
        severity_name: Название степени тяжести на русском.
    """

    zones: dict[str, ZoneDiagnosis]
    comparisons: list[str] = field(default_factory=list)
    pattern: str = "unknown"
    pattern_name: str = "Не определён"
    conclusion: str = ""
    recommendations: list[str] = field(default_factory=list)
    severity: str = "normal"
    severity_name: str = "Норма"


def get_zone_status(
    density: float,
    vellus_pct: float,
    tv_ratio: float,
    *,
    density_normal: float = _DEFAULT_DENSITY_NORMAL,
    density_problem: float = _DEFAULT_DENSITY_PROBLEM,
    vellus_normal_pct: float = _DEFAULT_VELLUS_NORMAL_PCT,
    vellus_problem_pct: float = _DEFAULT_VELLUS_PROBLEM_PCT,
    tv_normal: float = _DEFAULT_TV_NORMAL,
    tv_problem: float = _DEFAULT_TV_PROBLEM,
) -> str:
    """Определение статуса зоны по клиническим порогам.

    Критерии (METHODOLOGY.md §2.2):
    - 'problem': плотность <100 ИЛИ vellus >30% ИЛИ T/V <2
    - 'attention': плотность 100-150 ИЛИ vellus 20-30% ИЛИ T/V 2-4
    - 'normal': все показатели в норме

    Args:
        density: Плотность волос (волос/см²).
        vellus_pct: Процент пушковых волос.
        tv_ratio: Соотношение Terminal/Vellus.
        density_normal: Нижняя граница нормы плотности.
        density_problem: Граница патологии плотности.
        vellus_normal_pct: Верхняя граница нормы vellus %.
        vellus_problem_pct: Граница патологии vellus %.
        tv_normal: Нижняя граница нормы T/V.
        tv_problem: Граница патологии T/V.

    Returns:
        Статус: 'normal', 'attention' или 'problem'.
    """
    # Сначала проверяем problem (наиболее серьёзный)
    if density < density_problem or vellus_pct > vellus_problem_pct or tv_ratio < tv_problem:
        return "problem"

    # Затем attention
    if density < density_normal or vellus_pct > vellus_normal_pct or tv_ratio < tv_normal:
        return "attention"

    return "normal"


def _generate_zone_findings(
    zone_code: str,
    density: float,
    vellus_pct: float,
    terminal_pct: float,
    tv_ratio: float,
    mean_thickness_um: float,
    *,
    density_normal: float = _DEFAULT_DENSITY_NORMAL,
    density_problem: float = _DEFAULT_DENSITY_PROBLEM,
    vellus_normal_pct: float = _DEFAULT_VELLUS_NORMAL_PCT,
    vellus_problem_pct: float = _DEFAULT_VELLUS_PROBLEM_PCT,
    tv_normal: float = _DEFAULT_TV_NORMAL,
    tv_problem: float = _DEFAULT_TV_PROBLEM,
) -> list[str]:
    """Генерация списка находок для одной зоны.

    Каждая находка — отдельное наблюдение на русском языке.

    Args:
        zone_code: Код зоны.
        density: Плотность волос/см².
        vellus_pct: % пушковых волос.
        terminal_pct: % терминальных волос.
        tv_ratio: Соотношение Terminal/Vellus.
        mean_thickness_um: Средняя толщина (мкм).
        density_normal: Нижняя граница нормы плотности.
        density_problem: Граница патологии плотности.
        vellus_normal_pct: Верхняя граница нормы vellus %.
        vellus_problem_pct: Граница патологии vellus %.
        tv_normal: Нижняя граница нормы T/V.
        tv_problem: Граница патологии T/V.

    Returns:
        Список текстовых находок.
    """
    findings: list[str] = []

    # Плотность
    if density < density_problem:
        findings.append(
            f"Выраженное снижение плотности волос ({density:.0f} волос/см\u00B2, "
            f"норма >{density_normal:.0f})"
        )
    elif density < density_normal:
        findings.append(
            f"Умеренное снижение плотности волос ({density:.0f} волос/см\u00B2, "
            f"норма >{density_normal:.0f})"
        )
    else:
        findings.append(f"Плотность волос в пределах нормы ({density:.0f} волос/см\u00B2)")

    # Vellus
    if vellus_pct > vellus_problem_pct:
        findings.append(
            f"Значительное повышение доли пушковых волос ({vellus_pct:.1f}%, "
            f"норма <{vellus_normal_pct:.0f}%)"
        )
    elif vellus_pct > vellus_normal_pct:
        findings.append(
            f"Повышение доли пушковых волос ({vellus_pct:.1f}%, "
            f"норма <{vellus_normal_pct:.0f}%)"
        )

    # T/V ratio
    if tv_ratio < tv_problem:
        findings.append(
            f"Критическое снижение соотношения T/V ({tv_ratio:.1f}, "
            f"норма >{tv_normal:.0f})"
        )
    elif tv_ratio < tv_normal:
        findings.append(
            f"Снижение соотношения T/V ({tv_ratio:.1f}, норма >{tv_normal:.0f})"
        )

    # Средняя толщина — дополнительная информация
    if mean_thickness_um > 0:
        if mean_thickness_um < 30.0:
            findings.append(
                f"Низкая средняя толщина волос ({mean_thickness_um:.1f} мкм), "
                "признаки миниатюризации"
            )
        elif mean_thickness_um < 40.0:
            findings.append(
                f"Средняя толщина волос в пограничном диапазоне ({mean_thickness_um:.1f} мкм)"
            )

    return findings


def diagnose_zone(
    zone_code: str,
    analysis_result: AnalysisResult,
    *,
    density_normal: float = _DEFAULT_DENSITY_NORMAL,
    density_problem: float = _DEFAULT_DENSITY_PROBLEM,
    vellus_normal_pct: float = _DEFAULT_VELLUS_NORMAL_PCT,
    vellus_problem_pct: float = _DEFAULT_VELLUS_PROBLEM_PCT,
    tv_normal: float = _DEFAULT_TV_NORMAL,
    tv_problem: float = _DEFAULT_TV_PROBLEM,
) -> ZoneDiagnosis:
    """Диагностика одной зоны по результату анализа.

    Извлекает показатели из AnalysisResult, определяет статус зоны
    и генерирует список находок на русском языке.

    Args:
        zone_code: Код зоны ('F', 'P', 'T', 'O').
        analysis_result: Результат анализа снимка зоны.
        density_normal: Нижняя граница нормы плотности (волос/см²).
        density_problem: Граница патологии плотности.
        vellus_normal_pct: Верхняя граница нормы vellus %.
        vellus_problem_pct: Граница патологии vellus %.
        tv_normal: Нижняя граница нормы T/V.
        tv_problem: Граница патологии T/V.

    Returns:
        ZoneDiagnosis с полным описанием состояния зоны.
    """
    density = analysis_result.density_per_cm2
    vellus_pct = analysis_result.ratios.vellus_pct
    terminal_pct = analysis_result.ratios.terminal_pct
    tv_ratio = analysis_result.ratios.tv_ratio
    mean_thickness = analysis_result.thickness.mean

    threshold_kwargs = {
        "density_normal": density_normal,
        "density_problem": density_problem,
        "vellus_normal_pct": vellus_normal_pct,
        "vellus_problem_pct": vellus_problem_pct,
        "tv_normal": tv_normal,
        "tv_problem": tv_problem,
    }

    status = get_zone_status(density, vellus_pct, tv_ratio, **threshold_kwargs)

    findings = _generate_zone_findings(
        zone_code, density, vellus_pct, terminal_pct,
        tv_ratio, mean_thickness, **threshold_kwargs,
    )

    zone_name = ZONE_NAMES.get(zone_code, zone_code)

    logger.debug(
        "Диагностика зоны %s (%s): статус=%s, плотность=%.0f, vellus=%.1f%%, T/V=%.1f",
        zone_code, zone_name, status, density, vellus_pct, tv_ratio,
    )

    return ZoneDiagnosis(
        zone=zone_code,
        zone_name=zone_name,
        density_per_cm2=density,
        terminal_pct=terminal_pct,
        vellus_pct=vellus_pct,
        tv_ratio=tv_ratio,
        mean_thickness_um=mean_thickness,
        status=status,
        findings=findings,
    )


def _compare_with_control(
    zone_diag: ZoneDiagnosis,
    control_diag: ZoneDiagnosis,
    *,
    density_diff_significant: float = _DEFAULT_DENSITY_DIFF_SIGNIFICANT,
    vellus_diff_significant: float = _DEFAULT_VELLUS_DIFF_SIGNIFICANT,
) -> list[str]:
    """Текстовое сравнение зоны с контрольной (затылочной).

    Критерии значимости (METHODOLOGY.md §2.2, §4.8):
    - Разница плотности >20% — значимая
    - Разница vellus >10 п.п. — значимая

    Args:
        zone_diag: Диагностика сравниваемой зоны.
        control_diag: Диагностика контрольной зоны (O).
        density_diff_significant: Порог значимой разницы плотности (%).
        vellus_diff_significant: Порог значимой разницы vellus (п.п.).

    Returns:
        Список текстовых сравнений.
    """
    texts: list[str] = []
    zone_name = zone_diag.zone_name
    control_name = control_diag.zone_name

    # Разница плотности
    if control_diag.density_per_cm2 > 0:
        density_diff_pct = (
            (zone_diag.density_per_cm2 - control_diag.density_per_cm2)
            / control_diag.density_per_cm2
            * 100.0
        )
    else:
        density_diff_pct = 0.0

    # Разница vellus (в процентных пунктах)
    vellus_diff_pp = zone_diag.vellus_pct - control_diag.vellus_pct

    control_prep = _ZONE_NAMES_PREP.get(control_diag.zone, control_name.lower())

    if abs(density_diff_pct) > density_diff_significant:
        direction = "снижение" if density_diff_pct < 0 else "повышение"
        texts.append(
            f"{zone_name} зона: {direction} плотности на {abs(density_diff_pct):.0f}% "
            f"относительно контроля ({control_prep})"
        )

    if vellus_diff_pp > vellus_diff_significant:
        texts.append(
            f"{zone_name} зона: повышение доли пушковых волос на "
            f"{vellus_diff_pp:.0f} п.п. относительно контроля"
        )

    if not texts:
        texts.append(
            f"{zone_name} зона: показатели сопоставимы с контролем ({control_prep})"
        )

    return texts


def _detect_pattern(
    zone_diagnoses: dict[str, ZoneDiagnosis],
    control_zone: str = "O",
) -> tuple[str, str]:
    """Определение паттерна алопеции по совокупности зон.

    Паттерны (METHODOLOGY.md §2.5):
    - AGA: F и/или P снижены, O в норме
    - Диффузная: все зоны снижены равномерно (включая O)
    - Локальная: одна зона снижена, остальные в норме
    - Норма: все зоны в норме

    Args:
        zone_diagnoses: Диагностика по зонам.
        control_zone: Код контрольной зоны.

    Returns:
        Кортеж (код_паттерна, название_на_русском).
    """
    statuses = {code: diag.status for code, diag in zone_diagnoses.items()}

    control_status = statuses.get(control_zone, "normal")
    non_control = {
        code: status for code, status in statuses.items() if code != control_zone
    }

    # Подсчёт проблемных и пограничных зон (без контроля)
    problem_zones = [code for code, s in non_control.items() if s == "problem"]
    attention_zones = [code for code, s in non_control.items() if s == "attention"]
    affected_zones = problem_zones + attention_zones

    # Все зоны в норме
    if not affected_zones and control_status == "normal":
        return "normal", "Норма"

    # Контроль тоже снижен — диффузная алопеция
    if control_status in ("problem", "attention") and affected_zones:
        return "diffuse", "Диффузная алопеция"

    # Контроль в норме, поражены F и/или P — AGA
    control_ok = control_status == "normal"
    f_affected = "F" in affected_zones
    p_affected = "P" in affected_zones

    if control_ok and (f_affected or p_affected):
        return "aga", "Андрогенетическая алопеция"

    # Только одна зона поражена (не F и не P), контроль в норме
    if control_ok and len(affected_zones) == 1:
        return "localized", "Локальное снижение показателей"

    # Несколько зон поражены, не подходит под AGA
    if control_ok and len(affected_zones) > 1:
        return "aga", "Андрогенетическая алопеция"

    return "unknown", "Паттерн не определён"


def _determine_severity(zone_diagnoses: dict[str, ZoneDiagnosis]) -> tuple[str, str]:
    """Определение общей степени тяжести.

    Тяжесть определяется по наихудшему показателю:
    - severe: хотя бы одна зона 'problem' с плотностью <100
    - moderate: хотя бы одна зона 'problem'
    - mild: только 'attention'
    - normal: все в норме

    Args:
        zone_diagnoses: Диагностика по зонам.

    Returns:
        Кортеж (код_тяжести, название_на_русском).
    """
    has_problem = False
    has_attention = False
    has_severe = False

    for diag in zone_diagnoses.values():
        if diag.status == "problem":
            has_problem = True
            if diag.density_per_cm2 < _DEFAULT_DENSITY_PROBLEM:
                has_severe = True
        elif diag.status == "attention":
            has_attention = True

    if has_severe:
        return "severe", "Выраженная"
    if has_problem:
        return "moderate", "Умеренная"
    if has_attention:
        return "mild", "Начальная"
    return "normal", "Норма"


def _build_conclusion(
    pattern: str,
    pattern_name: str,
    severity: str,
    severity_name: str,
    zone_diagnoses: dict[str, ZoneDiagnosis],
    control_zone: str = "O",
) -> str:
    """Формирование общего текстового заключения.

    Args:
        pattern: Код паттерна.
        pattern_name: Название паттерна на русском.
        severity: Код тяжести.
        severity_name: Название тяжести на русском.
        zone_diagnoses: Диагностика по зонам.
        control_zone: Код контрольной зоны.

    Returns:
        Текстовое заключение (2-3 предложения).
    """
    if pattern == "normal":
        return (
            "Плотность и соотношение типов волос во всех зонах в пределах нормы. "
            "Признаков миниатюризации не выявлено."
        )

    parts: list[str] = []

    # Описание поражённых зон
    affected = [
        diag for code, diag in zone_diagnoses.items()
        if code != control_zone and diag.status != "normal"
    ]

    if pattern == "aga":
        zone_list = ", ".join(
            f"{_ZONE_NAMES_PREP.get(d.zone, d.zone_name.lower())} ({d.zone})"
            for d in affected
        )
        parts.append(
            f"Выявлены изменения, характерные для андрогенетической алопеции "
            f"в {zone_list} зонах"
        )

        # Конкретика по наиболее поражённой зоне
        worst = max(affected, key=lambda d: d.vellus_pct)
        worst_prep = _ZONE_NAMES_PREP.get(worst.zone, worst.zone_name.lower())
        control_diag = zone_diagnoses.get(control_zone)
        if control_diag and control_diag.density_per_cm2 > 0:
            diff_pct = abs(
                (worst.density_per_cm2 - control_diag.density_per_cm2)
                / control_diag.density_per_cm2
                * 100.0
            )
            parts.append(
                f"Снижение плотности в {worst_prep} зоне на {diff_pct:.0f}% "
                f"относительно контроля, повышение доли пушковых волос до {worst.vellus_pct:.0f}%"
            )

    elif pattern == "diffuse":
        parts.append(
            "Снижение показателей выявлено во всех зонах, включая затылочную (контрольную)"
        )
        parts.append(
            "Паттерн характерен для диффузного выпадения волос (telogen effluvium)"
        )

    elif pattern == "localized":
        zone_list = ", ".join(
            f"{_ZONE_NAMES_PREP.get(d.zone, d.zone_name.lower())} ({d.zone})"
            for d in affected
        )
        parts.append(
            f"Локальное снижение показателей в {zone_list} зоне"
        )
        parts.append("Рекомендуется дополнительное обследование для уточнения диагноза")

    elif pattern == "unknown":
        parts.append(
            "Выявлены отклонения от нормы, однако паттерн не соответствует "
            "типичным формам алопеции"
        )
        parts.append("Рекомендуется консультация трихолога")

    if severity != "normal" and pattern != "normal":
        parts.append(f"Степень выраженности: {severity_name.lower()}")

    return ". ".join(parts) + "."


def _build_recommendations(
    pattern: str,
    severity: str,
    zone_diagnoses: dict[str, ZoneDiagnosis],
) -> list[str]:
    """Формирование списка рекомендаций.

    Args:
        pattern: Код паттерна.
        severity: Код тяжести.
        zone_diagnoses: Диагностика по зонам.

    Returns:
        Список рекомендаций на русском.
    """
    recommendations: list[str] = []

    if pattern == "normal":
        recommendations.append("Контрольное обследование через 12 месяцев")
        return recommendations

    # Общие рекомендации при отклонениях
    recommendations.append("Консультация врача-трихолога для уточнения диагноза")

    if pattern == "aga":
        recommendations.append(
            "Повторное обследование через 3-6 месяцев для оценки динамики"
        )
        if severity in ("moderate", "severe"):
            recommendations.append(
                "Рассмотреть назначение терапии (по решению врача)"
            )

    elif pattern == "diffuse":
        recommendations.append("Исключить дефицит железа, заболевания щитовидной железы")
        recommendations.append(
            "Повторное обследование через 3 месяца для оценки динамики"
        )

    elif pattern == "localized":
        recommendations.append("Дополнительное обследование для уточнения причины")
        recommendations.append("Повторное обследование через 3 месяца")

    else:
        recommendations.append("Повторное обследование через 3-6 месяцев")

    # Фотофиксация
    recommendations.append("Фотофиксация для отслеживания динамики")

    return recommendations


def diagnose_multizone(
    zones: dict[str, AnalysisResult],
    control_zone: str = "O",
    *,
    density_normal: float = _DEFAULT_DENSITY_NORMAL,
    density_problem: float = _DEFAULT_DENSITY_PROBLEM,
    vellus_normal_pct: float = _DEFAULT_VELLUS_NORMAL_PCT,
    vellus_problem_pct: float = _DEFAULT_VELLUS_PROBLEM_PCT,
    tv_normal: float = _DEFAULT_TV_NORMAL,
    tv_problem: float = _DEFAULT_TV_PROBLEM,
    density_diff_significant: float = _DEFAULT_DENSITY_DIFF_SIGNIFICANT,
    vellus_diff_significant: float = _DEFAULT_VELLUS_DIFF_SIGNIFICANT,
) -> DiagnosisReport:
    """Мультизональная диагностика -- сравнение зон с контролем.

    Выполняет полный цикл диагностики:
    1. Диагностика каждой зоны отдельно
    2. Сравнение зон с контрольной (затылочной)
    3. Определение паттерна алопеции
    4. Оценка тяжести
    5. Формирование заключения и рекомендаций

    Args:
        zones: Словарь {код_зоны: AnalysisResult}.
        control_zone: Код контрольной зоны (по умолчанию 'O').
        density_normal: Нижняя граница нормы плотности (волос/см²).
        density_problem: Граница патологии плотности.
        vellus_normal_pct: Верхняя граница нормы vellus %.
        vellus_problem_pct: Граница патологии vellus %.
        tv_normal: Нижняя граница нормы T/V.
        tv_problem: Граница патологии T/V.
        density_diff_significant: Порог значимой разницы плотности (%).
        vellus_diff_significant: Порог значимой разницы vellus (п.п.).

    Returns:
        DiagnosisReport с полным диагностическим заключением.
    """
    if not zones:
        logger.warning("Нет данных для диагностики (пустой словарь зон)")
        return DiagnosisReport(zones={})

    threshold_kwargs = {
        "density_normal": density_normal,
        "density_problem": density_problem,
        "vellus_normal_pct": vellus_normal_pct,
        "vellus_problem_pct": vellus_problem_pct,
        "tv_normal": tv_normal,
        "tv_problem": tv_problem,
    }

    # 1. Диагностика каждой зоны
    zone_diagnoses: dict[str, ZoneDiagnosis] = {}
    for code, result in zones.items():
        zone_diagnoses[code] = diagnose_zone(code, result, **threshold_kwargs)

    # 2. Сравнение с контролем
    comparisons: list[str] = []
    control_diag = zone_diagnoses.get(control_zone)
    if control_diag:
        for code, diag in zone_diagnoses.items():
            if code == control_zone:
                continue
            comp_texts = _compare_with_control(
                diag, control_diag,
                density_diff_significant=density_diff_significant,
                vellus_diff_significant=vellus_diff_significant,
            )
            comparisons.extend(comp_texts)
    else:
        logger.warning(
            "Контрольная зона '%s' отсутствует, сравнение невозможно",
            control_zone,
        )

    # 3. Определение паттерна
    pattern, pattern_name = _detect_pattern(zone_diagnoses, control_zone)

    # 4. Степень тяжести
    severity, severity_name = _determine_severity(zone_diagnoses)

    # 5. Заключение и рекомендации
    conclusion = _build_conclusion(
        pattern, pattern_name, severity, severity_name,
        zone_diagnoses, control_zone,
    )
    recommendations = _build_recommendations(pattern, severity, zone_diagnoses)

    logger.info(
        "Мультизональная диагностика: паттерн=%s, тяжесть=%s, зон=%d",
        pattern, severity, len(zone_diagnoses),
    )

    return DiagnosisReport(
        zones=zone_diagnoses,
        comparisons=comparisons,
        pattern=pattern,
        pattern_name=pattern_name,
        conclusion=conclusion,
        recommendations=recommendations,
        severity=severity,
        severity_name=severity_name,
    )

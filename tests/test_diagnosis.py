"""
Тесты модуля диагностических выводов (core/diagnosis.py).

Покрывает:
- get_zone_status: все три статуса по всем критериям
- diagnose_zone: формирование ZoneDiagnosis из AnalysisResult
- diagnose_multizone: паттерны (AGA, диффузная, локальная, норма),
  сравнения с контролем, заключения, рекомендации
- Граничные случаи: пустые данные, отсутствие контроля, одна зона
"""

from __future__ import annotations

import numpy as np
import pytest

from phototrichogram.core.analyzer import AnalysisResult
from phototrichogram.core.diagnosis import (
    DiagnosisReport,
    ZoneDiagnosis,
    diagnose_multizone,
    diagnose_zone,
    get_zone_status,
)
from phototrichogram.core.stats import HairRatios, ThicknessStats


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

def _make_ratios(
    terminal_pct: float = 80.0,
    vellus_pct: float = 15.0,
    tv_ratio: float = 5.3,
) -> HairRatios:
    """Создание HairRatios с нужными процентами."""
    intermediate_pct = 100.0 - terminal_pct - vellus_pct
    total = 100
    return HairRatios(
        terminal_count=int(terminal_pct),
        vellus_count=int(vellus_pct),
        intermediate_count=int(intermediate_pct),
        total_count=total,
        terminal_pct=terminal_pct,
        vellus_pct=vellus_pct,
        intermediate_pct=round(intermediate_pct, 1),
        tv_ratio=tv_ratio,
    )


def _make_thickness(mean: float = 50.0) -> ThicknessStats:
    """Создание ThicknessStats с нужным средним."""
    return ThicknessStats(
        mean=mean,
        median=mean,
        std=5.0,
        min=max(mean - 15.0, 5.0),
        max=mean + 20.0,
        count=100,
    )


def _make_result(
    density: float = 200.0,
    terminal_pct: float = 80.0,
    vellus_pct: float = 15.0,
    tv_ratio: float = 5.3,
    mean_thickness: float = 50.0,
) -> AnalysisResult:
    """Создание AnalysisResult с заданными показателями."""
    return AnalysisResult(
        measurements=[],
        hair_count=int(density * 0.106),
        density_per_cm2=density,
        ratios=_make_ratios(terminal_pct, vellus_pct, tv_ratio),
        thickness=_make_thickness(mean_thickness),
        mask=np.zeros((480, 640), dtype=np.uint8),
        annotated_image=None,
        fov_area_mm2=10.6,
    )


# ---------------------------------------------------------------------------
# get_zone_status
# ---------------------------------------------------------------------------

class TestGetZoneStatus:
    """Тесты определения статуса зоны."""

    def test_normal_all_good(self) -> None:
        """Все показатели в норме -> normal."""
        assert get_zone_status(200, 15, 5.0) == "normal"

    def test_normal_boundary(self) -> None:
        """Ровно на границе нормы -> normal."""
        assert get_zone_status(150, 20, 4.0) == "normal"

    # --- attention ---

    def test_attention_low_density(self) -> None:
        """Плотность 100-150 -> attention."""
        assert get_zone_status(120, 15, 5.0) == "attention"

    def test_attention_high_vellus(self) -> None:
        """Vellus 20-30% -> attention."""
        assert get_zone_status(200, 25, 5.0) == "attention"

    def test_attention_low_tv(self) -> None:
        """T/V 2-4 -> attention."""
        assert get_zone_status(200, 15, 3.0) == "attention"

    def test_attention_boundary_density(self) -> None:
        """Плотность ровно 100 -> attention (не problem)."""
        assert get_zone_status(100, 15, 5.0) == "attention"

    # --- problem ---

    def test_problem_very_low_density(self) -> None:
        """Плотность <100 -> problem."""
        assert get_zone_status(80, 15, 5.0) == "problem"

    def test_problem_very_high_vellus(self) -> None:
        """Vellus >30% -> problem."""
        assert get_zone_status(200, 35, 5.0) == "problem"

    def test_problem_very_low_tv(self) -> None:
        """T/V <2 -> problem."""
        assert get_zone_status(200, 15, 1.5) == "problem"

    def test_problem_all_bad(self) -> None:
        """Все показатели плохие -> problem."""
        assert get_zone_status(50, 45, 1.0) == "problem"

    # --- Пользовательские пороги ---

    def test_custom_thresholds(self) -> None:
        """Пользовательские пороги переопределяют дефолты."""
        # С дефолтными порогами density=120 -> attention
        assert get_zone_status(120, 10, 5.0) == "attention"
        # С пониженным порогом нормы 100 -> normal
        assert get_zone_status(120, 10, 5.0, density_normal=100) == "normal"


# ---------------------------------------------------------------------------
# diagnose_zone
# ---------------------------------------------------------------------------

class TestDiagnoseZone:
    """Тесты диагностики одной зоны."""

    def test_normal_zone(self) -> None:
        """Нормальная зона -> status=normal, есть позитивная находка."""
        result = _make_result(density=200, vellus_pct=12, tv_ratio=6.5)
        diag = diagnose_zone("O", result)

        assert diag.zone == "O"
        assert diag.zone_name == "Затылочная"
        assert diag.status == "normal"
        assert diag.density_per_cm2 == 200
        assert diag.vellus_pct == 12
        assert diag.tv_ratio == 6.5
        assert len(diag.findings) > 0
        assert any("норм" in f.lower() for f in diag.findings)

    def test_problem_zone_findings(self) -> None:
        """Проблемная зона -> status=problem, находки о снижении."""
        result = _make_result(density=80, vellus_pct=35, tv_ratio=1.5, mean_thickness=25)
        diag = diagnose_zone("F", result)

        assert diag.zone == "F"
        assert diag.zone_name == "Фронтальная"
        assert diag.status == "problem"
        # Должны быть находки о плотности, vellus, T/V, толщине
        findings_text = " ".join(diag.findings)
        assert "снижение плотности" in findings_text.lower()
        assert "пушковых" in findings_text.lower()
        assert "T/V" in findings_text

    def test_attention_zone(self) -> None:
        """Пограничная зона -> status=attention."""
        result = _make_result(density=130, vellus_pct=22, tv_ratio=3.5, mean_thickness=38)
        diag = diagnose_zone("P", result)

        assert diag.status == "attention"
        assert diag.zone_name == "Париетальная"

    def test_unknown_zone_code(self) -> None:
        """Неизвестный код зоны -> используется сам код как имя."""
        result = _make_result(density=200)
        diag = diagnose_zone("X", result)

        assert diag.zone == "X"
        assert diag.zone_name == "X"  # fallback

    def test_findings_contain_numbers(self) -> None:
        """Находки содержат числовые значения показателей."""
        result = _make_result(density=90, vellus_pct=32, tv_ratio=1.8)
        diag = diagnose_zone("F", result)

        findings_text = " ".join(diag.findings)
        assert "90" in findings_text  # плотность
        assert "32" in findings_text  # vellus
        assert "1.8" in findings_text  # T/V

    def test_thickness_borderline_finding(self) -> None:
        """Средняя толщина 30-40 мкм -> пограничная находка."""
        result = _make_result(density=200, vellus_pct=10, tv_ratio=8.0, mean_thickness=35)
        diag = diagnose_zone("F", result)

        findings_text = " ".join(diag.findings)
        assert "пограничн" in findings_text.lower()

    def test_thickness_low_finding(self) -> None:
        """Средняя толщина <30 мкм -> находка о миниатюризации."""
        result = _make_result(density=200, vellus_pct=10, tv_ratio=8.0, mean_thickness=22)
        diag = diagnose_zone("F", result)

        findings_text = " ".join(diag.findings)
        assert "миниатюризаци" in findings_text.lower()


# ---------------------------------------------------------------------------
# diagnose_multizone
# ---------------------------------------------------------------------------

class TestDiagnoseMultizone:
    """Тесты мультизональной диагностики."""

    # --- Паттерны ---

    def test_pattern_normal(self) -> None:
        """Все зоны в норме -> pattern=normal."""
        zones = {
            "F": _make_result(200, 85, 12, 7.0, 50),
            "P": _make_result(210, 88, 10, 8.8, 52),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)

        assert report.pattern == "normal"
        assert report.pattern_name == "Норма"
        assert report.severity == "normal"
        assert report.severity_name == "Норма"
        assert "норм" in report.conclusion.lower()
        assert len(report.recommendations) > 0

    def test_pattern_aga(self) -> None:
        """F и P снижены, O в норме -> pattern=aga."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
            "P": _make_result(95, 55, 35, 1.6, 32),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        assert report.pattern == "aga"
        assert "андрогенетическ" in report.pattern_name.lower()
        assert report.severity in ("moderate", "severe")
        assert "андрогенетическ" in report.conclusion.lower()

    def test_pattern_aga_f_only(self) -> None:
        """Только F снижена, O в норме -> pattern=aga."""
        zones = {
            "F": _make_result(100, 70, 25, 2.8, 38),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)
        assert report.pattern == "aga"

    def test_pattern_aga_p_only(self) -> None:
        """Только P снижена, O в норме -> pattern=aga."""
        zones = {
            "P": _make_result(90, 60, 32, 1.9, 30),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)
        assert report.pattern == "aga"

    def test_pattern_diffuse(self) -> None:
        """Все зоны включая O снижены -> pattern=diffuse."""
        zones = {
            "F": _make_result(90, 60, 32, 1.9, 30),
            "P": _make_result(85, 55, 35, 1.6, 28),
            "O": _make_result(95, 58, 33, 1.8, 29),
        }
        report = diagnose_multizone(zones)

        assert report.pattern == "diffuse"
        assert "диффузн" in report.pattern_name.lower()
        assert "telogen" in report.conclusion.lower() or "диффузн" in report.conclusion.lower()

    def test_pattern_localized(self) -> None:
        """Одна нетипичная зона снижена, O в норме -> pattern=localized."""
        zones = {
            "T": _make_result(90, 60, 32, 1.9, 30),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)

        assert report.pattern == "localized"
        assert "локальн" in report.pattern_name.lower()

    # --- Тяжесть ---

    def test_severity_mild(self) -> None:
        """Только attention зоны -> severity=mild."""
        zones = {
            "F": _make_result(130, 72, 22, 3.3, 42),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)
        assert report.severity == "mild"
        assert report.severity_name == "Начальная"

    def test_severity_moderate(self) -> None:
        """problem зона с плотностью >100 -> severity=moderate."""
        zones = {
            "F": _make_result(120, 55, 35, 1.6, 32),  # problem по vellus и T/V
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)
        assert report.severity == "moderate"
        assert report.severity_name == "Умеренная"

    def test_severity_severe(self) -> None:
        """problem зона с плотностью <100 -> severity=severe."""
        zones = {
            "F": _make_result(70, 50, 40, 1.2, 25),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)
        assert report.severity == "severe"
        assert report.severity_name == "Выраженная"

    # --- Сравнения с контролем ---

    def test_comparisons_significant_density(self) -> None:
        """Значимая разница плотности отражена в сравнениях."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        assert len(report.comparisons) > 0
        comp_text = " ".join(report.comparisons)
        assert "плотности" in comp_text.lower()
        assert "50%" in comp_text  # (220-110)/220 = 50%

    def test_comparisons_significant_vellus(self) -> None:
        """Значимая разница vellus отражена в сравнениях."""
        zones = {
            "F": _make_result(200, 65, 25, 2.6, 45),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        comp_text = " ".join(report.comparisons)
        assert "пушковых" in comp_text.lower()

    def test_comparisons_no_significant_diff(self) -> None:
        """Незначимая разница -> сообщение о сопоставимости."""
        zones = {
            "F": _make_result(210, 87, 11, 7.9, 52),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        comp_text = " ".join(report.comparisons)
        assert "сопоставимы" in comp_text.lower()

    # --- Рекомендации ---

    def test_recommendations_normal(self) -> None:
        """При норме -> рекомендация контроля через 12 мес."""
        zones = {
            "F": _make_result(200, 85, 12, 7.0, 50),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)

        assert any("12 месяцев" in r for r in report.recommendations)

    def test_recommendations_aga(self) -> None:
        """При AGA -> рекомендация трихолога и повторного обследования."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        recs_text = " ".join(report.recommendations)
        assert "трихолог" in recs_text.lower()
        assert "повторн" in recs_text.lower()

    def test_recommendations_diffuse(self) -> None:
        """При диффузной -> рекомендация про железо/щитовидку."""
        zones = {
            "F": _make_result(90, 60, 32, 1.9, 30),
            "P": _make_result(85, 55, 35, 1.6, 28),
            "O": _make_result(95, 58, 33, 1.8, 29),
        }
        report = diagnose_multizone(zones)

        recs_text = " ".join(report.recommendations)
        assert "желез" in recs_text.lower()  # железо / щитовидной железы

    def test_recommendations_severe_aga_includes_therapy(self) -> None:
        """При тяжёлой AGA -> рекомендация терапии."""
        zones = {
            "F": _make_result(70, 50, 40, 1.2, 25),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)

        recs_text = " ".join(report.recommendations)
        assert "терапи" in recs_text.lower()

    # --- Граничные случаи ---

    def test_empty_zones(self) -> None:
        """Пустой словарь -> пустой отчёт."""
        report = diagnose_multizone({})

        assert report.zones == {}
        assert report.pattern == "unknown"
        assert report.conclusion == ""

    def test_single_zone_no_control(self) -> None:
        """Одна зона без контроля -> диагностика без сравнений."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
        }
        report = diagnose_multizone(zones)

        assert "F" in report.zones
        assert len(report.comparisons) == 0  # нет O -> нет сравнений

    def test_only_control_zone(self) -> None:
        """Только контрольная зона -> normal (нечего сравнивать)."""
        zones = {
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)

        assert report.pattern == "normal"

    def test_custom_control_zone(self) -> None:
        """Нестандартная контрольная зона."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
            "P": _make_result(220, 90, 8, 11.0, 55),  # P как контроль
        }
        report = diagnose_multizone(zones, control_zone="P")

        assert len(report.comparisons) > 0
        assert report.zones["F"].status != "normal"

    def test_all_four_zones(self) -> None:
        """Полный анализ с 4 зонами."""
        zones = {
            "F": _make_result(100, 65, 28, 2.3, 35),
            "P": _make_result(90, 55, 35, 1.6, 32),
            "T": _make_result(130, 72, 22, 3.3, 42),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }
        report = diagnose_multizone(zones)

        assert len(report.zones) == 4
        assert report.pattern == "aga"
        assert len(report.comparisons) >= 3  # F, P, T vs O

    # --- Русский текст ---

    def test_conclusion_in_russian(self) -> None:
        """Заключение на русском языке."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        # Проверяем наличие кириллицы
        assert any("\u0400" <= c <= "\u04ff" for c in report.conclusion)
        assert report.conclusion.endswith(".")

    def test_findings_in_russian(self) -> None:
        """Находки на русском языке."""
        result = _make_result(80, 55, 35, 1.5, 25)
        diag = diagnose_zone("F", result)

        for finding in diag.findings:
            assert any("\u0400" <= c <= "\u04ff" for c in finding)

    def test_zone_names_correct(self) -> None:
        """Названия зон корректные."""
        for code, name in [("F", "Фронтальная"), ("P", "Париетальная"),
                           ("T", "Височная"), ("O", "Затылочная")]:
            result = _make_result()
            diag = diagnose_zone(code, result)
            assert diag.zone_name == name

    # --- Пользовательские пороги в multizone ---

    def test_custom_thresholds_multizone(self) -> None:
        """Пользовательские пороги изменяют результат."""
        zones = {
            "F": _make_result(120, 75, 18, 4.2, 45),
            "O": _make_result(220, 90, 8, 11.0, 55),
        }

        # С дефолтными порогами: density=120 < 150 -> attention
        report_default = diagnose_multizone(zones)
        assert report_default.zones["F"].status == "attention"

        # С пониженным порогом: density_normal=100 -> normal
        report_custom = diagnose_multizone(zones, density_normal=100.0)
        assert report_custom.zones["F"].status == "normal"

    def test_proper_declension_in_conclusion(self) -> None:
        """Правильные падежи в заключении (предложный падеж зон)."""
        zones = {
            "F": _make_result(110, 65, 28, 2.3, 35),
            "O": _make_result(220, 88, 10, 8.8, 55),
        }
        report = diagnose_multizone(zones)

        # Должно быть "в фронтальной", а не "в фронтальная"
        assert "фронтальной" in report.conclusion
        assert "в фронтальная" not in report.conclusion

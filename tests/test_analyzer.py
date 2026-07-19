"""
Интеграционные тесты полного пайплайна анализа (core/analyzer.py).

Покрывает:
- Analyzer.analyze / analyze_with_annotation на синтетическом снимке
- Граничные случаи: None, пустой массив, снимок без волос
- Согласованность полей AnalysisResult (hair_count, ratios, thickness)
- Детерминизм результата при повторном вызове
- Площадь поля зрения считается по фактическому размеру снимка,
  а не по статичным config.fov_width_px/fov_height_px
- Влияние калибровки (um_per_px) на итоговые толщины в мкм
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from phototrichogram.core.analyzer import AnalysisResult, Analyzer
from phototrichogram.core.classification import HairType
from phototrichogram.core.measurement import measure_all, split_thick_bundles
from phototrichogram.core.segmentation import find_hair_contours, segment_hair
from phototrichogram.utils.config import AnalysisConfig

from .conftest import make_test_image


def _transit_image() -> np.ndarray:
    """Синтетический снимок: диагональный транзитный волос + растущий вертикальный.

    - Диагональ (0,120)->(639,380): оба конца упираются в левую/правую границу
      кадра — транзитный волос (проходит насквозь). Слегка наклонена, чтобы
      контур не сжимался CHAIN_APPROX_SIMPLE до 4 точек (иначе измерение бы его
      отбросило как осевой прямоугольник).
    - Вертикаль (300,0)->(300,210): верхний конец у границы, нижний в центре
      кадра — растущий волос (rooted).

    Линии не пересекаются (диагональ в точке x=300 имеет y≈242 > 210), поэтому
    сегментация выделяет два отдельных контура.
    """
    img = np.ones((480, 640, 3), dtype=np.uint8) * 220
    cv2.line(img, (0, 120), (639, 380), (30, 30, 30), 6)
    cv2.line(img, (300, 0), (300, 210), (30, 30, 30), 6)
    return img

# ---------------------------------------------------------------------------
# Граничные случаи входных данных
# ---------------------------------------------------------------------------


class TestAnalyzeInvalidInput:
    """Тесты поведения на невалидных входных изображениях."""

    def test_none_image_returns_none(self, default_config: AnalysisConfig) -> None:
        """None вместо изображения -> анализатор возвращает None, не падает."""
        analyzer = Analyzer(default_config)

        assert analyzer.analyze(None) is None

    def test_empty_array_returns_none(self, default_config: AnalysisConfig) -> None:
        """Массив нулевого размера -> None."""
        analyzer = Analyzer(default_config)
        empty = np.zeros((0, 0, 3), dtype=np.uint8)

        assert analyzer.analyze(empty) is None

    def test_none_image_with_annotation_returns_none(
        self, default_config: AnalysisConfig
    ) -> None:
        """None + аннотация -> тоже None."""
        analyzer = Analyzer(default_config)

        assert analyzer.analyze_with_annotation(None) is None


# ---------------------------------------------------------------------------
# Снимок без волос
# ---------------------------------------------------------------------------


class TestAnalyzeBlankImage:
    """Тесты анализа снимка без волос (граничный, но валидный случай)."""

    def test_zero_hair_count(self, blank_image: np.ndarray, default_config: AnalysisConfig) -> None:
        """Пустой светлый снимок -> hair_count=0."""
        result = Analyzer(default_config).analyze(blank_image)

        assert result is not None
        assert result.hair_count == 0

    def test_zero_density(self, blank_image: np.ndarray, default_config: AnalysisConfig) -> None:
        """Пустой снимок -> плотность 0.0, без деления на ноль."""
        result = Analyzer(default_config).analyze(blank_image)

        assert result is not None
        assert result.density_per_cm2 == 0.0

    def test_zero_ratios_and_thickness(
        self, blank_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Пустой снимок -> все соотношения и статистика толщины нулевые."""
        result = Analyzer(default_config).analyze(blank_image)

        assert result is not None
        assert result.ratios.total_count == 0
        assert result.thickness.count == 0
        assert result.thickness.mean == 0.0

    def test_mask_still_valid_array(
        self, blank_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Маска возвращается даже при отсутствии волос -- валидный массив."""
        result = Analyzer(default_config).analyze(blank_image)

        assert result is not None
        assert result.mask.shape == blank_image.shape[:2]
        assert np.count_nonzero(result.mask) == 0

    def test_empty_measurements_list(
        self, blank_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Список измерений пуст, но не None."""
        result = Analyzer(default_config).analyze(blank_image)

        assert result is not None
        assert result.measurements == []


# ---------------------------------------------------------------------------
# Аннотация
# ---------------------------------------------------------------------------


class TestAnnotation:
    """Тесты наличия/отсутствия аннотированного изображения."""

    def test_analyze_without_annotation_has_none(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """analyze() не рисует аннотацию -- annotated_image is None."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        assert result.annotated_image is None

    def test_analyze_with_annotation_has_image(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """analyze_with_annotation() возвращает изображение того же размера."""
        result = Analyzer(default_config).analyze_with_annotation(sample_image)

        assert result is not None
        assert result.annotated_image is not None
        assert result.annotated_image.shape == sample_image.shape

    def test_annotation_does_not_change_numeric_results(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Наличие аннотации не влияет на числовые показатели результата."""
        analyzer = Analyzer(default_config)

        without = analyzer.analyze(sample_image)
        with_annotation = analyzer.analyze_with_annotation(sample_image)

        assert without.hair_count == with_annotation.hair_count
        assert without.density_per_cm2 == with_annotation.density_per_cm2
        assert without.thickness == with_annotation.thickness

    def test_blank_image_with_annotation_still_returns_image(
        self, blank_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Даже без волос аннотированное изображение (только инфопанель) строится."""
        result = Analyzer(default_config).analyze_with_annotation(blank_image)

        assert result is not None
        assert result.annotated_image is not None


# ---------------------------------------------------------------------------
# Согласованность результата на синтетике
# ---------------------------------------------------------------------------


class TestAnalyzeSyntheticImage:
    """Тесты полного пайплайна на синтетическом снимке с 'волосами'."""

    def test_returns_analysis_result(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Результат -- объект AnalysisResult с найденными волосами."""
        result = Analyzer(default_config).analyze(sample_image)

        assert isinstance(result, AnalysisResult)
        assert result.hair_count > 0

    def test_hair_count_matches_measurements_length(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """hair_count равен длине списка measurements."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        assert result.hair_count == len(result.measurements)

    def test_ratios_total_matches_hair_count(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """ratios.total_count согласован с hair_count."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        assert result.ratios.total_count == result.hair_count

    def test_ratio_counts_sum_to_total(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Сумма terminal+vellus+intermediate равна общему количеству."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        summed = (
            result.ratios.terminal_count
            + result.ratios.vellus_count
            + result.ratios.intermediate_count
        )
        assert summed == result.ratios.total_count

    def test_thickness_count_matches_hair_count(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """thickness.count согласован с hair_count."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        assert result.thickness.count == result.hair_count

    def test_all_measurements_have_classification(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Каждое измерение имеет проставленную классификацию из HairType."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        valid_labels = {t.value for t in HairType}
        assert all(m.classification in valid_labels for m in result.measurements)

    def test_mask_shape_matches_image(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Маска сегментации имеет размер исходного изображения."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        assert result.mask.shape == sample_image.shape[:2]

    def test_is_deterministic(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Повторный анализ одного и того же снимка даёт идентичный результат."""
        analyzer = Analyzer(default_config)

        result1 = analyzer.analyze(sample_image)
        result2 = analyzer.analyze(sample_image)

        assert result1 is not None
        assert result2 is not None
        assert result1.hair_count == result2.hair_count
        assert result1.density_per_cm2 == result2.density_per_cm2
        assert result1.ratios == result2.ratios
        assert result1.thickness == result2.thickness

    def test_original_image_not_mutated(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """analyze() не изменяет переданный исходный массив."""
        original = sample_image.copy()

        Analyzer(default_config).analyze(sample_image)

        assert np.array_equal(sample_image, original)


# ---------------------------------------------------------------------------
# Площадь поля зрения и калибровка
# ---------------------------------------------------------------------------


class TestFovAreaAndCalibration:
    """Тесты расчёта площади поля зрения и влияния калибровки."""

    def test_fov_area_uses_actual_image_size(self, default_config: AnalysisConfig) -> None:
        """fov_area_mm2 считается по фактическим размерам снимка, а не по конфигу.

        default_config.fov_width_px/fov_height_px = 640x480, но снимок здесь
        320x240 -- результат должен отражать реальный размер кадра.
        """
        image = make_test_image(width=320, height=240, n_thick=2, n_thin=0)

        result = Analyzer(default_config).analyze(image)

        assert result is not None
        expected = (320 * default_config.um_per_px / 1000.0) * (
            240 * default_config.um_per_px / 1000.0
        )
        assert result.fov_area_mm2 == pytest.approx(expected)

    def test_fov_area_matches_config_for_default_size(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Для снимка стандартного размера fov_area_mm2 совпадает с config.fov_area_mm2."""
        result = Analyzer(default_config).analyze(sample_image)

        assert result is not None
        assert result.fov_area_mm2 == pytest.approx(default_config.fov_area_mm2)

    def test_thickness_scales_with_um_per_px(self, sample_image: np.ndarray) -> None:
        """Удвоение um_per_px удваивает измеренные толщины в мкм."""
        config_a = AnalysisConfig(um_per_px=5.9)
        config_b = AnalysisConfig(um_per_px=11.8)

        result_a = Analyzer(config_a).analyze(sample_image)
        result_b = Analyzer(config_b).analyze(sample_image)

        assert result_a is not None
        assert result_b is not None
        assert result_a.hair_count == result_b.hair_count
        thickness_a = sorted(m.thickness_um for m in result_a.measurements)
        thickness_b = sorted(m.thickness_um for m in result_b.measurements)
        for value_a, value_b in zip(thickness_a, thickness_b, strict=True):
            assert value_b == pytest.approx(value_a * 2.0)

    def test_density_zero_for_zero_um_per_px(self, sample_image: np.ndarray) -> None:
        """Нулевая калибровка -> нулевая площадь FOV -> плотность 0 без падения."""
        config = AnalysisConfig(um_per_px=0.0)

        result = Analyzer(config).analyze(sample_image)

        assert result is not None
        assert result.fov_area_mm2 == 0.0
        assert result.density_per_cm2 == 0.0


# ---------------------------------------------------------------------------
# Analyzer как класс -- конфигурация через DI
# ---------------------------------------------------------------------------


class TestAnalyzerDependencyInjection:
    """Тесты внедрения конфигурации через конструктор (без глобального состояния)."""

    def test_stores_injected_config(self, default_config: AnalysisConfig) -> None:
        """Analyzer хранит переданный конфиг без побочных изменений."""
        analyzer = Analyzer(default_config)

        assert analyzer.config is default_config

    def test_two_analyzers_with_different_configs_are_independent(
        self, sample_image: np.ndarray
    ) -> None:
        """Два анализатора с разными конфигами не влияют друг на друга."""
        strict_analyzer = Analyzer(AnalysisConfig(min_hair_area=100000))
        loose_analyzer = Analyzer(AnalysisConfig(min_hair_area=50))

        strict_result = strict_analyzer.analyze(sample_image)
        loose_result = loose_analyzer.analyze(sample_image)

        assert strict_result is not None
        assert loose_result is not None
        assert strict_result.hair_count == 0
        assert loose_result.hair_count > strict_result.hair_count


class TestBundledFonts:
    """Шрифты аннотаций из пакета (assets/fonts) — независимость от хоста."""

    def test_bundled_font_is_first_candidate_and_exists(self) -> None:
        """Первый кандидат шрифта — DejaVuSans из assets/, файл реально существует."""
        from pathlib import Path

        from phototrichogram.core.analyzer import _FONT_PATHS

        first = Path(_FONT_PATHS[0])
        assert first.name == "DejaVuSans.ttf"
        assert "assets" in first.parts
        assert first.exists()

    def test_get_pil_font_loads_truetype_with_cyrillic(self) -> None:
        """_get_pil_font загружает векторный TTF (кириллица), а не растровый дефолт."""
        from PIL import ImageFont

        from phototrichogram.core.analyzer import _get_pil_font

        font = _get_pil_font(15)
        assert isinstance(font, ImageFont.FreeTypeFont)


# ---------------------------------------------------------------------------
# Режим подсчёта: count_mode = "all" (дефолт) vs "rooted"
# ---------------------------------------------------------------------------


class TestCountMode:
    """Переключаемый режим подсчёта волос «от корней» (план v1.4, Phase 1)."""

    def test_default_count_mode_is_all(self) -> None:
        """Дефолт count_mode == 'all' — прежнее поведение не меняется."""
        assert AnalysisConfig().count_mode == "all"

    def test_all_mode_matches_pipeline_without_rooting(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Регрессия: в режиме 'all' счёт равен пайплайну БЕЗ фильтра корней.

        Доказывает, что при дефолтном count_mode='all' детекция корней не
        применяется — результат идентичен «сегментация → контуры → измерение →
        фильтр пучков», как было до появления режима.
        """
        result = Analyzer(default_config).analyze(sample_image)

        mask = segment_hair(sample_image, default_config)
        contours = find_hair_contours(mask, default_config)
        measurements = measure_all(contours, default_config)
        measurements, _ = split_thick_bundles(measurements, default_config)

        assert result is not None
        assert result.hair_count == len(measurements)

    def test_all_and_rooted_equal_when_no_transit(
        self, sample_image: np.ndarray
    ) -> None:
        """На снимке без транзитных волос 'all' и 'rooted' дают одинаковый счёт.

        В sample_image «волосы» не касаются границ (оба конца внутри кадра),
        поэтому все — растущие, и режимы совпадают.
        """
        all_result = Analyzer(AnalysisConfig(count_mode="all")).analyze(sample_image)
        rooted_result = Analyzer(AnalysisConfig(count_mode="rooted")).analyze(sample_image)

        assert all_result is not None
        assert rooted_result is not None
        assert rooted_result.hair_count == all_result.hair_count
        assert rooted_result.density_per_cm2 == all_result.density_per_cm2

    def test_rooted_mode_excludes_transit(self) -> None:
        """Режим 'rooted' исключает транзитный волос: all=2, rooted=1."""
        image = _transit_image()

        all_result = Analyzer(AnalysisConfig(count_mode="all")).analyze(image)
        rooted_result = Analyzer(AnalysisConfig(count_mode="rooted")).analyze(image)

        assert all_result is not None
        assert rooted_result is not None
        assert all_result.hair_count == 2
        assert rooted_result.hair_count == 1

    def test_rooted_density_below_all_when_transit_present(self) -> None:
        """Транзитный волос исключается → плотность в 'rooted' ниже, чем в 'all'."""
        image = _transit_image()

        all_result = Analyzer(AnalysisConfig(count_mode="all")).analyze(image)
        rooted_result = Analyzer(AnalysisConfig(count_mode="rooted")).analyze(image)

        assert all_result is not None
        assert rooted_result is not None
        assert rooted_result.density_per_cm2 < all_result.density_per_cm2

    def test_rooted_never_exceeds_all_count(
        self, sample_image: np.ndarray
    ) -> None:
        """Свойство: 'rooted' не может насчитать больше волос, чем 'all'."""
        all_result = Analyzer(AnalysisConfig(count_mode="all")).analyze(sample_image)
        rooted_result = Analyzer(AnalysisConfig(count_mode="rooted")).analyze(sample_image)

        assert all_result is not None
        assert rooted_result is not None
        assert rooted_result.hair_count <= all_result.hair_count

    def test_rooted_mode_annotation_same_shape(self) -> None:
        """Аннотация в режиме 'rooted' строится и сохраняет размер снимка."""
        image = _transit_image()

        result = Analyzer(AnalysisConfig(count_mode="rooted")).analyze_with_annotation(image)

        assert result is not None
        assert result.annotated_image is not None
        assert result.annotated_image.shape == image.shape

    def test_unknown_count_mode_behaves_like_all(
        self, sample_image: np.ndarray
    ) -> None:
        """Неизвестное значение count_mode не включает фильтр (безопасный дефолт)."""
        all_result = Analyzer(AnalysisConfig(count_mode="all")).analyze(sample_image)
        weird_result = Analyzer(AnalysisConfig(count_mode="whatever")).analyze(sample_image)

        assert all_result is not None
        assert weird_result is not None
        assert weird_result.hair_count == all_result.hair_count

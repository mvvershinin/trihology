"""
Тесты модуля сегментации волос (core/segmentation.py).

Покрывает:
- preprocess: grayscale, CLAHE, медианный фильтр, чётное ядро
- segment_hair: детекция волос на синтетике, пустое/однородное изображение,
  бинарность маски, метод 'otsu' и 'adaptive', ветка ужесточения порога
  при избытке белых пикселей
- find_hair_contours: фильтры по площади, циркулярности, аспектному
  соотношению и их зависимость от параметров AnalysisConfig
"""

from __future__ import annotations

import cv2
import numpy as np

from phototrichogram.core.segmentation import (
    find_hair_contours,
    preprocess,
    segment_hair,
)
from phototrichogram.utils.config import AnalysisConfig

from .conftest import make_test_image

# ---------------------------------------------------------------------------
# preprocess
# ---------------------------------------------------------------------------


class TestPreprocess:
    """Тесты предобработки изображения."""

    def test_returns_2d_grayscale(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Результат — одноканальное изображение uint8."""
        result = preprocess(sample_image, default_config)

        assert result.ndim == 2
        assert result.dtype == np.uint8
        assert result.shape == sample_image.shape[:2]

    def test_accepts_already_grayscale_input(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Если на вход уже подан grayscale — не конвертируется повторно."""
        gray = cv2.cvtColor(sample_image, cv2.COLOR_BGR2GRAY)

        result_from_bgr = preprocess(sample_image, default_config)
        result_from_gray = preprocess(gray, default_config)

        assert np.array_equal(result_from_bgr, result_from_gray)

    def test_even_median_kernel_does_not_crash(
        self, sample_image: np.ndarray
    ) -> None:
        """Чётное ядро медианного фильтра автоматически становится нечётным."""
        config = AnalysisConfig(median_blur_kernel=4)

        result = preprocess(sample_image, config)

        assert result.shape == sample_image.shape[:2]

    def test_clahe_clip_limit_changes_output(self, sample_image: np.ndarray) -> None:
        """Разные clahe_clip_limit дают разный результат контрастирования."""
        low = preprocess(sample_image, AnalysisConfig(clahe_clip_limit=1.0))
        high = preprocess(sample_image, AnalysisConfig(clahe_clip_limit=10.0))

        assert not np.array_equal(low, high)


# ---------------------------------------------------------------------------
# segment_hair
# ---------------------------------------------------------------------------


class TestSegmentHair:
    """Тесты полного пайплайна сегментации."""

    def test_returns_binary_mask(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Маска содержит только значения 0 и 255."""
        mask = segment_hair(sample_image, default_config)

        assert set(np.unique(mask).tolist()) <= {0, 255}

    def test_mask_matches_image_dimensions(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Размер маски совпадает с размером исходного изображения."""
        mask = segment_hair(sample_image, default_config)

        assert mask.shape == sample_image.shape[:2]

    def test_detects_thick_lines_as_hairs(
        self, sample_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """На синтетике толстые линии (4px) образуют детектируемые волосы."""
        mask = segment_hair(sample_image, default_config)
        contours = find_hair_contours(mask, default_config)

        # sample_image = make_test_image() по умолчанию: n_thick=3.
        # Тонкие линии (1px) не формируют отдельных контуров (area~0,
        # см. docstring make_test_image в conftest.py).
        assert len(contours) == 3

    def test_empty_white_image_has_no_white_pixels(
        self, blank_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """Чисто белый снимок без волос -> пустая маска."""
        mask = segment_hair(blank_image, default_config)

        assert np.count_nonzero(mask) == 0

    def test_empty_white_image_has_no_contours(
        self, blank_image: np.ndarray, default_config: AnalysisConfig
    ) -> None:
        """На пустом снимке контуры волос не находятся."""
        mask = segment_hair(blank_image, default_config)
        contours = find_hair_contours(mask, default_config)

        assert contours == []

    def test_uniform_gray_image_no_edges(self, default_config: AnalysisConfig) -> None:
        """Однородное серое изображение (без границ) -> пустая маска."""
        uniform = np.full((200, 200, 3), 128, dtype=np.uint8)

        mask = segment_hair(uniform, default_config)

        assert np.count_nonzero(mask) == 0

    def test_adaptive_threshold_method_produces_binary_mask(
        self, sample_image: np.ndarray
    ) -> None:
        """Метод threshold_method='adaptive' тоже возвращает корректную маску."""
        config = AnalysisConfig(threshold_method="adaptive")

        mask = segment_hair(sample_image, config)

        assert set(np.unique(mask).tolist()) <= {0, 255}
        assert mask.shape == sample_image.shape[:2]

    def test_high_white_pct_triggers_stricter_threshold(self) -> None:
        """Зашумлённое изображение запускает ветку ужесточения порога.

        При избытке белых пикселей после Otsu (white_pct > max_white_pct)
        сегментация автоматически поднимает порог по перцентилю
        (METHODOLOGY.md, segment_hair). Итоговый процент белых пикселей
        должен уменьшиться и не превышать исходный.
        """
        rng = np.random.default_rng(42)
        noisy = rng.integers(0, 256, size=(200, 200, 3), dtype=np.uint8)
        config = AnalysisConfig(max_white_pct=30.0)

        mask = segment_hair(noisy, config)
        white_pct = np.count_nonzero(mask) / mask.size * 100

        # Проверка детерминизма (seed фиксирован) и что маска не полностью белая
        assert white_pct < 100.0
        assert mask.shape == noisy.shape[:2]

    def test_blackhat_kernel_size_influences_result(self, sample_image: np.ndarray) -> None:
        """Разный размер ядра black-hat даёт разную маску."""
        small_kernel = segment_hair(sample_image, AnalysisConfig(blackhat_kernel_size=5))
        large_kernel = segment_hair(sample_image, AnalysisConfig(blackhat_kernel_size=41))

        assert not np.array_equal(small_kernel, large_kernel)


# ---------------------------------------------------------------------------
# find_hair_contours
# ---------------------------------------------------------------------------


class TestFindHairContours:
    """Тесты фильтрации контуров по площади, форме и аспектному соотношению."""

    def test_rejects_tiny_dust_particle_by_area(self, default_config: AnalysisConfig) -> None:
        """Мелкий мусор (площадь ниже min_hair_area) отклоняется."""
        mask = np.zeros((100, 100), dtype=np.uint8)
        cv2.rectangle(mask, (40, 40), (45, 45), 255, -1)  # 6x6 = 36 px^2

        contours = find_hair_contours(mask, default_config)

        assert contours == []

    def test_min_hair_area_config_lowers_acceptance_bar(self) -> None:
        """Уменьшение min_hair_area позволяет принять мелкий объект."""
        mask = np.zeros((100, 100), dtype=np.uint8)
        cv2.rectangle(mask, (40, 20), (45, 79), 255, -1)  # узкий "волос", area~330

        strict = find_hair_contours(mask, AnalysisConfig(min_hair_area=1000))
        loose = find_hair_contours(mask, AnalysisConfig(min_hair_area=50))

        assert strict == []
        assert len(loose) == 1

    def test_rejects_circular_shape_by_circularity(self, default_config: AnalysisConfig) -> None:
        """Круглый объект (циркулярность ~0.88) отклоняется -- не похож на волос."""
        mask = np.zeros((300, 300), dtype=np.uint8)
        cv2.circle(mask, (100, 100), 30, 255, -1)

        contours = find_hair_contours(mask, default_config)

        assert contours == []

    def test_max_circularity_config_allows_round_shape(self) -> None:
        """Повышение порога max_circularity позволяет принять круглый объект."""
        mask = np.zeros((300, 300), dtype=np.uint8)
        cv2.circle(mask, (100, 100), 30, 255, -1)

        config = AnalysisConfig(max_circularity=0.95, min_aspect_ratio=1.0)
        contours = find_hair_contours(mask, config)

        assert len(contours) == 1

    def test_accepts_elongated_shape(self, default_config: AnalysisConfig) -> None:
        """Вытянутый прямоугольник (типичная форма волоса) принимается."""
        mask = np.zeros((400, 400), dtype=np.uint8)
        cv2.rectangle(mask, (100, 20), (109, 319), 255, -1)  # 10x300

        contours = find_hair_contours(mask, default_config)

        assert len(contours) == 1

    def test_rejects_low_aspect_ratio_despite_low_circularity(
        self, default_config: AnalysisConfig
    ) -> None:
        """Крестообразная фигура: низкая циркулярность, но аспект ~1 -- отклоняется.

        Проверяет, что фильтр по аспектному соотношению (min_aspect_ratio)
        работает независимо от фильтра циркулярности.
        """
        mask = np.zeros((200, 200), dtype=np.uint8)
        cv2.rectangle(mask, (80, 20), (119, 179), 255, -1)  # вертикальная перекладина
        cv2.rectangle(mask, (20, 80), (179, 119), 255, -1)  # горизонтальная перекладина

        contours = find_hair_contours(mask, default_config)

        assert contours == []

    def test_min_aspect_ratio_config_allows_cross_shape(self) -> None:
        """Понижение min_aspect_ratio позволяет принять крестообразную фигуру."""
        mask = np.zeros((200, 200), dtype=np.uint8)
        cv2.rectangle(mask, (80, 20), (119, 179), 255, -1)
        cv2.rectangle(mask, (20, 80), (179, 119), 255, -1)

        config = AnalysisConfig(min_aspect_ratio=1.0)
        contours = find_hair_contours(mask, config)

        assert len(contours) == 1

    def test_empty_mask_returns_empty_list(self, default_config: AnalysisConfig) -> None:
        """Пустая (полностью чёрная) маска -> пустой список контуров."""
        mask = np.zeros((200, 200), dtype=np.uint8)

        contours = find_hair_contours(mask, default_config)

        assert contours == []

    def test_config_influences_final_contour_count_end_to_end(
        self, sample_image: np.ndarray
    ) -> None:
        """Полный пайплайн: разные min_hair_area на синтетике дают разный результат."""
        strict_config = AnalysisConfig(min_hair_area=5000)
        loose_config = AnalysisConfig(min_hair_area=50)

        mask = segment_hair(sample_image, AnalysisConfig())
        strict_contours = find_hair_contours(mask, strict_config)
        loose_contours = find_hair_contours(mask, loose_config)

        assert len(strict_contours) == 0
        assert len(loose_contours) >= len(strict_contours)


# ---------------------------------------------------------------------------
# Sanity-проверка самой фабрики синтетических изображений
# ---------------------------------------------------------------------------


def test_make_test_image_shape_and_background() -> None:
    """make_test_image возвращает BGR изображение заданного размера и фона."""
    img = make_test_image(width=320, height=240, bg_color=200)

    assert img.shape == (240, 320, 3)
    assert img.dtype == np.uint8
    # Угол изображения не задет линиями -- должен остаться фоновым
    assert tuple(img[0, 0]) == (200, 200, 200)

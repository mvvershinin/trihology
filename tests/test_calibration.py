"""
Тесты модуля автокалибровки по микрометрической шкале (core/calibration.py).

Покрывает:
- calibrate_from_ruler: расчёт um_per_px по известному шагу делений,
  размеры поля зрения, количество делений, доверие (confidence)
- Фильтрация по ширине полосы (max_stripe_width)
- Пользовательский диапазон строк (row_range), BGR-вход
- Порог по умолчанию Otsu (threshold=None) и повёрнутая на 90° шкала
- Проверка регулярности: отказ на снимке кожи с «волосами», при малом
  числе делений (< min_divisions) и нерегулярном шаге (CV > max_spacing_cv)
- CalibrationError: отсутствие делений на снимке
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from phototrichogram.core.calibration import CalibrationError, calibrate_from_ruler

from .conftest import make_ruler_image


def make_irregular_ruler(
    width: int = 400,
    height: int = 100,
    gaps: tuple[int, ...] = (16, 24),
    line_width: int = 2,
    start: int = 10,
) -> np.ndarray:
    """Синтетическая шкала с нерегулярным (чередующимся) шагом делений.

    Расстояния между делениями циклически берутся из gaps. По умолчанию
    16/24 px даёт коэффициент вариации ~0.25 (> порога max_spacing_cv=0.15) —
    такая шкала должна быть отклонена как нерегулярная.

    Args:
        width: Ширина изображения.
        height: Высота изображения.
        gaps: Цикл расстояний между соседними делениями (px).
        line_width: Ширина каждого деления (px).
        start: X первого деления (px).

    Returns:
        Grayscale изображение (uint8) размером (height, width).
    """
    img = np.ones((height, width), dtype=np.uint8) * 255
    x = start
    i = 0
    while x + line_width <= width:
        img[:, x : x + line_width] = 0
        x += gaps[i % len(gaps)]
        i += 1
    return img

# ---------------------------------------------------------------------------
# Расчёт масштаба по известному шагу делений
# ---------------------------------------------------------------------------


class TestCalibrateKnownSpacing:
    """Тесты расчёта um_per_px на шкале с заранее известным шагом."""

    def test_um_per_px_matches_known_spacing(self) -> None:
        """Деления через 20px при цене деления 100 мкм -> 5.0 мкм/px."""
        img = make_ruler_image(spacing_px=20, line_width=2)

        result = calibrate_from_ruler(img, division_um=100.0)

        assert result.um_per_px == pytest.approx(5.0)

    def test_um_per_px_scales_with_division_um(self) -> None:
        """При цене деления 50 мкм и том же шаге -> 2.5 мкм/px."""
        img = make_ruler_image(spacing_px=20, line_width=2)

        result = calibrate_from_ruler(img, division_um=50.0)

        assert result.um_per_px == pytest.approx(2.5)

    def test_median_distance_px_matches_spacing(self) -> None:
        """median_distance_px соответствует заданному шагу делений."""
        img = make_ruler_image(spacing_px=25, line_width=2)

        result = calibrate_from_ruler(img, division_um=100.0)

        assert result.median_distance_px == pytest.approx(25.0)

    def test_different_spacing_gives_different_scale(self) -> None:
        """Больший шаг делений при той же цене деления -> больший мкм/px."""
        img_narrow = make_ruler_image(spacing_px=10, line_width=2)
        img_wide = make_ruler_image(spacing_px=40, line_width=2)

        narrow_result = calibrate_from_ruler(img_narrow, division_um=100.0)
        wide_result = calibrate_from_ruler(img_wide, division_um=100.0)

        assert wide_result.um_per_px < narrow_result.um_per_px

    def test_fov_dimensions_calculated_from_scale(self) -> None:
        """FOV в мм рассчитывается по размерам изображения и масштабу."""
        img = make_ruler_image(width=400, height=100, spacing_px=20, line_width=2)

        result = calibrate_from_ruler(img, division_um=100.0)

        assert result.um_per_px == pytest.approx(5.0)
        assert result.fov_width_mm == pytest.approx(400 * 5.0 / 1000.0)
        assert result.fov_height_mm == pytest.approx(100 * 5.0 / 1000.0)
        assert result.fov_area_mm2 == pytest.approx(result.fov_width_mm * result.fov_height_mm)

    def test_divisions_detected_positive(self) -> None:
        """На корректной шкале обнаруживается положительное число делений."""
        img = make_ruler_image()

        result = calibrate_from_ruler(img, division_um=100.0)

        assert result.divisions_detected > 0

    def test_confidence_in_valid_range(self) -> None:
        """Оценка доверия лежит в диапазоне [0, 1]."""
        img = make_ruler_image()

        result = calibrate_from_ruler(img, division_um=100.0)

        assert 0.0 <= result.confidence <= 1.0

    def test_regular_spacing_gives_high_confidence(self) -> None:
        """Строго регулярная синтетическая шкала даёт высокое доверие."""
        img = make_ruler_image(spacing_px=20, line_width=2)

        result = calibrate_from_ruler(img, division_um=100.0)

        assert result.confidence > 0.8


# ---------------------------------------------------------------------------
# Параметры вызова
# ---------------------------------------------------------------------------


class TestCalibrateParameters:
    """Тесты параметров функции calibrate_from_ruler."""

    def test_bgr_input_gives_same_result_as_grayscale(self) -> None:
        """Цветной (BGR) снимок даёт тот же результат, что и grayscale."""
        gray = make_ruler_image(spacing_px=20, line_width=2)
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        result_gray = calibrate_from_ruler(gray, division_um=100.0)
        result_bgr = calibrate_from_ruler(bgr, division_um=100.0)

        assert result_gray.um_per_px == result_bgr.um_per_px

    def test_custom_row_range_used_for_detection(self) -> None:
        """Явно заданный row_range даёт тот же масштаб, что и авто-диапазон."""
        img = make_ruler_image(width=400, height=100, spacing_px=20, line_width=2)

        auto = calibrate_from_ruler(img, division_um=100.0)
        custom = calibrate_from_ruler(img, division_um=100.0, row_range=(0, 10))

        assert auto.um_per_px == pytest.approx(custom.um_per_px)

    def test_wide_bars_rejected_by_default_stripe_width_filter(self) -> None:
        """Полосы шире max_stripe_width (по умолчанию 12px) не считаются делениями."""
        wide = make_ruler_image(spacing_px=30, line_width=20)  # ширина 20 > 12

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(wide, division_um=100.0)

    def test_max_stripe_width_param_allows_wide_bars(self) -> None:
        """Увеличение max_stripe_width позволяет распознать широкие полосы."""
        wide = make_ruler_image(spacing_px=30, line_width=20)

        result = calibrate_from_ruler(wide, division_um=100.0, max_stripe_width=25)

        assert result.um_per_px == pytest.approx(100.0 / 30.0, rel=1e-2)

    def test_narrow_max_stripe_width_rejects_normal_divisions(self) -> None:
        """Слишком узкий max_stripe_width отбрасывает даже нормальные деления."""
        img = make_ruler_image(spacing_px=20, line_width=5)

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(img, division_um=100.0, max_stripe_width=1)


# ---------------------------------------------------------------------------
# Порог бинаризации и ориентация шкалы
# ---------------------------------------------------------------------------


class TestCalibrateOrientation:
    """Тесты порога Otsu по умолчанию и распознавания ориентации штрихов."""

    def test_default_threshold_uses_otsu(self) -> None:
        """threshold=None (Otsu по умолчанию) калибрует стандартную шкалу."""
        img = make_ruler_image(spacing_px=20, line_width=2)

        result = calibrate_from_ruler(img, division_um=100.0, threshold=None)

        assert result.um_per_px == pytest.approx(5.0)

    def test_default_threshold_equals_explicit_call(self) -> None:
        """Вызов без threshold эквивалентен явному threshold=None (Otsu)."""
        img = make_ruler_image(spacing_px=25, line_width=2)

        implicit = calibrate_from_ruler(img, division_um=100.0)
        explicit = calibrate_from_ruler(img, division_um=100.0, threshold=None)

        assert implicit.um_per_px == pytest.approx(explicit.um_per_px)

    def test_rotated_ruler_calibrated_via_columns(self) -> None:
        """Шкала, повёрнутая на 90° (горизонтальные штрихи), калибруется по столбцам."""
        vertical = make_ruler_image(spacing_px=20, line_width=2)
        rotated = np.ascontiguousarray(vertical.T)  # штрихи стали горизонтальными

        result = calibrate_from_ruler(rotated, division_um=100.0)

        assert result.um_per_px == pytest.approx(5.0)
        assert result.median_distance_px == pytest.approx(20.0)

    def test_rotated_ruler_matches_original_scale(self) -> None:
        """Поворот на 90° не меняет масштаб (мкм/px) — тот же шаг делений."""
        vertical = make_ruler_image(spacing_px=25, line_width=2)
        rotated = np.ascontiguousarray(vertical.T)

        straight = calibrate_from_ruler(vertical, division_um=100.0)
        turned = calibrate_from_ruler(rotated, division_um=100.0)

        assert straight.um_per_px == pytest.approx(turned.um_per_px)


# ---------------------------------------------------------------------------
# Проверка регулярности шкалы (отказ на мусоре)
# ---------------------------------------------------------------------------


class TestCalibrateRegularity:
    """Тесты валидации регулярности: отказ вместо мусорного результата."""

    def test_hair_image_rejected(self, sample_image: np.ndarray) -> None:
        """Снимок кожи с 'волосами' -> отказ: это не калибровочная шкала."""
        with pytest.raises(CalibrationError, match="шкала"):
            calibrate_from_ruler(sample_image, division_um=100.0)

    def test_too_few_divisions_rejected(self) -> None:
        """Регулярная, но короткая шкала (< min_divisions=8) -> отказ."""
        # width=140, spacing=30 -> деления на x=10,40,70,100,130 = 5 штук
        img = make_ruler_image(width=140, height=100, spacing_px=30, line_width=2)

        with pytest.raises(CalibrationError, match="не обнаружена"):
            calibrate_from_ruler(img, division_um=100.0)

    def test_min_divisions_param_allows_short_ruler(self) -> None:
        """Снижение min_divisions позволяет калибровать короткую шкалу."""
        img = make_ruler_image(width=140, height=100, spacing_px=30, line_width=2)

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(img, division_um=100.0)  # min_divisions=8 по умолчанию

        result = calibrate_from_ruler(img, division_um=100.0, min_divisions=4)

        assert result.um_per_px == pytest.approx(100.0 / 30.0, rel=1e-2)

    def test_irregular_spacing_rejected(self) -> None:
        """Нерегулярный шаг делений (CV ~0.25 > 0.15) -> отказ."""
        img = make_irregular_ruler(gaps=(16, 24))

        with pytest.raises(CalibrationError, match="разброс расстояний"):
            calibrate_from_ruler(img, division_um=100.0)

    def test_max_spacing_cv_param_allows_irregular(self) -> None:
        """Ослабление max_spacing_cv позволяет принять нерегулярную шкалу."""
        img = make_irregular_ruler(gaps=(16, 24))

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(img, division_um=100.0)  # max_spacing_cv=0.15

        result = calibrate_from_ruler(img, division_um=100.0, max_spacing_cv=0.5)

        assert result.divisions_detected >= 8

    def test_low_confidence_rejected_by_min_confidence(self) -> None:
        """Завышенный min_confidence отклоняет даже идеально регулярную шкалу."""
        img = make_ruler_image(spacing_px=20, line_width=2)

        # Идеальная шкала даёт confidence≈1.0; порог 1.1 недостижим -> отказ.
        with pytest.raises(CalibrationError, match="надёжност"):
            calibrate_from_ruler(img, division_um=100.0, min_confidence=1.1)

    def test_regular_ruler_high_confidence_and_divisions(self) -> None:
        """Регулярная шкала: высокая надёжность и число делений = число штрихов."""
        img = make_ruler_image(width=400, height=100, spacing_px=20, line_width=2)

        result = calibrate_from_ruler(img, division_um=100.0)

        assert result.confidence >= 0.5
        assert result.divisions_detected >= 8


# ---------------------------------------------------------------------------
# Ошибки калибровки
# ---------------------------------------------------------------------------


class TestCalibrateErrors:
    """Тесты CalibrationError при невалидных входных данных."""

    def test_blank_image_raises_calibration_error(self, blank_image: np.ndarray) -> None:
        """Снимок без делений (однородный) -> CalibrationError."""
        gray = cv2.cvtColor(blank_image, cv2.COLOR_BGR2GRAY)

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(gray, division_um=100.0)

    def test_error_message_mentions_missing_divisions(self) -> None:
        """Сообщение об ошибке информативно -- упоминает отсутствие делений."""
        blank = np.ones((100, 400), dtype=np.uint8) * 255

        with pytest.raises(CalibrationError, match="не обнаружены"):
            calibrate_from_ruler(blank, division_um=100.0)

    def test_extreme_threshold_raises_calibration_error(self) -> None:
        """Порог бинаризации 255 делает всё изображение чёрным -> нет делений."""
        img = make_ruler_image(spacing_px=20, line_width=2)

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(img, division_um=100.0, threshold=255)

    def test_single_division_insufficient_for_distance(self) -> None:
        """Единственное деление на снимке -- нет пары для расчёта расстояния."""
        img = np.ones((100, 400), dtype=np.uint8) * 255
        img[:, 200:202] = 0  # ровно одно деление

        with pytest.raises(CalibrationError):
            calibrate_from_ruler(img, division_um=100.0)

    def test_zero_division_um_raises_calibration_error(self) -> None:
        """Нулевая цена деления -- ошибка, а не тихая нулевая калибровка."""
        img = make_ruler_image()

        with pytest.raises(CalibrationError, match="положительн"):
            calibrate_from_ruler(img, division_um=0.0)

    def test_negative_division_um_raises_calibration_error(self) -> None:
        """Отрицательная цена деления -- ошибка, а не отрицательный масштаб."""
        img = make_ruler_image()

        with pytest.raises(CalibrationError, match="положительн"):
            calibrate_from_ruler(img, division_um=-100.0)


# ---------------------------------------------------------------------------
# Sanity-проверка фабрики синтетической шкалы
# ---------------------------------------------------------------------------


def test_make_ruler_image_shape_and_background() -> None:
    """make_ruler_image возвращает grayscale изображение с фоновыми пикселями."""
    img = make_ruler_image(width=200, height=50, spacing_px=20, line_width=2, bg=255)

    assert img.shape == (50, 200)
    assert img.dtype == np.uint8
    assert img[0, 0] == 255

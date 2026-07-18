"""
Калибровка микроскопа по микрометрической шкале.

Алгоритм (METHODOLOGY.md §3.2) с проверкой регулярности:
1. Перевод в grayscale
2. Бинаризация (Otsu по умолчанию либо явный порог) — тёмные линии на
   светлом фоне
3. Анализ строк (вертикальные штрихи) И столбцов (горизонтальные штрихи);
   выбирается ориентация с лучшей регулярностью
4. Для каждой линии сканирования — обнаружение центров штрихов и расстояний
   между ними, фильтр по ширине полосы
5. Медианная фильтрация выбросов (±30% от медианы)
6. Проверка регулярности серии: минимум делений, максимальный коэффициент
   вариации расстояний, минимальная надёжность. При нарушении — отказ
   (CalibrationError), а не тихий возврат мусора
7. Итоговый масштаб: цена_деления_мкм / медиана_расстояний_px

Регулярность отличает калибровочную шкалу от случайных тёмных объектов
(например, волосков на снимке кожи), которые дают несколько нерегулярных
краёв и не образуют согласованной периодической серии.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


class CalibrationError(Exception):
    """Ошибка калибровки — не удалось определить масштаб."""


@dataclass(frozen=True)
class CalibrationResult:
    """Результат автокалибровки по микрометрической шкале.

    Attributes:
        um_per_px: Масштаб (микрон на пиксель).
        fov_width_mm: Ширина поля зрения в мм.
        fov_height_mm: Высота поля зрения в мм.
        fov_area_mm2: Площадь поля зрения в мм^2.
        divisions_detected: Количество делений в согласованной серии.
        median_distance_px: Медианное расстояние между делениями (px).
        confidence: Оценка надёжности (0.0–1.0). Считается из регулярности:
                    (1 − коэффициент вариации расстояний) × доля строк,
                    давших согласованный период.
    """

    um_per_px: float
    fov_width_mm: float
    fov_height_mm: float
    fov_area_mm2: float
    divisions_detected: int
    median_distance_px: float
    confidence: float


@dataclass(frozen=True)
class _OrientationStats:
    """Диагностика регулярности штрихов для одной ориентации шкалы.

    Attributes:
        is_regular: Найдена ли согласованная регулярная серия делений.
        median_spacing_px: Медианный шаг делений (px) по согласованным линиям.
        n_divisions: Число делений в согласованной серии (или лучшая попытка
                     для диагностики, если серия не найдена).
        spacing_cv: Коэффициент вариации расстояний (std/median) — мера
                    разброса шага.
        consistency_fraction: Доля просканированных линий, давших согласованный
                              период (0.0–1.0).
        confidence: Итоговая надёжность (1 − spacing_cv) × consistency_fraction.
    """

    is_regular: bool
    median_spacing_px: float
    n_divisions: int
    spacing_cv: float
    consistency_fraction: float
    confidence: float


def _detect_divisions_in_row(
    row: NDArray[np.uint8],
    max_stripe_width: int = 12,
) -> list[float]:
    """Обнаружение центров тёмных полос (делений) в одной линии пикселей.

    Алгоритм:
    1. Найти переходы 0→255 (rising edge) и 255→0 (falling edge)
    2. Пары (falling, rising) дают тёмные полосы
    3. Фильтр по ширине — только узкие деления (< max_stripe_width)
    4. Центр полосы = (start + end) / 2

    Args:
        row: Бинаризованная линия пикселей (0 или 255) — строка либо столбец.
        max_stripe_width: Максимальная ширина полосы (px) — отсекает крупные объекты.

    Returns:
        Список координат центров обнаруженных делений вдоль линии.
    """
    # Переходы: diff > 0 = rising edge (тёмная→светлая), diff < 0 = falling edge
    binary_01 = (row > 0).astype(np.int8)
    diff = np.diff(binary_01)

    # Индексы переходов
    rising = np.where(diff > 0)[0]   # конец тёмной полосы
    falling = np.where(diff < 0)[0]  # начало тёмной полосы

    centers: list[float] = []

    for start in falling:
        # Найти ближайший rising edge после start
        candidates = rising[rising > start]
        if len(candidates) == 0:
            continue
        end = candidates[0]
        width = end - start

        if 1 <= width <= max_stripe_width:
            center = (start + end) / 2.0
            centers.append(center)

    return centers


def _median_filter_distances(
    distances: list[float],
    tolerance: float = 0.3,
) -> list[float]:
    """Медианная фильтрация расстояний — отброс выбросов.

    Выбросы за пределами ±tolerance от медианы отбрасываются.
    METHODOLOGY.md §3.2 шаг 9: ±30% от медианы.

    Args:
        distances: Расстояния между соседними делениями.
        tolerance: Доля отклонения от медианы (0.3 = ±30%).

    Returns:
        Отфильтрованный список расстояний.
    """
    if len(distances) == 0:
        return []

    median = float(np.median(distances))
    low = median * (1.0 - tolerance)
    high = median * (1.0 + tolerance)

    filtered = [d for d in distances if low <= d <= high]
    return filtered


def _line_band(length: int, explicit: tuple[int, int] | None) -> range:
    """Диапазон линий сканирования (строк или столбцов).

    Args:
        length: Размер вдоль сканируемой оси (высота для строк, ширина для столбцов).
        explicit: Явный диапазон (start, end) либо None для центральной полосы
                  (~10% размера вокруг центра, минимум 5 линий в каждую сторону).

    Returns:
        range индексов линий для анализа.
    """
    if explicit is not None:
        start, end = explicit
        return range(max(0, start), min(length, end))

    center = length // 2
    margin = max(length // 20, 5)
    return range(max(0, center - margin), min(length, center + margin))


def _analyze_orientation(
    binary: NDArray[np.uint8],
    band: range,
    max_stripe_width: int,
    min_divisions: int,
    max_spacing_cv: float,
) -> _OrientationStats:
    """Оценка регулярности штрихов вдоль одной ориентации.

    Маска подаётся уже ориентированной так, что искомые штрихи вертикальны и
    пересекаются строками (для горизонтальных штрихов подаётся транспонированная
    маска). Каждая линия band оценивается независимо: сколько делений и насколько
    равномерен их шаг. Линия считается «согласованной», если содержит не менее
    min_divisions делений с коэффициентом вариации расстояний ≤ max_spacing_cv.

    Args:
        binary: Бинарная маска (0/255), штрихи тёмные (0) на светлом фоне (255).
        band: Индексы строк для анализа.
        max_stripe_width: Макс. ширина полосы деления (px).
        min_divisions: Минимум делений в согласованной линии.
        max_spacing_cv: Макс. коэффициент вариации расстояний в согласованной линии.

    Returns:
        _OrientationStats с параметрами найденной серии либо диагностикой
        лучшей попытки, если регулярная серия не обнаружена.
    """
    n_lines = 0
    consistent_spacings: list[float] = []
    consistent_cvs: list[float] = []
    consistent_counts: list[int] = []

    # Лучшая попытка (линия с максимумом делений) — для диагностики отказа.
    diag_count = 0
    diag_cv = 1.0
    diag_spacing = 0.0

    height = binary.shape[0]
    for y in band:
        if y < 0 or y >= height:
            continue
        n_lines += 1

        line: NDArray[np.uint8] = binary[y, :]
        centers = _detect_divisions_in_row(line, max_stripe_width)
        n_centers = len(centers)

        if n_centers < 2:
            if n_centers > diag_count:
                diag_count = n_centers
                diag_cv = 1.0
                diag_spacing = 0.0
            continue

        # Коэффициент вариации считается по СЫРЫМ расстояниям (std/median):
        # это честная мера регулярности. Медианная фильтрация применяется
        # только к финальной оценке шага, чтобы её не «спасала» от
        # чередующихся расстояний (например, 16/24) — такая шкала должна
        # быть отклонена, а не усреднена до половины делений.
        raw_dists = np.diff(np.array(sorted(centers), dtype=np.float64)).tolist()
        raw_median = float(np.median(raw_dists))
        if raw_median <= 0:
            continue
        spacing_cv = float(np.std(raw_dists)) / raw_median

        if n_centers > diag_count:
            diag_count = n_centers
            diag_cv = spacing_cv
            diag_spacing = raw_median

        if n_centers >= min_divisions and spacing_cv <= max_spacing_cv:
            # Робастная оценка шага — медиана после отброса выбросов (±30%).
            filtered = _median_filter_distances(raw_dists)
            spacing = float(np.median(filtered)) if len(filtered) > 0 else raw_median
            consistent_spacings.append(spacing)
            consistent_cvs.append(spacing_cv)
            consistent_counts.append(n_centers)

    if len(consistent_spacings) == 0:
        return _OrientationStats(
            is_regular=False,
            median_spacing_px=diag_spacing,
            n_divisions=diag_count,
            spacing_cv=diag_cv,
            consistency_fraction=0.0,
            confidence=0.0,
        )

    median_spacing = float(np.median(consistent_spacings))
    spacing_cv = float(np.median(consistent_cvs))
    n_divisions = int(np.median(consistent_counts))
    consistency_fraction = len(consistent_spacings) / n_lines if n_lines > 0 else 0.0
    confidence = max(0.0, 1.0 - spacing_cv) * consistency_fraction

    return _OrientationStats(
        is_regular=True,
        median_spacing_px=median_spacing,
        n_divisions=n_divisions,
        spacing_cv=spacing_cv,
        consistency_fraction=consistency_fraction,
        confidence=confidence,
    )


def _regularity_message(stats: _OrientationStats) -> str:
    """Сообщение об отказе с числом делений и разбросом расстояний."""
    return (
        f"Регулярная шкала не обнаружена (найдено {stats.n_divisions} делений, "
        f"разброс расстояний {stats.spacing_cv * 100:.0f}%). Проверьте: в кадре "
        "калибровочная шкала, штрихи в фокусе, снимок не смазан."
    )


def calibrate_from_ruler(
    image: NDArray[np.uint8],
    division_um: float = 100.0,
    row_range: tuple[int, int] | None = None,
    threshold: int | None = None,
    max_stripe_width: int = 12,
    min_divisions: int = 8,
    max_spacing_cv: float = 0.15,
    min_confidence: float = 0.5,
) -> CalibrationResult:
    """Автокалибровка по снимку микрометрической шкалы.

    Алгоритм (METHODOLOGY.md §3.2) с проверкой регулярности:
    1. Grayscale + бинаризация (Otsu по умолчанию либо явный порог)
    2. Анализ строк (вертикальные штрихи) и столбцов (горизонтальные штрихи);
       выбирается ориентация с лучшей регулярностью
    3. Обнаружение делений и расстояний между ними, фильтр по ширине полосы
    4. Медианная фильтрация выбросов
    5. Проверка регулярности серии (min_divisions, max_spacing_cv, min_confidence)
    6. Итоговый масштаб = division_um / median_distance

    Регулярность серии отличает калибровочную шкалу от случайных тёмных
    объектов (волоски на коже и т.п.): такие объекты дают несколько
    нерегулярных краёв и не образуют согласованного периода, поэтому вместо
    мусорного результата поднимается CalibrationError.

    Args:
        image: BGR или grayscale снимок калибровочной шкалы.
        division_um: Цена деления шкалы в микронах (по умолчанию 100 мкм = 0.1 мм).
        row_range: Диапазон строк для анализа вертикальных штрихов (y_start, y_end).
                   None = центральная полоса (~10% высоты вокруг центра).
                   К анализу столбцов (горизонтальные штрихи) не применяется —
                   для них всегда берётся центральная полоса по ширине.
        threshold: Порог бинаризации (0–255). None (по умолчанию) = автоматический
                   порог Otsu (cv2.THRESH_OTSU); явное число задаёт фиксированный порог.
        max_stripe_width: Макс. ширина полосы деления в пикселях.
        min_divisions: Минимум делений в согласованной серии. Меньше — отказ.
        max_spacing_cv: Макс. коэффициент вариации расстояний (std/median) в
                        согласованной серии. Больше — расстояния нерегулярны, отказ.
        min_confidence: Минимальная надёжность результата (0.0–1.0). Ниже — отказ.

    Returns:
        CalibrationResult с рассчитанным масштабом.

    Raises:
        CalibrationError: Если цена деления не положительна; деления не обнаружены;
            регулярная серия не найдена (мало делений, большой разброс расстояний);
            либо надёжность ниже min_confidence.
    """
    if division_um <= 0:
        raise CalibrationError(
            f"Цена деления должна быть положительной, получено: {division_um}"
        )

    # Grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    h, w = gray.shape

    # Бинаризация — тёмные деления на светлом фоне.
    if threshold is None:
        _, binary_mat = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _, binary_mat = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    binary = cast(NDArray[np.uint8], binary_mat)

    # Ориентация 1: вертикальные штрихи — сканируем строки.
    row_band = _line_band(h, row_range)
    vertical = _analyze_orientation(
        binary, row_band, max_stripe_width, min_divisions, max_spacing_cv
    )

    # Ориентация 2: горизонтальные штрихи — сканируем столбцы (строки транспонированной маски).
    binary_t: NDArray[np.uint8] = np.ascontiguousarray(binary.T)
    col_band = _line_band(w, None)
    horizontal = _analyze_orientation(
        binary_t, col_band, max_stripe_width, min_divisions, max_spacing_cv
    )

    logger.debug(
        "Калибровка: вертикаль(регулярна=%s, делений=%d, CV=%.2f, надёжность=%.2f), "
        "горизонталь(регулярна=%s, делений=%d, CV=%.2f, надёжность=%.2f)",
        vertical.is_regular, vertical.n_divisions, vertical.spacing_cv, vertical.confidence,
        horizontal.is_regular, horizontal.n_divisions, horizontal.spacing_cv,
        horizontal.confidence,
    )

    # Выбор ориентации: среди регулярных — с максимальной надёжностью.
    regular = [s for s in (vertical, horizontal) if s.is_regular]
    if len(regular) == 0:
        # Регулярной серии нет ни по одной оси — отказ с диагностикой лучшей попытки.
        diag = max((vertical, horizontal), key=lambda s: s.n_divisions)
        if diag.n_divisions == 0:
            raise CalibrationError(
                "Деления не обнаружены на снимке. Проверьте, что в кадре "
                "калибровочная шкала и подходит порог бинаризации (threshold)."
            )
        raise CalibrationError(_regularity_message(diag))

    best = max(regular, key=lambda s: s.confidence)

    if best.confidence < min_confidence:
        raise CalibrationError(
            f"Низкая надёжность калибровки: {best.confidence:.2f} < {min_confidence:.2f} "
            f"(найдено {best.n_divisions} делений, разброс расстояний "
            f"{best.spacing_cv * 100:.0f}%). Проверьте: в кадре калибровочная шкала, "
            "штрихи в фокусе, снимок не смазан."
        )

    median_distance = best.median_spacing_px
    if median_distance <= 0:
        raise CalibrationError("Медианное расстояние между делениями = 0.")

    # Масштаб: мкм/px
    um_per_px = division_um / median_distance

    # Поле зрения
    fov_width_mm = w * um_per_px / 1000.0
    fov_height_mm = h * um_per_px / 1000.0
    fov_area_mm2 = fov_width_mm * fov_height_mm

    result = CalibrationResult(
        um_per_px=round(um_per_px, 2),
        fov_width_mm=round(fov_width_mm, 2),
        fov_height_mm=round(fov_height_mm, 2),
        fov_area_mm2=round(fov_area_mm2, 2),
        divisions_detected=best.n_divisions,
        median_distance_px=round(median_distance, 2),
        confidence=round(best.confidence, 3),
    )

    logger.info(
        "Калибровка: %.2f мкм/px, поле зрения %.1fx%.1f мм (%.1f мм²), "
        "делений=%d, надёжность=%.2f",
        result.um_per_px,
        result.fov_width_mm,
        result.fov_height_mm,
        result.fov_area_mm2,
        result.divisions_detected,
        result.confidence,
    )
    return result

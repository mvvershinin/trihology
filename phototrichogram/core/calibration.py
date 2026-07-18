"""
Калибровка микроскопа по микрометрической шкале.

Алгоритм (METHODOLOGY.md §3.2):
1. Перевод в grayscale
2. Бинаризация ~128 (тёмные линии на светлом фоне)
3. Построчный анализ — обнаружение тёмных полос
4. Фильтр по ширине полосы (только мелкие деления)
5. Вычисление центров полос и расстояний
6. Медианная фильтрация выбросов (±30% от медианы)
7. Итоговый масштаб: цена_деления_мкм / медиана_расстояний_px
8. Усреднение по множеству строк
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
        divisions_detected: Количество обнаруженных делений.
        median_distance_px: Медианное расстояние между делениями (px).
        confidence: Оценка надёжности (0.0–1.0). Чем больше делений
                    и меньше разброс — тем выше.
    """

    um_per_px: float
    fov_width_mm: float
    fov_height_mm: float
    fov_area_mm2: float
    divisions_detected: int
    median_distance_px: float
    confidence: float


def _detect_divisions_in_row(
    row: NDArray[np.uint8],
    max_stripe_width: int = 12,
) -> list[float]:
    """Обнаружение центров тёмных полос (делений) в одной строке пикселей.

    Алгоритм:
    1. Найти переходы 0→255 (rising edge) и 255→0 (falling edge)
    2. Пары (falling, rising) дают тёмные полосы
    3. Фильтр по ширине — только узкие деления (< max_stripe_width)
    4. Центр полосы = (start + end) / 2

    Args:
        row: Бинаризованная строка пикселей (0 или 255).
        max_stripe_width: Максимальная ширина полосы (px) — отсекает крупные объекты.

    Returns:
        Список X-координат центров обнаруженных делений.
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


def calibrate_from_ruler(
    image: NDArray[np.uint8],
    division_um: float = 100.0,
    row_range: tuple[int, int] | None = None,
    threshold: int = 128,
    max_stripe_width: int = 12,
) -> CalibrationResult:
    """Автокалибровка по снимку микрометрической шкалы.

    Алгоритм (METHODOLOGY.md §3.2):
    1. Grayscale + бинаризация по порогу
    2. Для каждой строки из row_range: обнаружение делений
    3. Вычисление расстояний между соседними делениями
    4. Медианная фильтрация выбросов
    5. Итоговый масштаб = division_um / median_distance

    Args:
        image: BGR или grayscale снимок калибровочной шкалы.
        division_um: Цена деления шкалы в микронах (по умолчанию 100 мкм = 0.1 мм).
        row_range: Диапазон строк для анализа (y_start, y_end).
                   None = центральная полоса (40% высоты ± 5%).
        threshold: Порог бинаризации (0–255).
        max_stripe_width: Макс. ширина полосы деления в пикселях.

    Returns:
        CalibrationResult с рассчитанным масштабом.

    Raises:
        CalibrationError: Если цена деления не положительна, делений
            не обнаружено или слишком мало для расчёта.
    """
    if division_um <= 0:
        raise CalibrationError(
            f"Цена деления должна быть положительной, получено: {division_um}"
        )

    # Grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    h, w = gray.shape

    # Бинаризация — тёмные деления на светлом фоне, инвертируем
    _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)

    # Диапазон строк для анализа
    if row_range is None:
        center_y = h // 2
        margin = max(h // 20, 5)
        y_start = max(0, center_y - margin)
        y_end = min(h, center_y + margin)
    else:
        y_start, y_end = row_range

    logger.debug("Калибровка: анализ строк %d..%d из %d", y_start, y_end, h)

    all_distances: list[float] = []

    for y in range(y_start, y_end):
        # cv2.threshold возвращает MatLike — строка бинарной маски это uint8.
        row = cast(NDArray[np.uint8], binary[y, :])
        centers = _detect_divisions_in_row(row, max_stripe_width)

        if len(centers) < 2:
            continue

        # Расстояния между соседними делениями
        centers_arr = np.array(sorted(centers))
        dists = np.diff(centers_arr).tolist()
        all_distances.extend(dists)

    if len(all_distances) == 0:
        raise CalibrationError(
            f"Деления не обнаружены в строках {y_start}..{y_end}. "
            "Проверьте снимок шкалы и параметры (threshold, max_stripe_width)."
        )

    # Медианная фильтрация
    filtered = _median_filter_distances(all_distances)
    if len(filtered) == 0:
        raise CalibrationError(
            "Все расстояния между делениями отброшены фильтрацией. "
            "Проверьте качество снимка шкалы."
        )

    median_distance = float(np.median(filtered))

    if median_distance <= 0:
        raise CalibrationError("Медианное расстояние между делениями = 0.")

    # Масштаб: мкм/px
    um_per_px = division_um / median_distance

    # Поле зрения
    fov_width_mm = w * um_per_px / 1000.0
    fov_height_mm = h * um_per_px / 1000.0
    fov_area_mm2 = fov_width_mm * fov_height_mm

    # Оценка надёжности: больше делений и меньше разброс → выше
    divisions_detected = len(filtered)
    if divisions_detected > 1:
        std = float(np.std(filtered))
        cv = std / median_distance if median_distance > 0 else 1.0
        confidence = min(1.0, max(0.0, 1.0 - cv)) * min(1.0, divisions_detected / 10.0)
    else:
        confidence = 0.1

    result = CalibrationResult(
        um_per_px=round(um_per_px, 2),
        fov_width_mm=round(fov_width_mm, 2),
        fov_height_mm=round(fov_height_mm, 2),
        fov_area_mm2=round(fov_area_mm2, 2),
        divisions_detected=divisions_detected,
        median_distance_px=round(median_distance, 2),
        confidence=round(confidence, 3),
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

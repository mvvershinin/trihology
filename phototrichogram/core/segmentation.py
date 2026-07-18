"""
Сегментация волос на трихоскопическом снимке.

Обновлённый пайплайн для реальных трихоскопических изображений:
    RGB -> Grayscale -> CLAHE -> Black-hat морфология -> Adaptive threshold ->
    Тонкая морфология -> Контуры

Ключевые отличия от наивного подхода (Otsu + THRESH_BINARY_INV):
- Black-hat transform выделяет ТОЛЬКО тёмные структуры (волосы) меньше ядра,
  игнорируя плавные градиенты кожи
- Adaptive threshold или Otsu на black-hat результате, где волосы уже
  хорошо отделены от фона
- Минимальная морфология — без fill_holes (убивает тонкие структуры)

Все параметры берутся из AnalysisConfig — ничего не захардкожено.
"""

from __future__ import annotations

import logging
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from phototrichogram.utils.config import AnalysisConfig

logger = logging.getLogger(__name__)


def preprocess(image: NDArray[np.uint8], config: AnalysisConfig) -> NDArray[np.uint8]:
    """Предобработка изображения перед сегментацией.

    Пайплайн:
    1. Перевод в grayscale
    2. Адаптивная нормализация контраста (CLAHE)
    3. Медианный фильтр для подавления шума

    Args:
        image: BGR изображение с камеры.
        config: Параметры анализа (clahe_clip_limit, clahe_tile_size, median_blur_kernel).

    Returns:
        Обработанное одноканальное изображение (uint8).
    """
    # Шаг 1 — Grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    # Шаг 2 — CLAHE
    tile = config.clahe_tile_size
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit,
        tileGridSize=(tile, tile),
    )
    enhanced = clahe.apply(gray)

    # Шаг 3 — Медианный фильтр (ядро должно быть нечётным)
    kernel = config.median_blur_kernel
    if kernel % 2 == 0:
        kernel += 1
    denoised = cv2.medianBlur(enhanced, kernel)

    # cv2 возвращает MatLike — после grayscale/CLAHE/медианы это одноканальный uint8.
    return cast(NDArray[np.uint8], denoised)


def segment_hair(image: NDArray[np.uint8], config: AnalysisConfig) -> NDArray[np.uint8]:
    """Сегментация волос — полный пайплайн до бинарной маски.

    Алгоритм для реальных трихоскопических снимков:
    1. Предобработка (grayscale, CLAHE, median blur)
    2. Black-hat морфология — выделяет тёмные структуры (волосы) на светлом фоне
    3. Бинаризация black-hat результата (Otsu или адаптивный порог)
    4. Лёгкая морфология — открытие для удаления мелкого шума
    5. БЕЗ fill_holes — волосы тонкие, заполнение дыр сливает их

    Почему black-hat работает:
    - Волосы — тонкие тёмные линии шириной 3-15 px
    - Кожа — плавный градиент розового/бежевого
    - Black-hat = closing(image) - image, выделяет тёмные объекты МЕНЬШЕ ядра
    - Размер ядра blackhat должен быть БОЛЬШЕ толщины самого толстого волоса

    Args:
        image: BGR изображение с камеры (любой размер).
        config: Параметры анализа — все пороги, размеры ядер.

    Returns:
        Бинарная маска (uint8): 255 = волос, 0 = фон/кожа.
    """
    # Предобработка
    denoised = preprocess(image, config)

    h, w = denoised.shape[:2]
    logger.debug(
        "Сегментация: изображение %dx%d, метод=%s",
        w, h, config.threshold_method,
    )

    # Black-hat морфология: выделяет тёмные структуры меньше ядра
    # Размер ядра должен быть больше толщины волоса (обычно 15-51 px)
    blackhat_size = config.blackhat_kernel_size
    # Ядро должно быть нечётным
    if blackhat_size % 2 == 0:
        blackhat_size += 1

    blackhat_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (blackhat_size, blackhat_size)
    )
    blackhat = cv2.morphologyEx(denoised, cv2.MORPH_BLACKHAT, blackhat_kernel)

    logger.debug(
        "Black-hat: kernel=%d, min=%.0f, max=%.0f, mean=%.1f",
        blackhat_size,
        blackhat.min(), blackhat.max(), blackhat.mean(),
    )

    # Бинаризация black-hat результата
    if config.threshold_method == "adaptive":
        # Адаптивный порог — хорош для неравномерного освещения
        block_size = config.adaptive_block_size
        if block_size % 2 == 0:
            block_size += 1
        binary = cv2.adaptiveThreshold(
            blackhat,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            -config.adaptive_c,
        )
    else:
        # Otsu на black-hat — волосы чётко отделены от нуля
        thresh_val, binary = cv2.threshold(
            blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        logger.debug("Otsu порог на black-hat: %d", int(thresh_val))

    # Подсчёт белых пикселей после бинаризации
    white_pct = np.count_nonzero(binary) / binary.size * 100
    logger.debug("После бинаризации: %.1f%% белых пикселей", white_pct)

    # Если слишком много белых пикселей — порог слишком мягкий,
    # применяем более жёсткую фильтрацию
    if white_pct > config.max_white_pct:
        logger.warning(
            "Слишком много белых пикселей (%.1f%% > %.1f%%), ужесточаем порог",
            white_pct, config.max_white_pct,
        )
        # Поднимаем порог — берём более высокий перцентиль
        # blackhat это MatLike (dtype integer|floating) — сужаем до uint8 для np.percentile
        p_thresh = (
            np.percentile(cast(NDArray[np.uint8], blackhat[blackhat > 0]), 75)
            if np.any(blackhat > 0)
            else 10
        )
        _, binary = cv2.threshold(
            blackhat, int(p_thresh), 255, cv2.THRESH_BINARY
        )
        white_pct = np.count_nonzero(binary) / binary.size * 100
        logger.debug("После ужесточения: %.1f%% белых, порог=%d", white_pct, int(p_thresh))

    # Лёгкая морфология — только открытие для удаления мелкого шума
    morph_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (config.morph_kernel_size, config.morph_kernel_size),
    )

    # Небольшое закрытие для соединения разрывов в волосах
    if config.morph_close_iterations > 0:
        binary = cv2.morphologyEx(
            binary, cv2.MORPH_CLOSE, morph_kernel,
            iterations=config.morph_close_iterations,
        )

    # Открытие — удаляет мелкий мусор
    if config.morph_open_iterations > 0:
        binary = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN, morph_kernel,
            iterations=config.morph_open_iterations,
        )

    # НЕ делаем fill_holes — это сливает тонкие волосы в блобы

    white_final = np.count_nonzero(binary) / binary.size * 100
    logger.debug(
        "Сегментация завершена: маска %dx%d, %.1f%% белых",
        w, h, white_final,
    )

    # cv2 морфология/порог возвращают MatLike — итоговая маска это uint8 (0/255).
    return cast(NDArray[np.uint8], binary)


def find_hair_contours(
    mask: NDArray[np.uint8], config: AnalysisConfig
) -> list[NDArray[np.int32]]:
    """Поиск контуров волос на бинарной маске с фильтрацией.

    Фильтры (METHODOLOGY.md §4.4):
    - Площадь >= min_hair_area (отсечка мусора, пыли)
    - Циркулярность < max_circularity (волосы вытянуты, круглое = не волос)
    - Аспектное соотношение >= min_aspect_ratio (волос длинный и тонкий)

    Циркулярность = 4*pi*area / perimeter^2. Круг = 1.0, вытянутый объект -> 0.

    Args:
        mask: Бинарная маска от segment_hair().
        config: Параметры анализа (min_hair_area, max_circularity).

    Returns:
        Список контуров (np.ndarray), прошедших фильтрацию.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    hair_contours: list[NDArray[np.int32]] = []
    rejected_area = 0
    rejected_shape = 0
    rejected_aspect = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)

        # Фильтр по площади
        if area < config.min_hair_area:
            rejected_area += 1
            continue

        # Фильтр по форме — циркулярность
        perimeter = cv2.arcLength(cnt, True)
        if perimeter <= 0:
            continue

        circularity = 4.0 * np.pi * area / (perimeter * perimeter)
        if circularity > config.max_circularity:
            rejected_shape += 1
            continue

        # Фильтр по аспектному соотношению (минимальный описанный прямоугольник)
        if len(cnt) >= 5:
            rect = cv2.minAreaRect(cnt)
            (_, _), (w, h), _ = rect
            if w > 0 and h > 0:
                aspect = max(w, h) / min(w, h)
                if aspect < config.min_aspect_ratio:
                    rejected_aspect += 1
                    continue

        # cv2.findContours отдаёт Sequence[MatLike] — контур это массив точек int32.
        hair_contours.append(cast(NDArray[np.int32], cnt))

    logger.debug(
        "Контуры: всего=%d, отклонено площадь=%d, форма=%d, аспект=%d, принято=%d",
        len(contours),
        rejected_area,
        rejected_shape,
        rejected_aspect,
        len(hair_contours),
    )
    return hair_contours

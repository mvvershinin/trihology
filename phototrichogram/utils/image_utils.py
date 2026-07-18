"""
Утилиты для работы с изображениями.

Конвертация между форматами numpy (OpenCV BGR), PIL и tkinter PhotoImage.
Вспомогательные функции для ресайза, работы с путями изображений.

ВАЖНО: Этот модуль НЕ использует cv2 напрямую.
Конвертация BGR→RGB делается через numpy, отображение — через PIL.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageTk

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Поддерживаемые форматы изображений
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif",
})


def cv2_to_photoimage(cv_image: NDArray[np.uint8]) -> ImageTk.PhotoImage:
    """Конвертация изображения из формата OpenCV (BGR numpy) в tkinter PhotoImage.

    Args:
        cv_image: Изображение в формате numpy array (BGR или Grayscale).

    Returns:
        ImageTk.PhotoImage для отображения в tkinter виджетах.

    Raises:
        ValueError: Если формат изображения не поддерживается.
    """
    pil_image = numpy_to_pil(cv_image)
    return ImageTk.PhotoImage(image=pil_image)


def numpy_to_pil(image: NDArray[np.uint8]) -> Image.Image:
    """Конвертация numpy array (BGR или Grayscale) в PIL Image (RGB).

    Args:
        image: numpy array в формате BGR (H, W, 3), BGRA (H, W, 4)
               или Grayscale (H, W).

    Returns:
        PIL Image в режиме RGB или L (grayscale).

    Raises:
        ValueError: Если формат массива не поддерживается.
    """
    if image.ndim == 2:
        # Grayscale
        return Image.fromarray(image, mode="L")

    if image.ndim == 3:
        channels = image.shape[2]
        if channels == 3:
            # BGR → RGB (через numpy, без cv2)
            rgb = image[:, :, ::-1]
            return Image.fromarray(rgb, mode="RGB")
        if channels == 4:
            # BGRA → RGBA
            rgba = np.empty_like(image)
            rgba[:, :, 0] = image[:, :, 2]  # R
            rgba[:, :, 1] = image[:, :, 1]  # G
            rgba[:, :, 2] = image[:, :, 0]  # B
            rgba[:, :, 3] = image[:, :, 3]  # A
            return Image.fromarray(rgba, mode="RGBA")

    msg = f"Неподдерживаемый формат изображения: ndim={image.ndim}, shape={image.shape}"
    raise ValueError(msg)


def pil_to_numpy(image: Image.Image) -> NDArray[np.uint8]:
    """Конвертация PIL Image в numpy array (BGR формат OpenCV).

    Args:
        image: PIL Image в любом режиме (RGB, RGBA, L, и т.д.).

    Returns:
        numpy array в формате BGR (H, W, 3) или Grayscale (H, W).
    """
    if image.mode == "L":
        return np.array(image)

    if image.mode != "RGB":
        image = image.convert("RGB")

    rgb_array = np.array(image)
    # RGB → BGR (через numpy slice, без cv2)
    bgr_array: NDArray[np.uint8] = rgb_array[:, :, ::-1].copy()
    return bgr_array


def resize_to_fit(
    image: NDArray[np.uint8],
    max_width: int,
    max_height: int,
) -> NDArray[np.uint8]:
    """Ресайз изображения с сохранением пропорций (вписать в рамку).

    Уменьшает изображение так, чтобы оно целиком вместилось
    в прямоугольник max_width x max_height. Если изображение
    уже меньше — возвращает без изменений.

    Использует PIL для ресайза (качественный LANCZOS фильтр).

    Args:
        image: numpy array (BGR или Grayscale).
        max_width: Максимальная ширина результата.
        max_height: Максимальная высота результата.

    Returns:
        numpy array с уменьшенным изображением (тот же формат).
    """
    if image.ndim == 2:
        h, w = image.shape
    else:
        h, w = image.shape[:2]

    if w <= max_width and h <= max_height:
        return image

    # Вычисление масштаба с сохранением пропорций
    scale = min(max_width / w, max_height / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    # Ресайз через PIL (LANCZOS — высокое качество)
    pil_image = numpy_to_pil(image)
    pil_resized = pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return pil_to_numpy(pil_resized)


def resize_to_fit_pil(
    image: Image.Image,
    max_width: int,
    max_height: int,
) -> Image.Image:
    """Ресайз PIL Image с сохранением пропорций.

    Args:
        image: PIL Image.
        max_width: Максимальная ширина.
        max_height: Максимальная высота.

    Returns:
        PIL Image уменьшенное до указанных размеров.
    """
    w, h = image.size

    if w <= max_width and h <= max_height:
        return image

    scale = min(max_width / w, max_height / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    return image.resize((new_w, new_h), Image.Resampling.LANCZOS)


def load_image(path: Path | str) -> NDArray[np.uint8] | None:
    """Загрузка изображения из файла в формат numpy BGR.

    Использует PIL для загрузки (без зависимости от cv2).

    Args:
        path: Путь к файлу изображения.

    Returns:
        numpy array в формате BGR (H, W, 3) или None при ошибке.
    """
    path = Path(path)

    if not path.exists():
        logger.warning("Файл изображения не найден: %s", path)
        return None

    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        logger.warning("Неподдерживаемый формат файла: %s", path.suffix)
        return None

    try:
        pil_image = Image.open(path)
        pil_image.load()  # Принудительная загрузка данных
        return pil_to_numpy(pil_image)
    except (OSError, ValueError) as e:
        logger.error("Ошибка загрузки изображения %s: %s", path, e)
        return None


def save_image(image: NDArray[np.uint8], path: Path | str) -> bool:
    """Сохранение numpy изображения (BGR) в файл.

    Создаёт родительскую директорию если не существует.

    Args:
        image: numpy array в формате BGR (H, W, 3) или Grayscale (H, W).
        path: Путь для сохранения (расширение определяет формат).

    Returns:
        True если сохранение успешно, False при ошибке.
    """
    path = Path(path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pil_image = numpy_to_pil(image)
        pil_image.save(path)
        logger.info("Изображение сохранено: %s", path)
        return True
    except (OSError, ValueError) as e:
        logger.error("Ошибка сохранения изображения %s: %s", path, e)
        return False


def is_image_file(path: Path | str) -> bool:
    """Проверка, является ли файл поддерживаемым изображением.

    Args:
        path: Путь к файлу.

    Returns:
        True если расширение файла в списке поддерживаемых.
    """
    return Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def list_images(directory: Path | str) -> list[Path]:
    """Получение списка файлов изображений в директории.

    Возвращает отсортированный по имени список.

    Args:
        directory: Путь к директории.

    Returns:
        Список Path объектов для найденных изображений.
    """
    directory = Path(directory)

    if not directory.is_dir():
        logger.warning("Директория не найдена: %s", directory)
        return []

    images = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ]
    images.sort(key=lambda p: p.name)
    return images

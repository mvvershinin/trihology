"""
Управление USB-микроскопом через V4L2 (OpenCV backend).

Единственный модуль в core/, который работает с камерой.
Поддерживает Protocol для мокирования в тестах.

Особенности (METHODOLOGY.md §3.1):
- USB-микроскоп работает как стандартная вебкамера (/dev/videoN)
- Формат: YUYV 4:2:2, разрешение 640x480 по умолчанию
- Кнопка захвата на корпусе (опционально, через evdev)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


@runtime_checkable
class CameraProtocol(Protocol):
    """Интерфейс камеры — позволяет мокировать в тестах.

    Любой объект, реализующий эти методы, может использоваться
    вместо реальной камеры (Duck Typing).
    """

    def read_frame(self) -> NDArray[np.uint8] | None:
        """Захват одного кадра. None если камера недоступна."""
        ...

    def is_opened(self) -> bool:
        """Открыта ли камера."""
        ...

    def release(self) -> None:
        """Освободить ресурсы камеры."""
        ...


@dataclass
class DeviceInfo:
    """Информация об обнаруженном видеоустройстве.

    Attributes:
        index: Индекс устройства (/dev/videoN → N).
        name: Читаемое имя (/dev/videoN).
        width: Текущая ширина кадра.
        height: Текущая высота кадра.
    """

    index: int
    name: str
    width: int
    height: int


def _device_name(index: int) -> str:
    """Стабильное имя видеоустройства (модель камеры) по индексу.

    На Linux читает модель из sysfs
    (``/sys/class/video4linux/video{i}/name``) — это имя не зависит от порядка
    перечисления устройств, поэтому пригодно для запоминания выбора между
    запусками (в отличие от ``/dev/videoN``, чей индекс может меняться).
    Если sysfs недоступен (не Linux, нет файла) — fallback на ``/dev/videoN``.

    Ограничение: две одинаковые модели камер вернут одно имя и неразличимы —
    для таких случаев остаётся резервный подбор по индексу.

    Args:
        index: Индекс устройства (/dev/videoN).

    Returns:
        Модель камеры из sysfs либо ``/dev/video{index}``.
    """
    sysfs_name = Path(f"/sys/class/video4linux/video{index}/name")
    try:
        name = sysfs_name.read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return f"/dev/video{index}"


def list_devices(max_index: int = 10) -> list[DeviceInfo]:
    """Перечисление доступных видеоустройств V4L2.

    Перебирает /dev/video0../dev/videoN и проверяет,
    можно ли открыть устройство через OpenCV.

    Args:
        max_index: Максимальный индекс для проверки.

    Returns:
        Список DeviceInfo для каждого доступного устройства.
        ``name`` — модель камеры из sysfs (стабильна между запусками),
        либо ``/dev/videoN`` если модель прочитать не удалось.
    """
    devices: list[DeviceInfo] = []

    # Backend: V4L2 на Linux, любой доступный на других платформах
    backend = cv2.CAP_V4L2 if sys.platform == "linux" else cv2.CAP_ANY

    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            devices.append(DeviceInfo(
                index=i,
                name=_device_name(i),
                width=w,
                height=h,
            ))
            cap.release()

    logger.debug("Обнаружено устройств: %d", len(devices))
    return devices


class Camera:
    """USB-микроскоп через V4L2 / OpenCV VideoCapture.

    Контекстный менеджер — автоматически освобождает ресурсы.

    Пример использования::

        with Camera() as cam:
            cam.open(device_index=0)
            cam.set_resolution(640, 480)
            frame = cam.read_frame()

    Или без контекстного менеджера::

        cam = Camera()
        cam.open(0)
        frame = cam.read_frame()
        cam.release()
    """

    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None
        self._device_index: int | None = None

    def __enter__(self) -> Camera:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.release()

    def open(self, device_index: int = 0) -> bool:
        """Открытие видеоустройства.

        Если камера уже открыта — сначала закрывает текущую.

        Args:
            device_index: Индекс устройства (/dev/videoN → N).

        Returns:
            True если устройство успешно открыто.
        """
        if self._cap is not None:
            self.release()

        backend = cv2.CAP_V4L2 if sys.platform == "linux" else cv2.CAP_ANY
        self._cap = cv2.VideoCapture(device_index, backend)
        self._device_index = device_index

        if self._cap.isOpened():
            logger.info("Камера открыта: /dev/video%d", device_index)
            return True

        logger.warning("Не удалось открыть камеру: /dev/video%d", device_index)
        self._cap.release()
        self._cap = None
        return False

    def close(self) -> None:
        """Закрытие камеры (синоним release)."""
        self.release()

    def release(self) -> None:
        """Освобождение ресурсов камеры."""
        if self._cap is not None:
            self._cap.release()
            logger.debug("Камера /dev/video%s освобождена", self._device_index)
            self._cap = None
            self._device_index = None

    def is_opened(self) -> bool:
        """Проверка, открыта ли камера.

        Returns:
            True если VideoCapture активен и устройство доступно.
        """
        return self._cap is not None and self._cap.isOpened()

    def read_frame(self) -> NDArray[np.uint8] | None:
        """Захват одного кадра с камеры.

        Returns:
            BGR изображение (np.ndarray) или None если захват не удался.
        """
        if not self.is_opened():
            return None

        assert self._cap is not None  # для mypy, после is_opened()
        ret, frame = self._cap.read()
        if ret:
            # cv2 возвращает MatLike (Mat | ndarray) — кадр камеры это BGR uint8.
            return cast(NDArray[np.uint8], frame)

        logger.warning("Не удалось захватить кадр с /dev/video%s", self._device_index)
        return None

    def set_resolution(self, width: int, height: int) -> bool:
        """Установка разрешения камеры.

        Камера может не поддерживать запрошенное разрешение —
        в этом случае будет установлено ближайшее поддерживаемое.

        Args:
            width: Запрашиваемая ширина кадра.
            height: Запрашиваемая высота кадра.

        Returns:
            True если разрешение установлено (может отличаться от запрошенного).
        """
        if not self.is_opened():
            return False

        assert self._cap is not None
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if actual_w != width or actual_h != height:
            logger.warning(
                "Запрошено разрешение %dx%d, установлено %dx%d",
                width,
                height,
                actual_w,
                actual_h,
            )
        else:
            logger.debug("Разрешение установлено: %dx%d", width, height)

        return True

    def set_fps(self, fps: int) -> bool:
        """Установка частоты кадров.

        Args:
            fps: Запрашиваемая частота кадров.

        Returns:
            True если камера открыта.
        """
        if not self.is_opened():
            return False

        assert self._cap is not None
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
        logger.debug("FPS: запрошено=%d, установлено=%.1f", fps, actual_fps)
        return True

    def get_resolution(self) -> tuple[int, int] | None:
        """Текущее разрешение камеры.

        Returns:
            (width, height) или None если камера не открыта.
        """
        if not self.is_opened():
            return None

        assert self._cap is not None
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (w, h)

    @property
    def device_index(self) -> int | None:
        """Индекс текущего устройства или None."""
        return self._device_index

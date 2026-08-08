"""
Управление USB-микроскопом через OpenCV (V4L2 на Linux, DirectShow на Windows).

Единственный модуль в core/, который работает с камерой.
Поддерживает Protocol для мокирования в тестах.

Особенности (METHODOLOGY.md §3.1):
- USB-микроскоп работает как стандартная вебкамера
  (``/dev/videoN`` на Linux, UVC-устройство на Windows — драйвер встроенный)
- Разрешение 640x480 по умолчанию
- Кнопка захвата на корпусе (опционально, через evdev)

Про backend. OpenCV умеет говорить с камерой несколькими способами, и выбор
влияет на то, заведётся ли конкретный микроскоп:

- **Linux** — V4L2, единственный вменяемый вариант.
- **Windows** — DirectShow (``dshow``) или Media Foundation (``msmf``).
  По умолчанию берём DirectShow: с дешёвыми USB-микроскопами он открывается
  быстрее и надёжнее, тогда как MSMF на части моделей отдаёт чёрный кадр или
  открывает устройство по несколько секунд. Если конкретный микроскоп с
  DirectShow не пошёл — переключается в настройках (``CameraConfig.backend``),
  переписывать код не нужно.
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

# Известные backend'ы OpenCV по человекочитаемому имени (значение из конфига).
# "any" — пусть OpenCV решает сам (крайний случай, поведение непредсказуемо).
_BACKENDS: dict[str, int] = {
    "v4l2": cv2.CAP_V4L2,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}

# Порядок перебора при backend="auto": первый удавшийся выигрывает.
# На Windows DirectShow первым, MSMF запасным — см. докстринг модуля.
_AUTO_CHAINS: dict[str, tuple[str, ...]] = {
    "linux": ("v4l2",),
    "win32": ("dshow", "msmf"),
}
_AUTO_CHAIN_FALLBACK: tuple[str, ...] = ("any",)


def backend_chain(name: str = "auto") -> list[int]:
    """Порядок перебора backend'ов OpenCV для текущей системы.

    Args:
        name: Значение ``CameraConfig.backend`` — конкретный backend
            ("v4l2" / "dshow" / "msmf" / "any") либо "auto" (по системе).

    Returns:
        Список констант ``cv2.CAP_*`` в порядке перебора. Для явно заданного
        backend'а — ровно один элемент (без подстановки чего-то другого:
        пользователь выбрал сознательно). Неизвестное имя трактуется как
        "auto" с предупреждением в лог.
    """
    key = name.strip().lower()

    if key in _BACKENDS:
        return [_BACKENDS[key]]

    if key != "auto":
        logger.warning("Неизвестный backend камеры %r, используется 'auto'", name)

    chain = _AUTO_CHAINS.get(sys.platform, _AUTO_CHAIN_FALLBACK)
    return [_BACKENDS[n] for n in chain]


def _windows_device_names() -> list[str]:
    """Имена видеоустройств Windows через DirectShow (по порядку индексов).

    Использует необязательный пакет ``pygrabber``: он спрашивает у DirectShow
    настоящие названия устройств («USB 2.0 Camera»). Нужен, чтобы запоминание
    выбранной камеры между запусками работало по имени модели, как на Linux.

    Returns:
        Имена устройств по возрастанию индекса; пустой список, если пакет не
        установлен или DirectShow не ответил (тогда вызывающий код обходится
        нумерацией).
    """
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError:
        logger.debug("pygrabber не установлен — имена камер Windows недоступны")
        return []

    try:
        return [str(n) for n in FilterGraph().get_input_devices()]
    except Exception:
        logger.warning("DirectShow не отдал список камер", exc_info=True)
        return []


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
        index: Индекс устройства (на Linux — /dev/videoN → N).
        name: Читаемое имя: модель камеры, либо ``/dev/videoN`` (Linux) /
            «Камера N» (Windows), если модель узнать не удалось.
        width: Текущая ширина кадра.
        height: Текущая высота кадра.
    """

    index: int
    name: str
    width: int
    height: int


def _device_name(index: int, windows_names: list[str] | None = None) -> str:
    """Стабильное имя видеоустройства (модель камеры) по индексу.

    Имя должно быть устойчиво к перенумерации устройств: выбранная камера
    запоминается между запусками именно по имени модели, потому что индекс
    может смениться при перетыкании USB.

    - **Linux** — модель из sysfs (``/sys/class/video4linux/video{i}/name``),
      при неудаче ``/dev/videoN``.
    - **Windows** — имя из DirectShow (см. ``_windows_device_names``), при
      неудаче «Камера N». Строку вида ``/dev/videoN`` на Windows не выдаём
      никогда: такого устройства там не существует, и в списке камер это
      выглядело бы враньём.

    Ограничение: две одинаковые модели камер вернут одно имя и неразличимы —
    для таких случаев остаётся резервный подбор по индексу.

    Args:
        index: Индекс устройства.
        windows_names: Уже полученный список имён DirectShow (чтобы не
            опрашивать его заново на каждое устройство). Только для Windows.

    Returns:
        Модель камеры либо запасное имя, осмысленное для текущей системы.
    """
    if sys.platform == "win32":
        names = _windows_device_names() if windows_names is None else windows_names
        if index < len(names) and names[index].strip():
            return names[index].strip()
        return f"Камера {index}"

    sysfs_name = Path(f"/sys/class/video4linux/video{index}/name")
    try:
        name = sysfs_name.read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return f"/dev/video{index}"


def list_devices(max_index: int = 10, backend: str = "auto") -> list[DeviceInfo]:
    """Перечисление доступных видеоустройств.

    Перебирает индексы 0..max_index и проверяет, открывается ли устройство.

    На Windows число проверяемых индексов сначала сужается по списку
    DirectShow: пробное открытие несуществующего индекса там стоит секунды
    (особенно через MSMF), и слепой перебор до 10 подвесил бы интерфейс на
    полминуты. Если список получить не удалось — честно перебираем всё.

    Args:
        max_index: Максимальный индекс для проверки.
        backend: Значение ``CameraConfig.backend`` (см. ``backend_chain``).

    Returns:
        Список DeviceInfo для каждого доступного устройства. ``name`` —
        модель камеры (стабильна между запусками) либо запасное имя.
    """
    devices: list[DeviceInfo] = []
    candidates = backend_chain(backend)

    windows_names: list[str] | None = None
    probe_limit = max_index
    if sys.platform == "win32":
        windows_names = _windows_device_names()
        if windows_names:
            probe_limit = min(max_index, len(windows_names))

    for i in range(probe_limit):
        for api in candidates:
            cap = cv2.VideoCapture(i, api)
            if not cap.isOpened():
                cap.release()
                continue
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            devices.append(DeviceInfo(
                index=i,
                name=_device_name(i, windows_names),
                width=w,
                height=h,
            ))
            cap.release()
            break

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
        self._device_label: str = "камера"

    def __enter__(self) -> Camera:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.release()

    def open(self, device_index: int = 0, backend: str = "auto") -> bool:
        """Открытие видеоустройства.

        Если камера уже открыта — сначала закрывает текущую.

        При ``backend="auto"`` backend'ы перебираются в порядке, разумном для
        системы (на Windows DirectShow, затем MSMF): дешёвые USB-микроскопы
        заводятся не с каждым. Явно заданный backend не подменяется — если
        пользователь выбрал его сам, молча уходить на другой нельзя.

        Args:
            device_index: Индекс устройства.
            backend: Значение ``CameraConfig.backend`` (см. ``backend_chain``).

        Returns:
            True если устройство успешно открыто.
        """
        if self._cap is not None:
            self.release()

        label = _device_name(device_index)

        for api in backend_chain(backend):
            cap = cv2.VideoCapture(device_index, api)
            if cap.isOpened():
                self._cap = cap
                self._device_index = device_index
                self._device_label = label
                logger.info("Камера открыта: %s (backend %s)", label, api)
                return True
            cap.release()

        logger.warning("Не удалось открыть камеру: %s", label)
        return False

    def close(self) -> None:
        """Закрытие камеры (синоним release)."""
        self.release()

    def release(self) -> None:
        """Освобождение ресурсов камеры."""
        if self._cap is not None:
            self._cap.release()
            logger.debug("Камера %s освобождена", self._device_label)
            self._cap = None
            self._device_index = None
            self._device_label = "камера"

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

        logger.warning("Не удалось захватить кадр с %s", self._device_label)
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

    def set_video_format(self, fourcc: str) -> bool:
        """Установка кодека видеопотока (FOURCC).

        Задавать формат нужно ДО разрешения: смена кодека сбрасывает у части
        драйверов уже выставленный размер кадра.

        Пустая строка — ничего не делать и оставить выбор драйверу. Это дефолт
        и историческое поведение: на Linux поток берётся как есть, и трогать
        рабочую настройку врача незачем. На Windows осмысленно ставить MJPG —
        часть USB-микроскопов на несжатом потоке отдаёт 5–10 кадров в секунду
        вместо заявленных 30.

        Args:
            fourcc: Четырёхбуквенный код кодека ('MJPG', 'YUYV', ...) либо
                пустая строка (не задавать).

        Returns:
            True если формат применён; False если камера закрыта, код пустой
            или неверной длины.
        """
        code = fourcc.strip().upper()
        if not code:
            return False

        if len(code) != 4:
            logger.warning("Код видеоформата %r не четырёхбуквенный, игнорируется", fourcc)
            return False

        if not self.is_opened():
            return False

        assert self._cap is not None
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*code))
        logger.debug("Формат видеопотока запрошен: %s", code)
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

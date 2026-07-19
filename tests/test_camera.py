"""
Тесты управления USB-микроскопом (core/camera.py).

Реальное устройство не требуется -- cv2.VideoCapture подменяется через
monkeypatch (см. паттерн в docs/ARCHITECTURE.md, раздел «Тестирование»).

Покрывает:
- list_devices: обнаружение доступных индексов, пустой список, max_index
- Camera: open/is_opened/release/read_frame, повторное открытие,
  set_resolution/get_resolution, set_fps, device_index, контекстный менеджер
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from phototrichogram.core.camera import Camera, DeviceInfo, list_devices

# ---------------------------------------------------------------------------
# Подделка cv2.VideoCapture
# ---------------------------------------------------------------------------


def _make_fake_capture(available: set[int]) -> type:
    """Создаёт класс-подделку cv2.VideoCapture с заданными доступными индексами.

    Args:
        available: Множество индексов устройств, которые "успешно открываются".

    Returns:
        Класс, совместимый по интерфейсу с cv2.VideoCapture
        (isOpened/get/set/read/release).
    """

    class _FakeCapture:
        def __init__(self, index: int, backend: int | None = None) -> None:
            self._index = index
            self._opened = index in available
            self._width = 640
            self._height = 480
            self._fps = 30.0

        def isOpened(self) -> bool:  # noqa: N802 -- имя метода диктуется cv2.VideoCapture
            return self._opened

        def get(self, prop: int) -> float:
            if prop == cv2.CAP_PROP_FRAME_WIDTH:
                return float(self._width)
            if prop == cv2.CAP_PROP_FRAME_HEIGHT:
                return float(self._height)
            if prop == cv2.CAP_PROP_FPS:
                return self._fps
            return 0.0

        def set(self, prop: int, value: float) -> bool:
            if prop == cv2.CAP_PROP_FRAME_WIDTH:
                self._width = int(value)
            elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
                self._height = int(value)
            elif prop == cv2.CAP_PROP_FPS:
                self._fps = value
            return True

        def read(self) -> tuple[bool, np.ndarray | None]:
            if not self._opened:
                return False, None
            frame = np.full((self._height, self._width, 3), 128, dtype=np.uint8)
            return True, frame

        def release(self) -> None:
            self._opened = False

    return _FakeCapture


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


class TestListDevices:
    """Тесты перечисления доступных видеоустройств."""

    def test_returns_only_available_indices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """В список попадают только устройства, которые успешно открылись."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0, 2}))

        devices = list_devices(max_index=5)

        assert [d.index for d in devices] == [0, 2]

    def test_device_info_contains_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DeviceInfo содержит корректное имя и разрешение."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        # Имя устройства зависит от sysfs хоста — фиксируем детерминированно.
        monkeypatch.setattr(
            "phototrichogram.core.camera._device_name", lambda i: f"/dev/video{i}"
        )

        devices = list_devices(max_index=1)

        assert devices == [DeviceInfo(index=0, name="/dev/video0", width=640, height=480)]

    def test_device_name_reads_sysfs_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_device_name читает модель камеры из sysfs, когда файл доступен."""
        from phototrichogram.core import camera

        monkeypatch.setattr(
            camera.Path, "read_text", lambda self, encoding="utf-8": "USB Microscope\n"
        )
        assert camera._device_name(2) == "USB Microscope"

    def test_device_name_fallback_when_sysfs_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Нет sysfs (не Linux / нет файла) -> имя откатывается на /dev/videoN."""
        from phototrichogram.core import camera

        def _raise(self: object, encoding: str = "utf-8") -> str:
            raise OSError("no sysfs")

        monkeypatch.setattr(camera.Path, "read_text", _raise)
        assert camera._device_name(3) == "/dev/video3"

    def test_no_devices_available_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ни одно устройство не открылось -> пустой список."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture(set()))

        devices = list_devices(max_index=5)

        assert devices == []

    def test_respects_max_index_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Индексы >= max_index не проверяются и не попадают в результат."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0, 1, 2, 3, 4}))

        devices = list_devices(max_index=2)

        assert [d.index for d in devices] == [0, 1]


# ---------------------------------------------------------------------------
# Camera -- открытие/закрытие
# ---------------------------------------------------------------------------


class TestCameraOpenClose:
    """Тесты открытия, закрытия и статуса камеры."""

    def test_open_existing_device_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Успешное открытие устройства возвращает True."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.open(0) is True
        assert cam.is_opened() is True

    def test_open_missing_device_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Попытка открыть отсутствующее устройство возвращает False."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.open(5) is False
        assert cam.is_opened() is False

    def test_is_opened_false_before_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """До вызова open() камера считается закрытой."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.is_opened() is False

    def test_release_closes_camera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """release() закрывает камеру и сбрасывает device_index."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)

        cam.release()

        assert cam.is_opened() is False
        assert cam.device_index is None

    def test_release_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Повторный вызов release() не вызывает ошибок."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)
        cam.release()

        cam.release()  # не должно упасть

        assert cam.is_opened() is False

    def test_close_is_alias_for_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """close() -- синоним release()."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)

        cam.close()

        assert cam.is_opened() is False

    def test_reopen_switches_active_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Повторный open() с другим индексом освобождает предыдущее устройство."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0, 1}))
        cam = Camera()
        cam.open(0)

        cam.open(1)

        assert cam.device_index == 1
        assert cam.is_opened() is True

    def test_device_index_none_when_never_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """device_index -- None, пока камера ни разу не открывалась."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.device_index is None

    def test_context_manager_releases_on_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """При выходе из `with Camera() as cam` ресурсы освобождаются."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))

        with Camera() as cam:
            cam.open(0)
            assert cam.is_opened() is True

        assert cam.is_opened() is False


# ---------------------------------------------------------------------------
# Camera -- захват кадров
# ---------------------------------------------------------------------------


class TestCameraReadFrame:
    """Тесты захвата кадров с камеры."""

    def test_read_frame_returns_array_when_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Открытая камера возвращает кадр правильного размера."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)

        frame = cam.read_frame()

        assert frame is not None
        assert frame.shape == (480, 640, 3)

    def test_read_frame_none_when_not_opened(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Незапущенная камера возвращает None вместо кадра."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.read_frame() is None

    def test_read_frame_none_after_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """После release() захват кадра возвращает None."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)
        cam.release()

        assert cam.read_frame() is None


# ---------------------------------------------------------------------------
# Camera -- разрешение и FPS
# ---------------------------------------------------------------------------


class TestCameraResolutionAndFps:
    """Тесты настройки разрешения и частоты кадров."""

    def test_get_resolution_none_when_not_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Разрешение неоткрытой камеры -- None."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.get_resolution() is None

    def test_get_resolution_returns_tuple_when_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Разрешение открытой камеры -- кортеж (width, height)."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)

        assert cam.get_resolution() == (640, 480)

    def test_set_resolution_false_when_not_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Попытка установить разрешение неоткрытой камеры -> False."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.set_resolution(320, 240) is False

    def test_set_resolution_updates_get_resolution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Установленное разрешение отражается в get_resolution()."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)

        cam.set_resolution(320, 240)

        assert cam.get_resolution() == (320, 240)

    def test_set_fps_false_when_not_opened(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Попытка установить FPS неоткрытой камеры -> False."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()

        assert cam.set_fps(30) is False

    def test_set_fps_true_when_opened(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Установка FPS открытой камеры возвращает True."""
        monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture({0}))
        cam = Camera()
        cam.open(0)

        assert cam.set_fps(15) is True

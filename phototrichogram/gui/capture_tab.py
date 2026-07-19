"""
Вкладка сбора данных по зонам головы.

Содержит CaptureTab — управление камерой, загрузка/захват снимков
для 4 зон скальпа (F/P/T/O), запуск мультизонального анализа.

Layout:
- Сверху: полоска управления камерой (combobox, подключить/отключить)
- Слева: большое превью (камера или выбранный снимок)
- Справа: 4 карточки зон (2x2 grid) с миниатюрами
- Снизу: статус зон + кнопка «Анализировать загруженные»

Обновление превью через after() (~15 fps).
НЕ импортирует cv2 — использует core.camera и utils.image_utils.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

import numpy as np

from phototrichogram.gui.widgets import ImageCanvas, ImageFileDialog, ToolTip

if TYPE_CHECKING:
    from phototrichogram.core.analyzer import AnalysisResult
    from phototrichogram.core.camera import Camera, DeviceInfo
    from phototrichogram.gui.app import App
    from phototrichogram.gui.series import ZoneSeriesResult

logger = logging.getLogger(__name__)

# Интервал обновления превью (мс) — ~15 fps
_PREVIEW_INTERVAL_MS = 66

# Описания зон на русском
_ZONE_INFO: dict[str, dict[str, str]] = {
    "F": {"name": "Лобная зона", "short": "F — Лобная"},
    "P": {"name": "Теменная зона", "short": "P — Теменная"},
    "T": {"name": "Височная зона", "short": "T — Височная"},
    "O": {"name": "Затылочная зона (контроль)", "short": "O — Затылочная"},
}

# Порядок зон в grid (row, col)
_ZONE_GRID: list[tuple[str, int, int]] = [
    ("F", 0, 0),
    ("P", 0, 1),
    ("T", 1, 0),
    ("O", 1, 1),
]

# Цвета статуса
_COLOR_LOADED = "#4CAF50"    # зелёный — загружено
_COLOR_EMPTY = "#9E9E9E"     # серый — пусто
_COLOR_ANALYZING = "#FF9800"  # оранжевый — анализ


class _ZoneCard(ttk.LabelFrame):
    """Карточка одной зоны скальпа.

    Хранит серию до :data:`_MAX_SERIES` снимков зоны. «Снимок»/«Загрузить»
    ДОБАВЛЯЮТ кадр в серию (пока не заполнена), «Очистить» чистит всю серию.
    Итоговые метрики зоны считаются как среднее по серии (см. gui.series).

    Содержит:
    - Миниатюру последнего снимка серии (или placeholder)
    - Счётчик «N/5» в индикаторе статуса
    - Кнопки: Загрузить, Снимок (с камеры), Очистить
    - Цветной индикатор статуса
    """

    # Размер миниатюры
    _THUMB_SIZE = 120

    # Максимум снимков в серии на зону (план v1.4: до 5 контрольных кадров)
    _MAX_SERIES = 5

    def __init__(
        self,
        parent: tk.Widget,
        zone_code: str,
        on_preview_click: Callable[[str, np.ndarray[Any, Any]], None],
        **kwargs: Any,
    ) -> None:
        """Инициализация карточки зоны.

        Args:
            parent: Родительский виджет.
            zone_code: Код зоны (F/P/T/O).
            on_preview_click: Callback при клике — показать снимок в большом превью.
            **kwargs: Аргументы ttk.LabelFrame.
        """
        zone_info = _ZONE_INFO[zone_code]
        super().__init__(parent, text=zone_info["short"], padding=5, **kwargs)

        self._zone_code = zone_code
        self._on_preview_click = on_preview_click
        # Серия снимков зоны (BGR numpy). Пусто → зона не снята.
        self._images: list[np.ndarray[Any, Any]] = []
        self._photo_thumb: object | None = None  # ImageTk.PhotoImage reference

        self._create_widgets()

    @property
    def zone_code(self) -> str:
        """Код зоны."""
        return self._zone_code

    @property
    def images(self) -> list[np.ndarray[Any, Any]]:
        """Серия снимков зоны (копия списка, BGR numpy array'и)."""
        return list(self._images)

    @property
    def snapshot_count(self) -> int:
        """Число снимков в серии зоны."""
        return len(self._images)

    @property
    def is_full(self) -> bool:
        """Заполнена ли серия (достигнут максимум снимков)."""
        return len(self._images) >= self._MAX_SERIES

    @property
    def has_image(self) -> bool:
        """Есть ли хотя бы один снимок в серии."""
        return len(self._images) > 0

    def _create_widgets(self) -> None:
        """Создание виджетов карточки."""
        self.grid_columnconfigure(0, weight=1)

        # Индикатор статуса
        status_frame = ttk.Frame(self)
        status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 3))

        self._status_canvas = tk.Canvas(
            status_frame, width=10, height=10, highlightthickness=0,
        )
        self._status_canvas.pack(side="left", padx=(0, 4))
        self._status_dot = self._status_canvas.create_oval(
            1, 1, 9, 9, fill=_COLOR_EMPTY, outline="",
        )

        self._status_label = ttk.Label(status_frame, text="--", foreground=_COLOR_EMPTY)
        self._status_label.pack(side="left")

        # Миниатюра (канвас)
        self._thumb_canvas = tk.Canvas(
            self,
            width=self._THUMB_SIZE,
            height=self._THUMB_SIZE,
            bg="#2b2b2b",
            highlightthickness=1,
            highlightbackground="#555555",
            cursor="hand2",
        )
        self._thumb_canvas.grid(row=1, column=0, pady=3)

        # Placeholder текст
        self._placeholder_id = self._thumb_canvas.create_text(
            self._THUMB_SIZE // 2,
            self._THUMB_SIZE // 2,
            text="--",
            fill="#888888",
            font=("", 20),
        )

        # Клик по миниатюре — показать в большом превью
        self._thumb_canvas.bind("<Button-1>", self._on_thumb_click)

        # Кнопки
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self._load_btn = ttk.Button(
            btn_frame, text="Загрузить", command=self._on_load, width=10,
        )
        self._load_btn.grid(row=0, column=0, padx=1, sticky="ew")
        ToolTip(self._load_btn, "Добавить снимок из файла в серию (до 5)")

        self._capture_btn = ttk.Button(
            btn_frame, text="Снимок", command=self._on_capture,
            state="disabled", width=10,
        )
        self._capture_btn.grid(row=0, column=1, padx=1, sticky="ew")
        ToolTip(self._capture_btn, "Добавить снимок с камеры в серию (до 5)")

        self._clear_btn = ttk.Button(
            btn_frame, text="Очистить", command=self._on_clear,
            state="disabled", width=10,
        )
        self._clear_btn.grid(row=1, column=0, columnspan=2, pady=(2, 0), sticky="ew")
        ToolTip(self._clear_btn, "Очистить всю серию снимков зоны")

        # Callback'и, которые установит CaptureTab
        self._load_callback: Callable[[str], None] | None = None
        self._capture_callback: Callable[[str], None] | None = None

    def set_callbacks(
        self,
        load_callback: Callable[[str], None],
        capture_callback: Callable[[str], None],
    ) -> None:
        """Установить callback'и для загрузки и захвата.

        Args:
            load_callback: Вызывается при загрузке из файла.
            capture_callback: Вызывается при захвате с камеры.
        """
        self._load_callback = load_callback
        self._capture_callback = capture_callback

    def set_camera_available(self, available: bool) -> None:
        """Установить доступность кнопки «Снимок».

        Args:
            available: True если камера подключена.
        """
        self._capture_btn.config(state="normal" if available else "disabled")

    def add_image(self, image: np.ndarray[Any, Any]) -> bool:
        """Добавить снимок в серию зоны и обновить миниатюру.

        Снимок добавляется в конец серии, миниатюрой становится последний
        кадр. Если серия уже заполнена (:attr:`is_full`) — снимок НЕ
        добавляется.

        Args:
            image: BGR numpy array.

        Returns:
            True если снимок добавлен, False если серия уже заполнена.
        """
        if self.is_full:
            return False
        self._images.append(image.copy())
        self._update_thumbnail()
        self._update_status()
        self._clear_btn.config(state="normal")
        return True

    def clear_series(self) -> None:
        """Очистить всю серию снимков зоны."""
        self._images.clear()
        self._photo_thumb = None
        self._thumb_canvas.delete("all")
        self._placeholder_id = self._thumb_canvas.create_text(
            self._THUMB_SIZE // 2,
            self._THUMB_SIZE // 2,
            text="--",
            fill="#888888",
            font=("", 20),
        )
        self._update_status()
        self._clear_btn.config(state="disabled")

    def _update_thumbnail(self) -> None:
        """Обновить миниатюру из последнего снимка серии."""
        if not self._images:
            return

        try:
            from PIL import ImageTk

            from phototrichogram.utils.image_utils import numpy_to_pil, resize_to_fit_pil

            pil_image = numpy_to_pil(self._images[-1])
            thumb = resize_to_fit_pil(pil_image, self._THUMB_SIZE, self._THUMB_SIZE)
            self._photo_thumb = ImageTk.PhotoImage(thumb)

            self._thumb_canvas.delete("all")
            self._thumb_canvas.create_image(
                self._THUMB_SIZE // 2,
                self._THUMB_SIZE // 2,
                anchor="center",
                image=self._photo_thumb,
            )
        except Exception as e:
            logger.error("Ошибка создания миниатюры для зоны %s: %s", self._zone_code, e)

    def _update_status(self) -> None:
        """Обновить индикатор статуса — счётчик «N/max» снимков серии."""
        count = len(self._images)
        if count > 0:
            self._status_canvas.itemconfig(self._status_dot, fill=_COLOR_LOADED)
            self._status_label.config(
                text=f"{count}/{self._MAX_SERIES}", foreground=_COLOR_LOADED,
            )
        else:
            self._status_canvas.itemconfig(self._status_dot, fill=_COLOR_EMPTY)
            self._status_label.config(text="--", foreground=_COLOR_EMPTY)

    def _on_load(self) -> None:
        """Загрузить снимок из файла."""
        if self._load_callback:
            self._load_callback(self._zone_code)

    def _on_capture(self) -> None:
        """Сделать снимок с камеры."""
        if self._capture_callback:
            self._capture_callback(self._zone_code)

    def _on_clear(self) -> None:
        """Очистить всю серию снимков зоны."""
        self.clear_series()

    def _on_thumb_click(self, event: tk.Event) -> None:
        """Клик по миниатюре — показать последний снимок серии в большом превью."""
        if self._images:
            self._on_preview_click(self._zone_code, self._images[-1])


class CaptureTab(ttk.Frame):
    """Вкладка сбора данных по зонам головы.

    Содержит:
    - Сверху: полоска управления камерой
    - Слева: большое превью камеры или выбранного снимка
    - Справа: 4 карточки зон (F, P, T, O) — загрузка/захват снимков
    - Снизу: статус зон + кнопка «Анализировать загруженные»
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: Any) -> None:
        """Инициализация вкладки сбора данных.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно приложения.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app
        self._camera: Camera | None = None  # Camera из core
        self._is_previewing = False
        self._current_frame: np.ndarray[Any, Any] | None = None
        self._zone_cards: dict[str, _ZoneCard] = {}
        self._devices: list[DeviceInfo] = []

        self._create_widgets()
        self._update_device_list()

        logger.info("Вкладка захвата (мультизональная) инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        self._create_camera_bar()
        self._create_preview_panel()
        self._create_zones_panel()
        self._create_status_bar()

    def _create_camera_bar(self) -> None:
        """Создание полоски управления камерой (сверху)."""
        cam_frame = ttk.Frame(self, padding=(5, 5, 5, 2))
        cam_frame.grid(row=0, column=0, columnspan=2, sticky="ew")

        ttk.Label(cam_frame, text="Камера:").pack(side="left", padx=(0, 5))

        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(
            cam_frame,
            textvariable=self._device_var,
            state="readonly",
            width=30,
        )
        self._device_combo.pack(side="left", padx=(0, 5))
        ToolTip(self._device_combo, "Выберите видеоустройство микроскопа")

        self._refresh_btn = ttk.Button(
            cam_frame, text="Обновить", command=self._update_device_list, width=10,
        )
        self._refresh_btn.pack(side="left", padx=(0, 5))
        ToolTip(self._refresh_btn, "Повторный поиск подключённых камер")

        self._connect_btn = ttk.Button(
            cam_frame, text="Подключить", command=self._on_connect, width=12,
        )
        self._connect_btn.pack(side="left", padx=(0, 2))

        self._disconnect_btn = ttk.Button(
            cam_frame, text="Отключить", command=self._on_disconnect,
            state="disabled", width=12,
        )
        self._disconnect_btn.pack(side="left", padx=(0, 5))

        # Статус камеры
        self._cam_status_var = tk.StringVar(value="Отключена")
        ttk.Label(cam_frame, textvariable=self._cam_status_var).pack(side="right", padx=5)

    def _create_preview_panel(self) -> None:
        """Создание панели превью (слева)."""
        preview_frame = ttk.LabelFrame(
            self, text="Превью", padding=5,
        )
        preview_frame.grid(row=1, column=0, sticky="nsew", padx=(5, 2), pady=(2, 2))
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

        self._image_canvas = ImageCanvas(preview_frame, width=640, height=480)
        self._image_canvas.grid(row=0, column=0, sticky="nsew")

        # Метка когда камера не подключена
        self._no_camera_label = ttk.Label(
            preview_frame,
            text=(
                "Камера не подключена\n\n"
                "Выберите устройство и нажмите «Подключить»\n"
                "или загрузите снимки из файлов"
            ),
            anchor="center",
            justify="center",
            style="Header.TLabel",
        )
        self._no_camera_label.grid(row=0, column=0, sticky="nsew")

    def _create_zones_panel(self) -> None:
        """Создание панели карточек зон (справа, 2x2 grid)."""
        zones_frame = ttk.LabelFrame(
            self, text="Зоны скальпа", padding=5,
        )
        zones_frame.grid(row=1, column=1, sticky="nsew", padx=(2, 5), pady=(2, 2))
        zones_frame.grid_rowconfigure(0, weight=1)
        zones_frame.grid_rowconfigure(1, weight=1)
        zones_frame.grid_columnconfigure(0, weight=1)
        zones_frame.grid_columnconfigure(1, weight=1)

        for zone_code, row, col in _ZONE_GRID:
            card = _ZoneCard(
                zones_frame,
                zone_code=zone_code,
                on_preview_click=self._on_zone_preview,
            )
            card.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
            card.set_callbacks(
                load_callback=self._on_zone_load,
                capture_callback=self._on_zone_capture,
            )
            self._zone_cards[zone_code] = card

    def _create_status_bar(self) -> None:
        """Создание полоски статуса зон (снизу)."""
        bottom_frame = ttk.Frame(self, padding=(5, 2, 5, 5))
        bottom_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom_frame.grid_columnconfigure(0, weight=1)

        # Статус зон
        status_left = ttk.Frame(bottom_frame)
        status_left.pack(side="left", fill="x", expand=True)

        self._zones_status_var = tk.StringVar(value="F: --  P: --  T: --  O: --")
        ttk.Label(
            status_left,
            textvariable=self._zones_status_var,
            font=("", 11),
        ).pack(side="left")

        # Кнопка анализа
        self._analyze_btn = ttk.Button(
            bottom_frame,
            text="Анализировать загруженные",
            command=self._on_analyze_all,
            state="disabled",
        )
        self._analyze_btn.pack(side="right", padx=(10, 0))
        ToolTip(
            self._analyze_btn,
            "Запустить анализ всех загруженных зон и перейти к результатам",
        )

        # Прогрессбар
        self._progress = ttk.Progressbar(bottom_frame, mode="indeterminate", length=150)
        self._progress.pack(side="right", padx=5)
        self._progress.pack_forget()  # Скрыт по умолчанию

        # Привязка Space для снимка
        self.winfo_toplevel().bind("<space>", self._on_space_key)

    # ──────────────────────────── Управление камерой ──────────────────────────

    def _update_device_list(self) -> None:
        """Обновить список доступных видеоустройств."""
        try:
            from phototrichogram.core.camera import list_devices
            devices = list_devices()
            self._devices = devices

            if devices:
                device_names = [
                    f"{d.name} ({d.width}x{d.height})" for d in devices
                ]
                self._device_combo["values"] = device_names
                self._device_combo.current(0)
                self._app.status_bar.set_text(
                    f"Обнаружено устройств: {len(devices)}",
                )
            else:
                self._device_combo["values"] = ["Устройства не найдены"]
                self._device_combo.current(0)
                self._devices = []
                self._app.status_bar.set_text("Видеоустройства не обнаружены")

        except ImportError:
            logger.warning("Модуль core.camera недоступен")
            self._devices = []
            self._device_combo["values"] = ["Модуль камеры недоступен"]
            self._device_combo.current(0)
        except Exception as e:
            logger.error("Ошибка обновления списка устройств: %s", e)
            self._devices = []
            self._app.status_bar.set_error(f"Ошибка: {e}")

    def _on_connect(self) -> None:
        """Подключение к выбранной камере."""
        if not self._devices:
            self._app.status_bar.set_error("Нет доступных устройств")
            return

        idx = self._device_combo.current()
        if idx < 0 or idx >= len(self._devices):
            return

        device = self._devices[idx]

        try:
            from phototrichogram.core.camera import Camera

            self._camera = Camera()
            success = self._camera.open(device.index)

            if success:
                cam_config = self._app.config.camera
                self._camera.set_resolution(cam_config.width, cam_config.height)
                self._camera.set_fps(cam_config.fps)

                resolution = self._camera.get_resolution()
                res_str = f"{resolution[0]}x{resolution[1]}" if resolution else "?"

                self._cam_status_var.set(f"Подключена ({res_str})")
                self._connect_btn.config(state="disabled")
                self._disconnect_btn.config(state="normal")
                self._no_camera_label.grid_remove()

                # Включить кнопки «Снимок» на карточках
                for card in self._zone_cards.values():
                    card.set_camera_available(True)

                # Запуск превью
                self._is_previewing = True
                self._update_preview()

                self._app.status_bar.set_ready(f"Камера подключена: {device.name}")
                logger.info("Камера подключена: %s", device.name)
            else:
                self._camera = None
                self._app.status_bar.set_error(
                    f"Не удалось открыть камеру: {device.name}",
                )
        except Exception as e:
            logger.error("Ошибка подключения камеры: %s", e)
            self._camera = None
            self._app.status_bar.set_error(f"Ошибка: {e}")

    def _on_disconnect(self) -> None:
        """Отключение камеры."""
        self._is_previewing = False
        self.release_camera()

        self._cam_status_var.set("Отключена")
        self._connect_btn.config(state="normal")
        self._disconnect_btn.config(state="disabled")
        self._no_camera_label.grid()

        # Отключить кнопки «Снимок» на карточках
        for card in self._zone_cards.values():
            card.set_camera_available(False)

        self._image_canvas.clear()
        self._app.status_bar.set_text("Камера отключена")

    def release_camera(self) -> None:
        """Освобождение ресурсов камеры (вызывается при закрытии приложения)."""
        self._is_previewing = False
        if self._camera is not None and hasattr(self._camera, "release"):
            try:
                self._camera.release()
            except Exception as e:
                logger.error("Ошибка освобождения камеры: %s", e)
            self._camera = None

    # ──────────────────────────── Превью ──────────────────────────────────────

    def _update_preview(self) -> None:
        """Обновление живого превью с камеры (вызывается через after)."""
        if not self._is_previewing or self._camera is None:
            return

        try:
            frame = self._camera.read_frame()
            if frame is not None:
                self._current_frame = frame
                self._image_canvas.show_numpy(frame)
        except Exception as e:
            logger.warning("Ошибка захвата кадра: %s", e)

        if self._is_previewing:
            self.after(_PREVIEW_INTERVAL_MS, self._update_preview)

    def get_camera_frame(self) -> np.ndarray[Any, Any] | None:
        """Вернуть свежий кадр с подключённой камеры.

        Публичный метод для других вкладок (например, «Калибровка»): отдаёт
        копию последнего кадра живого превью — тот же источник, что использует
        «Снимок» в карточке зоны (``self._current_frame``).

        Returns:
            Копия последнего кадра (BGR numpy array) или None, если камера
            не подключена либо кадр ещё не получен.
        """
        if self._camera is None or self._current_frame is None:
            return None
        return np.copy(self._current_frame)

    # ──────────────────────────── Операции с зонами ───────────────────────────

    def _on_zone_load(self, zone_code: str) -> None:
        """Загрузить снимок из файла для зоны.

        Args:
            zone_code: Код зоны (F/P/T/O).
        """
        card = self._zone_cards[zone_code]
        if card.is_full:
            self._app.status_bar.set_text(
                f"Зона {zone_code}: серия заполнена ({card.snapshot_count}/5)",
            )
            return

        dialog = ImageFileDialog(self, title=f"Загрузить снимок — {_ZONE_INFO[zone_code]['name']}")
        path = dialog.show()
        if path is not None:
            from phototrichogram.utils.image_utils import load_image
            image = load_image(path)
            if image is not None:
                card.add_image(image)
                self._image_canvas.show_numpy(image)
                self._update_zones_status()
                self._app.status_bar.set_ready(
                    f"Зона {zone_code}: добавлен {Path(path).name} "
                    f"(снимков: {card.snapshot_count}/5)",
                )
            else:
                self._app.status_bar.set_error(f"Не удалось загрузить: {path}")

    def _on_zone_capture(self, zone_code: str) -> None:
        """Сделать снимок с камеры для зоны.

        Args:
            zone_code: Код зоны (F/P/T/O).
        """
        card = self._zone_cards[zone_code]
        if card.is_full:
            self._app.status_bar.set_text(
                f"Зона {zone_code}: серия заполнена ({card.snapshot_count}/5)",
            )
            return

        if self._current_frame is None:
            self._app.status_bar.set_text("Нет кадра для снимка")
            return

        frame_copy = np.copy(self._current_frame)
        card.add_image(frame_copy)
        self._update_zones_status()

        # Автосохранение если включено
        if self._app.config.app.autosave:
            self._autosave_snapshot(frame_copy, zone_code)

        self._app.status_bar.set_ready(
            f"Зона {zone_code}: снимок добавлен (снимков: {card.snapshot_count}/5)",
        )
        logger.info(
            "Снимок для зоны %s добавлен (серия: %d/5)",
            zone_code, card.snapshot_count,
        )

    def _on_zone_preview(self, zone_code: str, image: np.ndarray[Any, Any]) -> None:
        """Показать снимок зоны в большом превью.

        Args:
            zone_code: Код зоны.
            image: BGR numpy array.
        """
        self._image_canvas.show_numpy(image)
        self._app.status_bar.set_text(
            f"Превью: зона {zone_code} — {_ZONE_INFO[zone_code]['name']}",
        )

    def _on_space_key(self, event: tk.Event) -> None:
        """Обработка нажатия Space — снимок если превью активно.

        Не делает снимок, если фокус в текстовом поле.

        Args:
            event: Событие клавиши.
        """
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        # Space без активной зоны — не делаем ничего
        # (пользователь должен явно нажать кнопку «Снимок» на карточке)

    def _update_zones_status(self) -> None:
        """Обновить строку статуса зон и доступность кнопки анализа.

        Показывает число снимков в серии каждой зоны («F: 3/5»), либо «--»
        для пустой зоны.
        """
        parts: list[str] = []
        loaded_count = 0

        for zone_code in ["F", "P", "T", "O"]:
            card = self._zone_cards[zone_code]
            if card.has_image:
                parts.append(f"{zone_code}: {card.snapshot_count}/5")
                loaded_count += 1
            else:
                parts.append(f"{zone_code}: --")

        self._zones_status_var.set("  ".join(parts))

        # Кнопка анализа доступна если хотя бы 1 зона имеет снимки
        if loaded_count > 0:
            self._analyze_btn.config(state="normal")
        else:
            self._analyze_btn.config(state="disabled")

    def _autosave_snapshot(self, frame: np.ndarray[Any, Any], zone_code: str) -> None:
        """Автоматическое сохранение снимка в директорию из конфига.

        Args:
            frame: BGR numpy array.
            zone_code: Код зоны для имени файла.
        """
        try:
            images_dir = Path(self._app.config.app.images_dir)
            images_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"zone_{zone_code}_{timestamp}.png"
            path = images_dir / filename

            from phototrichogram.utils.image_utils import save_image
            save_image(frame, path)
            logger.debug("Автосохранение зоны %s: %s", zone_code, path)
        except Exception as e:
            logger.error("Ошибка автосохранения: %s", e)

    # ──────────────────────────── Мультизональный анализ ──────────────────────

    def _on_analyze_all(self) -> None:
        """Запуск анализа всех зон с непустой серией в отдельном потоке."""
        # Собрать серии снимков по зонам (копии — анализ идёт в фоне, пока
        # пользователь может очистить/дополнить карточку).
        zone_series: dict[str, list[np.ndarray[Any, Any]]] = {}
        for zone_code, card in self._zone_cards.items():
            images = card.images
            if images:
                zone_series[zone_code] = [np.copy(img) for img in images]

        if not zone_series:
            self._app.status_bar.set_text("Нет загруженных снимков для анализа")
            return

        # Предупредить о потере результатов, если пациент не выбран:
        # без активного пациента анализ не сохранится в историю (см.
        # _save_results_to_db). Даём пользователю осознанный выбор.
        if self._app.active_patient is None:
            proceed = messagebox.askyesno(
                "Пациент не выбран",
                "Результаты анализа НЕ будут сохранены в историю.\n\n"
                "Выберите пациента двойным кликом на вкладке «Пациенты»,\n"
                "либо продолжите без сохранения.\n\n"
                "Продолжить без сохранения?",
            )
            if not proceed:
                return

        # Блокировка UI
        self._analyze_btn.config(state="disabled")
        self._progress.pack(side="right", padx=5)
        self._progress.start(10)
        self._app.status_bar.set_busy(
            f"Анализ {len(zone_series)} зон...",
        )

        def do_analysis() -> None:
            """Анализ всех зон в отдельном потоке.

            Для каждой зоны прогоняется анализ КАЖДОГО снимка серии, затем
            метрики усредняются (gui.series.aggregate_series). Снимки, которые
            анализатор отверг (вернул None), пропускаются; зона без единого
            валидного результата в итог не попадает.
            """
            try:
                from phototrichogram.core.analyzer import Analyzer
                from phototrichogram.gui.series import aggregate_series

                analyzer = Analyzer(self._app.config.analysis)
                results: dict[str, ZoneSeriesResult] = {}

                for zone_code, images in zone_series.items():
                    logger.info(
                        "Анализ зоны %s (снимков: %d)...", zone_code, len(images),
                    )
                    per_snapshot: list[AnalysisResult] = []
                    for img in images:
                        result = analyzer.analyze_with_annotation(img)
                        if result is not None:
                            per_snapshot.append(result)

                    if per_snapshot:
                        results[zone_code] = aggregate_series(per_snapshot)

                self.after(0, lambda: self._on_analysis_complete(results))
            except Exception as e:
                logger.error("Ошибка мультизонального анализа: %s", e)
                error_msg = str(e)
                self.after(0, lambda: self._on_analysis_error(error_msg))

        threading.Thread(target=do_analysis, daemon=True).start()

    def _on_analysis_complete(self, results: dict[str, ZoneSeriesResult]) -> None:
        """Обработка завершения мультизонального анализа.

        Args:
            results: Словарь {код_зоны: ZoneSeriesResult} — усреднённые по
                серии метрики зоны + число снимков и разброс плотности.
        """
        self._progress.stop()
        self._progress.pack_forget()
        self._analyze_btn.config(state="normal")

        if not results:
            self._app.status_bar.set_error("Анализ не дал результатов")
            return

        # Сохранить результаты в БД если есть активный пациент
        self._save_results_to_db(results)

        # Передать результаты во вкладку «Результаты»
        self._app.navigate_to_results(results)

        self._app.status_bar.set_ready(
            f"Анализ завершён: {len(results)} зон обработано",
        )

    def _save_results_to_db(self, results: dict[str, ZoneSeriesResult]) -> None:
        """Сохранить результаты анализа в базу данных.

        Создаёт калибровку, сессию и измерения для активного пациента.
        Если пациент не выбран — показывает предупреждение в статусбаре.
        В каждое измерение пишутся усреднённые по серии метрики плюс
        n_snapshots (число снимков) и density_std (разброс плотности).

        Args:
            results: Словарь {код_зоны: ZoneSeriesResult}.
        """
        patient = self._app.active_patient
        if patient is None:
            self._app.status_bar.set_text(
                "Выберите пациента для сохранения результатов",
            )
            return

        try:
            db = self._app.db
            config = self._app.config.analysis

            # Создать калибровку из текущего конфига
            calibration = db.add_calibration(
                um_per_px=config.um_per_px,
                fov_width_px=config.fov_width_px,
                fov_height_px=config.fov_height_px,
                description="Автоматически из анализа",
            )

            # Подготовить данные измерений
            today = datetime.now().strftime("%Y-%m-%d")
            measurements_data: list[dict[str, Any]] = []

            for zone_code, zone_result in results.items():
                result = zone_result.result
                meas_dict = {
                    "zone": zone_code,
                    "hair_count": result.hair_count,
                    "density_per_cm2": result.density_per_cm2,
                    "mean_thickness_um": result.thickness.mean,
                    "median_thickness_um": result.thickness.median,
                    "std_thickness_um": result.thickness.std,
                    "min_thickness_um": result.thickness.min,
                    "max_thickness_um": result.thickness.max,
                    "terminal_count": result.ratios.terminal_count,
                    "vellus_count": result.ratios.vellus_count,
                    "intermediate_count": result.ratios.intermediate_count,
                    "terminal_pct": result.ratios.terminal_pct,
                    "vellus_pct": result.ratios.vellus_pct,
                    "intermediate_pct": result.ratios.intermediate_pct,
                    "tv_ratio": result.ratios.tv_ratio,
                    "fov_area_mm2": result.fov_area_mm2,
                    # Серия снимков (план v1.4): число усреднённых кадров и
                    # разброс плотности (None для одиночного снимка).
                    "n_snapshots": zone_result.n_snapshots,
                    "density_std": zone_result.density_std,
                }
                measurements_data.append(meas_dict)

            # id гарантированы: пациент активен (из БД), калибровка только что создана
            patient_id = patient.id
            calibration_id = calibration.id
            assert patient_id is not None
            assert calibration_id is not None

            # Атомарное создание сессии со всеми измерениями
            db.create_session_with_measurements(
                patient_id=patient_id,
                calibration_id=calibration_id,
                date=today,
                measurements_data=measurements_data,
            )

            self._app.status_bar.set_ready(
                f"Результаты сохранены для {patient.name} ({len(results)} зон)",
            )
            logger.info(
                "Результаты сохранены: пациент id=%d, %d зон",
                patient.id, len(results),
            )
        except Exception as e:
            logger.error("Ошибка сохранения результатов в БД: %s", e)
            self._app.status_bar.set_error(f"Ошибка сохранения: {e}")

    def _on_analysis_error(self, error_msg: str) -> None:
        """Обработка ошибки анализа.

        Args:
            error_msg: Текст ошибки.
        """
        self._progress.stop()
        self._progress.pack_forget()
        self._analyze_btn.config(state="normal")
        self._app.status_bar.set_error(f"Ошибка анализа: {error_msg}")

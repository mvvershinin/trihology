"""
Вкладка калибровки микроскопа.

Содержит CalibrationTab — загрузка снимка шкалы, автокалибровка,
ручной ввод масштаба, сохранение в конфиг.

НЕ импортирует cv2. Использует core.calibration и utils.image_utils.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any

import numpy as np

from phototrichogram.gui.widgets import ImageCanvas, ImageFileDialog, ToolTip

if TYPE_CHECKING:
    from phototrichogram.core.calibration import CalibrationResult
    from phototrichogram.gui.app import App

logger = logging.getLogger(__name__)


class CalibrationTab(ttk.Frame):
    """Вкладка калибровки микроскопа по микрометрической шкале.

    Содержит:
    - Слева: изображение шкалы (ImageCanvas)
    - Справа: параметры калибровки
        - Цена деления (мкм) — по умолчанию 100
        - Кнопка автокалибровки
        - Ручной ввод um_per_px
        - Результат: um_per_px, FOV размеры, надёжность
        - Кнопка сохранения в конфиг
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: Any) -> None:
        """Инициализация вкладки калибровки.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app
        self._ruler_image: np.ndarray[Any, Any] | None = None
        self._calibration_result: CalibrationResult | None = None

        self._create_widgets()
        self._load_current_config()

        logger.info("Вкладка калибровки инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self._create_image_panel()
        self._create_settings_panel()

    def _create_image_panel(self) -> None:
        """Создание панели изображения шкалы (слева)."""
        left_frame = ttk.LabelFrame(self, text="Снимок калибровочной шкалы", padding=5)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(5, 2), pady=5)
        left_frame.grid_rowconfigure(1, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        # Кнопка загрузки
        btn_frame = ttk.Frame(left_frame)
        btn_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self._load_btn = ttk.Button(
            btn_frame, text="Загрузить снимок шкалы...", command=self._on_load_ruler,
        )
        self._load_btn.pack(side="left")
        ToolTip(self._load_btn, "Загрузить фотографию калибровочной шкалы (микрометр)")

        # Канвас
        self._image_canvas = ImageCanvas(left_frame, width=640, height=480)
        self._image_canvas.grid(row=1, column=0, sticky="nsew")

        # Прогрессбар
        self._progress = ttk.Progressbar(left_frame, mode="indeterminate")
        self._progress.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        self._progress.grid_remove()

    def _create_settings_panel(self) -> None:
        """Создание панели настроек калибровки (справа)."""
        right_frame = ttk.Frame(self)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 5), pady=5)
        right_frame.grid_columnconfigure(0, weight=1)

        # Параметры автокалибровки
        auto_frame = ttk.LabelFrame(right_frame, text="Автокалибровка", padding=10)
        auto_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        auto_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(auto_frame, text="Цена деления (мкм):").grid(
            row=0, column=0, sticky="w", pady=3,
        )
        self._division_um_var = tk.DoubleVar(value=100.0)
        division_entry = ttk.Entry(
            auto_frame, textvariable=self._division_um_var, width=10,
        )
        division_entry.grid(row=0, column=1, sticky="e", pady=3)
        ToolTip(division_entry, "Цена одного деления шкалы в микронах (обычно 100)")

        ttk.Label(auto_frame, text="Порог бинаризации:").grid(
            row=1, column=0, sticky="w", pady=3,
        )
        self._threshold_var = tk.IntVar(value=128)
        threshold_entry = ttk.Entry(
            auto_frame, textvariable=self._threshold_var, width=10,
        )
        threshold_entry.grid(row=1, column=1, sticky="e", pady=3)
        ToolTip(threshold_entry, "Порог бинаризации 0-255 (128 по умолчанию)")

        self._auto_btn = ttk.Button(
            auto_frame, text="Автокалибровка", command=self._on_auto_calibrate,
            state="disabled",
        )
        self._auto_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ToolTip(
            self._auto_btn,
            "Автоматическое определение масштаба по снимку шкалы",
        )

        # Ручной ввод
        manual_frame = ttk.LabelFrame(right_frame, text="Ручная калибровка", padding=10)
        manual_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        manual_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(manual_frame, text="Масштаб (мкм/px):").grid(
            row=0, column=0, sticky="w", pady=3,
        )
        self._manual_um_px_var = tk.DoubleVar(value=5.9)
        manual_entry = ttk.Entry(
            manual_frame, textvariable=self._manual_um_px_var, width=10,
        )
        manual_entry.grid(row=0, column=1, sticky="e", pady=3)
        ToolTip(manual_entry, "Ручной ввод масштаба в мкм/пиксель")

        self._apply_manual_btn = ttk.Button(
            manual_frame, text="Применить", command=self._on_apply_manual,
        )
        self._apply_manual_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        # Результат калибровки
        result_frame = ttk.LabelFrame(right_frame, text="Результат", padding=10)
        result_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        result_frame.grid_columnconfigure(1, weight=1)

        self._result_vars: dict[str, tk.StringVar] = {}
        result_fields = [
            ("um_per_px", "Масштаб (мкм/px):"),
            ("fov_width", "Ширина поля (мм):"),
            ("fov_height", "Высота поля (мм):"),
            ("fov_area", "Площадь поля (мм^2):"),
            ("divisions", "Обнаружено делений:"),
            ("confidence", "Надёжность:"),
        ]

        for row, (key, label) in enumerate(result_fields):
            ttk.Label(result_frame, text=label).grid(
                row=row, column=0, sticky="w", pady=2,
            )
            var = tk.StringVar(value="--")
            self._result_vars[key] = var
            ttk.Label(result_frame, textvariable=var, style="Value.TLabel").grid(
                row=row, column=1, sticky="e", pady=2,
            )

        # Кнопка сохранения
        self._save_btn = ttk.Button(
            right_frame, text="Сохранить калибровку", command=self._on_save,
            state="disabled",
        )
        self._save_btn.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        ToolTip(self._save_btn, "Сохранить калибровку в конфигурацию приложения")

        # Текущие значения из конфига
        current_frame = ttk.LabelFrame(
            right_frame, text="Текущая конфигурация", padding=10,
        )
        current_frame.grid(row=4, column=0, sticky="ew")
        current_frame.grid_columnconfigure(1, weight=1)

        self._current_vars: dict[str, tk.StringVar] = {}
        current_fields = [
            ("cur_um_px", "Масштаб (мкм/px):"),
            ("cur_fov_w", "Ширина поля (мм):"),
            ("cur_fov_h", "Высота поля (мм):"),
            ("cur_fov_a", "Площадь поля (мм^2):"),
        ]
        for row, (key, label) in enumerate(current_fields):
            ttk.Label(current_frame, text=label).grid(
                row=row, column=0, sticky="w", pady=2,
            )
            var = tk.StringVar(value="--")
            self._current_vars[key] = var
            ttk.Label(current_frame, textvariable=var).grid(
                row=row, column=1, sticky="e", pady=2,
            )

    # ──────────────────────────── Загрузка конфига ────────────────────────────

    def _load_current_config(self) -> None:
        """Загрузить текущие значения калибровки из конфига."""
        analysis = self._app.config.analysis
        self._current_vars["cur_um_px"].set(f"{analysis.um_per_px:.2f}")
        self._current_vars["cur_fov_w"].set(f"{analysis.fov_width_mm:.2f}")
        self._current_vars["cur_fov_h"].set(f"{analysis.fov_height_mm:.2f}")
        self._current_vars["cur_fov_a"].set(f"{analysis.fov_area_mm2:.2f}")
        self._manual_um_px_var.set(analysis.um_per_px)

    # ──────────────────────────── Загрузка изображения ────────────────────────

    def _on_load_ruler(self) -> None:
        """Загрузить снимок калибровочной шкалы через диалог с превью."""
        dialog = ImageFileDialog(self, title="Открыть снимок шкалы")
        path = dialog.show()
        if path is None:
            return

        from phototrichogram.utils.image_utils import load_image

        image = load_image(path)
        if image is not None:
            self._ruler_image = image
            self._image_canvas.show_numpy(image)
            self._auto_btn.config(state="normal")
            self._app.status_bar.set_ready(f"Шкала загружена: {path.name}")
        else:
            self._app.status_bar.set_error(f"Не удалось загрузить: {path}")

    # ──────────────────────────── Автокалибровка ──────────────────────────────

    def _on_auto_calibrate(self) -> None:
        """Запуск автокалибровки в отдельном потоке."""
        if self._ruler_image is None:
            return

        self._auto_btn.config(state="disabled")
        self._progress.grid()
        self._progress.start(10)
        self._app.status_bar.set_busy("Автокалибровка...")

        division_um = self._division_um_var.get()
        threshold = self._threshold_var.get()
        # Копия изображения для потокобезопасности
        ruler_copy = self._ruler_image.copy()

        def do_calibration() -> None:
            """Калибровка в отдельном потоке."""
            try:
                from phototrichogram.core.calibration import calibrate_from_ruler

                result = calibrate_from_ruler(
                    ruler_copy,
                    division_um=division_um,
                    threshold=threshold,
                )
                self.after(0, lambda: self._on_calibration_complete(result))
            except Exception as e:
                logger.error("Ошибка калибровки: %s", e)
                error_msg = str(e)
                self.after(0, lambda: self._on_calibration_error(error_msg))

        threading.Thread(target=do_calibration, daemon=True).start()

    def _on_calibration_complete(self, result: CalibrationResult) -> None:
        """Обработка завершения калибровки (в GUI-потоке).

        Args:
            result: Результат калибровки.
        """
        self._progress.stop()
        self._progress.grid_remove()
        self._auto_btn.config(state="normal")

        self._calibration_result = result

        # Отобразить результаты
        self._result_vars["um_per_px"].set(f"{result.um_per_px:.2f}")
        self._result_vars["fov_width"].set(f"{result.fov_width_mm:.2f}")
        self._result_vars["fov_height"].set(f"{result.fov_height_mm:.2f}")
        self._result_vars["fov_area"].set(f"{result.fov_area_mm2:.2f}")
        self._result_vars["divisions"].set(str(result.divisions_detected))

        # Цветовая оценка надёжности
        confidence = result.confidence
        if confidence >= 0.7:
            conf_text = f"{confidence:.2f} (отлично)"
        elif confidence >= 0.4:
            conf_text = f"{confidence:.2f} (приемлемо)"
        else:
            conf_text = f"{confidence:.2f} (низкая)"
        self._result_vars["confidence"].set(conf_text)

        self._save_btn.config(state="normal")
        self._manual_um_px_var.set(result.um_per_px)

        self._app.status_bar.set_ready(
            f"Калибровка: {result.um_per_px:.2f} мкм/px, "
            f"надёжность {confidence:.2f}",
        )
        logger.info(
            "Автокалибровка завершена: %.2f мкм/px, %d делений, надёжность %.2f",
            result.um_per_px,
            result.divisions_detected,
            result.confidence,
        )

    def _on_calibration_error(self, error_msg: str) -> None:
        """Обработка ошибки калибровки (в GUI-потоке).

        Args:
            error_msg: Текст ошибки.
        """
        self._progress.stop()
        self._progress.grid_remove()
        self._auto_btn.config(state="normal")
        self._app.status_bar.set_error(f"Ошибка калибровки: {error_msg}")

    # ──────────────────────────── Ручная калибровка ───────────────────────────

    def _on_apply_manual(self) -> None:
        """Применить ручной ввод масштаба."""
        try:
            um_per_px = self._manual_um_px_var.get()
            if um_per_px <= 0:
                self._app.status_bar.set_error("Масштаб должен быть > 0")
                return

            analysis = self._app.config.analysis
            fov_w_mm = analysis.fov_width_px * um_per_px / 1000.0
            fov_h_mm = analysis.fov_height_px * um_per_px / 1000.0
            fov_area = fov_w_mm * fov_h_mm

            self._result_vars["um_per_px"].set(f"{um_per_px:.2f}")
            self._result_vars["fov_width"].set(f"{fov_w_mm:.2f}")
            self._result_vars["fov_height"].set(f"{fov_h_mm:.2f}")
            self._result_vars["fov_area"].set(f"{fov_area:.2f}")
            self._result_vars["divisions"].set("(ручной)")
            self._result_vars["confidence"].set("(ручной)")

            self._save_btn.config(state="normal")
            self._calibration_result = None  # Ручная — без CalibrationResult

            self._app.status_bar.set_ready(
                f"Ручная калибровка: {um_per_px:.2f} мкм/px",
            )
        except tk.TclError:
            self._app.status_bar.set_error("Введите корректное число")

    # ──────────────────────────── Сохранение ──────────────────────────────────

    def _on_save(self) -> None:
        """Сохранить калибровку в конфиг."""
        try:
            um_per_px = self._manual_um_px_var.get()
            if um_per_px <= 0:
                self._app.status_bar.set_error("Масштаб должен быть > 0")
                return

            # Обновить конфиг
            self._app.config.analysis.um_per_px = um_per_px

            # Сохранить в файл
            from phototrichogram.utils.config import save_config
            success = save_config(self._app.config)

            if success:
                self._load_current_config()
                self._app.status_bar.set_ready(
                    f"Калибровка сохранена: {um_per_px:.2f} мкм/px",
                )
                logger.info("Калибровка сохранена: %.2f мкм/px", um_per_px)
            else:
                self._app.status_bar.set_error("Не удалось сохранить калибровку")

        except (tk.TclError, ValueError, OSError) as e:
            logger.error("Ошибка сохранения калибровки: %s", e)
            self._app.status_bar.set_error(f"Ошибка: {e}")

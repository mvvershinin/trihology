"""
Вкладка анализа трихоскопического снимка.

Содержит AnalysisTab — загрузка/отображение изображения, запуск анализа,
отображение результатов (таблица + гистограмма толщин).

Тяжёлые операции (анализ) выполняются в отдельном потоке
с возвратом в GUI через after().

НЕ импортирует cv2. Использует core.analyzer и utils.image_utils.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont
from typing import TYPE_CHECKING, Any

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from phototrichogram.gui.widgets import ImageCanvas, ImageFileDialog, ToolTip, format_tv_ratio

if TYPE_CHECKING:
    from phototrichogram.core.analyzer import AnalysisResult
    from phototrichogram.gui.app import App

logger = logging.getLogger(__name__)

# ── Референсные нормы (справочно) ─ источник: docs/METHODOLOGY.md §2.2 ──────
# METHODOLOGY §2.2: плотность 150–300 волос/см²; Vellus < 20 %; T/V > 4:1;
# толщина Terminal > 40 мкм. Верхняя граница средней толщины (80 мкм) в §2.2
# явно не задана — это ориентировочный верх нормы диаметра стержня.
# Значения справочные, диагностическими порогами НЕ являются.
_NORM_DENSITY_MIN = 150    # волос/см², нижняя граница нормы плотности
_NORM_DENSITY_MAX = 300    # волос/см², верхняя граница нормы плотности
_NORM_VELLUS_MAX_PCT = 20  # %, верхняя граница нормы доли Vellus
_NORM_TV_RATIO_MIN = 4     # T/V, нижняя граница нормы соотношения
_NORM_THICKNESS_MIN = 40   # мкм, нижняя граница нормы средней толщины
_NORM_THICKNESS_MAX = 80   # мкм, ориентировочный верх нормы средней толщины


class AnalysisTab(ttk.Frame):
    """Вкладка анализа трихоскопического снимка.

    Содержит:
    - Слева: исходное/аннотированное изображение (переключение)
    - Справа: результаты анализа
        - Плотность (волос/см^2)
        - Terminal / Vellus / Intermediate количество и %
        - T/V ratio
        - Статистика толщины (mean, median, std, min, max)
        - Гистограмма толщины (matplotlib)
    - Кнопки: Загрузить, Анализировать, Экспорт
    - Прогрессбар при анализе
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: Any) -> None:
        """Инициализация вкладки анализа.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app
        self._current_image: np.ndarray[Any, Any] | None = None
        self._analysis_result: AnalysisResult | None = None
        self._showing_annotated = False

        self._create_widgets()
        logger.info("Вкладка анализа инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self._small_font = self._make_small_font()

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self._create_image_panel()
        self._create_results_panel()

    def _make_small_font(self) -> tkfont.Font:
        """Создать уменьшенную копию системного шрифта для дисклеймера.

        Размер — на 2 единицы меньше TkDefaultFont (по модулю, минимум 8),
        что корректно и для положительного (пункты), и для отрицательного
        (пиксели) размера Tk. Ссылку на шрифт держим в ``self._small_font``,
        иначе Tk удалит именованный шрифт при сборке мусора Python-объекта.

        Returns:
            Именованный шрифт уменьшенного размера.
        """
        small_font = tkfont.nametofont("TkDefaultFont", root=self).copy()
        base_size = int(small_font.cget("size"))
        sign = -1 if base_size < 0 else 1
        small_font.configure(size=sign * max(abs(base_size) - 2, 8))
        return small_font

    def _create_image_panel(self) -> None:
        """Создание панели изображения (слева)."""
        left_frame = ttk.Frame(self)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(5, 2), pady=5)
        left_frame.grid_rowconfigure(1, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        # Панель кнопок сверху
        btn_frame = ttk.Frame(left_frame)
        btn_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self._load_btn = ttk.Button(
            btn_frame, text="Загрузить изображение...", command=self._on_load_image,
        )
        self._load_btn.pack(side="left", padx=(0, 5))
        ToolTip(self._load_btn, "Загрузить снимок из файла")

        self._analyze_btn = ttk.Button(
            btn_frame, text="Анализировать", command=self._on_analyze, state="disabled",
        )
        self._analyze_btn.pack(side="left", padx=(0, 5))
        ToolTip(self._analyze_btn, "Запустить анализ загруженного изображения")

        self._toggle_btn = ttk.Button(
            btn_frame,
            text="Показать разметку",
            command=self._on_toggle_view,
            state="disabled",
        )
        self._toggle_btn.pack(side="left", padx=(0, 5))
        ToolTip(self._toggle_btn, "Переключение: исходное / с аннотацией")

        self._export_btn = ttk.Button(
            btn_frame, text="Экспорт...", command=self._on_export, state="disabled",
        )
        self._export_btn.pack(side="right")
        ToolTip(self._export_btn, "Сохранить аннотированное изображение")

        # Канвас изображения
        self._image_canvas = ImageCanvas(left_frame, width=640, height=480)
        self._image_canvas.grid(row=1, column=0, sticky="nsew")

        # Прогрессбар
        self._progress = ttk.Progressbar(left_frame, mode="indeterminate")
        self._progress.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        self._progress.grid_remove()  # Скрыт по умолчанию

    def _create_results_panel(self) -> None:
        """Создание панели результатов (справа)."""
        right_frame = ttk.Frame(self)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 5), pady=5)
        # Растягивается только гистограмма (row 2); таблица и нормы — по контенту
        right_frame.grid_rowconfigure(2, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        # Таблица результатов
        results_frame = ttk.LabelFrame(right_frame, text="Результаты анализа", padding=10)
        results_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
        results_frame.grid_columnconfigure(1, weight=1)

        tooltips = self._build_result_tooltips()
        self._result_vars: dict[str, tk.StringVar] = {}
        fields = [
            ("density", "Плотность (волос/см^2):"),
            ("hair_count", "Всего волос:"),
            ("sep1", "---"),
            ("terminal", "Terminal:"),
            ("vellus", "Vellus:"),
            ("intermediate", "Intermediate:"),
            ("tv_ratio", "T/V соотношение:"),
            ("sep2", "---"),
            ("mean_thickness", "Средняя толщина (мкм):"),
            ("median_thickness", "Медиана толщины (мкм):"),
            ("std_thickness", "Ст. откл. толщины (мкм):"),
            ("min_thickness", "Мин. толщина (мкм):"),
            ("max_thickness", "Макс. толщина (мкм):"),
            ("sep3", "---"),
            ("fov_area", "Площадь поля (мм^2):"),
        ]

        for row, (key, label) in enumerate(fields):
            if key.startswith("sep"):
                ttk.Separator(results_frame, orient="horizontal").grid(
                    row=row, column=0, columnspan=2, sticky="ew", pady=3,
                )
                continue

            name_label = ttk.Label(results_frame, text=label)
            name_label.grid(row=row, column=0, sticky="w", pady=1)

            var = tk.StringVar(value="--")
            self._result_vars[key] = var
            value_label = ttk.Label(
                results_frame, textvariable=var, style="Value.TLabel",
            )
            value_label.grid(row=row, column=1, sticky="e", pady=1)

            # Подсказка с расшифровкой термина — на метку и на значение строки
            hint = tooltips.get(key)
            if hint is not None:
                ToolTip(name_label, hint)
                ToolTip(value_label, hint)

        # Блок справочных норм (между таблицей и гистограммой)
        self._create_norms_panel(right_frame, row=1)

        # Гистограмма толщин (matplotlib)
        chart_frame = ttk.LabelFrame(right_frame, text="Гистограмма толщин", padding=5)
        chart_frame.grid(row=2, column=0, sticky="nsew")
        chart_frame.grid_rowconfigure(0, weight=1)
        chart_frame.grid_columnconfigure(0, weight=1)

        self._figure = Figure(figsize=(4, 3), dpi=80)
        self._ax = self._figure.add_subplot(111)
        self._figure.tight_layout(pad=2.0)

        # matplotlib backend (backend_tkagg) не типизирован → no-untyped-call
        self._chart_canvas = FigureCanvasTkAgg(  # type: ignore[no-untyped-call]
            self._figure, master=chart_frame,
        )
        self._chart_widget = self._chart_canvas.get_tk_widget()  # type: ignore[no-untyped-call]
        self._chart_widget.grid(row=0, column=0, sticky="nsew")

        self._draw_empty_histogram()

    def _build_result_tooltips(self) -> dict[str, str]:
        """Собрать тексты подсказок для строк таблицы результатов.

        Пороги классификации берутся из конфига (``vellus_threshold_um`` и
        ``intermediate_threshold_um``), референсные нормы — из локальных
        констант (METHODOLOGY §2.2). Тексты — простым языком, без диагнозов.

        Returns:
            Отображение «ключ строки таблицы → текст подсказки».
        """
        vellus_um = self._app.config.analysis.vellus_threshold_um
        intermediate_um = self._app.config.analysis.intermediate_threshold_um

        return {
            "density": (
                "Число волос на квадратный сантиметр. "
                f"Норма: {_NORM_DENSITY_MIN}–{_NORM_DENSITY_MAX}/см²"
            ),
            "hair_count": "Обнаружено волос в поле зрения снимка",
            "terminal": (
                f"Зрелые толстые волосы (толще {intermediate_um:.0f} мкм). "
                "Основа здорового покрова"
            ),
            "vellus": (
                f"Пушковые — тонкие бледные волоски (тоньше {vellus_um:.0f} мкм). "
                f"Их рост — признак истончения. Норма: до {_NORM_VELLUS_MAX_PCT}%"
            ),
            "intermediate": (
                f"Промежуточные ({vellus_um:.0f}–{intermediate_um:.0f} мкм) — "
                "между пушковым и зрелым"
            ),
            "tv_ratio": (
                f"Отношение зрелых к пушковым. Норма: ≥{_NORM_TV_RATIO_MIN}. "
                "Если пушковых не найдено вообще — вместо числа пишется «нет пушковых»"
            ),
            "mean_thickness": (
                "Средний диаметр стержня по всем волосам. "
                f"Норма: {_NORM_THICKNESS_MIN}–{_NORM_THICKNESS_MAX} мкм"
            ),
            "median_thickness": "Медианный диаметр стержня, устойчив к выбросам",
            "std_thickness": "Разброс толщины волос — стандартное отклонение",
            "min_thickness": "Толщина самого тонкого обнаруженного волоса",
            "max_thickness": "Толщина самого толстого обнаруженного волоса",
            "fov_area": "Площадь снимка в мм² по текущей калибровке",
        }

    def _create_norms_panel(self, parent: ttk.Frame, row: int) -> None:
        """Создать компактный блок справочных норм.

        Показывает референсные значения (METHODOLOGY §2.2) без диагностики
        и рекомендаций. Размещается между таблицей результатов и гистограммой;
        по высоте не растягивается — grid-вес остаётся у гистограммы.

        Args:
            parent: Родительский фрейм (правая панель).
            row: Номер строки в сетке ``parent``.
        """
        norms_frame = ttk.LabelFrame(parent, text="Нормы (справочно)", padding=(10, 5))
        norms_frame.grid(row=row, column=0, sticky="ew", pady=(0, 5))
        norms_frame.grid_columnconfigure(0, weight=1)

        summary = (
            f"Плотность {_NORM_DENSITY_MIN}–{_NORM_DENSITY_MAX}/см² · "
            f"Vellus ≤{_NORM_VELLUS_MAX_PCT}% · "
            f"T/V ≥{_NORM_TV_RATIO_MIN} · "
            f"Толщина {_NORM_THICKNESS_MIN}–{_NORM_THICKNESS_MAX} мкм"
        )
        summary_label = ttk.Label(norms_frame, text=summary, justify="left", wraplength=250)
        summary_label.grid(row=0, column=0, sticky="w")

        disclaimer_label = ttk.Label(
            norms_frame,
            text="Значения справочные (METHODOLOGY §2.2), не являются диагнозом",
            foreground="gray",
            font=self._small_font,
            justify="left",
            wraplength=250,
        )
        disclaimer_label.grid(row=1, column=0, sticky="w", pady=(3, 0))

        # Адаптивный перенос строк под фактическую ширину панели
        last_width = {"value": 0}

        def _update_wraplength(event: tk.Event) -> None:
            width = max(event.width - 24, 120)
            if width == last_width["value"]:
                return
            last_width["value"] = width
            summary_label.configure(wraplength=width)
            disclaimer_label.configure(wraplength=width)

        norms_frame.bind("<Configure>", _update_wraplength)

    # ──────────────────────────── Загрузка изображений ────────────────────────

    def _on_load_image(self) -> None:
        """Загрузить изображение из файла через диалог с превью."""
        dialog = ImageFileDialog(self, title="Открыть изображение")
        path = dialog.show()
        if path is not None:
            self.load_image_from_path(path)

    def load_image_from_path(self, path: str | Path) -> None:
        """Загрузить изображение по пути (вызывается из меню и capture_tab).

        Args:
            path: Путь к файлу изображения.
        """
        from phototrichogram.utils.image_utils import load_image

        image = load_image(path)
        if image is not None:
            self._set_image(image)
            self._app.status_bar.set_ready(f"Изображение загружено: {Path(path).name}")
        else:
            self._app.status_bar.set_error(f"Не удалось загрузить: {path}")

    def load_image_from_array(self, image: np.ndarray[Any, Any]) -> None:
        """Загрузить изображение из numpy массива (из capture_tab).

        Args:
            image: BGR numpy array.
        """
        self._set_image(image.copy())
        self._app.status_bar.set_ready("Изображение загружено из захвата")

    def _set_image(self, image: np.ndarray[Any, Any]) -> None:
        """Установить текущее изображение и обновить UI.

        Args:
            image: BGR numpy array.
        """
        self._current_image = image
        self._analysis_result = None
        self._showing_annotated = False

        self._image_canvas.show_numpy(image)
        self._analyze_btn.config(state="normal")
        self._toggle_btn.config(state="disabled")
        self._export_btn.config(state="disabled")
        self._clear_results()

    # ──────────────────────────── Анализ ──────────────────────────────────────

    def _on_analyze(self) -> None:
        """Запуск анализа в отдельном потоке."""
        if self._current_image is None:
            return

        # Блокировка UI на время анализа
        self._analyze_btn.config(state="disabled")
        self._load_btn.config(state="disabled")
        self._progress.grid()
        self._progress.start(10)
        self._app.status_bar.set_busy("Анализ изображения...")

        # Копия изображения для потокобезопасности
        image_copy = self._current_image.copy()

        def do_analysis() -> None:
            """Анализ в отдельном потоке."""
            try:
                from phototrichogram.core.analyzer import Analyzer

                analyzer = Analyzer(self._app.config.analysis)
                result = analyzer.analyze_with_annotation(image_copy)

                # Возврат в GUI-поток
                self.after(0, lambda: self._on_analysis_complete(result))
            except Exception as e:
                logger.error("Ошибка анализа: %s", e)
                error_msg = str(e)
                self.after(0, lambda: self._on_analysis_error(error_msg))

        threading.Thread(target=do_analysis, daemon=True).start()

    def _on_analysis_complete(self, result: AnalysisResult | None) -> None:
        """Обработка завершения анализа (в GUI-потоке).

        Args:
            result: Результат анализа или None при ошибке.
        """
        self._progress.stop()
        self._progress.grid_remove()
        self._analyze_btn.config(state="normal")
        self._load_btn.config(state="normal")

        if result is None:
            self._app.status_bar.set_error("Анализ не дал результатов")
            return

        self._analysis_result = result
        self._display_results(result)
        self._draw_histogram(result)

        self._toggle_btn.config(state="normal")
        self._export_btn.config(state="normal")

        self._app.status_bar.set_ready(
            f"Анализ завершён: {result.hair_count} волос, "
            f"плотность {result.density_per_cm2:.0f}/см^2",
        )

    def _on_analysis_error(self, error_msg: str) -> None:
        """Обработка ошибки анализа (в GUI-потоке).

        Args:
            error_msg: Текст ошибки.
        """
        self._progress.stop()
        self._progress.grid_remove()
        self._analyze_btn.config(state="normal")
        self._load_btn.config(state="normal")
        self._app.status_bar.set_error(f"Ошибка анализа: {error_msg}")

    # ──────────────────────────── Отображение результатов ─────────────────────

    def _display_results(self, result: AnalysisResult) -> None:
        """Заполнить таблицу результатов.

        Args:
            result: Результат анализа.
        """
        r = result
        ratios = r.ratios
        thickness = r.thickness

        self._result_vars["density"].set(f"{r.density_per_cm2:.1f}")
        self._result_vars["hair_count"].set(str(r.hair_count))

        self._result_vars["terminal"].set(
            f"{ratios.terminal_count} ({ratios.terminal_pct:.1f}%)",
        )
        self._result_vars["vellus"].set(
            f"{ratios.vellus_count} ({ratios.vellus_pct:.1f}%)",
        )
        self._result_vars["intermediate"].set(
            f"{ratios.intermediate_count} ({ratios.intermediate_pct:.1f}%)",
        )
        self._result_vars["tv_ratio"].set(format_tv_ratio(ratios.tv_ratio))

        self._result_vars["mean_thickness"].set(f"{thickness.mean:.1f}")
        self._result_vars["median_thickness"].set(f"{thickness.median:.1f}")
        self._result_vars["std_thickness"].set(f"{thickness.std:.1f}")
        self._result_vars["min_thickness"].set(f"{thickness.min:.1f}")
        self._result_vars["max_thickness"].set(f"{thickness.max:.1f}")

        self._result_vars["fov_area"].set(f"{r.fov_area_mm2:.2f}")

    def _clear_results(self) -> None:
        """Очистить таблицу результатов."""
        for var in self._result_vars.values():
            var.set("--")

    def _on_toggle_view(self) -> None:
        """Переключение между исходным и аннотированным изображением."""
        if self._analysis_result is None:
            return

        if self._showing_annotated:
            # Показать исходное
            if self._current_image is not None:
                self._image_canvas.show_numpy(self._current_image)
            self._toggle_btn.config(text="Показать разметку")
            self._showing_annotated = False
        else:
            # Показать аннотированное
            annotated = self._analysis_result.annotated_image
            if annotated is not None:
                self._image_canvas.show_numpy(annotated)
            self._toggle_btn.config(text="Показать оригинал")
            self._showing_annotated = True

    # ──────────────────────────── Гистограмма ─────────────────────────────────

    def _draw_empty_histogram(self) -> None:
        """Нарисовать пустую гистограмму (до анализа)."""
        self._ax.clear()
        self._ax.set_xlabel("Толщина (мкм)")
        self._ax.set_ylabel("Количество")
        self._ax.set_title("Распределение толщины волос")
        self._ax.text(
            0.5, 0.5,
            "Нет данных",
            transform=self._ax.transAxes,
            ha="center", va="center",
            fontsize=12, color="gray",
        )
        self._figure.tight_layout(pad=2.0)
        self._chart_canvas.draw()  # type: ignore[no-untyped-call]

    def _draw_histogram(self, result: AnalysisResult) -> None:
        """Нарисовать гистограмму толщин по результатам анализа.

        Args:
            result: Результат анализа с измерениями.
        """
        self._ax.clear()

        if not result.measurements:
            self._draw_empty_histogram()
            return

        thicknesses = [m.thickness_um for m in result.measurements]
        vellus_threshold = self._app.config.analysis.vellus_threshold_um
        intermediate_threshold = self._app.config.analysis.intermediate_threshold_um

        # Разделение по типам
        vellus_t = [t for t in thicknesses if t < vellus_threshold]
        intermediate_t = [
            t for t in thicknesses
            if vellus_threshold <= t < intermediate_threshold
        ]
        terminal_t = [t for t in thicknesses if t >= intermediate_threshold]

        # Общие бины
        max_val = max(thicknesses) if thicknesses else 100
        bins = list(range(0, int(max_val) + 10, 5))

        # Рисуем гистограммы стопкой
        if vellus_t:
            self._ax.hist(
                vellus_t, bins=bins, color="#FF6B6B",
                alpha=0.7, label=f"Vellus (<{vellus_threshold:.0f} мкм)",
            )
        if intermediate_t:
            self._ax.hist(
                intermediate_t, bins=bins, color="#FFD93D", alpha=0.7,
                label=f"Intermediate ({vellus_threshold:.0f}-{intermediate_threshold:.0f} мкм)",
            )
        if terminal_t:
            self._ax.hist(
                terminal_t, bins=bins, color="#6BCB77",
                alpha=0.7, label=f"Terminal (>{intermediate_threshold:.0f} мкм)",
            )

        # Пороговые линии
        self._ax.axvline(
            x=vellus_threshold, color="red", linestyle="--", linewidth=1,
            label=f"Порог Vellus ({vellus_threshold:.0f} мкм)",
        )
        self._ax.axvline(
            x=intermediate_threshold, color="orange", linestyle="--", linewidth=1,
            label=f"Порог Terminal ({intermediate_threshold:.0f} мкм)",
        )

        self._ax.set_xlabel("Толщина (мкм)")
        self._ax.set_ylabel("Количество")
        self._ax.set_title("Распределение толщины волос")
        self._ax.legend(fontsize=7, loc="upper right")

        self._figure.tight_layout(pad=2.0)
        self._chart_canvas.draw()  # type: ignore[no-untyped-call]

    # ──────────────────────────── Экспорт ─────────────────────────────────────

    def _on_export(self) -> None:
        """Экспорт аннотированного изображения."""
        if self._analysis_result is None or self._analysis_result.annotated_image is None:
            self._app.status_bar.set_text("Нет результатов для экспорта")
            return

        filetypes = [
            ("PNG", "*.png"),
            ("JPEG", "*.jpg"),
        ]
        path = filedialog.asksaveasfilename(
            title="Сохранить результат",
            filetypes=filetypes,
            defaultextension=".png",
        )
        if path:
            from phototrichogram.utils.image_utils import save_image

            success = save_image(self._analysis_result.annotated_image, path)
            if success:
                self._app.status_bar.set_ready(f"Результат сохранён: {path}")
            else:
                self._app.status_bar.set_error("Ошибка сохранения")

    def save_result(self) -> None:
        """Сохранение результата (вызывается из меню Файл > Сохранить)."""
        self._on_export()

"""
Вкладка истории измерений и динамики.

Содержит HistoryTab: таблица измерений активного пациента,
графики динамики плотности и T/V ratio по зонам с полосами нормы
и интерактивными аннотациями по клику на точку.

Layout:
- Сверху: имя пациента + кнопка «Обновить»
- Середина: Treeview со всеми измерениями
- Снизу: два графика matplotlib (плотность, T/V ratio)

Полосы нормы берутся из единого мостика ``gui.widgets.patient_norms``
(персональные нормы активного пациента). Одиночный клик по точке графика
показывает matplotlib-аннотацию с числовыми показателями измерения (без
диагнозов); двойной клик по той же точке открывает окно со снимками этого
измерения (серия до 5 кадров, ImageCanvas с зумом).

НЕ импортирует cv2 — чисто GUI-слой (снимки грузятся через PIL/ImageCanvas).
"""

from __future__ import annotations

import contextlib
import logging
import math
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING, Any

import matplotlib
from PIL import Image, ImageTk

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from phototrichogram.core.stats import TV_RATIO_MAX
from phototrichogram.data.models import Measurement
from phototrichogram.gui.widgets import ImageCanvas, format_tv_ratio, patient_norms

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.backend_bases import MouseEvent
    from matplotlib.text import Annotation

    from phototrichogram.core.norms import PatientNorms
    from phototrichogram.data.database import Database
    from phototrichogram.data.models import MeasurementImage
    from phototrichogram.gui.app import App

logger = logging.getLogger(__name__)

# Цвета линий для зон на графиках
_ZONE_COLORS: dict[str, str] = {
    "F": "#E53935",  # красный
    "P": "#1E88E5",  # синий
    "T": "#43A047",  # зелёный
    "O": "#FB8C00",  # оранжевый
}

_ZONE_LABELS: dict[str, str] = {
    "F": "F (лобная)",
    "P": "P (теменная)",
    "T": "T (височная)",
    "O": "O (затылочная)",
}

# Зоны в стандартном порядке обхода
_ZONE_ORDER: tuple[str, ...] = ("F", "P", "T", "O")

# Полупрозрачная зелёная полоса нормы
_NORM_COLOR: str = "#4CAF50"
_NORM_ALPHA: float = 0.10

# Радиус «прицеливания» по точке при клике (в пикселях экрана)
_PICK_RADIUS_PX: float = 20.0

# Тип точки серии для поиска ближайшей при клике: (x_данные, y_данные, инфо)
_SeriesPoint = tuple[float, float, "_PointInfo"]


@dataclass(frozen=True)
class _PointInfo:
    """Числовые показатели точки графика для аннотации по клику.

    Хранит только числа (без диагностических оценок): аннотация показывает
    дату, зону и измеренные параметры. Поле ``measurement_id`` привязывает
    точку к измерению в БД — по нему двойной клик загружает снимки серии.
    """

    zone: str
    date: str
    density: float
    vellus_pct: float
    tv_ratio: float
    mean_thickness_um: float
    measurement_id: int


@dataclass(frozen=True)
class _ZoneSeries:
    """Серия измерений одной зоны для построения графика динамики.

    Списки ``dates`` и ``measurements`` параллельны (один индекс — одна точка)
    и отсортированы по дате возрастанию (как отдаёт get_zone_history).
    """

    zone: str
    dates: list[str]
    measurements: list[Measurement]


class HistoryTab(ttk.Frame):
    """Вкладка истории измерений и динамики.

    Отображает:
    - Таблицу всех измерений активного пациента
    - Графики динамики плотности и T/V ratio по зонам с полосами нормы
    - Аннотацию с числами по клику на точку графика
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: Any) -> None:
        """Инициализация вкладки истории.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно приложения.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app

        self._create_widgets()

        logger.info("Вкладка истории инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._create_header()
        self._create_table()
        self._create_charts()

    def _create_header(self) -> None:
        """Создание заголовка с именем пациента и кнопкой обновления."""
        header_frame = ttk.Frame(self, padding=(5, 5, 5, 2))
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(header_frame, text="Пациент:", style="Header.TLabel").grid(
            row=0, column=0, padx=(0, 5),
        )

        self._patient_name_var = tk.StringVar(value="не выбран")
        ttk.Label(
            header_frame,
            textvariable=self._patient_name_var,
            font=("", 12),
        ).grid(row=0, column=1, sticky="w")

        self._refresh_btn = ttk.Button(
            header_frame, text="Обновить", command=self.refresh, width=12,
        )
        self._refresh_btn.grid(row=0, column=2, padx=(5, 0))

    def _create_table(self) -> None:
        """Создание таблицы измерений."""
        table_frame = ttk.LabelFrame(self, text="Измерения", padding=3)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(2, 2))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        columns = ("date", "zone", "density", "vellus_pct", "tv_ratio", "thickness")
        self._tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=8,
        )
        self._tree.heading("date", text="Дата")
        self._tree.heading("zone", text="Зона")
        self._tree.heading("density", text="Плотность")
        self._tree.heading("vellus_pct", text="Vellus %")
        self._tree.heading("tv_ratio", text="T/V")
        self._tree.heading("thickness", text="Толщина (мкм)")

        self._tree.column("date", width=100, minwidth=80, anchor="center")
        self._tree.column("zone", width=60, minwidth=40, anchor="center")
        self._tree.column("density", width=90, minwidth=60, anchor="center")
        self._tree.column("vellus_pct", width=80, minwidth=60, anchor="center")
        self._tree.column("tv_ratio", width=110, minwidth=90, anchor="center")
        self._tree.column("thickness", width=110, minwidth=80, anchor="center")

        tree_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        # Плейсхолдер
        self._table_placeholder = ttk.Label(
            table_frame,
            text="Выберите пациента на вкладке «Пациенты»",
            anchor="center",
            font=("", 11),
            foreground="#888888",
        )
        self._table_placeholder.grid(row=0, column=0, sticky="nsew")

    def _create_charts(self) -> None:
        """Создание области графиков matplotlib."""
        charts_frame = ttk.LabelFrame(self, text="Динамика", padding=3)
        charts_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=(2, 5))
        charts_frame.grid_rowconfigure(0, weight=1)
        charts_frame.grid_columnconfigure(0, weight=1)

        # Состояние интерактивных графиков
        self._annotation: Annotation | None = None
        self._density_points: list[_SeriesPoint] = []
        self._tv_points: list[_SeriesPoint] = []
        # Окно просмотра снимков — одно на вкладку, переиспользуется (см.
        # _open_images_window); None пока ни разу не открывали.
        self._images_window: _ImagesWindow | None = None

        # Figure с двумя subplot'ами
        self._figure = Figure(figsize=(10, 3.5), dpi=90)
        self._figure.set_facecolor("#f0f0f0")

        self._ax_density = self._figure.add_subplot(1, 2, 1)
        self._ax_tv = self._figure.add_subplot(1, 2, 2)

        self._figure.subplots_adjust(
            left=0.08, right=0.95, top=0.88, bottom=0.22, wspace=0.3,
        )

        # matplotlib backend (backend_tkagg) не типизирован → no-untyped-call
        self._canvas_widget = FigureCanvasTkAgg(  # type: ignore[no-untyped-call]
            self._figure, master=charts_frame,
        )
        self._canvas_widget.get_tk_widget().grid(  # type: ignore[no-untyped-call]
            row=0, column=0, sticky="nsew",
        )
        # Клик по графику — показать/убрать аннотацию точки
        self._canvas_widget.mpl_connect("button_press_event", self._on_chart_click)

        # Плейсхолдер для графиков
        self._charts_placeholder = ttk.Label(
            charts_frame,
            text="Нет данных для построения графиков",
            anchor="center",
            font=("", 11),
            foreground="#888888",
        )
        self._charts_placeholder.grid(row=0, column=0, sticky="nsew")

        # Начальное состояние — показать плейсхолдеры
        self._clear_charts()

    # ──────────────────────────── Обновление данных ────────────────────────────

    def refresh(self) -> None:
        """Обновить данные для текущего активного пациента."""
        patient = self._app.active_patient
        if patient is None:
            self._patient_name_var.set("не выбран")
            self._show_placeholders()
            return

        self._patient_name_var.set(patient.name)
        # Активный пациент загружен из БД — id гарантированно заполнен
        patient_id = patient.id
        assert patient_id is not None

        try:
            db = self._app.db
            measurements = db.get_patient_measurements(patient_id)
            sessions = db.get_patient_sessions(patient_id)
        except Exception as e:
            logger.error("Ошибка загрузки данных пациента id=%d: %s", patient_id, e)
            self._app.status_bar.set_error(f"Ошибка загрузки истории: {e}")
            return

        # Маппинг session_id -> дата
        session_dates: dict[int, str] = {}
        for s in sessions:
            # Сессия загружена из БД — id заполнен
            assert s.id is not None
            session_dates[s.id] = s.date

        self._populate_table(measurements, session_dates)
        self._update_charts(patient_id)

        count = len(measurements)
        self._app.status_bar.set_ready(
            f"История: {patient.name} — {count} измерений",
        )

    def _show_placeholders(self) -> None:
        """Показать плейсхолдеры вместо данных."""
        self._tree.delete(*self._tree.get_children())
        self._table_placeholder.grid()
        self._table_placeholder.lift()
        self._clear_charts()
        self._charts_placeholder.grid()
        self._charts_placeholder.lift()

    # ──────────────────────────── Таблица ──────────────────────────────────────

    def _populate_table(
        self,
        measurements: list[Measurement],
        session_dates: dict[int, str],
    ) -> None:
        """Заполнить таблицу измерений.

        Args:
            measurements: Список измерений.
            session_dates: Маппинг session_id -> дата сессии.
        """
        self._tree.delete(*self._tree.get_children())

        if not measurements:
            self._table_placeholder.grid()
            self._table_placeholder.lift()
            self._table_placeholder.config(text="Нет измерений")
            return

        self._table_placeholder.grid_remove()

        for m in measurements:
            date = session_dates.get(m.session_id, m.created_at[:10] if m.created_at else "--")
            self._tree.insert("", "end", values=(
                date,
                m.zone,
                f"{m.density_per_cm2:.0f}",
                f"{m.vellus_pct:.1f}%",
                format_tv_ratio(m.tv_ratio),
                f"{m.mean_thickness_um:.1f}",
            ))

    # ──────────────────────────── Графики ──────────────────────────────────────

    def _clear_charts(self) -> None:
        """Очистить графики, снять аннотацию и сбросить данные точек."""
        self._clear_annotation()
        self._density_points = []
        self._tv_points = []

        self._ax_density.clear()
        self._ax_tv.clear()

        self._ax_density.set_title("Плотность (волос/см²)", fontsize=10)
        self._ax_density.set_xlabel("Дата", fontsize=9)
        self._ax_density.set_ylabel("волос/см²", fontsize=9)

        self._ax_tv.set_title("T/V соотношение", fontsize=10)
        self._ax_tv.set_xlabel("Дата", fontsize=9)
        self._ax_tv.set_ylabel("T/V", fontsize=9)

        self._canvas_widget.draw_idle()  # type: ignore[no-untyped-call]

    def _load_series(self, patient_id: int) -> list[_ZoneSeries]:
        """Загрузить серии измерений по зонам для пациента.

        Для каждой точки берётся дата соответствующей сессии (fallback —
        дата создания измерения).

        Args:
            patient_id: ID пациента.

        Returns:
            Непустые серии по зонам в порядке F/P/T/O.
        """
        db = self._app.db
        series: list[_ZoneSeries] = []

        for zone_code in _ZONE_ORDER:
            try:
                history = db.get_zone_history(patient_id, zone_code)
            except Exception as e:
                logger.error("Ошибка загрузки истории зоны %s: %s", zone_code, e)
                continue

            if not history:
                continue

            dates: list[str] = []
            measurements: list[Measurement] = []
            for m in history:
                session = db.get_session(m.session_id)
                if session is not None:
                    date_str = session.date
                elif m.created_at:
                    date_str = m.created_at[:10]
                else:
                    date_str = "--"
                dates.append(date_str)
                measurements.append(m)

            if dates:
                series.append(_ZoneSeries(zone_code, dates, measurements))

        return series

    def _update_charts(self, patient_id: int) -> None:
        """Обновить графики динамики для пациента.

        Строит серии по зонам, полосы нормы (из единого мостика
        patient_norms) и подготавливает точки для аннотаций по клику.
        Автомасштаб оси ведётся по данным и норме, чтобы данные оставались
        читаемы (для T/V «нет пушковых» = TV_RATIO_MAX обрабатывается как
        сентинел и рисуется у верхней кромки).

        Args:
            patient_id: ID пациента.
        """
        self._clear_annotation()
        self._density_points = []
        self._tv_points = []

        series = self._load_series(patient_id)
        if not series:
            self._charts_placeholder.grid()
            self._charts_placeholder.lift()
            self._charts_placeholder.config(text="Нет данных для построения графиков")
            self._clear_charts()
            return

        self._charts_placeholder.grid_remove()

        self._ax_density.clear()
        self._ax_tv.clear()

        norms = patient_norms(self._app.active_patient)
        all_dates = sorted({d for s in series for d in s.dates})
        date_index = {d: i for i, d in enumerate(all_dates)}

        # Параметры оси T/V с учётом сентинела «нет пушковых» (TV_RATIO_MAX)
        real_tv = [
            m.tv_ratio
            for s in series
            for m in s.measurements
            if m.tv_ratio < TV_RATIO_MAX
        ]
        tv_top = max([*real_tv, norms.tv_ratio.high])
        tv_sentinel_y = tv_top * 1.10
        tv_y_top = tv_top * 1.20

        for s in series:
            xs = [float(date_index[d]) for d in s.dates]
            color = _ZONE_COLORS.get(s.zone, "#333333")
            label = _ZONE_LABELS.get(s.zone, s.zone)

            densities = [m.density_per_cm2 for m in s.measurements]
            self._ax_density.plot(
                xs, densities,
                marker="o", markersize=5, color=color, label=label, linewidth=1.5,
            )

            tv_plot = [
                m.tv_ratio if m.tv_ratio < TV_RATIO_MAX else tv_sentinel_y
                for m in s.measurements
            ]
            self._ax_tv.plot(
                xs, tv_plot,
                marker="s", markersize=5, color=color, label=label, linewidth=1.5,
            )

            for x, date, m in zip(xs, s.dates, s.measurements, strict=True):
                # Измерение загружено из БД — id заполнен (нужен для снимков)
                assert m.id is not None
                info = _PointInfo(
                    zone=s.zone,
                    date=date,
                    density=m.density_per_cm2,
                    vellus_pct=m.vellus_pct,
                    tv_ratio=m.tv_ratio,
                    mean_thickness_um=m.mean_thickness_um,
                    measurement_id=m.id,
                )
                self._density_points.append((x, m.density_per_cm2, info))
                tv_y = m.tv_ratio if m.tv_ratio < TV_RATIO_MAX else tv_sentinel_y
                self._tv_points.append((x, tv_y, info))

        self._finalize_density_axis(norms, all_dates)
        self._finalize_tv_axis(norms, all_dates, tv_y_top)

        self._figure.tight_layout()
        self._canvas_widget.draw_idle()  # type: ignore[no-untyped-call]

    def _finalize_density_axis(self, norms: PatientNorms, all_dates: list[str]) -> None:
        """Оформить ось плотности: полоса нормы, автомасштаб, подписи.

        Args:
            norms: Персональные нормы пациента.
            all_dates: Общий отсортированный список дат (для оси X).
        """
        ax = self._ax_density
        low = norms.density.low
        high = norms.density.high

        dens_vals = [y for _, y, _ in self._density_points]
        lo = min([*dens_vals, low])
        hi = max([*dens_vals, high])
        pad = max((hi - lo) * 0.08, 5.0)
        ax.set_ylim(max(0.0, lo - pad), hi + pad)

        ax.axhspan(
            low, high,
            color=_NORM_COLOR, alpha=_NORM_ALPHA, zorder=0,
            label=f"норма: {norms.basis}",
        )

        self._apply_date_axis(ax, all_dates)
        ax.set_title("Плотность (волос/см²)", fontsize=10)
        ax.set_xlabel("Дата", fontsize=9)
        ax.set_ylabel("волос/см²", fontsize=9)
        ax.legend(fontsize=8, loc="best")
        ax.grid(True, alpha=0.3)

    def _finalize_tv_axis(
        self, norms: PatientNorms, all_dates: list[str], y_top: float,
    ) -> None:
        """Оформить ось T/V: полоса нормы от low вверх, автомасштаб, подписи.

        Args:
            norms: Персональные нормы пациента.
            all_dates: Общий отсортированный список дат (для оси X).
            y_top: Верхняя граница оси T/V (данные координаты).
        """
        ax = self._ax_tv
        low = norms.tv_ratio.low

        ax.set_ylim(0.0, y_top)
        ax.axhspan(
            low, y_top,
            color=_NORM_COLOR, alpha=_NORM_ALPHA, zorder=0,
            label=f"норма: {norms.basis}",
        )
        ax.axhline(low, color=_NORM_COLOR, linestyle="--", linewidth=0.9, alpha=0.7)

        self._apply_date_axis(ax, all_dates)
        ax.set_title("T/V соотношение", fontsize=10)
        ax.set_xlabel("Дата", fontsize=9)
        ax.set_ylabel("T/V", fontsize=9)
        ax.legend(fontsize=8, loc="best")
        ax.grid(True, alpha=0.3)

    def _apply_date_axis(self, ax: Axes, all_dates: list[str]) -> None:
        """Единая настройка оси дат: тики, подписи, границы.

        Args:
            ax: Ось для настройки.
            all_dates: Отсортированный список дат (позиции 0..N-1).
        """
        n = len(all_dates)
        ax.set_xticks(list(range(n)))
        ax.set_xticklabels(all_dates, rotation=30, ha="right", fontsize=8)
        ax.set_xlim(-0.6, n - 0.4)
        ax.tick_params(axis="y", labelsize=8)

    # ──────────────────────────── Интерактивность ─────────────────────────────

    def _on_chart_click(self, event: MouseEvent) -> None:
        """Клик по графику: аннотация точки (одиночный) или снимки (двойной).

        Ищет ближайшую точку на оси, по которой кликнули. Если она в радиусе
        прицеливания: одиночный клик показывает аннотацию с числами измерения,
        двойной — открывает окно со снимками этого измерения. Иначе (пустое
        место или клик вне осей) — убирает текущую аннотацию.

        Двойной и одиночный клик не конфликтуют: matplotlib на двойном клике
        шлёт два события — первый press (dblclick=False) уже показал аннотацию,
        второй (dblclick=True) открывает окно снимков.

        Args:
            event: Событие мыши matplotlib.
        """
        ax = event.inaxes
        if ax is None or event.x is None or event.y is None:
            self._dismiss_annotation()
            return

        if ax is self._ax_density:
            points = self._density_points
        elif ax is self._ax_tv:
            points = self._tv_points
        else:
            self._dismiss_annotation()
            return

        nearest = self._nearest_point(ax, points, float(event.x), float(event.y))
        if nearest is None:
            self._dismiss_annotation()
            return

        x_data, y_data, info = nearest
        if event.dblclick:
            self._open_images_window(info)
            return
        self._show_annotation(ax, x_data, y_data, info)

    def _open_images_window(self, info: _PointInfo) -> None:
        """Открыть окно снимков измерения (двойной клик по точке графика).

        Одно окно на вкладку: если оно уже открыто — переиспользуется (грузит
        снимки выбранного измерения и поднимается на передний план), второе
        окно не создаётся.

        Args:
            info: Данные точки, по которой сделан двойной клик.
        """
        zone_label = _ZONE_LABELS.get(info.zone, info.zone)
        window = self._images_window
        if window is not None and window.winfo_exists():
            window.show(info.measurement_id, zone_label, info.date)
            return
        self._images_window = _ImagesWindow(self.winfo_toplevel(), self._app.db)
        self._images_window.show(info.measurement_id, zone_label, info.date)

    def _nearest_point(
        self,
        ax: Axes,
        points: list[_SeriesPoint],
        px: float,
        py: float,
    ) -> _SeriesPoint | None:
        """Ближайшая к (px, py) точка серии в пределах радиуса прицеливания.

        Args:
            ax: Ось, на которой ищем точку.
            points: Точки серии (x_данные, y_данные, инфо).
            px: X-координата клика в пикселях экрана.
            py: Y-координата клика в пикселях экрана.

        Returns:
            Ближайшая точка или None, если ни одна не попала в радиус.
        """
        best: _SeriesPoint | None = None
        best_dist = _PICK_RADIUS_PX
        for x_data, y_data, info in points:
            disp = ax.transData.transform((x_data, y_data))
            dist = math.hypot(float(disp[0]) - px, float(disp[1]) - py)
            if dist <= best_dist:
                best_dist = dist
                best = (x_data, y_data, info)
        return best

    def _show_annotation(
        self, ax: Axes, x_data: float, y_data: float, info: _PointInfo,
    ) -> None:
        """Показать аннотацию у точки, заменив предыдущую.

        Подпись отводится внутрь области осей (направление смещения зависит
        от положения точки), чтобы текст не уходил за край графика.

        Args:
            ax: Ось, на которой находится точка.
            x_data: X-координата точки (данные).
            y_data: Y-координата точки (данные).
            info: Числовые показатели точки.
        """
        self._clear_annotation()

        # Положение точки в долях оси → сторона смещения подписи
        disp = ax.transData.transform((x_data, y_data))
        frac = ax.transAxes.inverted().transform(disp)
        fx, fy = float(frac[0]), float(frac[1])
        dx = 14.0 if fx < 0.5 else -14.0
        dy = 14.0 if fy < 0.5 else -14.0
        ha = "left" if fx < 0.5 else "right"
        va = "bottom" if fy < 0.5 else "top"

        self._annotation = ax.annotate(
            self._format_point_text(info),
            xy=(x_data, y_data),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8,
            ha=ha,
            va=va,
            zorder=20,
            bbox={
                "boxstyle": "round,pad=0.4",
                "facecolor": "#FFFDE7",
                "edgecolor": "#9E9E9E",
                "alpha": 0.95,
            },
            arrowprops={"arrowstyle": "-", "color": "#616161", "linewidth": 0.8},
        )
        self._canvas_widget.draw_idle()  # type: ignore[no-untyped-call]

    def _format_point_text(self, info: _PointInfo) -> str:
        """Текст аннотации точки — только числа, без диагноза.

        Последняя строка — подсказка, что двойной клик по точке открывает
        снимки этого измерения.

        Args:
            info: Числовые показатели точки.

        Returns:
            Многострочный текст: дата, зона, измеренные параметры и подсказка.
        """
        zone_label = _ZONE_LABELS.get(info.zone, info.zone)
        return (
            f"{info.date} — {zone_label}\n"
            f"Плотность: {info.density:.0f} волос/см²\n"
            f"Vellus: {info.vellus_pct:.1f}%\n"
            f"T/V: {format_tv_ratio(info.tv_ratio)}\n"
            f"Толщина: {info.mean_thickness_um:.1f} мкм\n"
            f"Двойной клик — снимки зоны"
        )

    def _dismiss_annotation(self) -> None:
        """Убрать аннотацию и перерисовать (если она была)."""
        if self._annotation is not None:
            self._clear_annotation()
            self._canvas_widget.draw_idle()  # type: ignore[no-untyped-call]

    def _clear_annotation(self) -> None:
        """Снять текущую аннотацию без перерисовки."""
        if self._annotation is not None:
            with contextlib.suppress(NotImplementedError, ValueError, AttributeError):
                self._annotation.remove()
            self._annotation = None

    # ──────────────────────────── Событие вкладки ─────────────────────────────

    def on_tab_selected(self) -> None:
        """Вызывается при переключении на эту вкладку — автообновление."""
        self.refresh()


class _ImagesWindow(tk.Toplevel):
    """Окно просмотра снимков одного измерения (серия до 5 кадров).

    Миниатюры серии выстроены в ряд; клик по миниатюре открывает кадр крупно в
    ImageCanvas (зум колесом, панорамирование, сброс двойным кликом). Если у
    снимков есть размеченные версии — доступен переключатель «исходный /
    размеченный».

    Устойчиво к отсутствию файла на диске: запись в БД есть, а файла нет
    (перемещён/удалён) — показывается заглушка «файл не найден», окно не падает.
    Пустая серия (старые измерения до Phase 5a) — сообщение «Снимки не сохранены
    для этого измерения». Escape и системная кнопка закрытия закрывают окно.

    НЕ импортирует cv2 — снимки грузятся через PIL и показываются в ImageCanvas.
    """

    _THUMB_SIZE: int = 110
    _MAX_THUMBS: int = 5
    _BORDER_IDLE: str = "#555555"
    _BORDER_ACTIVE: str = "#1E88E5"

    def __init__(self, parent: tk.Tk | tk.Toplevel, db: Database) -> None:
        """Создать пустой каркас окна (наполняется в show()).

        Args:
            parent: Родительское окно (Tk/Toplevel) для transient-привязки —
                как возвращает ``winfo_toplevel()``.
            db: База данных — источник списка снимков измерения.
        """
        super().__init__(parent)
        self._db = db
        self._images: list[MeasurementImage] = []
        self._selected: int = 0
        self._show_annotated_var = tk.BooleanVar(value=False)
        # Ссылки на PhotoImage миниатюр — иначе tkinter удалит их сборщиком.
        self._thumb_photos: list[object] = []
        self._thumb_canvases: list[tk.Canvas] = []

        with contextlib.suppress(tk.TclError):
            self.transient(parent)
        self.minsize(520, 460)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())

        self._build_ui()

    def _build_ui(self) -> None:
        """Построить статичный каркас окна (переключатель, канвас, лента)."""
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        root = ttk.Frame(self, padding=6)
        root.grid(row=0, column=0, sticky="nsew")
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        # Панель переключателя «исходный / размеченный»
        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._toggle = ttk.Checkbutton(
            top,
            text="Показать размеченный снимок",
            variable=self._show_annotated_var,
            command=self._render_selected,
        )
        # Виден только если в серии есть размеченные снимки (_rebuild_thumbnails).

        # Крупный просмотр выбранного снимка
        self._canvas = ImageCanvas(root, width=560, height=340)
        self._canvas.grid(row=1, column=0, sticky="nsew")

        # Лента миниатюр серии
        self._thumb_frame = ttk.Frame(root, padding=(0, 6, 0, 0))
        self._thumb_frame.grid(row=2, column=0, sticky="ew")

        # Статусная строка (номер кадра, «файл не найден» и т.п.)
        self._status_var = tk.StringVar(value="")
        ttk.Label(
            root, textvariable=self._status_var, anchor="w", foreground="#666666",
        ).grid(row=3, column=0, sticky="ew", pady=(4, 0))

    def show(self, measurement_id: int, zone_label: str, date: str) -> None:
        """Загрузить снимки измерения и поднять окно на передний план.

        Args:
            measurement_id: ID измерения, чьи снимки показываем.
            zone_label: Метка зоны для заголовка окна.
            date: Дата измерения для заголовка окна.
        """
        self.title(f"Снимки: {zone_label}, {date}")
        try:
            self._images = self._db.get_measurement_images(measurement_id)
        except Exception as e:
            logger.error(
                "Ошибка загрузки снимков измерения id=%d: %s", measurement_id, e,
            )
            self._images = []

        self._selected = 0
        self._show_annotated_var.set(False)
        self._rebuild_thumbnails()
        self._render_selected()

        self.deiconify()
        self.lift()
        self.focus_set()

    def _rebuild_thumbnails(self) -> None:
        """Пересобрать ленту миниатюр и переключатель под текущую серию."""
        for child in self._thumb_frame.winfo_children():
            child.destroy()
        self._thumb_photos = []
        self._thumb_canvases = []

        # Переключатель разметки — только если есть хотя бы один размеченный кадр.
        if any(img.annotated_image_path for img in self._images):
            self._toggle.pack(side="left")
        else:
            self._toggle.pack_forget()

        if not self._images:
            ttk.Label(
                self._thumb_frame,
                text="Снимки не сохранены для этого измерения",
                foreground="#888888",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, img in enumerate(self._images[: self._MAX_THUMBS]):
            self._add_thumbnail(index, img)
        self._highlight_selected()

    def _add_thumbnail(self, index: int, img: MeasurementImage) -> None:
        """Добавить миниатюру кадра (или заглушку, если файла нет на диске).

        Args:
            index: Позиция кадра в серии (0-based).
            img: Запись снимка серии.
        """
        holder = tk.Canvas(
            self._thumb_frame,
            width=self._THUMB_SIZE,
            height=self._THUMB_SIZE,
            bg="#2b2b2b",
            highlightthickness=2,
            highlightbackground=self._BORDER_IDLE,
            cursor="hand2",
        )
        holder.grid(row=0, column=index, padx=3)
        holder.bind("<Button-1>", self._make_thumb_handler(index))

        thumb = self._load_thumbnail(Path(img.image_path))
        if thumb is None:
            holder.create_text(
                self._THUMB_SIZE // 2,
                self._THUMB_SIZE // 2,
                text=f"#{index + 1}\nнет файла",
                fill="#E57373",
                justify="center",
                font=("", 9),
            )
        else:
            photo = ImageTk.PhotoImage(thumb)
            self._thumb_photos.append(photo)
            holder.create_image(
                self._THUMB_SIZE // 2,
                self._THUMB_SIZE // 2,
                anchor="center",
                image=photo,
            )
        self._thumb_canvases.append(holder)

    def _make_thumb_handler(self, index: int) -> Callable[[tk.Event], None]:
        """Собрать обработчик клика по миниатюре (замыкание по параметру).

        Захват index через параметр метода, а не переменную цикла — исключает
        общий баг с поздним связыванием в замыканиях.

        Args:
            index: Позиция миниатюры в серии.

        Returns:
            Функция-обработчик события клика.
        """

        def handler(_event: tk.Event) -> None:
            self._select(index)

        return handler

    def _load_thumbnail(self, path: Path) -> Image.Image | None:
        """Загрузить снимок и уменьшить до размера миниатюры.

        Args:
            path: Путь к файлу снимка.

        Returns:
            PIL-миниатюра или None, если файла нет либо он не читается.
        """
        if not path.exists():
            logger.warning("Файл снимка не найден (миниатюра): %s", path)
            return None
        try:
            pil: Image.Image = Image.open(path)
            pil.load()
            pil = pil.convert("RGB")
        except (OSError, ValueError) as e:
            logger.error("Ошибка чтения снимка %s: %s", path, e)
            return None
        pil.thumbnail((self._THUMB_SIZE, self._THUMB_SIZE), Image.Resampling.LANCZOS)
        return pil

    def _select(self, index: int) -> None:
        """Выбрать миниатюру и показать соответствующий кадр крупно.

        Args:
            index: Позиция кадра в серии.
        """
        self._selected = index
        self._highlight_selected()
        self._render_selected()

    def _highlight_selected(self) -> None:
        """Подсветить рамку активной миниатюры, снять подсветку с прочих."""
        for i, canvas in enumerate(self._thumb_canvases):
            color = self._BORDER_ACTIVE if i == self._selected else self._BORDER_IDLE
            canvas.configure(highlightbackground=color, highlightcolor=color)

    def _render_selected(self) -> None:
        """Показать выбранный кадр крупно с учётом переключателя разметки.

        Устойчиво к отсутствию файла: если записанного в БД файла нет на диске
        (перемещён/удалён) — показывает заглушку в статусе и пишет в лог, окно
        не падает.
        """
        if not self._images:
            self._canvas.clear()
            self._status_var.set("")
            return

        self._selected = max(0, min(self._selected, len(self._images) - 1))
        img = self._images[self._selected]

        if self._show_annotated_var.get() and img.annotated_image_path:
            path = Path(img.annotated_image_path)
            kind = "размеченный"
        else:
            path = Path(img.image_path)
            kind = "исходный"

        total = len(self._images)
        if not path.exists():
            self._canvas.clear()
            self._status_var.set(f"файл не найден: {path}")
            logger.warning("Файл снимка не найден на диске: %s", path)
            return

        try:
            pil = Image.open(path)
            pil.load()
        except (OSError, ValueError) as e:
            self._canvas.clear()
            self._status_var.set(f"не удалось открыть файл: {path}")
            logger.error("Ошибка открытия снимка %s: %s", path, e)
            return

        self._canvas.show_pil(pil)
        self._status_var.set(f"Снимок {self._selected + 1} из {total} ({kind})")

    def _close(self) -> None:
        """Закрыть окно снимков."""
        self.destroy()

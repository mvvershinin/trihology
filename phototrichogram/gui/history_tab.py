"""
Вкладка истории измерений и динамики.

Содержит HistoryTab: таблица измерений активного пациента,
графики динамики плотности и T/V ratio по зонам.

Layout:
- Сверху: имя пациента + кнопка «Обновить»
- Середина: Treeview со всеми измерениями
- Снизу: два графика matplotlib (плотность, T/V ratio)

НЕ импортирует cv2 — чисто GUI-слой.
"""

from __future__ import annotations

import logging
import tkinter as tk
from collections import defaultdict
from tkinter import ttk
from typing import TYPE_CHECKING

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from phototrichogram.data.models import Measurement

if TYPE_CHECKING:
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


class HistoryTab(ttk.Frame):
    """Вкладка истории измерений и динамики.

    Отображает:
    - Таблицу всех измерений активного пациента
    - Графики динамики плотности и T/V ratio по зонам
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: object) -> None:
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
        self._tree.column("tv_ratio", width=70, minwidth=50, anchor="center")
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

        # Figure с двумя subplot'ами
        self._figure = Figure(figsize=(10, 3.5), dpi=90)
        self._figure.set_facecolor("#f0f0f0")

        self._ax_density = self._figure.add_subplot(1, 2, 1)
        self._ax_tv = self._figure.add_subplot(1, 2, 2)

        self._figure.subplots_adjust(
            left=0.08, right=0.95, top=0.88, bottom=0.22, wspace=0.3,
        )

        self._canvas_widget = FigureCanvasTkAgg(self._figure, master=charts_frame)
        self._canvas_widget.get_tk_widget().grid(row=0, column=0, sticky="nsew")

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

        try:
            db = self._app.db
            measurements = db.get_patient_measurements(patient.id)
            sessions = db.get_patient_sessions(patient.id)
        except Exception as e:
            logger.error("Ошибка загрузки данных пациента id=%d: %s", patient.id, e)
            self._app.status_bar.set_error(f"Ошибка загрузки истории: {e}")
            return

        # Маппинг session_id -> дата
        session_dates: dict[int, str] = {}
        for s in sessions:
            session_dates[s.id] = s.date

        self._populate_table(measurements, session_dates)
        self._update_charts(patient.id)

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
                f"{m.tv_ratio:.1f}",
                f"{m.mean_thickness_um:.1f}",
            ))

    # ──────────────────────────── Графики ──────────────────────────────────────

    def _clear_charts(self) -> None:
        """Очистить графики."""
        self._ax_density.clear()
        self._ax_tv.clear()

        self._ax_density.set_title("Плотность (волос/см\u00b2)", fontsize=10)
        self._ax_density.set_xlabel("Дата", fontsize=9)
        self._ax_density.set_ylabel("волос/см\u00b2", fontsize=9)

        self._ax_tv.set_title("T/V соотношение", fontsize=10)
        self._ax_tv.set_xlabel("Дата", fontsize=9)
        self._ax_tv.set_ylabel("T/V", fontsize=9)

        self._canvas_widget.draw_idle()

    def _update_charts(self, patient_id: int) -> None:
        """Обновить графики динамики для пациента.

        Args:
            patient_id: ID пациента.
        """
        db = self._app.db
        has_data = False

        self._ax_density.clear()
        self._ax_tv.clear()

        for zone_code in ["F", "P", "T", "O"]:
            try:
                history = db.get_zone_history(patient_id, zone_code)
            except Exception as e:
                logger.error("Ошибка загрузки истории зоны %s: %s", zone_code, e)
                continue

            if not history:
                continue

            # Получить даты сессий для этих измерений
            dates: list[str] = []
            densities: list[float] = []
            tv_ratios: list[float] = []

            for m in history:
                # Получаем дату из сессии
                session = db.get_session(m.session_id)
                date_str = session.date if session else (m.created_at[:10] if m.created_at else "")
                dates.append(date_str)
                densities.append(m.density_per_cm2)
                tv_ratios.append(m.tv_ratio)

            if not dates:
                continue

            has_data = True
            color = _ZONE_COLORS.get(zone_code, "#333333")
            label = _ZONE_LABELS.get(zone_code, zone_code)

            self._ax_density.plot(
                dates, densities,
                marker="o", markersize=5,
                color=color, label=label,
                linewidth=1.5,
            )

            self._ax_tv.plot(
                dates, tv_ratios,
                marker="s", markersize=5,
                color=color, label=label,
                linewidth=1.5,
            )

        if has_data:
            self._charts_placeholder.grid_remove()

            self._ax_density.set_title("Плотность (волос/см\u00b2)", fontsize=10)
            self._ax_density.set_xlabel("Дата", fontsize=9)
            self._ax_density.set_ylabel("волос/см\u00b2", fontsize=9)
            self._ax_density.legend(fontsize=8, loc="best")
            self._ax_density.tick_params(axis="x", rotation=30, labelsize=8)
            self._ax_density.grid(True, alpha=0.3)

            self._ax_tv.set_title("T/V соотношение", fontsize=10)
            self._ax_tv.set_xlabel("Дата", fontsize=9)
            self._ax_tv.set_ylabel("T/V", fontsize=9)
            self._ax_tv.legend(fontsize=8, loc="best")
            self._ax_tv.tick_params(axis="x", rotation=30, labelsize=8)
            self._ax_tv.grid(True, alpha=0.3)

            self._figure.tight_layout()
        else:
            self._charts_placeholder.grid()
            self._charts_placeholder.lift()
            self._charts_placeholder.config(text="Нет данных для построения графиков")
            self._clear_charts()

        self._canvas_widget.draw_idle()

    # ──────────────────────────── Событие вкладки ─────────────────────────────

    def on_tab_selected(self) -> None:
        """Вызывается при переключении на эту вкладку — автообновление."""
        self.refresh()

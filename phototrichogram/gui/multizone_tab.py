"""
Вкладка результатов мультизонального анализа.

Содержит MultizoneTab — визуализация результатов анализа по зонам скальпа:
карточки зон с цветовыми индикаторами по метрикам, сравнительные графики,
детали зоны по клику.

НЕ содержит диагнозов, названий болезней, рекомендаций.
Приложение — измерительный инструмент, не диагност.

Принимает dict[str, ZoneSeriesResult] из capture_tab (усреднённый по серии
снимков результат зоны + число снимков и разброс плотности).
НЕ импортирует cv2. Использует core.analyzer и core.stats.
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from phototrichogram.core.norms import NormStatus
from phototrichogram.gui.widgets import (
    ToolTip,
    format_density,
    format_tv_ratio,
    patient_norms,
)
from phototrichogram.reports.pdf_report import ReportData, generate_report

if TYPE_CHECKING:
    from phototrichogram.core.analyzer import AnalysisResult
    from phototrichogram.core.norms import PatientNorms
    from phototrichogram.data.models import Patient
    from phototrichogram.gui.app import App
    from phototrichogram.gui.series import ZoneSeriesResult

logger = logging.getLogger(__name__)

# Описания зон
_ZONE_NAMES: dict[str, str] = {
    "F": "Лобная зона",
    "P": "Теменная зона",
    "T": "Височная зона",
    "O": "Затылочная зона (контроль)",
}

# Порядок отображения зон
_ZONE_ORDER: list[str] = ["F", "P", "T", "O"]

# Цвета для зон в графиках
_ZONE_COLORS: dict[str, str] = {
    "F": "#FF6B6B",
    "P": "#4ECDC4",
    "T": "#FFD93D",
    "O": "#6BCB77",
}

# Цвета статуса
_STATUS_COLORS: dict[str, str] = {
    "good": "#4CAF50",       # зелёный — норма
    "warning": "#FF9800",    # жёлтый — внимание
    "bad": "#F44336",        # красный — проблема
    "neutral": "#9E9E9E",    # серый — нет данных / контроль
}

# Пороги сравнения зоны с контрольной затылочной зоной O (относительная
# разница плотности, %). Это НЕ референсные нормы показателей — те приходят
# из core.norms через patient_norms(активный пациент). Здесь остаются только
# пороги межзонального сравнения, специфичные для этой вкладки.
_DENSITY_DIFF_BORDERLINE = -15.0  # %, погранично: зона ниже контроля
_DENSITY_DIFF_BAD = -30.0         # %, отклонение: зона заметно ниже контроля


def _assess_status(
    norms: PatientNorms,
    density: float,
    vellus_pct: float,
    tv_ratio: float,
    density_diff_pct: float | None = None,
) -> str:
    """Оценка состояния зоны по числовым показателям.

    Статус каждого показателя берётся из персональных норм пациента
    (``NormRange.status`` → NORMAL / BORDERLINE / ABNORMAL), поэтому пороги
    раскраски не дублируются, а совпадают со справочным блоком норм. Разница
    с контролем (``density_diff_pct``) оценивается отдельными порогами
    межзонального сравнения — это не референсная норма показателя.

    Args:
        norms: Персональные референсные нормы активного пациента.
        density: Плотность волос/см².
        vellus_pct: Процент пушковых волос.
        tv_ratio: Соотношение Terminal/Vellus.
        density_diff_pct: Разница плотности с контролем (%) или None.

    Returns:
        Статус: 'good', 'warning' или 'bad'.
    """
    bad_markers = 0
    warn_markers = 0

    for norm, value in (
        (norms.density, density),
        (norms.vellus_pct, vellus_pct),
        (norms.tv_ratio, tv_ratio),
    ):
        status = norm.status(value)
        if status is NormStatus.ABNORMAL:
            bad_markers += 1
        elif status is NormStatus.BORDERLINE:
            warn_markers += 1

    if density_diff_pct is not None:
        if density_diff_pct < _DENSITY_DIFF_BAD:
            bad_markers += 1
        elif density_diff_pct < _DENSITY_DIFF_BORDERLINE:
            warn_markers += 1

    # Хотя бы один показатель за пределами нормы (ABNORMAL) → красный:
    # для скринингового инструмента спрятать реальное отклонение под жёлтым
    # опаснее, чем показать красным. Пограничные (BORDERLINE) → жёлтый.
    if bad_markers >= 1:
        return "bad"
    if warn_markers >= 1:
        return "warning"
    return "good"


class _ZoneResultCard(ttk.Frame):
    """Карточка результатов одной зоны для вкладки результатов.

    Показывает: код, цветовой индикатор, плотность, % vellus, T/V, разницу с контролем.
    """

    def __init__(
        self,
        parent: tk.Widget,
        zone_code: str,
        **kwargs: Any,
    ) -> None:
        """Инициализация карточки результата.

        Args:
            parent: Родительский виджет.
            zone_code: Код зоны (F/P/T/O).
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._zone_code = zone_code

        self.configure(relief="ridge", borderwidth=2, padding=8)
        self._create_widgets()

    def _create_widgets(self) -> None:
        """Создание виджетов карточки."""
        self.grid_columnconfigure(0, weight=1)

        # Заголовок с индикатором
        header_frame = ttk.Frame(self)
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self._indicator_canvas = tk.Canvas(
            header_frame, width=16, height=16, highlightthickness=0,
        )
        self._indicator_canvas.pack(side="left", padx=(0, 5))
        self._indicator_dot = self._indicator_canvas.create_oval(
            2, 2, 14, 14, fill=_STATUS_COLORS["neutral"], outline="",
        )

        zone_name = _ZONE_NAMES.get(self._zone_code, self._zone_code)
        self._title_label = ttk.Label(
            header_frame, text=f"{self._zone_code}", font=("", 14, "bold"),
        )
        self._title_label.pack(side="left")

        # Подзаголовок — имя зоны
        ttk.Label(
            self, text=zone_name, font=("", 8),
        ).grid(row=1, column=0, sticky="w", pady=(0, 3))

        ttk.Separator(self, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=3,
        )

        # Показатели
        self._vars: dict[str, tk.StringVar] = {}
        fields = [
            ("density", "Плотность:"),
            ("vellus", "Vellus:"),
            ("tv_ratio", "T/V:"),
            ("diff", "vs контроль:"),
        ]

        for i, (key, label) in enumerate(fields):
            row = 3 + i
            ttk.Label(self, text=label, font=("", 9)).grid(
                row=row, column=0, sticky="w",
            )
            var = tk.StringVar(value="--")
            self._vars[key] = var
            ttk.Label(
                self, textvariable=var, font=("", 10, "bold"),
            ).grid(row=row, column=0, sticky="e")

    def update_data(
        self,
        density: float,
        vellus_pct: float,
        tv_ratio: float,
        density_diff_pct: float | None = None,
        status: str = "neutral",
        density_std: float | None = None,
        n_snapshots: int = 1,
    ) -> None:
        """Обновить данные карточки.

        Args:
            density: Плотность волос/см² (среднее по серии).
            vellus_pct: Процент пушковых волос.
            tv_ratio: Соотношение T/V.
            density_diff_pct: Разница с контролем в % (None для зоны O).
            status: Статус ('good', 'warning', 'bad', 'neutral').
            density_std: Разброс плотности по серии (None для одиночного снимка).
            n_snapshots: Число снимков в серии (1 — одиночный). При >1 карточка
                показывает плотность с разбросом «250 ± 30/см²».
        """
        # Компактно на карточке — «250 ± 30/см²» без «(N снимков)»: знак «±» уже
        # сигнализирует серию, а число снимков видно в деталях зоны.
        self._vars["density"].set(format_density(density, density_std, n_snapshots))
        self._vars["vellus"].set(f"{vellus_pct:.0f}%")
        self._vars["tv_ratio"].set(format_tv_ratio(tv_ratio))

        if density_diff_pct is not None:
            sign = "+" if density_diff_pct >= 0 else ""
            self._vars["diff"].set(f"{sign}{density_diff_pct:.0f}%")
        else:
            self._vars["diff"].set("контроль")

        # Цвет индикатора
        color = _STATUS_COLORS.get(status, _STATUS_COLORS["neutral"])
        self._indicator_canvas.itemconfig(self._indicator_dot, fill=color)

    def clear_data(self) -> None:
        """Очистить данные карточки."""
        for var in self._vars.values():
            var.set("--")
        self._indicator_canvas.itemconfig(
            self._indicator_dot, fill=_STATUS_COLORS["neutral"],
        )


class MultizoneTab(ttk.Frame):
    """Вкладка результатов мультизонального анализа.

    Показывает только метрики без диагнозов и рекомендаций:
    - 4 карточки зон с цветовыми индикаторами по метрикам
    - Сравнительные графики (плотность, типы волос, гистограмма толщин)
    - Детальный просмотр статистики зоны по клику
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: Any) -> None:
        """Инициализация вкладки результатов.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app
        # Усреднённые AnalysisResult по зонам (для карточек, графиков, деталей).
        self._zone_results: dict[str, AnalysisResult] = {}
        # Метаданные серии по зонам (n_snapshots, density_std) — источник для
        # показа разброса плотности. Ключи совпадают с _zone_results.
        self._zone_series: dict[str, ZoneSeriesResult] = {}
        self._zone_cards: dict[str, _ZoneResultCard] = {}
        # id сессии, в которую сохранены показанные результаты (или None, если
        # не сохранены). Экспорт PDF строится по ЭТОЙ сессии — иначе выгрузил бы
        # чужие/старые данные, не соответствующие числам на экране.
        self._current_session_id: int | None = None

        self._create_widgets()
        logger.info("Вкладка результатов инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self.grid_columnconfigure(0, weight=1)
        # Растягивается только нижняя панель (графики + детали). Карточки зон
        # (row 0) и справочный блок норм (row 1) — фиксированной высоты,
        # всегда видимы.
        self.grid_rowconfigure(2, weight=1)

        self._create_zones_panel()
        self._create_norms_panel()
        self._create_bottom_panel()

    def _create_zones_panel(self) -> None:
        """Создание панели карточек зон (верхняя часть)."""
        zones_outer = ttk.LabelFrame(self, text="Зоны", padding=5)
        zones_outer.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 2))
        zones_outer.grid_columnconfigure(0, weight=1)
        zones_outer.grid_columnconfigure(1, weight=1)
        zones_outer.grid_columnconfigure(2, weight=1)
        zones_outer.grid_columnconfigure(3, weight=1)

        for i, zone_code in enumerate(_ZONE_ORDER):
            card = _ZoneResultCard(zones_outer, zone_code)
            card.grid(row=0, column=i, sticky="nsew", padx=3, pady=3)

            # Клик по карточке — показать детали.
            # zc=zone_code фиксирует значение зоны на текущей итерации цикла.
            def _on_card_click(event: tk.Event[Any], zc: str = zone_code) -> None:
                self._on_zone_click(zc)

            card.bind("<Button-1>", _on_card_click)
            self._zone_cards[zone_code] = card

    def _create_norms_panel(self) -> None:
        """Создание справочного блока норм (компактный, всегда видим).

        Блок располагается между карточками зон и графиками. Показывает
        ПЕРСОНАЛЬНЫЕ референсные диапазоны активного пациента (единый
        источник — ``patient_norms`` / ``core.norms``; те же ``NormRange``,
        что и раскраска индикаторов) с подписью базы подбора «Норма для: …»
        и расшифровку цветов. Только числа и цвета — без диагнозов, названий
        болезней и рекомендаций.
        """
        norms_frame = ttk.LabelFrame(self, text="Нормы (справочно)", padding=6)
        norms_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 2))
        norms_frame.grid_columnconfigure(0, weight=1)

        # База подбора норм активного пациента («Норма для: жен., 42 г., …»
        # или «Норма для: общая норма»). Обновляется при смене пациента
        # (on_tab_selected) и при показе результатов (_update_norms_panel).
        self._norms_basis_var = tk.StringVar()
        ttk.Label(
            norms_frame, textvariable=self._norms_basis_var, font=("", 9, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))

        # Строка персональных диапазонов нормы. Заполняется _update_norms_panel
        # из тех же NormRange, что и раскраска индикаторов, поэтому значения
        # всегда совпадают с логикой статусов.
        self._norms_label = ttk.Label(
            norms_frame, text="", font=("", 9), justify="left",
        )
        self._norms_label.grid(row=1, column=0, sticky="w", pady=(0, 4))

        # Легенда цветовых индикаторов с реальными маркерами _STATUS_COLORS.
        legend = self._create_color_legend(norms_frame)
        legend.grid(row=2, column=0, sticky="w", pady=(0, 4))

        # Обязательная приписка мелким шрифтом.
        ttk.Label(
            norms_frame,
            text="Значения справочные, не являются диагнозом",
            font=("", 7),
            foreground="#757575",
        ).grid(row=3, column=0, sticky="w")

        # Адаптивный перенос длинной строки норм по ширине блока — чтобы
        # справочный блок не растягивал вкладку по горизонтали.
        norms_frame.bind("<Configure>", self._on_norms_configure)

        # Первичное заполнение персональными нормами (без пациента — общая).
        self._update_norms_panel()

    def _create_color_legend(self, parent: tk.Widget) -> ttk.Frame:
        """Собрать легенду цветовых индикаторов с реальными маркерами.

        Каждый пункт — цветной квадрат (tk.Canvas в цвете из _STATUS_COLORS)
        и подпись. Цвета берутся из _STATUS_COLORS, поэтому совпадают
        с индикаторами карточек зон.

        Args:
            parent: Родительский виджет.

        Returns:
            Frame с пунктами легенды, расположенными в ряд.
        """
        legend = ttk.Frame(parent)

        legend_items: list[tuple[str, str]] = [
            ("good", "в пределах норм"),
            ("warning", "погранично"),
            ("bad", "за пределами норм"),
            ("neutral", "контроль / нет данных"),
        ]

        for status_key, caption in legend_items:
            item = ttk.Frame(legend)
            item.pack(side="left", padx=(0, 14))

            swatch = tk.Canvas(item, width=13, height=13, highlightthickness=0)
            swatch.create_rectangle(
                1, 1, 12, 12,
                fill=_STATUS_COLORS[status_key],
                outline="#616161",
            )
            swatch.pack(side="left", padx=(0, 4))

            ttk.Label(item, text=caption, font=("", 8)).pack(side="left")

        return legend

    def _on_norms_configure(self, event: tk.Event[Any]) -> None:
        """Обновить перенос строки норм при изменении ширины блока.

        Держит справочный блок компактным и не даёт длинной строке
        растягивать вкладку по горизонтали.

        Args:
            event: Событие изменения размера LabelFrame норм.
        """
        self._norms_label.configure(wraplength=max(event.width - 24, 120))

    def _current_norms(self) -> PatientNorms:
        """Персональные референсные нормы активного пациента (единый источник).

        Делегирует ``patient_norms`` (мостик к ``core.norms``). Без выбранного
        пациента возвращается общая норма (basis = «общая норма»), поэтому блок
        и раскраска работают и когда пациент не выбран.

        Returns:
            ``PatientNorms`` активного пациента или общая норма.
        """
        return patient_norms(self._app.active_patient)

    def _update_norms_panel(self, norms: PatientNorms | None = None) -> None:
        """Обновить справочный блок норм под активного пациента.

        Ставит подпись «Норма для: {basis}» и строку персональных диапазонов
        (из тех же ``NormRange``, что и раскраска). Вызывается при показе
        результатов и при переключении на вкладку (пациент мог смениться
        на другой вкладке).

        Args:
            norms: Готовые нормы, если уже вычислены вызывающей стороной;
                None — вычислить через ``_current_norms``.
        """
        if norms is None:
            norms = self._current_norms()
        self._norms_basis_var.set(f"Норма для: {norms.basis}")
        self._norms_label.configure(text=self._format_norms_text(norms))

    @staticmethod
    def _format_norms_text(norms: PatientNorms) -> str:
        """Собрать строку персональных диапазонов нормы для справочного блока.

        Диапазоны берутся из ``NormRange`` активного пациента — того же
        источника, что и раскраска индикаторов, поэтому текст и цвета всегда
        согласованы между собой.

        Args:
            norms: Персональные референсные нормы активного пациента.

        Returns:
            Однострочное описание диапазонов: плотность, vellus, T/V, толщина.
        """
        density = norms.density
        vellus = norms.vellus_pct
        tv_ratio = norms.tv_ratio
        thickness = norms.thickness_um

        # borderline_high у vellus всегда задан; fallback на high — только
        # чтобы удовлетворить строгую типизацию (float | None) без падения.
        vellus_border = (
            vellus.borderline_high if vellus.borderline_high is not None else vellus.high
        )

        return (
            f"Плотность: ≥{density.low:.0f}/см² норма, "
            f"{density.borderline_low:.0f}–{density.low:.0f} погранично"
            f"     ·     "
            f"Vellus: ≤{vellus.high:.0f}% норма, "
            f"{vellus.high:.0f}–{vellus_border:.0f}% погранично"
            f"     ·     "
            f"T/V: ≥{tv_ratio.low:.0f} норма, "
            f"{tv_ratio.borderline_low:.0f}–{tv_ratio.low:.0f} погранично"
            f"     ·     "
            f"Средняя толщина: {thickness.low:.0f}–{thickness.high:.0f} мкм"
        )

    def _create_bottom_panel(self) -> None:
        """Создание нижней панели (графики + детали зоны)."""
        bottom_frame = ttk.Frame(self)
        bottom_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=(2, 5))
        bottom_frame.grid_columnconfigure(0, weight=3)
        bottom_frame.grid_columnconfigure(1, weight=2)
        bottom_frame.grid_rowconfigure(0, weight=1)

        # Слева: график
        chart_frame = ttk.LabelFrame(bottom_frame, text="Сравнение зон", padding=5)
        chart_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        chart_frame.grid_rowconfigure(0, weight=1)
        chart_frame.grid_columnconfigure(0, weight=1)

        self._figure = Figure(figsize=(7, 4), dpi=80)
        # matplotlib backend (backend_tkagg) не типизирован → no-untyped-call
        self._chart_canvas = FigureCanvasTkAgg(  # type: ignore[no-untyped-call]
            self._figure, master=chart_frame,
        )
        self._chart_widget = self._chart_canvas.get_tk_widget()  # type: ignore[no-untyped-call]
        self._chart_widget.grid(row=0, column=0, sticky="nsew")

        self._draw_empty_chart()

        # Справа: детали зоны
        right_frame = ttk.Frame(bottom_frame)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 0))
        right_frame.grid_rowconfigure(0, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        # Детали зоны (по клику)
        detail_frame = ttk.LabelFrame(right_frame, text="Детали зоны", padding=5)
        detail_frame.grid(row=0, column=0, sticky="nsew")
        detail_frame.grid_rowconfigure(0, weight=1)
        detail_frame.grid_columnconfigure(0, weight=1)

        self._detail_text = tk.Text(
            detail_frame,
            height=8,
            wrap="word",
            font=("", 10),
            state="disabled",
            relief="flat",
        )
        self._detail_text.grid(row=0, column=0, sticky="nsew")

        # Кнопка экспорта в PDF. Активна только при наличии результатов и
        # выбранного пациента (см. _update_export_button); отчёт формируется
        # по сохранённой последней сессии пациента.
        export_frame = ttk.Frame(right_frame)
        export_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        self._export_btn = ttk.Button(
            export_frame,
            text="Экспорт в PDF",
            command=self._on_export_pdf,
            state="disabled",
        )
        self._export_btn.pack(side="right")
        ToolTip(
            self._export_btn,
            "Экспорт результатов последней сессии пациента в PDF",
        )

    # ──────────────────────────── Публичный API ───────────────────────────────

    def load_results(
        self,
        zone_results: dict[str, ZoneSeriesResult],
        session_id: int | None = None,
    ) -> None:
        """Загрузить результаты мультизонального анализа.

        Обновляет справочный блок норм, карточки зон, графики и сбрасывает
        детали. Раскраска карточек и справочные диапазоны берутся из
        персональных норм активного пациента (единый источник — core.norms).
        Показывает только метрики без диагнозов и рекомендаций.

        Каждая зона приходит как ``ZoneSeriesResult`` (усреднённый по серии
        снимков результат + число снимков и разброс плотности). Внутри карточки,
        графики и детали продолжают работать с ``AnalysisResult`` из
        ``.result``; метаданные серии хранятся отдельно (``_zone_series``) и
        используются для показа разброса плотности.

        Args:
            zone_results: Словарь {код_зоны: ZoneSeriesResult}.
            session_id: id сессии этих результатов в БД, или None (не сохранены).
                Экспорт PDF использует именно эту сессию.
        """
        self._zone_series = zone_results
        self._zone_results = {z: zsr.result for z, zsr in zone_results.items()}
        self._current_session_id = session_id

        # Единый источник норм на весь вызов: справочный блок, раскраска
        # карточек и линии норм на графике используют один PatientNorms.
        norms = self._current_norms()
        self._update_norms_panel(norms)

        # Обновить карточки зон (раскраска по персональным нормам)
        self._refresh_zone_cards(norms)

        # Обновить график (по усреднённым AnalysisResult)
        self._draw_comparison_chart(self._zone_results, norms)

        # Очистить детали
        self._set_text_widget(
            self._detail_text,
            "Нажмите на карточку зоны для просмотра деталей",
        )

        # Кнопка экспорта активна при наличии результатов и выбранного пациента.
        self._update_export_button()

        logger.info(
            "Результаты мультизонального анализа загружены: %d зон",
            len(zone_results),
        )

    def on_tab_selected(self) -> None:
        """Обновить вкладку при переключении на неё.

        Активный пациент мог смениться на другой вкладке — пересчитываем
        справочный блок норм и (если результаты уже загружены) раскраску
        карточек и линии норм на графике под текущего пациента, а также
        доступность кнопки экспорта (зависит от выбранного пациента).
        """
        norms = self._current_norms()
        self._update_norms_panel(norms)
        if self._zone_results:
            self._refresh_zone_cards(norms)
            self._draw_comparison_chart(self._zone_results, norms)
        self._update_export_button()

    # ──────────────────────────── Экспорт в PDF ───────────────────────────────

    def _update_export_button(self) -> None:
        """Обновить доступность кнопки «Экспорт в PDF».

        Кнопка активна только когда показанные результаты СОХРАНЕНЫ в сессию
        (`_current_session_id` задан): отчёт формируется по этой конкретной
        сессии. Если результаты не сохранены (анализ без пациента) или пациент
        сменился — кнопка неактивна, чтобы не выгрузить данные не из показанных.
        """
        can_export = (
            bool(self._zone_results)
            and self._current_session_id is not None
            and self._app.active_patient is not None
        )
        self._export_btn.config(state="normal" if can_export else "disabled")

    def _on_export_pdf(self) -> None:
        """Сформировать PDF-отчёт по сессии, показанной на экране.

        Отчёт строится по КОНКРЕТНОЙ сессии (`_current_session_id`), которая
        дала показанные результаты, а не по «последней сессии из базы» — иначе
        при рассинхроне (сбой сохранения, смена пациента) в PDF попали бы числа,
        не соответствующие экрану. Если результаты не сохранены — пользователь
        предупреждается, экспорт не выполняется.

        Ошибки подготовки данных и генерации PDF показываются через messagebox
        и пишутся в лог; приложение не падает.
        """
        patient = self._app.active_patient
        if patient is None:
            messagebox.showwarning(
                "Экспорт в PDF",
                "Выберите пациента на вкладке «Пациенты».",
                parent=self,
            )
            return

        if self._current_session_id is None:
            messagebox.showwarning(
                "Экспорт в PDF",
                "Показанные результаты не сохранены в базу\n"
                "(анализ без выбранного пациента или ошибка сохранения).\n\n"
                "Выберите пациента и выполните анализ заново — тогда результаты "
                "сохранятся и их можно будет выгрузить в отчёт.",
                parent=self,
            )
            return

        try:
            report_data = self._build_report_data(patient, self._current_session_id)
        except Exception as e:
            logger.error("Ошибка подготовки данных отчёта: %s", e)
            messagebox.showerror(
                "Экспорт в PDF",
                f"Не удалось подготовить данные отчёта:\n{e}",
                parent=self,
            )
            return

        if report_data is None:
            messagebox.showwarning(
                "Экспорт в PDF",
                "Не удалось загрузить сохранённую сессию для отчёта.",
                parent=self,
            )
            return

        # Стартовая папка диалога — настроенная папка отчётов
        # (config.app.reports_dir). Создаём заранее, чтобы диалог открылся
        # именно в ней; сбой создания не критичен — просто откроется в другом.
        reports_dir = self._app.config.app.reports_dir
        try:
            Path(reports_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Не удалось создать папку отчётов %s: %s", reports_dir, e)

        output = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить отчёт PDF",
            initialdir=reports_dir,
            defaultextension=".pdf",
            initialfile=self._default_report_filename(patient, report_data.today_iso),
            filetypes=[("PDF", "*.pdf")],
        )
        if not output:
            return

        output_path = Path(output)
        self._app.status_bar.set_busy("Формирование отчёта...")
        # Форсируем перерисовку статусбара до синхронной генерации PDF.
        self.update_idletasks()

        try:
            generate_report(report_data, output_path)
        except Exception as e:
            logger.error("Ошибка генерации PDF-отчёта: %s", e)
            self._app.status_bar.set_error("Не удалось сформировать отчёт")
            messagebox.showerror(
                "Экспорт в PDF",
                f"Не удалось сформировать отчёт:\n{e}",
                parent=self,
            )
            return

        self._app.status_bar.set_ready(f"Отчёт сохранён: {output_path}")
        logger.info("PDF-отчёт сохранён: %s", output_path)
        messagebox.showinfo(
            "Экспорт в PDF",
            f"Отчёт сохранён:\n{output_path}",
            parent=self,
        )

    def _build_report_data(self, patient: Patient, session_id: int) -> ReportData | None:
        """Собрать данные отчёта по КОНКРЕТНОЙ сессии (показанной на экране).

        Берёт измерения именно этой сессии (session_id), её калибровку и дату.
        Дата сессии идёт в отчёт как «дата исследования» отдельно от даты
        формирования. Калибровка — строго из сессии: если записи нет, передаётся
        None (отчёт покажет «не откалибровано»), НЕ подставляется чужая — иначе
        в документ попал бы масштаб, которым не считались числа. Снимки зоны —
        размеченный при наличии, иначе исходный. Нормы — персональные
        (``patient_norms``).

        Args:
            patient: Активный пациент.
            session_id: id сессии, давшей показанные результаты.

        Returns:
            Готовый ``ReportData`` либо None, если сессия/измерения не найдены.
        """
        db = self._app.db
        session = db.get_session(session_id)
        if session is None:
            return None

        measurements = db.get_session_measurements(session_id)
        if not measurements:
            return None

        zone_measurements = {m.zone: m for m in measurements}

        # Калибровка строго этой сессии; нет записи → None (не подставляем чужую).
        calibration = db.get_calibration(session.calibration_id)

        # Пути снимков по зонам: размеченный при наличии, иначе исходный.
        # Несуществующие файлы отсеет сам генератор отчёта.
        zone_images: dict[str, list[Path]] = {}
        for m in measurements:
            if m.id is None:
                continue
            paths = [
                Path(img.annotated_image_path or img.image_path)
                for img in db.get_measurement_images(m.id)
                if (img.annotated_image_path or img.image_path)
            ]
            if paths:
                zone_images[m.zone] = paths

        return ReportData(
            patient=patient,
            today_iso=datetime.now().strftime("%Y-%m-%d"),
            zone_measurements=zone_measurements,
            calibration=calibration,
            norms=patient_norms(patient),
            zone_images=zone_images,
            session_date=session.date,
        )

    @staticmethod
    def _default_report_filename(patient: Patient, today_iso: str) -> str:
        """Имя файла отчёта по умолчанию «отчёт_Фамилия_ГГГГ-ММ-ДД.pdf».

        Фамилия — первое слово из ФИО; разделители пути из неё убираются,
        чтобы значение оставалось именем файла, а не путём.

        Args:
            patient: Пациент (источник ФИО).
            today_iso: Дата отчёта (ISO 8601) для суффикса имени.

        Returns:
            Имя файла с расширением .pdf.
        """
        parts = patient.name.split()
        surname = parts[0] if parts else "пациент"
        surname = surname.replace("/", "_").replace("\\", "_")
        return f"отчёт_{surname}_{today_iso}.pdf"

    # ──────────────────────────── Внутренняя логика ───────────────────────────

    def _refresh_zone_cards(self, norms: PatientNorms) -> None:
        """Обновить карточки зон и их раскраску под текущие нормы.

        Раскраска статусов берётся из ``NormRange.status`` персональных норм,
        поэтому при смене пациента цвета обновляются вместе со справочным
        блоком. Контрольная зона O всегда нейтральна.

        Args:
            norms: Персональные референсные нормы активного пациента.
        """
        density_diffs = self._calculate_density_diffs(self._zone_results)

        for zone_code in _ZONE_ORDER:
            card = self._zone_cards[zone_code]
            if zone_code in self._zone_results:
                r = self._zone_results[zone_code]
                diff = density_diffs.get(zone_code)

                status = _assess_status(
                    norms,
                    density=r.density_per_cm2,
                    vellus_pct=r.ratios.vellus_pct,
                    tv_ratio=r.ratios.tv_ratio,
                    density_diff_pct=diff,
                )

                # Для контрольной зоны — всегда нейтральный статус
                if zone_code == "O":
                    status = "neutral"

                # Метаданные серии зоны (число снимков, разброс плотности) —
                # для показа «250 ± 30/см²» на карточке при серии из >1 снимка.
                series = self._zone_series.get(zone_code)
                card.update_data(
                    density=r.density_per_cm2,
                    vellus_pct=r.ratios.vellus_pct,
                    tv_ratio=r.ratios.tv_ratio,
                    density_diff_pct=diff,
                    status=status,
                    density_std=series.density_std if series is not None else None,
                    n_snapshots=series.n_snapshots if series is not None else 1,
                )
            else:
                card.clear_data()

    def _calculate_density_diffs(
        self,
        zone_results: dict[str, AnalysisResult],
    ) -> dict[str, float]:
        """Рассчитать разницу плотности с контрольной зоной.

        Args:
            zone_results: Результаты анализа по зонам.

        Returns:
            Словарь {код_зоны: разница_в_процентах} (без зоны O).
        """
        diffs: dict[str, float] = {}

        if "O" not in zone_results:
            return diffs

        control_density = zone_results["O"].density_per_cm2
        if control_density <= 0:
            return diffs

        for code, r in zone_results.items():
            if code != "O":
                diff = (r.density_per_cm2 - control_density) / control_density * 100.0
                diffs[code] = round(diff, 1)

        return diffs

    def _set_text_widget(self, widget: tk.Text, text: str) -> None:
        """Установить текст в виджет Text (readonly).

        Args:
            widget: Виджет tk.Text.
            text: Текст для установки.
        """
        widget.config(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.config(state="disabled")

    # ──────────────────────────── Детали зоны ─────────────────────────────────

    def _on_zone_click(self, zone_code: str) -> None:
        """Показать детали зоны по клику на карточку.

        Args:
            zone_code: Код зоны.
        """
        if zone_code not in self._zone_results:
            self._set_text_widget(self._detail_text, f"Зона {zone_code}: нет данных")
            return

        result = self._zone_results[zone_code]

        # Показать детальную информацию
        r = result
        t = r.thickness
        ratios = r.ratios

        # Плотность зоны с разбросом по серии: «250 ± 30/см² (5 снимков)».
        series = self._zone_series.get(zone_code)
        n_snap = series.n_snapshots if series is not None else 1
        dens_std = series.density_std if series is not None else None
        density_text = format_density(
            r.density_per_cm2, dens_std, n_snap, with_count=True,
        )

        info_lines = [
            f"Зона {zone_code} — {_ZONE_NAMES.get(zone_code, '')}",
            "",
            f"Волос: {r.hair_count}",
            f"Плотность: {density_text}",
            "",
            f"Terminal: {ratios.terminal_count} ({ratios.terminal_pct:.0f}%)",
            f"Vellus: {ratios.vellus_count} ({ratios.vellus_pct:.0f}%)",
            f"Intermediate: {ratios.intermediate_count} ({ratios.intermediate_pct:.0f}%)",
            f"T/V: {format_tv_ratio(ratios.tv_ratio)}",
            "",
            f"Толщина: {t.mean:.1f} \u00b1 {t.std:.1f} мкм",
            f"Медиана: {t.median:.1f} мкм",
            f"Диапазон: {t.min:.1f} \u2014 {t.max:.1f} мкм",
        ]

        self._set_text_widget(self._detail_text, "\n".join(info_lines))

    # ──────────────────────────── Графики ─────────────────────────────────────

    def _draw_empty_chart(self) -> None:
        """Нарисовать пустой график (до загрузки данных)."""
        self._figure.clear()
        ax = self._figure.add_subplot(111)
        ax.text(
            0.5, 0.5,
            "Нет данных\nЗагрузите снимки и запустите анализ",
            transform=ax.transAxes,
            ha="center", va="center",
            fontsize=12, color="gray",
        )
        ax.set_axis_off()
        self._figure.tight_layout(pad=2.0)
        self._chart_canvas.draw()  # type: ignore[no-untyped-call]

    def _draw_comparison_chart(
        self, zone_results: dict[str, AnalysisResult], norms: PatientNorms,
    ) -> None:
        """Нарисовать сравнительную диаграмму по зонам.

        Три subplot'а:
        1. Плотность (волос/см²) — столбчатая (с линиями персональных норм)
        2. Terminal / Vellus / Intermediate (%) — stacked bar
        3. Гистограмма толщин по зонам — overlapping

        Args:
            zone_results: Результаты анализа по зонам.
            norms: Персональные референсные нормы активного пациента; задают
                линии нормы/пограничной плотности на первом subplot'е.
        """
        if not zone_results:
            self._draw_empty_chart()
            return

        self._figure.clear()

        # Зоны в стандартном порядке (только имеющиеся)
        zones = [z for z in _ZONE_ORDER if z in zone_results]
        if not zones:
            self._draw_empty_chart()
            return

        colors = [_ZONE_COLORS.get(z, "#888888") for z in zones]

        # === Subplot 1: Плотность ===
        ax1 = self._figure.add_subplot(131)
        densities = [zone_results[z].density_per_cm2 for z in zones]
        bars = ax1.bar(zones, densities, color=colors, edgecolor="white", linewidth=0.5)

        # Подписи значений на столбцах
        for bar, val in zip(bars, densities, strict=True):
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 2,
                f"{val:.0f}",
                ha="center", va="bottom", fontsize=8,
            )

        # Горизонтальные линии персональных норм плотности
        ax1.axhline(y=norms.density.low, color="blue", linestyle="--",
                     linewidth=0.8, alpha=0.6)
        ax1.axhline(y=norms.density.borderline_low, color="red", linestyle="--",
                     linewidth=0.8, alpha=0.6)

        ax1.set_title("Плотность\n(волос/см\u00B2)", fontsize=9)
        ax1.set_ylabel("волос/см\u00B2", fontsize=8)
        ax1.tick_params(axis="both", labelsize=8)

        # === Subplot 2: Типы волос (%) ===
        ax2 = self._figure.add_subplot(132)
        terminal_pcts = [zone_results[z].ratios.terminal_pct for z in zones]
        intermediate_pcts = [zone_results[z].ratios.intermediate_pct for z in zones]
        vellus_pcts = [zone_results[z].ratios.vellus_pct for z in zones]

        x_pos = range(len(zones))
        bar_width = 0.6

        ax2.bar(x_pos, terminal_pcts, bar_width, label="Terminal", color="#6BCB77")
        ax2.bar(
            x_pos, intermediate_pcts, bar_width,
            bottom=terminal_pcts, label="Intermediate", color="#FFD93D",
        )
        bottoms = [t + i for t, i in zip(terminal_pcts, intermediate_pcts, strict=True)]
        ax2.bar(
            x_pos, vellus_pcts, bar_width,
            bottom=bottoms, label="Vellus", color="#FF6B6B",
        )

        ax2.set_xticks(x_pos)
        ax2.set_xticklabels(zones)
        ax2.set_title("Типы волос (%)", fontsize=9)
        ax2.set_ylabel("%", fontsize=8)
        ax2.legend(fontsize=7, loc="upper right")
        ax2.tick_params(axis="both", labelsize=8)
        ax2.set_ylim(0, 105)

        # === Subplot 3: Гистограмма толщин ===
        ax3 = self._figure.add_subplot(133)

        vellus_threshold = self._app.config.analysis.vellus_threshold_um

        for z in zones:
            r = zone_results[z]
            if r.measurements:
                thicknesses = [m.thickness_um for m in r.measurements]
                ax3.hist(
                    thicknesses,
                    bins=range(0, int(max(thicknesses)) + 10, 5),
                    alpha=0.5,
                    label=z,
                    color=_ZONE_COLORS.get(z, "#888888"),
                )

        ax3.axvline(
            x=vellus_threshold, color="red", linestyle="--", linewidth=0.8,
        )
        ax3.set_title("Толщина (мкм)", fontsize=9)
        ax3.set_xlabel("мкм", fontsize=8)
        ax3.set_ylabel("Количество", fontsize=8)
        ax3.legend(fontsize=7)
        ax3.tick_params(axis="both", labelsize=8)

        self._figure.tight_layout(pad=1.5)
        self._chart_canvas.draw()  # type: ignore[no-untyped-call]

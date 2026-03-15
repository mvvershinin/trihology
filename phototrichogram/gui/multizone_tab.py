"""
Вкладка результатов мультизонального анализа.

Содержит MultizoneTab — визуализация результатов анализа по зонам скальпа:
карточки зон с цветовыми индикаторами по метрикам, сравнительные графики,
детали зоны по клику.

НЕ содержит диагнозов, названий болезней, рекомендаций.
Приложение — измерительный инструмент, не диагност.

Принимает dict[str, AnalysisResult] из capture_tab.
НЕ импортирует cv2. Использует core.analyzer и core.stats.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from phototrichogram.gui.widgets import ToolTip

if TYPE_CHECKING:
    from phototrichogram.core.analyzer import AnalysisResult
    from phototrichogram.gui.app import App

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

# Референсные нормы из METHODOLOGY.md §2.2
_DENSITY_NORM_MIN = 150.0       # волос/см²
_DENSITY_BORDERLINE_MIN = 100.0
_VELLUS_PCT_NORM_MAX = 20.0     # %
_VELLUS_PCT_BORDERLINE_MAX = 30.0
_TV_RATIO_NORM_MIN = 4.0
_TV_RATIO_BORDERLINE_MIN = 2.0
_DENSITY_DIFF_BORDERLINE = -15.0  # %
_DENSITY_DIFF_BAD = -30.0


def _assess_status(
    density: float,
    vellus_pct: float,
    tv_ratio: float,
    density_diff_pct: float | None = None,
) -> str:
    """Оценка состояния зоны по числовым показателям.

    Возвращает цветовой статус на основе метрик, без привязки к диагнозам.

    Args:
        density: Плотность волос/см².
        vellus_pct: Процент пушковых волос.
        tv_ratio: Соотношение Terminal/Vellus.
        density_diff_pct: Разница плотности с контролем (%) или None.

    Returns:
        Статус: 'good', 'warning' или 'bad'.
    """
    bad_markers = 0
    warn_markers = 0

    if density < _DENSITY_BORDERLINE_MIN:
        bad_markers += 1
    elif density < _DENSITY_NORM_MIN:
        warn_markers += 1

    if vellus_pct > _VELLUS_PCT_BORDERLINE_MAX:
        bad_markers += 1
    elif vellus_pct > _VELLUS_PCT_NORM_MAX:
        warn_markers += 1

    if tv_ratio < _TV_RATIO_BORDERLINE_MIN:
        bad_markers += 1
    elif tv_ratio < _TV_RATIO_NORM_MIN:
        warn_markers += 1

    if density_diff_pct is not None:
        if density_diff_pct < _DENSITY_DIFF_BAD:
            bad_markers += 1
        elif density_diff_pct < _DENSITY_DIFF_BORDERLINE:
            warn_markers += 1

    if bad_markers >= 2:
        return "bad"
    if bad_markers >= 1 or warn_markers >= 2:
        return "warning"
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
        **kwargs: object,
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
    ) -> None:
        """Обновить данные карточки.

        Args:
            density: Плотность волос/см².
            vellus_pct: Процент пушковых волос.
            tv_ratio: Соотношение T/V.
            density_diff_pct: Разница с контролем в % (None для зоны O).
            status: Статус ('good', 'warning', 'bad', 'neutral').
        """
        self._vars["density"].set(f"{density:.0f}/см\u00B2")
        self._vars["vellus"].set(f"{vellus_pct:.0f}%")
        self._vars["tv_ratio"].set(f"{tv_ratio:.1f}")

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

    def __init__(self, parent: tk.Widget, app: App, **kwargs: object) -> None:
        """Инициализация вкладки результатов.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app
        self._zone_results: dict[str, AnalysisResult] = {}
        self._zone_cards: dict[str, _ZoneResultCard] = {}

        self._create_widgets()
        logger.info("Вкладка результатов инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._create_zones_panel()
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
            # Клик по карточке — показать детали
            card.bind("<Button-1>", lambda e, zc=zone_code: self._on_zone_click(zc))
            self._zone_cards[zone_code] = card

    def _create_bottom_panel(self) -> None:
        """Создание нижней панели (графики + детали зоны)."""
        bottom_frame = ttk.Frame(self)
        bottom_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(2, 5))
        bottom_frame.grid_columnconfigure(0, weight=3)
        bottom_frame.grid_columnconfigure(1, weight=2)
        bottom_frame.grid_rowconfigure(0, weight=1)

        # Слева: график
        chart_frame = ttk.LabelFrame(bottom_frame, text="Сравнение зон", padding=5)
        chart_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        chart_frame.grid_rowconfigure(0, weight=1)
        chart_frame.grid_columnconfigure(0, weight=1)

        self._figure = Figure(figsize=(7, 4), dpi=80)
        self._chart_canvas = FigureCanvasTkAgg(self._figure, master=chart_frame)
        self._chart_widget = self._chart_canvas.get_tk_widget()
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

        # Кнопка экспорта (placeholder)
        export_frame = ttk.Frame(right_frame)
        export_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        self._export_btn = ttk.Button(
            export_frame,
            text="Экспорт отчёта (в разработке)",
            state="disabled",
        )
        self._export_btn.pack(side="right")
        ToolTip(self._export_btn, "Экспорт результатов в PDF (в разработке)")

    # ──────────────────────────── Публичный API ───────────────────────────────

    def load_results(self, zone_results: dict[str, AnalysisResult]) -> None:
        """Загрузить результаты мультизонального анализа.

        Обновляет карточки зон, графики и сбрасывает детали.
        Показывает только метрики без диагнозов и рекомендаций.

        Args:
            zone_results: Словарь {код_зоны: AnalysisResult}.
        """
        self._zone_results = zone_results

        # Рассчитать сравнение с контролем
        density_diffs = self._calculate_density_diffs(zone_results)

        # Обновить карточки зон
        for zone_code in _ZONE_ORDER:
            card = self._zone_cards[zone_code]
            if zone_code in zone_results:
                r = zone_results[zone_code]
                diff = density_diffs.get(zone_code)

                status = _assess_status(
                    density=r.density_per_cm2,
                    vellus_pct=r.ratios.vellus_pct,
                    tv_ratio=r.ratios.tv_ratio,
                    density_diff_pct=diff,
                )

                # Для контрольной зоны — всегда нейтральный статус
                if zone_code == "O":
                    status = "neutral"

                card.update_data(
                    density=r.density_per_cm2,
                    vellus_pct=r.ratios.vellus_pct,
                    tv_ratio=r.ratios.tv_ratio,
                    density_diff_pct=diff,
                    status=status,
                )
            else:
                card.clear_data()

        # Обновить график
        self._draw_comparison_chart(zone_results)

        # Очистить детали
        self._set_text_widget(
            self._detail_text,
            "Нажмите на карточку зоны для просмотра деталей",
        )

        # Активировать экспорт (placeholder)
        self._export_btn.config(state="disabled")

        logger.info(
            "Результаты мультизонального анализа загружены: %d зон",
            len(zone_results),
        )

    # ──────────────────────────── Внутренняя логика ───────────────────────────

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

        info_lines = [
            f"Зона {zone_code} — {_ZONE_NAMES.get(zone_code, '')}",
            "",
            f"Волос: {r.hair_count}",
            f"Плотность: {r.density_per_cm2:.0f}/см\u00B2",
            "",
            f"Terminal: {ratios.terminal_count} ({ratios.terminal_pct:.0f}%)",
            f"Vellus: {ratios.vellus_count} ({ratios.vellus_pct:.0f}%)",
            f"Intermediate: {ratios.intermediate_count} ({ratios.intermediate_pct:.0f}%)",
            f"T/V: {ratios.tv_ratio:.1f}",
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
        self._chart_canvas.draw()

    def _draw_comparison_chart(self, zone_results: dict[str, AnalysisResult]) -> None:
        """Нарисовать сравнительную диаграмму по зонам.

        Три subplot'а:
        1. Плотность (волос/см²) — столбчатая
        2. Terminal / Vellus / Intermediate (%) — stacked bar
        3. Гистограмма толщин по зонам — overlapping

        Args:
            zone_results: Результаты анализа по зонам.
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
        for bar, val in zip(bars, densities):
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 2,
                f"{val:.0f}",
                ha="center", va="bottom", fontsize=8,
            )

        # Горизонтальные линии норм
        ax1.axhline(y=_DENSITY_NORM_MIN, color="blue", linestyle="--",
                     linewidth=0.8, alpha=0.6)
        ax1.axhline(y=_DENSITY_BORDERLINE_MIN, color="red", linestyle="--",
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
        bottoms = [t + i for t, i in zip(terminal_pcts, intermediate_pcts)]
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
        self._chart_canvas.draw()

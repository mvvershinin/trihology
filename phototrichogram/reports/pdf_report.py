"""
Генератор PDF-отчётов трихоскопического исследования.

Чистый модуль слоя ``reports``: принимает уже подготовленные данные
(dataclasses из ``core`` и ``data``) и формирует PDF средствами fpdf2.
Модуль НЕ обращается к базе данных и НЕ импортирует ``gui`` — данные для
отчёта собирает и передаёт вызывающая сторона (GUI), поэтому генерацию
можно тестировать без БД и без интерфейса.

Отчёт — измерительный документ: только измеренные показатели и справочные
референсные нормы. Диагнозов, названий заболеваний и рекомендаций отчёт
не содержит (см. METHODOLOGY.md §9).

Кириллица:
    Встроенные шрифты fpdf2 (Helvetica и пр.) не содержат кириллицы, поэтому
    подключаются TrueType-шрифты DejaVu Sans (обычный и жирный) из
    ``assets/fonts`` через :func:`phototrichogram.utils.resources.resource_path`
    (работает и из репозитория, и в сборке PyInstaller). В fpdf2 2.x параметр
    ``uni=True`` не требуется — Unicode включён для TTF по умолчанию.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from PIL import Image

from phototrichogram.core.norms import (
    Ethnicity,
    NormRange,
    NormStatus,
    PatientNorms,
    age_from_birthdate,
)
from phototrichogram.core.stats import TV_RATIO_MAX
from phototrichogram.data.models import Calibration, Measurement, Patient
from phototrichogram.utils.resources import resource_path

logger = logging.getLogger(__name__)

__all__ = ["ReportData", "format_tv_ratio", "generate_report"]


# ===========================================================================
# Входные данные отчёта
# ===========================================================================


@dataclass
class ReportData:
    """Подготовленные данные для одного PDF-отчёта.

    Все значения передаются вызывающей стороной (GUI): модуль отчётов не
    обращается ни к БД, ни к системным часам. Отсутствующие данные (нет
    калибровки, нет снимков, зона не снята) допустимы — отчёт формируется
    без падения, недостающее показывается как «—» или пропускается.

    Attributes:
        patient: Профиль пациента (ФИО, пол, дата рождения, этнос, заметки).
        today_iso: Дата формирования отчёта (ISO 8601, YYYY-MM-DD). Передаётся
            явно (не из системных часов) — детерминируемость и тестируемость.
        session_date: Дата исследования (когда сделаны измерения) или None.
            Для меддокумента критично отделять дату съёмки от даты печати:
            отчёт строится по сохранённой сессии, которая может быть давней.
        zone_measurements: Итоговые измерения последней сессии по зонам,
            ``{"F": Measurement, "O": Measurement, ...}``. Может быть пустым.
        calibration: Профиль калибровки микроскопа или None (не откалибровано).
        norms: Персональные референсные нормы пациента
            (``core.norms.select_norms``).
        zone_images: Пути к аннотированным снимкам по зонам,
            ``{"F": [Path, ...], ...}``. Может быть пустым; несуществующие
            файлы пропускаются.
        control_zone: Код контрольной зоны (затылок) для сравнения плотности.
            По умолчанию ``"O"``.
    """

    patient: Patient
    today_iso: str
    zone_measurements: dict[str, Measurement]
    calibration: Calibration | None
    norms: PatientNorms
    zone_images: dict[str, list[Path]] = field(default_factory=dict)
    session_date: str | None = None
    control_zone: str = "O"


# ===========================================================================
# Константы оформления и справочные словари
# ===========================================================================

# Порядок вывода зон в таблице и на снимках (METHODOLOGY.md §2.3).
_ZONE_ORDER: tuple[str, ...] = ("F", "P", "T", "O")

# Названия зон на русском (в нижнем регистре — для подписей «Зона F — лобная»).
_ZONE_NAMES: dict[str, str] = {
    "F": "лобная",
    "P": "теменная",
    "T": "височная",
    "O": "затылочная",
}

# Человекочитаемые названия этносов (ключ — нормализованный Ethnicity из норм).
_ETHNICITY_RU: dict[Ethnicity, str] = {
    Ethnicity.CAUCASIAN: "европеоид",
    Ethnicity.HISPANIC: "латиноамериканец",
    Ethnicity.AFRICAN: "африканец",
    Ethnicity.EAST_ASIAN: "восточноазиат",
    Ethnicity.SOUTH_ASIAN: "южноазиат",
    Ethnicity.UNSPECIFIED: "не указан",
}

# Отображение кода пола data-слоя ('M'/'F') в русскую букву.
_GENDER_RU: dict[str, str] = {"M": "М", "F": "Ж"}

# Цвета статуса относительно нормы (NormRange.status → RGB).
_STATUS_COLORS: dict[NormStatus, tuple[int, int, int]] = {
    NormStatus.NORMAL: (34, 139, 34),      # зелёный
    NormStatus.BORDERLINE: (200, 140, 0),  # жёлто-оранжевый
    NormStatus.ABNORMAL: (200, 40, 40),    # красный
}
_NEUTRAL_COLOR: tuple[int, int, int] = (90, 90, 90)   # нет данных по показателю
_BLACK: tuple[int, int, int] = (0, 0, 0)
_GRAY: tuple[int, int, int] = (110, 110, 110)

# Шрифты (кириллица).
_FONT_FAMILY = "DejaVu"
_FONT_REGULAR_REL = "assets/fonts/DejaVuSans.ttf"
_FONT_BOLD_REL = "assets/fonts/DejaVuSans-Bold.ttf"
_FALLBACK_FAMILY = "Helvetica"

# Геометрия страницы A4 (мм).
_MARGIN_LR = 15.0
_MARGIN_TOP = 12.0
_MARGIN_BOTTOM = 18.0
_CONTENT_W = 210.0 - 2 * _MARGIN_LR

# Таблица метрик: ширины столбцов (сумма = _CONTENT_W = 180).
_TABLE_COL_W: tuple[float, ...] = (30.0, 40.0, 28.0, 24.0, 28.0, 30.0)
_TABLE_HEADERS: tuple[str, ...] = (
    "Зона",
    "Плотность/см²",
    "Толщина, мкм",
    "Vellus %",
    "T/V",
    "Снимков",
)
_TABLE_ROW_H = 7.0
_TABLE_HEADER_FILL: tuple[int, int, int] = (230, 230, 230)

# Снимки.
_IMG_MAX_PER_ROW = 2
_IMG_GAP = 4.0
_IMG_MAX_H = 58.0
_IMG_CAPTION_H = 6.0
_IMG_BORDER_COLOR: tuple[int, int, int] = (185, 185, 185)

_FOOTER_TEXT = (
    "Значения справочные, не являются диагнозом. "
    "Приложение — измерительный инструмент."
)


# ===========================================================================
# Форматтеры (публичный format_tv_ratio + внутренние)
# ===========================================================================


def format_tv_ratio(tv_ratio: float) -> str:
    """Текст показа соотношения T/V для отчёта.

    Собственный форматтер слоя ``reports`` (аналог из ``gui`` недоступен —
    его нельзя импортировать через границу слоёв). Когда пушковых волос нет
    вообще, ядро возвращает условный максимум ``TV_RATIO_MAX``; показывать
    его числом бессмысленно — пишем словами.

    Args:
        tv_ratio: Соотношение Terminal/Vellus из ``core.stats``.

    Returns:
        Число с одним знаком после запятой либо «нет пушковых».
    """
    if tv_ratio >= TV_RATIO_MAX:
        return "нет пушковых"
    return f"{tv_ratio:.1f}"


def _plural_years(n: int) -> str:
    """Русское склонение слова «год» по числу лет.

    Args:
        n: Число полных лет.

    Returns:
        «год» / «года» / «лет» по правилам русского языка.
    """
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 14:
        return "лет"
    if n1 == 1:
        return "год"
    if 2 <= n1 <= 4:
        return "года"
    return "лет"


def _format_age(birth_date_iso: str | None, today_iso: str) -> str:
    """Возраст пациента в формате «N лет» или «—».

    Args:
        birth_date_iso: Дата рождения (ISO 8601) или None.
        today_iso: Опорная дата отчёта (ISO 8601).

    Returns:
        Строка «42 года» / «5 лет» / «1 год»; «—» если возраст не определён
        (нет даты рождения, некорректные даты, дата в будущем).
    """
    try:
        age = age_from_birthdate(birth_date_iso, today_iso)
    except ValueError:
        # today_iso вне контракта (некорректная дата) — не роняем отчёт.
        logger.warning("Некорректная дата отчёта today_iso=%r", today_iso)
        return "—"
    if age is None:
        return "—"
    return f"{age} {_plural_years(age)}"


def _format_gender(gender: str | None) -> str:
    """Пол пациента как русская буква «М»/«Ж» или «—»."""
    if gender is None:
        return "—"
    return _GENDER_RU.get(gender.strip().upper(), "—")


def _format_calibration(calibration: Calibration | None) -> str:
    """Масштаб калибровки «5.9 мкм/px» или «не откалибровано»."""
    if calibration is None:
        return "не откалибровано"
    return f"{calibration.um_per_px:.1f} мкм/px"


def _format_density_cell(m: Measurement) -> str:
    """Плотность зоны с разбросом по серии, если снимков > 1.

    Args:
        m: Измерение зоны.

    Returns:
        «250» для одиночного снимка либо «250 ± 30» для серии
        (n_snapshots > 1 и известен density_std).
    """
    if m.n_snapshots > 1 and m.density_std is not None:
        return f"{m.density_per_cm2:.0f} ± {m.density_std:.0f}"
    return f"{m.density_per_cm2:.0f}"


def _metric_color(
    norm: NormRange, value: float, hair_count: int
) -> tuple[int, int, int]:
    """Цвет значения показателя по его статусу относительно нормы.

    Args:
        norm: Референсный диапазон показателя.
        value: Измеренное значение.
        hair_count: Число волос в зоне (0 → показатель не определён → нейтраль).

    Returns:
        RGB-цвет: зелёный/жёлтый/красный по ``NormRange.status`` либо
        нейтрально-серый при отсутствии волос в зоне.
    """
    if hair_count <= 0:
        return _NEUTRAL_COLOR
    return _STATUS_COLORS[norm.status(value)]


def _zone_metric_color(
    norm: NormRange, value: float, hair_count: int, is_control: bool
) -> tuple[int, int, int]:
    """Цвет значения зоны с учётом контрольной зоны.

    Контрольная зона (затылок) — эталон самого пациента, не оценивается по
    популяционным нормам (нейтральный цвет, как индикатор на экране). Иначе у
    пациента с изначально низкой нормой контроль ложно краснел бы.

    Args:
        norm: Референсный диапазон показателя.
        value: Измеренное значение.
        hair_count: Число волос в зоне.
        is_control: True для контрольной зоны (O).

    Returns:
        RGB-цвет значения.
    """
    if is_control:
        return _BLACK
    return _metric_color(norm, value, hair_count)


# ===========================================================================
# PDF-документ с постраничным подвалом
# ===========================================================================


class _ReportPDF(FPDF):
    """FPDF с русским подвалом-дисклеймером на каждой странице.

    Атрибут ``report_font`` — разрешённое имя семейства (DejaVu или fallback);
    отдельное имя, чтобы не конфликтовать с внутренним ``FPDF.font_family``
    (текущий выбранный шрифт).
    """

    def __init__(self) -> None:
        """Инициализация A4-портрет, поля, автоперенос, подключение шрифтов."""
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(_MARGIN_LR, _MARGIN_TOP, _MARGIN_LR)
        self.set_auto_page_break(auto=True, margin=_MARGIN_BOTTOM)
        self.report_font: str = _register_fonts(self)

    def footer(self) -> None:
        """Подвал: справочный дисклеймер серым мелким шрифтом."""
        self.set_y(-14)
        self.set_font(self.report_font, "", 7)
        self.set_text_color(*_GRAY)
        self.multi_cell(
            0, 4, _FOOTER_TEXT, align="C", new_x=XPos.LMARGIN, new_y=YPos.TOP
        )
        self.set_text_color(*_BLACK)


# ===========================================================================
# Подключение шрифтов
# ===========================================================================


def _register_fonts(pdf: FPDF) -> str:
    """Подключение кириллических шрифтов DejaVu (обычный + жирный).

    Args:
        pdf: Документ, к которому подключаются шрифты.

    Returns:
        Имя семейства для дальнейшего ``set_font``: ``"DejaVu"`` при успехе
        либо ``"Helvetica"`` (fallback), если файлы шрифтов недоступны —
        генерация не прерывается, но кириллица во fallback не отобразится.
    """
    regular = resource_path(_FONT_REGULAR_REL)
    bold = resource_path(_FONT_BOLD_REL)
    if not regular.exists() or not bold.exists():
        logger.warning(
            "Шрифты DejaVu не найдены (%s / %s) — откат на %s без кириллицы",
            regular, bold, _FALLBACK_FAMILY,
        )
        return _FALLBACK_FAMILY
    try:
        pdf.add_font(_FONT_FAMILY, "", str(regular))
        pdf.add_font(_FONT_FAMILY, "B", str(bold))
    except (OSError, RuntimeError) as exc:
        logger.warning("Не удалось подключить шрифты DejaVu: %s — откат", exc)
        return _FALLBACK_FAMILY
    return _FONT_FAMILY


# ===========================================================================
# Секции отчёта
# ===========================================================================


def _draw_title(pdf: FPDF, fam: str, today_iso: str, session_date: str | None) -> None:
    """Заголовок отчёта, дата исследования и дата формирования."""
    pdf.set_font(fam, "B", 15)
    pdf.set_text_color(*_BLACK)
    pdf.multi_cell(
        0, 8, "Отчёт фототрихографического исследования",
        align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    # Дата исследования (когда снято) — отдельно от даты печати отчёта.
    pdf.set_font(fam, "", 9)
    pdf.set_text_color(*_GRAY)
    study = session_date if session_date else "—"
    pdf.multi_cell(
        0, 5, f"Дата исследования: {study}     Отчёт сформирован: {today_iso}",
        align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.set_text_color(*_BLACK)
    pdf.ln(2)


def _draw_field(pdf: FPDF, fam: str, label: str, value: str) -> None:
    """Строка «Метка: значение» блока пациента."""
    pdf.set_font(fam, "B", 10)
    pdf.cell(42, 6, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font(fam, "", 10)
    pdf.multi_cell(0, 6, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _draw_patient_block(pdf: FPDF, fam: str, data: ReportData) -> None:
    """Блок пациента: ФИО, пол/возраст, этнос, калибровка."""
    p = data.patient
    ethn = _ETHNICITY_RU.get(data.norms.ethnicity, "не указан")
    gender = _format_gender(p.gender)
    age = _format_age(p.birth_date, data.today_iso)

    _draw_field(pdf, fam, "ФИО:", p.name or "—")
    _draw_field(pdf, fam, "Пол / возраст:", f"{gender} · {age}")
    _draw_field(pdf, fam, "Этнос:", ethn)
    _draw_field(pdf, fam, "Калибровка:", _format_calibration(data.calibration))
    pdf.ln(2)


def _section_title(pdf: FPDF, fam: str, text: str) -> None:
    """Подзаголовок секции."""
    pdf.set_font(fam, "B", 11)
    pdf.set_text_color(*_BLACK)
    pdf.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _draw_metrics_table(pdf: FPDF, fam: str, data: ReportData) -> None:
    """Таблица метрик по зонам с цветовой пометкой статуса значений."""
    _section_title(pdf, fam, "Показатели по зонам")

    # Шапка таблицы.
    pdf.set_font(fam, "B", 8)
    pdf.set_fill_color(*_TABLE_HEADER_FILL)
    pdf.set_text_color(*_BLACK)
    for width, header in zip(_TABLE_COL_W, _TABLE_HEADERS, strict=True):
        pdf.cell(
            width, _TABLE_ROW_H, header, border=1, align="C",
            fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP,
        )
    pdf.ln(_TABLE_ROW_H)

    present = [c for c in _ZONE_ORDER if c in data.zone_measurements]
    if not present:
        pdf.set_font(fam, "", 9)
        pdf.set_text_color(*_GRAY)
        pdf.cell(
            _CONTENT_W, _TABLE_ROW_H, "Нет измерений", border=1, align="C",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        pdf.set_text_color(*_BLACK)
        pdf.ln(1)
        return

    norms = data.norms
    pdf.set_font(fam, "", 9)
    for code in present:
        m = data.zone_measurements[code]
        empty = m.hair_count <= 0
        # Контрольная зона (затылок) — эталон самого пациента, не оценивается
        # по популяционным нормам (как и нейтральный индикатор на экране).
        # Иначе у пациента с изначально низкой нормой (пожилой/азиат) контроль
        # ложно краснел бы, ломая смысл сравнения зон с ним.
        is_control = code == data.control_zone
        hc = m.hair_count

        cells: list[tuple[float, str, tuple[int, int, int], str]] = [
            (_TABLE_COL_W[0], f" {code} — {_ZONE_NAMES.get(code, code)}", _BLACK, "L"),
            (
                _TABLE_COL_W[1], _format_density_cell(m),
                _zone_metric_color(norms.density, m.density_per_cm2, hc, is_control), "C",
            ),
            (
                _TABLE_COL_W[2], "—" if empty else f"{m.mean_thickness_um:.0f}",
                _zone_metric_color(norms.thickness_um, m.mean_thickness_um, hc, is_control), "C",
            ),
            (
                _TABLE_COL_W[3], "—" if empty else f"{m.vellus_pct:.0f}%",
                _zone_metric_color(norms.vellus_pct, m.vellus_pct, hc, is_control), "C",
            ),
            (
                _TABLE_COL_W[4], "—" if empty else format_tv_ratio(m.tv_ratio),
                _zone_metric_color(norms.tv_ratio, m.tv_ratio, hc, is_control), "C",
            ),
            (_TABLE_COL_W[5], str(m.n_snapshots), _BLACK, "C"),
        ]
        for width, text, color, align in cells:
            pdf.set_text_color(*color)
            pdf.cell(
                width, _TABLE_ROW_H, text, border=1, align=align,
                new_x=XPos.RIGHT, new_y=YPos.TOP,
            )
        pdf.set_text_color(*_BLACK)
        pdf.ln(_TABLE_ROW_H)
    pdf.ln(2)


def _colored_runs(
    pdf: FPDF, fam: str, size: float,
    runs: list[tuple[str, tuple[int, int, int]]],
) -> None:
    """Печать нескольких цветных фрагментов текста в одну строку."""
    pdf.set_font(fam, "", size)
    for text, color in runs:
        pdf.set_text_color(*color)
        pdf.cell(
            pdf.get_string_width(text) + 0.5, 5, text,
            new_x=XPos.RIGHT, new_y=YPos.TOP,
        )
    pdf.set_text_color(*_BLACK)
    pdf.ln(5)


def _draw_norms_block(pdf: FPDF, fam: str, norms: PatientNorms) -> None:
    """Строка референсных норм пациента и легенда цветов статуса."""
    _section_title(pdf, fam, "Референсные нормы")

    pdf.set_font(fam, "B", 9)
    pdf.multi_cell(
        0, 5, f"Норма для: {norms.basis}",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )

    d, t, v, tv = norms.density, norms.thickness_um, norms.vellus_pct, norms.tv_ratio
    ranges = (
        f"Плотность {d.low:.0f}–{d.high:.0f}/см²   ·   "
        f"Толщина {t.low:.0f}–{t.high:.0f} мкм   ·   "
        f"Vellus ≤{v.high:.0f}%   ·   "
        f"T/V ≥{tv.low:.0f}"
    )
    pdf.set_font(fam, "", 9)
    pdf.set_text_color(*_BLACK)
    pdf.multi_cell(0, 5, ranges, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Легенда: каждое слово окрашено в цвет соответствующего статуса.
    _colored_runs(
        pdf, fam, 8,
        [
            ("Цвет значения: ", _GRAY),
            ("норма", _STATUS_COLORS[NormStatus.NORMAL]),
            (", ", _GRAY),
            ("погранично", _STATUS_COLORS[NormStatus.BORDERLINE]),
            (", ", _GRAY),
            ("отклонение", _STATUS_COLORS[NormStatus.ABNORMAL]),
            (".", _GRAY),
        ],
    )
    pdf.ln(1)


def _draw_control_comparison(pdf: FPDF, fam: str, data: ReportData) -> None:
    """Сравнение плотности зон F/P/T с контрольной зоной (затылок)."""
    _section_title(pdf, fam, "Сравнение с контролем")

    control_code = data.control_zone
    control = data.zone_measurements.get(control_code)
    pdf.set_font(fam, "", 9)

    if control is None or control.density_per_cm2 <= 0:
        pdf.set_text_color(*_GRAY)
        pdf.multi_cell(
            0, 5,
            f"Контрольная зона {control_code} не снята или без волос — "
            "сравнение недоступно.",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        pdf.set_text_color(*_BLACK)
        pdf.ln(1)
        return

    control_density = control.density_per_cm2
    any_zone = False
    for code in _ZONE_ORDER:
        if code == control_code or code not in data.zone_measurements:
            continue
        any_zone = True
        m = data.zone_measurements[code]
        diff = (m.density_per_cm2 - control_density) / control_density * 100.0
        # Знак «−» (U+2212) для минуса — типографски аккуратнее ASCII-дефиса.
        sign = "−" if diff < 0 else "+"
        line = (
            f"Зона {code} ({_ZONE_NAMES.get(code, code)}): "
            f"{sign}{abs(diff):.0f}% плотности к контролю "
            f"{control_code} ({_ZONE_NAMES.get(control_code, control_code)})"
        )
        pdf.set_text_color(*_BLACK)
        pdf.multi_cell(0, 5, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if not any_zone:
        pdf.set_text_color(*_GRAY)
        pdf.multi_cell(
            0, 5, "Другие зоны не сняты — сравнивать не с чем.",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        pdf.set_text_color(*_BLACK)
    pdf.ln(1)


def _read_image_size(path: Path) -> tuple[int, int] | None:
    """Размер изображения (ширина, высота) в пикселях или None при ошибке.

    Файл декодируется полностью (``im.load()``), а не только заголовок:
    снимок с валидным заголовком, но обрезанными/битыми пиксельными данными
    (обрыв записи, неполное копирование) иначе прошёл бы проверку, а потом
    уронил fpdf при вставке. Битые снимки отсеиваются здесь и пропускаются.
    """
    try:
        with Image.open(path) as im:
            width, height = im.size
            im.load()  # полная декодировка — ловит битое тело файла
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось прочитать снимок %s: %s", path, exc)
        return None
    if width <= 0 or height <= 0:
        return None
    return int(width), int(height)


def _collect_valid_images(paths: list[Path]) -> list[tuple[Path, int, int]]:
    """Существующие читаемые снимки зоны (до _IMG_MAX_PER_ROW штук)."""
    result: list[tuple[Path, int, int]] = []
    for path in paths:
        if not path.exists():
            logger.info("Снимок отсутствует, пропуск: %s", path)
            continue
        size = _read_image_size(path)
        if size is None:
            continue
        result.append((path, size[0], size[1]))
        if len(result) >= _IMG_MAX_PER_ROW:
            break
    return result


def _draw_image_row(
    pdf: FPDF, fam: str, code: str, images: list[tuple[Path, int, int]]
) -> None:
    """Подпись зоны и ряд из 1–2 аннотированных снимков этой зоны."""
    n = len(images)
    disp_w = (_CONTENT_W - _IMG_GAP * (n - 1)) / n

    # Габариты снимков с сохранением пропорций и ограничением высоты.
    dims: list[tuple[float, float]] = []
    for _path, iw, ih in images:
        w = disp_w
        h = w * ih / iw
        if h > _IMG_MAX_H:
            h = _IMG_MAX_H
            w = h * iw / ih
        dims.append((w, h))
    row_h = max(h for _w, h in dims)

    # Перенос на новую страницу, если блок «подпись + ряд» не помещается.
    needed = _IMG_CAPTION_H + row_h + 3.0
    if pdf.get_y() + needed > pdf.h - pdf.b_margin:
        pdf.add_page()

    pdf.set_font(fam, "B", 10)
    pdf.set_text_color(*_BLACK)
    pdf.multi_cell(
        0, _IMG_CAPTION_H, f"Зона {code} — {_ZONE_NAMES.get(code, code)}",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )

    top = pdf.get_y()
    x = pdf.l_margin
    for (path, _iw, _ih), (w, h) in zip(images, dims, strict=True):
        # Страховка: даже после полной декодировки в _read_image_size вставка
        # может споткнуться — пропускаем один снимок, не роняя весь отчёт.
        try:
            pdf.image(str(path), x=x, y=top, w=w, h=h)
            pdf.set_draw_color(*_IMG_BORDER_COLOR)
            pdf.rect(x, top, w, h)
        except Exception as exc:
            logger.warning("Не удалось вставить снимок %s в отчёт: %s", path, exc)
        x += disp_w + _IMG_GAP
    pdf.set_y(top + row_h + 3.0)


def _draw_images(pdf: FPDF, fam: str, data: ReportData) -> None:
    """Секция аннотированных снимков по зонам (пропускает пустые/битые)."""
    zones_with_images = [
        code for code in _ZONE_ORDER
        if _collect_valid_images(data.zone_images.get(code, []))
    ]
    if not zones_with_images:
        return

    _section_title(pdf, fam, "Аннотированные снимки")
    for code in zones_with_images:
        images = _collect_valid_images(data.zone_images.get(code, []))
        _draw_image_row(pdf, fam, code, images)


# ===========================================================================
# Публичный API
# ===========================================================================


def generate_report(data: ReportData, output_path: Path) -> None:
    """Сгенерировать PDF-отчёт трихоскопии и записать его в файл.

    Формирует одностраничный (при большом числе снимков — многостраничный)
    отчёт A4-портрет: заголовок с датой, блок пациента, таблица метрик по
    зонам с цветовой пометкой статуса относительно нормы, строка референсных
    норм, сравнение зон с контролем и аннотированные снимки. На каждой
    странице — справочный подвал-дисклеймер.

    Отсутствующие данные (нет калибровки, нет снимков, зона не снята, файл
    снимка пропал) не приводят к ошибке — соответствующие места заполняются
    «—» или пропускаются.

    Args:
        data: Подготовленные данные отчёта.
        output_path: Путь к создаваемому PDF-файлу. Родительские каталоги
            создаются при необходимости.
    """
    logger.info(
        "Генерация PDF-отчёта: пациент=%r, зон=%d, снимков-зон=%d → %s",
        data.patient.name,
        len(data.zone_measurements),
        len(data.zone_images),
        output_path,
    )

    pdf = _ReportPDF()
    fam = pdf.report_font

    pdf.add_page()

    _draw_title(pdf, fam, data.today_iso, data.session_date)
    _draw_patient_block(pdf, fam, data)
    _draw_metrics_table(pdf, fam, data)
    _draw_norms_block(pdf, fam, data.norms)
    _draw_control_comparison(pdf, fam, data)
    _draw_images(pdf, fam, data)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    logger.info("PDF-отчёт записан: %s", output_path)

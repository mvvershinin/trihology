"""
Референсные нормы трихоскопии по научной литературе.

Модуль отдаёт справочные диапазоны нормы для ключевых показателей
фототрихограммы (плотность, толщина терминального волоса, доля vellus,
соотношение Terminal/Vellus) с учётом пола, возраста и этнической
принадлежности пациента.

ВАЖНО — ЭТО НЕ ДИАГНОЗ.
    Возвращаемые диапазоны — справочные ориентиры для интерпретации
    результатов, а не медицинское заключение. Итоговую оценку делает
    трихолог с учётом анамнеза, клинической картины и сравнения зон
    (в первую очередь — с контрольной затылочной зоной O, устойчивой
    к андрогенной миниатюризации). См. METHODOLOGY.md §2.5 и §9.

Изоляция слоёв:
    Модуль расположен в слое ``core`` и не импортирует ``data`` / ``gui``,
    не использует sqlite3 / tkinter. Только stdlib (dataclasses, datetime,
    enum, logging). Пол и этнос приходят «с границы» как строковые коды
    (как их хранит data-слой) либо как перечисления этого модуля и
    нормализуются через ``Sex.from_code`` / ``Ethnicity.from_code``.

Источники (в комментариях у таблиц-констант):
    PMC6219221   — вариабельность плотности волос между этносами и снижение
                   плотности с возрастом.
    PMC7035527   — сниженная плотность фолликулов у восточноазиатов.
    PMC11753574  — плотность волос у южноазиатов (индийская популяция).
    PMC3157785   — реальная доля vellus у здоровых (~6–11 % по зонам).
    PMC11497558  — возрастная динамика показателей волос.
    METHODOLOGY.md §2.2 — клинические пороги проекта (общая норма, T/V).

Принятые эвристики (документированные допущения, НЕ строгие клинические
пороги — их следует уточнять при накоплении собственных данных):
    1. Пограничная полоса плотности = 50 волос/см² ниже нижней границы
       нормы. Согласуется с явными данными для европеоидов (норма 180–250,
       погранично 130–180) и общей нормой проекта (150–300, погранично
       100–150).
    2. Пограничная полоса толщины = 15 % ниже нижней границы нормы.
    3. Возрастная коррекция плотности: для возраста > 35 лет нижняя граница
       нормы (и её пограничная полоса) снижается на 10 % за каждые 15 лет
       после 35, но не более чем на 40 % (консервативный «пол»).
    4. Влияние пола — умеренное и отражено только в описании (``basis``):
       робастных отдельных числовых порогов по полу в цитируемых источниках
       нет, а при облысении решающим является сравнение зон, а не абсолютные
       значения. Численные диапазоны от пола не меняются.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

logger = logging.getLogger(__name__)

__all__ = [
    "Ethnicity",
    "NormRange",
    "NormStatus",
    "PatientNorms",
    "Sex",
    "age_from_birthdate",
    "select_norms",
]


# ===========================================================================
# Перечисления
# ===========================================================================


class Sex(Enum):
    """Пол пациента.

    Значения ``MALE`` / ``FEMALE`` совпадают с кодами data-слоя ('M' / 'F').
    Отсутствие данных о поле кодируется ``UNKNOWN`` (в data-слое — None).
    """

    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "unknown"

    @classmethod
    def from_code(cls, code: Sex | str | None) -> Sex:
        """Нормализация кода пола с границы в ``Sex``.

        Args:
            code: Уже готовый ``Sex``; строковый код ('M' / 'F',
                регистр не важен); либо None.

        Returns:
            ``Sex.MALE`` для 'M', ``Sex.FEMALE`` для 'F',
            иначе (None или нераспознанное) — ``Sex.UNKNOWN``.
        """
        if isinstance(code, Sex):
            return code
        if code is None:
            return cls.UNKNOWN
        normalized = code.strip().upper()
        if normalized == cls.MALE.value:
            return cls.MALE
        if normalized == cls.FEMALE.value:
            return cls.FEMALE
        return cls.UNKNOWN


class Ethnicity(Enum):
    """Этническая принадлежность пациента (самоопределение).

    Строковые значения — согласованные с data-слоем коды (по значению,
    а не по импорту: data-слой не импортирует core). Отсутствие данных —
    ``UNSPECIFIED`` (в data-слое — None или строка 'unspecified').
    """

    CAUCASIAN = "caucasian"
    HISPANIC = "hispanic"
    AFRICAN = "african"
    EAST_ASIAN = "east_asian"
    SOUTH_ASIAN = "south_asian"
    UNSPECIFIED = "unspecified"

    @classmethod
    def from_code(cls, code: Ethnicity | str | None) -> Ethnicity:
        """Нормализация кода этноса с границы в ``Ethnicity``.

        Args:
            code: Готовый ``Ethnicity``; строковый код (регистр не важен);
                либо None.

        Returns:
            Соответствующий ``Ethnicity``; для None или нераспознанного
            кода — ``Ethnicity.UNSPECIFIED``.
        """
        if isinstance(code, Ethnicity):
            return code
        if code is None:
            return cls.UNSPECIFIED
        try:
            return cls(code.strip().lower())
        except ValueError:
            return cls.UNSPECIFIED


class NormStatus(Enum):
    """Положение измеренного значения относительно референсной нормы."""

    NORMAL = "normal"
    BORDERLINE = "borderline"
    ABNORMAL = "abnormal"


# ===========================================================================
# Диапазоны нормы
# ===========================================================================


@dataclass(frozen=True)
class NormRange:
    """Референсный диапазон нормы одного показателя.

    Норма — интервал ``[low, high]``. Пограничная («серая») зона примыкает
    к норме со стороны, в которую значение ухудшается; за ней — отклонение.

    Ориентация задаётся ``higher_is_better``:
        * True (плотность, толщина, T/V) — «чем больше, тем лучше».
          Норма: значение >= ``low`` (превышение ``high`` не является
          отклонением). Погранично: ``borderline_low`` <= значение < ``low``.
          Отклонение: значение < ``borderline_low``. Поле ``high`` —
          типичная верхняя граница здорового диапазона (справочно).
        * False (доля vellus) — «чем меньше, тем лучше». Норма: значение
          <= ``high``. Погранично: ``high`` < значение <= ``borderline_high``.
          Отклонение: значение > ``borderline_high``.

    Attributes:
        low: Нижняя граница нормы.
        high: Верхняя граница нормы (для «меньше=лучше» — верхний порог нормы).
        borderline_low: Нижняя граница пограничной зоны (для «больше=лучше»).
        borderline_high: Верхняя граница пограничной зоны (для «меньше=лучше»);
            None, если метрика ориентирована «больше=лучше».
        higher_is_better: Ориентация метрики (см. выше).
        source: Человекочитаемая подпись источника/обоснования (опционально).
    """

    low: float
    high: float
    borderline_low: float
    borderline_high: float | None = None
    higher_is_better: bool = True
    source: str | None = None

    def __post_init__(self) -> None:
        """Проверка согласованности границ (защита от ошибок в таблицах)."""
        if not self.low < self.high:
            raise ValueError(
                f"NormRange: нижняя граница нормы должна быть меньше верхней "
                f"(low={self.low}, high={self.high})"
            )
        if self.borderline_low > self.low:
            raise ValueError(
                f"NormRange: borderline_low не может превышать low "
                f"(borderline_low={self.borderline_low}, low={self.low})"
            )
        if self.borderline_high is not None and self.borderline_high < self.high:
            raise ValueError(
                f"NormRange: borderline_high не может быть меньше high "
                f"(borderline_high={self.borderline_high}, high={self.high})"
            )

    def status(self, value: float) -> NormStatus:
        """Классификация измеренного значения относительно нормы.

        Args:
            value: Измеренное значение показателя.

        Returns:
            ``NormStatus.NORMAL`` / ``BORDERLINE`` / ``ABNORMAL``.
        """
        if self.higher_is_better:
            if value >= self.low:
                return NormStatus.NORMAL
            if value >= self.borderline_low:
                return NormStatus.BORDERLINE
            return NormStatus.ABNORMAL
        # higher_is_better is False — «чем меньше, тем лучше» (vellus)
        if value <= self.high:
            return NormStatus.NORMAL
        if self.borderline_high is not None and value <= self.borderline_high:
            return NormStatus.BORDERLINE
        return NormStatus.ABNORMAL


@dataclass(frozen=True)
class PatientNorms:
    """Набор референсных норм, подобранный под конкретного пациента.

    Attributes:
        density: Норма плотности (волос/см²).
        thickness_um: Норма толщины терминального волоса (мкм).
        vellus_pct: Норма доли пушковых (vellus) волос (%).
        tv_ratio: Норма соотношения Terminal/Vellus.
        basis: Человекочитаемое описание базы подбора
            (например, «жен., 42 г., европеоид» или «общая норма»).
        sex: Нормализованный пол, использованный при подборе.
        age_years: Возраст в полных годах или None (если не задан).
        ethnicity: Нормализованный этнос, использованный при подборе.
    """

    density: NormRange
    thickness_um: NormRange
    vellus_pct: NormRange
    tv_ratio: NormRange
    basis: str
    sex: Sex
    age_years: int | None
    ethnicity: Ethnicity


# ===========================================================================
# Таблицы-константы (все пороги — только здесь, не в теле select_norms)
# ===========================================================================

# --- Плотность волос (волос/см²) -------------------------------------------
# Источники:
#   PMC6219221  — европеоиды: лоб 230, макушка 226, затылок 214;
#                 латиноамериканцы 169–178; африканцы 148–160.
#   PMC7035527  — восточноазиаты (ханьцы ~143, тайцы): меньше фолликулов.
#   PMC11753574 — южноазиаты (индийские мужчины).
#   METHODOLOGY.md §2.2 — общая норма проекта 150–300 (заглушка unspecified).
# Эвристика №1: пограничная полоса = low − 50 волос/см² (см. docstring модуля).
_DENSITY_NORMS: dict[Ethnicity, NormRange] = {
    Ethnicity.CAUCASIAN: NormRange(
        low=180.0, high=250.0, borderline_low=130.0,
        source="PMC6219221 (лоб 230, макушка 226, затылок 214)",
    ),
    Ethnicity.HISPANIC: NormRange(
        low=150.0, high=200.0, borderline_low=100.0,
        source="PMC6219221 (169–178)",
    ),
    Ethnicity.AFRICAN: NormRange(
        low=130.0, high=175.0, borderline_low=80.0,
        source="PMC6219221 (148–160)",
    ),
    Ethnicity.EAST_ASIAN: NormRange(
        low=120.0, high=170.0, borderline_low=70.0,
        source="PMC7035527 (ханьцы ~143, тайцы; меньше фолликулов)",
    ),
    Ethnicity.SOUTH_ASIAN: NormRange(
        low=140.0, high=190.0, borderline_low=90.0,
        source="PMC11753574 (индийские мужчины)",
    ),
    Ethnicity.UNSPECIFIED: NormRange(
        low=150.0, high=300.0, borderline_low=100.0,
        source="METHODOLOGY.md §2.2 (общая норма 150–300)",
    ),
}

# --- Толщина терминального волоса (мкм) ------------------------------------
# Диапазоны нормы по этносам (задание проекта, обобщение литературы).
#   east_asian 80–120 · caucasian 60–90 · african 55–90 · прочие 50–90.
# Эвристика №2: пограничная полоса = 15 % ниже low (borderline_low = low·0.85).
_THICKNESS_NORMS: dict[Ethnicity, NormRange] = {
    Ethnicity.CAUCASIAN: NormRange(
        low=60.0, high=90.0, borderline_low=51.0,
        source="Терминальный волос европеоидов 60–90 мкм",
    ),
    Ethnicity.HISPANIC: NormRange(
        low=50.0, high=90.0, borderline_low=42.5,
        source="Обобщённый диапазон 50–90 мкм",
    ),
    Ethnicity.AFRICAN: NormRange(
        low=55.0, high=90.0, borderline_low=47.0,
        source="Терминальный волос африканцев 55–90 мкм",
    ),
    Ethnicity.EAST_ASIAN: NormRange(
        low=80.0, high=120.0, borderline_low=68.0,
        source="Терминальный волос восточноазиатов 80–120 мкм (самый толстый)",
    ),
    Ethnicity.SOUTH_ASIAN: NormRange(
        low=50.0, high=90.0, borderline_low=42.5,
        source="Обобщённый диапазон 50–90 мкм",
    ),
    Ethnicity.UNSPECIFIED: NormRange(
        low=50.0, high=90.0, borderline_low=42.5,
        source="Обобщённый диапазон 50–90 мкм",
    ),
}

# --- Доля пушковых волос (vellus, %) ---------------------------------------
# «Меньше = лучше». Норма ≤15 %, погранично 15–25 %, отклонение >25 %.
# Реальная норма у здоровых ~6–11 % по зонам (PMC3157785); прежний грубый
# порог «<20 %» заменён более строгим. Не зависит от этноса.
_VELLUS_NORM = NormRange(
    low=0.0,
    high=15.0,
    borderline_low=0.0,
    borderline_high=25.0,
    higher_is_better=False,
    source="PMC3157785 (здоровые ~6–11 %); норма ≤15 %, погранично 15–25 %",
)

# --- Соотношение Terminal/Vellus (T/V) -------------------------------------
# «Больше = лучше». Норма ≥4, погранично 2–4, отклонение <2 (METHODOLOGY §2.2).
# high=7 — справочная верхняя граница здорового T/V (затылок ~7:1).
_TV_NORM = NormRange(
    low=4.0,
    high=7.0,
    borderline_low=2.0,
    source="METHODOLOGY.md §2.2 (норма ≥4:1, погранично 2–4:1)",
)

# --- Возрастная коррекция плотности (эвристика №3) -------------------------
_AGE_CORRECTION_START_YEARS = 35
_AGE_CORRECTION_STEP_YEARS = 15.0
_AGE_CORRECTION_FRACTION_PER_STEP = 0.10
_AGE_CORRECTION_MAX_REDUCTION = 0.40  # «пол»: максимум −40 % к нижней границе

# --- Русские названия для поля basis ---------------------------------------
_ETHNICITY_RU: dict[Ethnicity, str] = {
    Ethnicity.CAUCASIAN: "европеоид",
    Ethnicity.HISPANIC: "латиноамериканец",
    Ethnicity.AFRICAN: "африканец",
    Ethnicity.EAST_ASIAN: "восточноазиат",
    Ethnicity.SOUTH_ASIAN: "южноазиат",
    Ethnicity.UNSPECIFIED: "",
}
_SEX_RU: dict[Sex, str] = {
    Sex.MALE: "муж.",
    Sex.FEMALE: "жен.",
    Sex.UNKNOWN: "",
}


# ===========================================================================
# Возрастная коррекция и описание
# ===========================================================================


def _age_density_factor(age_years: int) -> float:
    """Коэффициент снижения нижней границы плотности по возрасту.

    Эвристика №3: для возраста > 35 лет — −10 % за каждые 15 лет после 35,
    но не более −40 % (коэффициент не опускается ниже 0.60). Расчёт
    непрерывный (линейный), без ступенек.

    Args:
        age_years: Возраст в полных годах.

    Returns:
        Множитель в диапазоне [0.60, 1.0] для ``low`` и ``borderline_low``.
    """
    if age_years <= _AGE_CORRECTION_START_YEARS:
        return 1.0
    steps = (age_years - _AGE_CORRECTION_START_YEARS) / _AGE_CORRECTION_STEP_YEARS
    reduction = min(steps * _AGE_CORRECTION_FRACTION_PER_STEP, _AGE_CORRECTION_MAX_REDUCTION)
    return 1.0 - reduction


def _apply_age_correction_density(density: NormRange, age_years: int) -> NormRange:
    """Применение возрастной коррекции к норме плотности.

    Снижает ``low`` и ``borderline_low`` на один и тот же коэффициент
    (сохраняя порядок границ); ``high`` не меняется. Для возраста
    <= 35 лет возвращает исходный диапазон без изменений.

    Args:
        density: Базовая норма плотности для этноса.
        age_years: Возраст в полных годах.

    Returns:
        Скорректированный (или исходный) ``NormRange``.
    """
    factor = _age_density_factor(age_years)
    if factor >= 1.0:
        return density
    return replace(
        density,
        low=density.low * factor,
        borderline_low=density.borderline_low * factor,
    )


def _build_basis(sex: Sex, age_years: int | None, ethnicity: Ethnicity) -> str:
    """Человекочитаемое описание базы подбора норм.

    Собирает доступные части: пол, возраст, этнос. Если ни одна часть
    не задана — возвращает «общая норма».

    Args:
        sex: Нормализованный пол.
        age_years: Возраст в полных годах или None.
        ethnicity: Нормализованный этнос.

    Returns:
        Строка вида «жен., 42 г., европеоид» или «общая норма».
    """
    parts: list[str] = []
    if sex is not Sex.UNKNOWN:
        parts.append(_SEX_RU[sex])
    if age_years is not None:
        parts.append(f"{age_years} г.")
    if ethnicity is not Ethnicity.UNSPECIFIED:
        parts.append(_ETHNICITY_RU[ethnicity])
    if not parts:
        return "общая норма"
    return ", ".join(parts)


# ===========================================================================
# Публичный API
# ===========================================================================


def select_norms(
    sex: Sex | str | None,
    age_years: int | None,
    ethnicity: Ethnicity | str | None = None,
) -> PatientNorms:
    """Подбор референсных норм под пол, возраст и этнос пациента.

    Чистая функция без побочных эффектов и обращений к системным часам:
    все пороги берутся из таблиц-констант модуля. Возвращаемые диапазоны —
    справочные ориентиры, НЕ диагноз (см. docstring модуля).

    Fallback-логика:
        * ``ethnicity`` None / 'unspecified' / нераспознанное → общая норма.
        * ``age_years`` None → взрослый без возрастной коррекции.
        * ``sex`` None / нераспознанный → пол не влияет на числа
          (только на описание ``basis``).

    Влияние параметров:
        * Этнос — задаёт базовые диапазоны плотности и толщины.
        * Возраст (> 35) — снижает нижнюю границу нормы плотности
          (эвристика №3). Толщину / vellus / T/V не корректирует.
        * Пол — умеренное влияние, отражено только в ``basis`` (эвристика №4);
          при облысении важнее сравнение зон, а не абсолютные значения.

    Args:
        sex: ``Sex``, строковый код ('M'/'F') или None.
        age_years: Возраст в полных годах или None.
        ethnicity: ``Ethnicity``, строковый код или None.

    Returns:
        ``PatientNorms`` с диапазонами density / thickness_um / vellus_pct /
        tv_ratio и человекочитаемым ``basis``.
    """
    resolved_sex = Sex.from_code(sex)
    resolved_ethnicity = Ethnicity.from_code(ethnicity)

    # Отрицательный возраст (некорректный ввод прямого вызова) — игнорируем,
    # трактуем как «не задан»: мостик из GUI такого не порождает, но защищаемся.
    if age_years is not None and age_years < 0:
        age_years = None

    density = _DENSITY_NORMS[resolved_ethnicity]
    thickness = _THICKNESS_NORMS[resolved_ethnicity]

    if age_years is not None:
        density = _apply_age_correction_density(density, age_years)

    basis = _build_basis(resolved_sex, age_years, resolved_ethnicity)
    logger.debug(
        "Подобраны нормы: sex=%s, age=%s, ethnicity=%s → basis=%r",
        resolved_sex.value,
        age_years,
        resolved_ethnicity.value,
        basis,
    )

    return PatientNorms(
        density=density,
        thickness_um=thickness,
        vellus_pct=_VELLUS_NORM,
        tv_ratio=_TV_NORM,
        basis=basis,
        sex=resolved_sex,
        age_years=age_years,
        ethnicity=resolved_ethnicity,
    )


def age_from_birthdate(birth_date_iso: str | None, today_iso: str) -> int | None:
    """Возраст в полных годах на заданную дату.

    «Сегодня» передаётся аргументом — ядро НЕ обращается к системным часам
    (детерминируемость и тестируемость).

    Args:
        birth_date_iso: Дата рождения в формате ISO 8601 (YYYY-MM-DD)
            или None.
        today_iso: Опорная дата «сегодня» в формате ISO 8601 (YYYY-MM-DD).

    Returns:
        Возраст в полных годах; None, если дата рождения не задана,
        нераспознаваема или лежит в будущем относительно ``today_iso``.

    Raises:
        ValueError: Если ``today_iso`` не является корректной датой ISO 8601
            (нарушение контракта вызывающей стороны).
    """
    if birth_date_iso is None:
        return None
    try:
        birth = date.fromisoformat(birth_date_iso)
    except ValueError:
        return None

    today = date.fromisoformat(today_iso)

    before_birthday = (today.month, today.day) < (birth.month, birth.day)
    age = today.year - birth.year - int(before_birthday)
    if age < 0:
        return None
    return age

"""
Тесты модуля референсных норм трихоскопии (core/norms.py).

Покрывает:
- Sex.from_code / Ethnicity.from_code: нормализация кодов с границы,
  fallback для None и нераспознанных значений, passthrough перечислений
- select_norms: каждая ветка этноса, fallback unspecified/None,
  возрастная коррекция плотности (35-, 35+, 60+, «пол»), метаданные
- age_from_birthdate: обычная дата, None, невалидная строка, граница
  дня рождения, будущая дата, рождение «сегодня»
- NormRange: корректность границ (low<high, borderline_low), классификация
  status() для обеих ориентаций, валидация в __post_init__
- basis: человекочитаемое описание
"""

from __future__ import annotations

import pytest

from phototrichogram.core.norms import (
    Ethnicity,
    NormRange,
    NormStatus,
    PatientNorms,
    Sex,
    age_from_birthdate,
    select_norms,
)

# Ожидаемые базовые границы плотности (волос/см²) по этносам.
_EXPECTED_DENSITY: dict[Ethnicity, tuple[float, float, float]] = {
    Ethnicity.CAUCASIAN: (130.0, 180.0, 250.0),
    Ethnicity.HISPANIC: (100.0, 150.0, 200.0),
    Ethnicity.AFRICAN: (80.0, 130.0, 175.0),
    Ethnicity.EAST_ASIAN: (70.0, 120.0, 170.0),
    Ethnicity.SOUTH_ASIAN: (90.0, 140.0, 190.0),
    Ethnicity.UNSPECIFIED: (100.0, 150.0, 300.0),
}

# Ожидаемые базовые границы толщины (мкм) по этносам.
_EXPECTED_THICKNESS: dict[Ethnicity, tuple[float, float, float]] = {
    Ethnicity.CAUCASIAN: (51.0, 60.0, 90.0),
    Ethnicity.HISPANIC: (42.5, 50.0, 90.0),
    Ethnicity.AFRICAN: (47.0, 55.0, 90.0),
    Ethnicity.EAST_ASIAN: (68.0, 80.0, 120.0),
    Ethnicity.SOUTH_ASIAN: (42.5, 50.0, 90.0),
    Ethnicity.UNSPECIFIED: (42.5, 50.0, 90.0),
}


# ---------------------------------------------------------------------------
# Sex.from_code
# ---------------------------------------------------------------------------


class TestSexFromCode:
    """Нормализация кода пола с границы data-слоя."""

    def test_male_code(self) -> None:
        assert Sex.from_code("M") is Sex.MALE

    def test_female_code(self) -> None:
        assert Sex.from_code("F") is Sex.FEMALE

    def test_case_insensitive(self) -> None:
        """Регистр и пробелы не важны."""
        assert Sex.from_code(" m ") is Sex.MALE
        assert Sex.from_code("f") is Sex.FEMALE

    def test_none_is_unknown(self) -> None:
        assert Sex.from_code(None) is Sex.UNKNOWN

    def test_unrecognized_is_unknown(self) -> None:
        assert Sex.from_code("X") is Sex.UNKNOWN
        assert Sex.from_code("") is Sex.UNKNOWN

    def test_enum_passthrough(self) -> None:
        assert Sex.from_code(Sex.FEMALE) is Sex.FEMALE
        assert Sex.from_code(Sex.UNKNOWN) is Sex.UNKNOWN


# ---------------------------------------------------------------------------
# Ethnicity.from_code
# ---------------------------------------------------------------------------


class TestEthnicityFromCode:
    """Нормализация кода этноса с границы data-слоя."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("caucasian", Ethnicity.CAUCASIAN),
            ("hispanic", Ethnicity.HISPANIC),
            ("african", Ethnicity.AFRICAN),
            ("east_asian", Ethnicity.EAST_ASIAN),
            ("south_asian", Ethnicity.SOUTH_ASIAN),
            ("unspecified", Ethnicity.UNSPECIFIED),
        ],
    )
    def test_all_codes(self, code: str, expected: Ethnicity) -> None:
        """Каждый согласованный код разбирается в своё перечисление."""
        assert Ethnicity.from_code(code) is expected

    def test_case_and_whitespace(self) -> None:
        assert Ethnicity.from_code("  EAST_ASIAN  ") is Ethnicity.EAST_ASIAN

    def test_none_is_unspecified(self) -> None:
        assert Ethnicity.from_code(None) is Ethnicity.UNSPECIFIED

    def test_unrecognized_is_unspecified(self) -> None:
        assert Ethnicity.from_code("martian") is Ethnicity.UNSPECIFIED

    def test_enum_passthrough(self) -> None:
        assert Ethnicity.from_code(Ethnicity.AFRICAN) is Ethnicity.AFRICAN


# ---------------------------------------------------------------------------
# select_norms — ветки этноса
# ---------------------------------------------------------------------------


class TestSelectNormsEthnicity:
    """Подбор норм по каждому этносу."""

    @pytest.mark.parametrize("ethnicity", list(Ethnicity))
    def test_density_matches_table(self, ethnicity: Ethnicity) -> None:
        """Границы плотности соответствуют таблице (без возрастной коррекции)."""
        norms = select_norms(None, None, ethnicity)
        bl, low, high = _EXPECTED_DENSITY[ethnicity]
        assert norms.density.borderline_low == pytest.approx(bl)
        assert norms.density.low == pytest.approx(low)
        assert norms.density.high == pytest.approx(high)

    @pytest.mark.parametrize("ethnicity", list(Ethnicity))
    def test_thickness_matches_table(self, ethnicity: Ethnicity) -> None:
        """Границы толщины соответствуют таблице."""
        norms = select_norms(None, None, ethnicity)
        bl, low, high = _EXPECTED_THICKNESS[ethnicity]
        assert norms.thickness_um.borderline_low == pytest.approx(bl)
        assert norms.thickness_um.low == pytest.approx(low)
        assert norms.thickness_um.high == pytest.approx(high)

    @pytest.mark.parametrize("ethnicity", list(Ethnicity))
    def test_every_ethnicity_resolves(self, ethnicity: Ethnicity) -> None:
        """Для каждого этноса возвращается валидный PatientNorms без KeyError."""
        norms = select_norms(None, None, ethnicity)
        assert isinstance(norms, PatientNorms)
        assert norms.ethnicity is ethnicity

    def test_east_asian_thickest(self) -> None:
        """Восточноазиаты — самый толстый терминальный волос."""
        east = select_norms(None, None, Ethnicity.EAST_ASIAN)
        cauc = select_norms(None, None, Ethnicity.CAUCASIAN)
        assert east.thickness_um.low > cauc.thickness_um.low

    def test_caucasian_densest_lower_bound(self) -> None:
        """Европеоиды — самая высокая нижняя граница нормы плотности."""
        cauc = select_norms(None, None, Ethnicity.CAUCASIAN)
        for ethnicity in Ethnicity:
            other = select_norms(None, None, ethnicity)
            assert cauc.density.low >= other.density.low


# ---------------------------------------------------------------------------
# select_norms — fallback и нормализация входа
# ---------------------------------------------------------------------------


class TestSelectNormsFallback:
    """Fallback на общую норму и приведение кодов с границы."""

    def test_ethnicity_none_is_unspecified(self) -> None:
        norms = select_norms(None, None, None)
        assert norms.ethnicity is Ethnicity.UNSPECIFIED
        bl, low, high = _EXPECTED_DENSITY[Ethnicity.UNSPECIFIED]
        assert norms.density.low == pytest.approx(low)
        assert norms.density.high == pytest.approx(high)
        assert norms.density.borderline_low == pytest.approx(bl)

    def test_ethnicity_string_code(self) -> None:
        """Строковый код с границы разбирается так же, как перечисление."""
        by_str = select_norms("M", 40, "african")
        by_enum = select_norms(Sex.MALE, 40, Ethnicity.AFRICAN)
        assert by_str.density == by_enum.density
        assert by_str.thickness_um == by_enum.thickness_um

    def test_unspecified_string(self) -> None:
        norms = select_norms(None, None, "unspecified")
        assert norms.ethnicity is Ethnicity.UNSPECIFIED

    def test_invalid_ethnicity_falls_back(self) -> None:
        norms = select_norms(None, None, "klingon")
        assert norms.ethnicity is Ethnicity.UNSPECIFIED

    def test_sex_normalized_but_numbers_unchanged(self) -> None:
        """Пол не меняет числовые диапазоны (эвристика №4)."""
        male = select_norms("M", 40, "caucasian")
        female = select_norms("F", 40, "caucasian")
        unknown = select_norms(None, 40, "caucasian")
        assert male.density == female.density == unknown.density
        assert male.thickness_um == female.thickness_um == unknown.thickness_um
        assert male.sex is Sex.MALE
        assert female.sex is Sex.FEMALE
        assert unknown.sex is Sex.UNKNOWN

    def test_vellus_and_tv_are_ethnicity_independent(self) -> None:
        """Vellus и T/V одинаковы для всех этносов."""
        a = select_norms(None, None, Ethnicity.EAST_ASIAN)
        b = select_norms(None, None, Ethnicity.AFRICAN)
        assert a.vellus_pct == b.vellus_pct
        assert a.tv_ratio == b.tv_ratio


# ---------------------------------------------------------------------------
# Возрастная коррекция плотности
# ---------------------------------------------------------------------------


class TestAgeCorrection:
    """Возрастная коррекция нижней границы нормы плотности (эвристика №3)."""

    def test_age_none_no_correction(self) -> None:
        norms = select_norms(None, None, Ethnicity.CAUCASIAN)
        assert norms.density.low == pytest.approx(180.0)
        assert norms.density.borderline_low == pytest.approx(130.0)

    def test_below_35_no_correction(self) -> None:
        """Возраст 30 (35-) — коррекции нет."""
        norms = select_norms(None, 30, Ethnicity.CAUCASIAN)
        assert norms.density.low == pytest.approx(180.0)
        assert norms.density.borderline_low == pytest.approx(130.0)

    def test_exactly_35_no_correction(self) -> None:
        """Ровно 35 лет — граница, коррекции ещё нет."""
        norms = select_norms(None, 35, Ethnicity.CAUCASIAN)
        assert norms.density.low == pytest.approx(180.0)

    def test_age_50_reduces_10pct(self) -> None:
        """Возраст 50 (35+): −10 % к нижней границе, high не меняется."""
        norms = select_norms(None, 50, Ethnicity.CAUCASIAN)
        assert norms.density.low == pytest.approx(180.0 * 0.9)
        assert norms.density.borderline_low == pytest.approx(130.0 * 0.9)
        assert norms.density.high == pytest.approx(250.0)

    def test_age_65_reduces_20pct(self) -> None:
        """Возраст 65 (60+): −20 % к нижней границе."""
        norms = select_norms(None, 65, Ethnicity.CAUCASIAN)
        assert norms.density.low == pytest.approx(180.0 * 0.8)
        assert norms.density.borderline_low == pytest.approx(130.0 * 0.8)

    def test_reduction_capped_at_40pct(self) -> None:
        """Очень пожилой возраст: снижение не глубже −40 % («пол» 0.60)."""
        norms = select_norms(None, 200, Ethnicity.CAUCASIAN)
        assert norms.density.low == pytest.approx(180.0 * 0.6)
        assert norms.density.borderline_low == pytest.approx(130.0 * 0.6)

    def test_correction_preserves_order(self) -> None:
        """После коррекции border_low < low < high сохраняется."""
        norms = select_norms(None, 70, Ethnicity.EAST_ASIAN)
        d = norms.density
        assert d.borderline_low < d.low < d.high

    def test_age_does_not_affect_thickness(self) -> None:
        """Возраст не корректирует толщину/vellus/T-V."""
        young = select_norms(None, 25, Ethnicity.CAUCASIAN)
        old = select_norms(None, 70, Ethnicity.CAUCASIAN)
        assert young.thickness_um == old.thickness_um
        assert young.vellus_pct == old.vellus_pct
        assert young.tv_ratio == old.tv_ratio


# ---------------------------------------------------------------------------
# basis — человекочитаемое описание
# ---------------------------------------------------------------------------


class TestBasis:
    """Формирование поля basis."""

    def test_full_description(self) -> None:
        norms = select_norms("F", 42, "caucasian")
        assert norms.basis == "жен., 42 г., европеоид"

    def test_general_norm(self) -> None:
        norms = select_norms(None, None, None)
        assert norms.basis == "общая норма"

    def test_sex_and_ethnicity_without_age(self) -> None:
        norms = select_norms("M", None, "east_asian")
        assert norms.basis == "муж., восточноазиат"

    def test_only_sex(self) -> None:
        norms = select_norms("F", None, None)
        assert norms.basis == "жен."

    def test_only_age(self) -> None:
        norms = select_norms(None, 50, None)
        assert norms.basis == "50 г."


# ---------------------------------------------------------------------------
# NormRange — корректность границ всех норм
# ---------------------------------------------------------------------------


class TestNormRangeInvariants:
    """Инварианты границ у всех возвращаемых норм."""

    @pytest.mark.parametrize("ethnicity", list(Ethnicity))
    def test_density_and_thickness_bounds(self, ethnicity: Ethnicity) -> None:
        """Для «больше=лучше»: borderline_low < low < high."""
        norms = select_norms(None, None, ethnicity)
        for nr in (norms.density, norms.thickness_um):
            assert nr.low < nr.high
            assert nr.borderline_low < nr.low
            assert nr.higher_is_better is True
            assert nr.borderline_high is None

    def test_vellus_bounds(self) -> None:
        """Для «меньше=лучше» (vellus): low < high < borderline_high."""
        nr = select_norms(None, None, None).vellus_pct
        assert nr.higher_is_better is False
        assert nr.low < nr.high
        assert nr.borderline_high is not None
        assert nr.borderline_high > nr.high

    def test_tv_bounds(self) -> None:
        nr = select_norms(None, None, None).tv_ratio
        assert nr.higher_is_better is True
        assert nr.borderline_low < nr.low < nr.high


# ---------------------------------------------------------------------------
# NormRange.status — классификация значения
# ---------------------------------------------------------------------------


class TestNormRangeStatus:
    """Классификация измеренного значения относительно нормы."""

    def test_higher_is_better_density(self) -> None:
        nr = select_norms(None, None, Ethnicity.CAUCASIAN).density  # 130 / 180 / 250
        assert nr.status(200.0) is NormStatus.NORMAL
        assert nr.status(180.0) is NormStatus.NORMAL  # ровно нижняя граница
        assert nr.status(300.0) is NormStatus.NORMAL  # выше high — всё ещё норма
        assert nr.status(150.0) is NormStatus.BORDERLINE
        assert nr.status(130.0) is NormStatus.BORDERLINE  # ровно граница
        assert nr.status(129.0) is NormStatus.ABNORMAL

    def test_lower_is_better_vellus(self) -> None:
        nr = select_norms(None, None, None).vellus_pct  # 0 / 15 / bl_high 25
        assert nr.status(8.0) is NormStatus.NORMAL
        assert nr.status(15.0) is NormStatus.NORMAL  # ровно верхняя граница нормы
        assert nr.status(20.0) is NormStatus.BORDERLINE
        assert nr.status(25.0) is NormStatus.BORDERLINE  # ровно граница
        assert nr.status(30.0) is NormStatus.ABNORMAL

    def test_tv_ratio(self) -> None:
        nr = select_norms(None, None, None).tv_ratio  # 2 / 4 / 7
        assert nr.status(5.0) is NormStatus.NORMAL
        assert nr.status(4.0) is NormStatus.NORMAL
        assert nr.status(3.0) is NormStatus.BORDERLINE
        assert nr.status(2.0) is NormStatus.BORDERLINE
        assert nr.status(1.5) is NormStatus.ABNORMAL


# ---------------------------------------------------------------------------
# NormRange — валидация в __post_init__
# ---------------------------------------------------------------------------


class TestNormRangeValidation:
    """Защита от некорректных таблиц норм."""

    def test_valid_range_ok(self) -> None:
        nr = NormRange(low=10.0, high=20.0, borderline_low=5.0)
        assert nr.low == pytest.approx(10.0)

    def test_low_not_less_than_high(self) -> None:
        with pytest.raises(ValueError, match="меньше верхней"):
            NormRange(low=20.0, high=10.0, borderline_low=5.0)

    def test_low_equals_high(self) -> None:
        with pytest.raises(ValueError, match="меньше верхней"):
            NormRange(low=10.0, high=10.0, borderline_low=5.0)

    def test_borderline_low_above_low(self) -> None:
        with pytest.raises(ValueError, match="borderline_low"):
            NormRange(low=10.0, high=20.0, borderline_low=15.0)

    def test_borderline_high_below_high(self) -> None:
        with pytest.raises(ValueError, match="borderline_high"):
            NormRange(
                low=0.0,
                high=20.0,
                borderline_low=0.0,
                borderline_high=15.0,
                higher_is_better=False,
            )


# ---------------------------------------------------------------------------
# age_from_birthdate
# ---------------------------------------------------------------------------


class TestAgeFromBirthdate:
    """Расчёт возраста без обращения к системным часам."""

    def test_regular_date(self) -> None:
        assert age_from_birthdate("1980-01-15", "2026-07-19") == 46

    def test_none_birthdate(self) -> None:
        assert age_from_birthdate(None, "2026-07-19") is None

    def test_invalid_birthdate_string(self) -> None:
        assert age_from_birthdate("не дата", "2026-07-19") is None
        assert age_from_birthdate("1980-13-01", "2026-07-19") is None

    def test_birthday_today(self) -> None:
        """День рождения ровно сегодня — полный год засчитан."""
        assert age_from_birthdate("1980-07-19", "2026-07-19") == 46

    def test_day_before_birthday(self) -> None:
        """За день до дня рождения — на год меньше."""
        assert age_from_birthdate("1980-07-20", "2026-07-19") == 45

    def test_day_after_birthday(self) -> None:
        assert age_from_birthdate("1980-07-18", "2026-07-19") == 46

    def test_born_today_is_zero(self) -> None:
        assert age_from_birthdate("2026-07-19", "2026-07-19") == 0

    def test_future_birthdate_is_none(self) -> None:
        """Дата рождения в будущем — некорректно, None."""
        assert age_from_birthdate("2030-01-01", "2026-07-19") is None

    def test_leap_day(self) -> None:
        """29 февраля: до 28.02 включительно год ещё не наступил."""
        assert age_from_birthdate("2000-02-29", "2026-02-28") == 25
        assert age_from_birthdate("2000-02-29", "2026-03-01") == 26

    def test_invalid_today_raises(self) -> None:
        """Некорректная опорная дата — нарушение контракта, ValueError."""
        with pytest.raises(ValueError):
            age_from_birthdate("1980-01-15", "не дата")


# ---------------------------------------------------------------------------
# Метаданные PatientNorms
# ---------------------------------------------------------------------------


class TestPatientNormsMetadata:
    """Резолвнутые метаданные в результате."""

    def test_metadata_populated(self) -> None:
        norms = select_norms("F", 42, "caucasian")
        assert norms.sex is Sex.FEMALE
        assert norms.age_years == 42
        assert norms.ethnicity is Ethnicity.CAUCASIAN

    def test_age_preserved_even_without_correction(self) -> None:
        norms = select_norms("M", 20, "african")
        assert norms.age_years == 20

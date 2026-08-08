"""
Тесты генератора PDF-отчётов (reports/pdf_report.py).

Модуль отчётов — чистый (не тянет tkinter и не обращается к БД/системным
часам): принимает уже подготовленный ReportData и пишет PDF на диск через
fpdf2. Тесты собирают ReportData из настоящих dataclass-моделей
(data.models: Patient, Calibration, Measurement) и настоящих норм
(core.norms.select_norms), детерминированы (today_iso — фиксированная
строка "2026-07-19", а не datetime.now) и работают без дисплея.

В проекте нет стороннего PDF-парсера (content stream у fpdf2 к тому же
сжат), поэтому проверить текст внутри PDF нельзя — тесты ограничиваются
структурными проверками: файл создан, начинается с сигнатуры "%PDF-",
generate_report не падает на отсутствующих/неполных данных, размер файла
растёт при встраивании настоящих снимков.

Покрывает:
- generate_report: полный набор данных (4 зоны + калибровка + нормы),
  пустые данные (без измерений/калибровки/снимков), отсутствие контрольной
  зоны в измерениях, несуществующие пути к снимкам, настоящие PNG-снимки
  (встраивание увеличивает размер файла), измерение-серия
  (n_snapshots > 1, density_std задан), создание родительских каталогов
  output_path, разные демографии пациента (этнос/возраст/пол)
- format_tv_ratio: «нет пушковых» на границе и выше TV_RATIO_MAX,
  обычное значение, число чуть ниже границы
- _format_norm_ranges: пороги «больше=лучше» записаны как ≥low, vellus — как
  ≤high, верхние значения вынесены в справочную строку и не читаются потолком
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from phototrichogram.core.norms import NormStatus, PatientNorms, select_norms
from phototrichogram.core.stats import TV_RATIO_MAX
from phototrichogram.data.models import Calibration, Measurement, Patient
from phototrichogram.reports.pdf_report import (
    ReportData,
    _format_norm_ranges,
    format_tv_ratio,
    generate_report,
)

# Дата отчёта фиксирована (не datetime.now()) — детерминизм (см. докстринг модуля).
_TODAY_ISO = "2026-07-19"

# Сигнатура начала PDF-файла (спецификация PDF, §7.5.2).
_PDF_MAGIC = b"%PDF-"


# ---------------------------------------------------------------------------
# Фабрики
# ---------------------------------------------------------------------------


def _make_patient(
    *,
    name: str = "Иванова Мария Петровна",
    birth_date: str | None = "1985-03-10",
    gender: str | None = "F",
    ethnicity: str | None = "caucasian",
) -> Patient:
    """Patient с разумными значениями по умолчанию (как после загрузки из БД)."""
    return Patient(
        id=1,
        name=name,
        birth_date=birth_date,
        gender=gender,
        diagnosis=None,
        notes=None,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        ethnicity=ethnicity,
    )


def _make_calibration(*, um_per_px: float = 5.9) -> Calibration:
    """Calibration профиля USB-микроскопа 200x с разумными значениями по умолчанию."""
    width_mm = 640 * um_per_px / 1000.0
    height_mm = 480 * um_per_px / 1000.0
    return Calibration(
        id=1,
        um_per_px=um_per_px,
        fov_width_px=640,
        fov_height_px=480,
        fov_width_mm=width_mm,
        fov_height_mm=height_mm,
        fov_area_mm2=width_mm * height_mm,
        description="USB 200x, шкала 0.1 мм",
        created_at="2026-01-01T00:00:00",
    )


def _make_measurement(
    *,
    zone: str,
    hair_count: int = 100,
    density_per_cm2: float = 180.0,
    mean_thickness_um: float = 60.0,
    vellus_pct: float = 12.0,
    tv_ratio: float = 5.0,
    n_snapshots: int = 1,
    density_std: float | None = None,
) -> Measurement:
    """Measurement зоны; terminal_count/vellus_count согласованы с vellus_pct."""
    vellus_count = round(hair_count * vellus_pct / 100.0)
    terminal_count = hair_count - vellus_count
    return Measurement(
        id=1,
        session_id=1,
        zone=zone,
        hair_count=hair_count,
        density_per_cm2=density_per_cm2,
        mean_thickness_um=mean_thickness_um,
        median_thickness_um=mean_thickness_um,
        std_thickness_um=8.0,
        min_thickness_um=max(mean_thickness_um - 25.0, 5.0),
        max_thickness_um=mean_thickness_um + 25.0,
        terminal_count=terminal_count,
        vellus_count=vellus_count,
        intermediate_count=0,
        terminal_pct=round(100.0 - vellus_pct, 1),
        vellus_pct=vellus_pct,
        intermediate_pct=0.0,
        tv_ratio=tv_ratio,
        fov_area_mm2=10.6,
        image_path=None,
        annotated_image_path=None,
        assessment=None,
        created_at="2026-01-01T00:00:00",
        n_snapshots=n_snapshots,
        density_std=density_std,
    )


def _full_zone_measurements() -> dict[str, Measurement]:
    """Измерения всех 4 зон (F/P/T/O) с разными показателями (норма/погранично)."""
    return {
        "F": _make_measurement(
            zone="F", hair_count=95, density_per_cm2=150.0,
            mean_thickness_um=55.0, vellus_pct=18.0, tv_ratio=3.2,
        ),
        "P": _make_measurement(
            zone="P", hair_count=110, density_per_cm2=175.0,
            mean_thickness_um=58.0, vellus_pct=14.0, tv_ratio=4.5,
        ),
        "T": _make_measurement(
            zone="T", hair_count=90, density_per_cm2=140.0,
            mean_thickness_um=50.0, vellus_pct=20.0, tv_ratio=2.8,
        ),
        "O": _make_measurement(
            zone="O", hair_count=130, density_per_cm2=210.0,
            mean_thickness_um=65.0, vellus_pct=9.0, tv_ratio=6.0,
        ),
    }


def _default_norms() -> PatientNorms:
    """Референсные нормы для условной пациентки-европеоидки 41 года."""
    return select_norms("F", 41, "caucasian")


def _make_png(path: Path, *, width: int = 64, height: int = 48, seed: int = 0) -> None:
    """Сохраняет по path настоящий маленький PNG со случайным содержимым.

    Случайные (не однотонные) пиксели — чтобы файл не сжимался до
    вырожденно малого размера и надёжно увеличивал итоговый PDF.
    """
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    Image.fromarray(pixels, mode="RGB").save(path, format="PNG")


def _assert_valid_pdf(path: Path) -> None:
    """Файл существует, непустой и начинается с сигнатуры PDF (%PDF-)."""
    assert path.exists(), f"Отчёт не создан: {path}"
    assert path.stat().st_size > 0, "PDF-файл пустой"
    with path.open("rb") as fh:
        assert fh.read(5) == _PDF_MAGIC, "Файл не начинается с сигнатуры %PDF-"


# ---------------------------------------------------------------------------
# Базовая генерация — полный набор данных
# ---------------------------------------------------------------------------


class TestGenerateReportBasic:
    """Полные данные: пациент, 4 зоны (F/P/T/O), калибровка, нормы."""

    def test_creates_valid_pdf_file(self, tmp_path: Path) -> None:
        """generate_report создаёт непустой файл с сигнатурой %PDF-."""
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=_default_norms(),
        )
        output_path = tmp_path / "report.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Пустые данные
# ---------------------------------------------------------------------------


class TestGenerateReportEmptyData:
    """Нет измерений, нет калибровки, нет снимков — не должно падать."""

    def test_no_measurements_no_calibration_no_images(self, tmp_path: Path) -> None:
        """Пустой zone_measurements, calibration=None, zone_images={} — PDF создан."""
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements={},
            calibration=None,
            norms=select_norms(None, None, None),
            zone_images={},
        )
        output_path = tmp_path / "empty.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Отсутствие контрольной зоны
# ---------------------------------------------------------------------------


class TestGenerateReportNoControlZone:
    """Контрольная зона (O) не снята — сравнение с контролем пропускается."""

    def test_missing_control_zone(self, tmp_path: Path) -> None:
        """zone_measurements без ключа 'O' — не падает, PDF создан."""
        zones = _full_zone_measurements()
        del zones["O"]
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=zones,
            calibration=_make_calibration(),
            norms=_default_norms(),
        )
        output_path = tmp_path / "no_control.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)

    def test_custom_control_zone_missing(self, tmp_path: Path) -> None:
        """Нестандартная контрольная зона, тоже отсутствующая в измерениях."""
        zones = _full_zone_measurements()
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=zones,
            calibration=_make_calibration(),
            norms=_default_norms(),
            control_zone="X",
        )
        output_path = tmp_path / "custom_control.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Отсутствующие файлы снимков
# ---------------------------------------------------------------------------


class TestGenerateReportMissingImageFiles:
    """Пути к снимкам указывают на несуществующие файлы."""

    def test_nonexistent_image_paths(self, tmp_path: Path) -> None:
        """Ядро проверяет Path.exists() — отсутствующие снимки пропускаются."""
        zone_images = {
            "F": [tmp_path / "нет.png", tmp_path / "тоже_нет.jpg"],
            "O": [tmp_path / "пропал.png"],
        }
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=_default_norms(),
            zone_images=zone_images,
        )
        output_path = tmp_path / "missing_images.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Настоящие снимки — встраивание в PDF
# ---------------------------------------------------------------------------


class TestGenerateReportRealImages:
    """Настоящие PNG-снимки должны встраиваться в итоговый PDF."""

    def test_embedded_images_increase_file_size(self, tmp_path: Path) -> None:
        """PDF со снимками весит больше, чем тот же отчёт без снимков."""
        img1 = tmp_path / "snap1.png"
        img2 = tmp_path / "snap2.png"
        _make_png(img1, seed=1)
        _make_png(img2, seed=2)

        patient = _make_patient()
        zones = _full_zone_measurements()
        calibration = _make_calibration()
        norms = _default_norms()

        data_without = ReportData(
            patient=patient, today_iso=_TODAY_ISO, zone_measurements=zones,
            calibration=calibration, norms=norms, zone_images={},
        )
        data_with = ReportData(
            patient=patient, today_iso=_TODAY_ISO, zone_measurements=zones,
            calibration=calibration, norms=norms,
            zone_images={"O": [img1, img2]},
        )

        path_without = tmp_path / "without_images.pdf"
        path_with = tmp_path / "with_images.pdf"
        generate_report(data_without, path_without)
        generate_report(data_with, path_with)

        _assert_valid_pdf(path_without)
        _assert_valid_pdf(path_with)
        assert path_with.stat().st_size > path_without.stat().st_size

    def test_single_image_zone(self, tmp_path: Path) -> None:
        """Один снимок в зоне (не пара) — тоже валидный ряд снимков."""
        img = tmp_path / "solo.png"
        _make_png(img, seed=3)
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=_default_norms(),
            zone_images={"F": [img]},
        )
        output_path = tmp_path / "solo.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)

    def test_mixed_existing_and_missing_paths(self, tmp_path: Path) -> None:
        """В одной зоне вперемешку существующий и отсутствующий снимки."""
        real = tmp_path / "real.png"
        _make_png(real, seed=4)
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=_default_norms(),
            zone_images={"T": [tmp_path / "нет.png", real]},
        )
        output_path = tmp_path / "mixed.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# format_tv_ratio
# ---------------------------------------------------------------------------


class TestFormatTvRatio:
    """Текстовое представление соотношения Terminal/Vellus для отчёта."""

    @pytest.mark.parametrize(
        ("tv_ratio", "expected"),
        [
            (TV_RATIO_MAX, "нет пушковых"),
            (TV_RATIO_MAX + 10.0, "нет пушковых"),  # выше условного максимума — тоже текст
            (TV_RATIO_MAX - 0.1, "99.8"),  # граница: чуть ниже максимума — ещё число
            (5.3, "5.3"),
            (0.0, "0.0"),
        ],
    )
    def test_formatting(self, tv_ratio: float, expected: str) -> None:
        """Формат для «нет пушковых», границы TV_RATIO_MAX и обычных значений."""
        assert format_tv_ratio(tv_ratio) == expected


# ---------------------------------------------------------------------------
# Измерение-серия (n_snapshots > 1)
# ---------------------------------------------------------------------------


class TestGenerateReportSeries:
    """Измерение усреднено по серии снимков зоны (план v1.4)."""

    def test_series_measurement_does_not_break_generation(self, tmp_path: Path) -> None:
        """n_snapshots=5, density_std=30 — генерация проходит (разброс в таблице)."""
        zones = _full_zone_measurements()
        zones["F"] = _make_measurement(
            zone="F", hair_count=95, density_per_cm2=150.0,
            mean_thickness_um=55.0, vellus_pct=18.0, tv_ratio=3.2,
            n_snapshots=5, density_std=30.0,
        )
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=zones,
            calibration=_make_calibration(),
            norms=_default_norms(),
        )
        output_path = tmp_path / "series.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Создание родительских каталогов
# ---------------------------------------------------------------------------


class TestGenerateReportOutputPath:
    """Родительские каталоги output_path создаются автоматически."""

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        """Путь в несуществующей вложенной подпапке tmp_path — каталоги создаются."""
        output_path = tmp_path / "отчёты" / "2026" / "report.pdf"
        assert not output_path.parent.exists()

        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=_default_norms(),
        )

        generate_report(data, output_path)

        assert output_path.parent.is_dir()
        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Разные демографии норм
# ---------------------------------------------------------------------------


class TestGenerateReportDemographics:
    """Разные пол/возраст/этнос пациента → разные нормы, оба варианта валидны."""

    def test_east_asian_young_vs_caucasian_older(self, tmp_path: Path) -> None:
        """Молодой восточноазиат и пожилая европеоидка — разные нормы, оба PDF валидны."""
        patient_east = _make_patient(
            name="Ким Артём Сергеевич", birth_date="2001-02-10",
            gender="M", ethnicity="east_asian",
        )
        patient_cauc = _make_patient(
            name="Смирнова Ольга Ивановна", birth_date="1966-05-20",
            gender="F", ethnicity="caucasian",
        )
        norms_east = select_norms("M", 25, "east_asian")
        norms_cauc = select_norms("F", 60, "caucasian")
        # Нормы действительно разные (иначе тест не проверял бы то, что заявлено).
        assert norms_east.basis != norms_cauc.basis
        assert norms_east.thickness_um != norms_cauc.thickness_um

        data_east = ReportData(
            patient=patient_east, today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(), norms=norms_east,
        )
        data_cauc = ReportData(
            patient=patient_cauc, today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(), norms=norms_cauc,
        )

        path_east = tmp_path / "east_asian.pdf"
        path_cauc = tmp_path / "caucasian.pdf"
        generate_report(data_east, path_east)
        generate_report(data_cauc, path_cauc)

        _assert_valid_pdf(path_east)
        _assert_valid_pdf(path_cauc)

    def test_unspecified_ethnicity_and_unknown_gender(self, tmp_path: Path) -> None:
        """Пациент без указанных пола/этноса/возраста — общая норма, тоже валидна."""
        patient = _make_patient(
            name="Без Демографии", birth_date=None, gender=None, ethnicity=None,
        )
        norms = select_norms(None, None, None)
        data = ReportData(
            patient=patient, today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(), norms=norms,
        )
        output_path = tmp_path / "unspecified.pdf"

        generate_report(data, output_path)

        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Провенанс и устойчивость (правки ревью 2026-07-19)
# ---------------------------------------------------------------------------


class TestSessionDate:
    """Дата исследования (session_date) отдельно от даты формирования."""

    def test_session_date_accepted(self, tmp_path: Path) -> None:
        """session_date задан — отчёт формируется (дата исследования в шапке)."""
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=select_norms("M", 40, "caucasian"),
            session_date="2026-05-01",
        )
        output_path = tmp_path / "with_session_date.pdf"
        generate_report(data, output_path)
        _assert_valid_pdf(output_path)

    def test_session_date_none(self, tmp_path: Path) -> None:
        """session_date по умолчанию None — дата исследования как «—», не падает."""
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=select_norms("M", 40, "caucasian"),
        )
        output_path = tmp_path / "no_session_date.pdf"
        generate_report(data, output_path)
        _assert_valid_pdf(output_path)


class TestCorruptImage:
    """Битый по телу PNG не должен ронять весь отчёт (ключевая устойчивость)."""

    @staticmethod
    def _write_truncated_png(path: Path) -> None:
        """Записать PNG с валидным заголовком, но обрезанным телом."""
        import io

        buf = io.BytesIO()
        Image.fromarray(
            np.random.default_rng(1).integers(0, 255, (40, 40, 3), dtype=np.uint8)
        ).save(buf, "PNG")
        raw = buf.getvalue()
        # Обрезаем до трети — заголовок цел, пиксельные данные повреждены.
        path.write_bytes(raw[: len(raw) // 3])

    def test_truncated_png_is_skipped_not_crash(self, tmp_path: Path) -> None:
        """Обрезанный PNG действительно битый, а отчёт всё равно генерируется."""
        bad = tmp_path / "битый.png"
        self._write_truncated_png(bad)

        # Самопроверка: файл реально не декодируется целиком.
        with pytest.raises((OSError, ValueError)), Image.open(bad) as im:
            im.load()

        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=select_norms("M", 40, "caucasian"),
            zone_images={"F": [bad]},
        )
        output_path = tmp_path / "corrupt_img.pdf"
        generate_report(data, output_path)
        _assert_valid_pdf(output_path)

    def test_mixed_good_bad_missing_images(self, tmp_path: Path) -> None:
        """Смесь [хороший, битый, несуществующий] снимок — отчёт создаётся."""
        good = tmp_path / "good.png"
        Image.fromarray(
            np.random.default_rng(2).integers(0, 255, (50, 50, 3), dtype=np.uint8)
        ).save(good, "PNG")
        bad = tmp_path / "bad.png"
        self._write_truncated_png(bad)
        missing = tmp_path / "нет.png"

        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=_full_zone_measurements(),
            calibration=_make_calibration(),
            norms=select_norms("M", 40, "caucasian"),
            zone_images={"F": [good, bad, missing]},
        )
        output_path = tmp_path / "mixed_img.pdf"
        generate_report(data, output_path)
        _assert_valid_pdf(output_path)


class TestControlZoneColoring:
    """Контрольная зона O не должна валить генерацию при плотности вне нормы."""

    def test_control_zone_below_norm(self, tmp_path: Path) -> None:
        """O с низкой плотностью (вне нормы) — отчёт формируется (O нейтральна)."""
        zones = {
            "F": _make_measurement(zone="F", density_per_cm2=200.0),
            "O": _make_measurement(zone="O", density_per_cm2=50.0),  # ниже нормы
        }
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=zones,
            calibration=_make_calibration(),
            norms=select_norms("M", 40, "caucasian"),
        )
        output_path = tmp_path / "control_low.pdf"
        generate_report(data, output_path)
        _assert_valid_pdf(output_path)

    def test_noncontrol_zone_below_norm(self, tmp_path: Path) -> None:
        """Та же низкая плотность в НЕконтрольной зоне F — тоже формируется."""
        zones = {"F": _make_measurement(zone="F", density_per_cm2=50.0)}
        data = ReportData(
            patient=_make_patient(),
            today_iso=_TODAY_ISO,
            zone_measurements=zones,
            calibration=_make_calibration(),
            norms=select_norms("M", 40, "caucasian"),
        )
        output_path = tmp_path / "noncontrol_low.pdf"
        generate_report(data, output_path)
        _assert_valid_pdf(output_path)


# ---------------------------------------------------------------------------
# Формулировка референсных норм в отчёте
# ---------------------------------------------------------------------------


class TestFormatNormRanges:
    """Строки блока «Референсные нормы»: пороги и справочные верхние значения.

    Плотность/толщина/T/V — «больше значит лучше»: превышение ``high`` нормой
    остаётся (``NormRange.status``). Отчёт не должен подавать ``high`` как
    потолок нормы.
    """

    def test_thresholds_use_lower_bound_for_higher_is_better(self) -> None:
        """Плотность, толщина и T/V записаны как «≥нижняя граница»."""
        norms = _default_norms()
        thresholds, _ = _format_norm_ranges(norms)

        assert f"Плотность ≥{norms.density.low:.0f}/см²" in thresholds
        assert f"Толщина ≥{norms.thickness_um.low:.0f} мкм" in thresholds
        assert f"T/V ≥{norms.tv_ratio.low:.0f}" in thresholds

    def test_thresholds_keep_upper_bound_for_vellus(self) -> None:
        """Vellus — «меньше значит лучше», порог остаётся верхним (≤high)."""
        norms = _default_norms()
        thresholds, _ = _format_norm_ranges(norms)

        assert f"Vellus ≤{norms.vellus_pct.high:.0f}%" in thresholds

    def test_thresholds_do_not_read_as_closed_range(self) -> None:
        """В строке порогов нет записи «low–high» — она читалась как потолок."""
        norms = _default_norms()
        thresholds, _ = _format_norm_ranges(norms)

        assert f"{norms.density.low:.0f}–{norms.density.high:.0f}" not in thresholds
        assert f"{norms.thickness_um.low:.0f}–{norms.thickness_um.high:.0f}" not in thresholds

    def test_typical_line_marks_upper_values_as_reference(self) -> None:
        """Верхние значения вынесены отдельно и явно помечены «не потолок»."""
        norms = _default_norms()
        _, typical = _format_norm_ranges(norms)

        assert "не потолок нормы" in typical
        assert f"плотность до {norms.density.high:.0f}/см²" in typical
        assert f"толщина до {norms.thickness_um.high:.0f} мкм" in typical

    def test_density_above_high_is_still_normal(self) -> None:
        """Смысловая страховка: значение выше high — норма, подпись не врёт."""
        norms = _default_norms()

        assert norms.density.status(norms.density.high + 50.0) is NormStatus.NORMAL

"""
Тесты слоя данных -- SQLite БД (data/database.py).

Покрывает:
- Миграции: версия схемы после инициализации, идемпотентность повторного
  открытия
- CRUD пациентов, калибровок, сессий, измерений
- Поиск пациентов (регистронезависимый, кириллица, экранирование LIKE)
- Каскадное удаление (пациент -> сессии -> измерения)
- Транзакции: нарушение внешнего ключа (IntegrityError) с полным rollback,
  atomarность create_session_with_measurements при ошибке в середине пакета
- Валидация входных данных (пустое имя, недопустимые поля, некорректная
  калибровка)
- Сортировка выборок (по имени, по дате, по зоне)
- Резервное копирование и восстановление (backup_to/restore_from): корректность
  при WAL, безопасность валидации перед заменой файла, защита от осиротевших
  -wal/-shm, миграция бэкапов старой схемы
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from phototrichogram.data.database import (
    _MIGRATIONS,
    BackupError,
    Database,
    RestoreError,
    _split_statements,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _measurement_kwargs(**overrides: object) -> dict[str, object]:
    """Полный набор обязательных аргументов save_measurement с возможностью override."""
    base: dict[str, object] = {
        "hair_count": 100,
        "density_per_cm2": 200.0,
        "mean_thickness_um": 50.0,
        "median_thickness_um": 48.0,
        "std_thickness_um": 5.0,
        "min_thickness_um": 20.0,
        "max_thickness_um": 80.0,
        "terminal_count": 80,
        "vellus_count": 10,
        "intermediate_count": 10,
        "terminal_pct": 80.0,
        "vellus_pct": 10.0,
        "intermediate_pct": 10.0,
        "tv_ratio": 8.0,
        "fov_area_mm2": 10.6,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Миграции
# ---------------------------------------------------------------------------


class TestMigrations:
    """Тесты применения версионных миграций схемы."""

    def test_fresh_db_reaches_final_schema_version(self, tmp_path: Path) -> None:
        """Свежая БД после инициализации имеет максимальную версию схемы."""
        db = Database(tmp_path / "fresh.db")

        version = db._conn.execute("PRAGMA user_version").fetchone()[0]

        assert version == 11
        db.close()

    def test_reopening_db_is_idempotent(self, tmp_path: Path) -> None:
        """Повторное открытие той же БД не переприменяет миграции."""
        db_path = tmp_path / "reopen.db"
        db1 = Database(db_path)
        db1.close()

        db2 = Database(db_path)
        version = db2._conn.execute("PRAGMA user_version").fetchone()[0]

        assert version == 11
        db2.close()

    def test_fresh_db_has_ethnicity_column(self, temp_db: Database) -> None:
        """Свежая БД сразу содержит колонку patients.ethnicity (миграция v9)."""
        columns = {
            row["name"]
            for row in temp_db._conn.execute("PRAGMA table_info(patients)").fetchall()
        }

        assert "ethnicity" in columns

    def test_legacy_db_without_ethnicity_column_is_migrated(self, tmp_path: Path) -> None:
        """БД, созданная до появления ethnicity (версия схемы 8), догоняет до текущей.

        Схема воспроизводится вручную из миграций 1..8 (без 9) -- имитация
        реальной базы пользователя, обновившегося с более старой версии
        приложения. Открытие через Database должно добавить колонку
        ethnicity (v9) без потери существующих данных, а старые пациенты --
        получить ethnicity = NULL (в модели -- None). Заодно применяются и
        все последующие миграции (например, v10 -- колонки серии снимков
        measurements, v11 -- таблица measurement_images), версия схемы
        догоняет до последней (11).
        """
        db_path = tmp_path / "legacy.db"

        raw = sqlite3.connect(str(db_path))
        try:
            for version, sql in _MIGRATIONS:
                if version >= 9:
                    continue
                raw.execute(sql)
            raw.execute(
                "INSERT INTO patients (name, gender) VALUES (?, ?)",
                ("Легаси Пациент", "F"),
            )
            raw.commit()
            raw.execute("PRAGMA user_version=8")
            raw.commit()
        finally:
            raw.close()

        db = Database(db_path)
        try:
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 11

            columns = {
                row["name"]
                for row in db._conn.execute("PRAGMA table_info(patients)").fetchall()
            }
            assert "ethnicity" in columns

            patients = db.get_all_patients()
            assert len(patients) == 1
            assert patients[0].name == "Легаси Пациент"
            assert patients[0].ethnicity is None
        finally:
            db.close()

    def test_fresh_db_has_measurement_series_columns(self, temp_db: Database) -> None:
        """Свежая БД сразу содержит колонки n_snapshots/density_std (миграция v10)."""
        columns = {
            row["name"]
            for row in temp_db._conn.execute("PRAGMA table_info(measurements)").fetchall()
        }

        assert "n_snapshots" in columns
        assert "density_std" in columns

    def test_legacy_db_without_series_columns_is_migrated(self, tmp_path: Path) -> None:
        """БД на версии схемы 9 (без n_snapshots/density_std) догоняет до текущей.

        Схема воспроизводится вручную из миграций 1..9 (без 10) -- имитация
        реальной базы пользователя, обновившегося с версии приложения до
        появления серии снимков (план v1.4). Открытие через Database должно
        добавить обе колонки без потери существующих измерений, а старые
        измерения -- получить n_snapshots=1, density_std=NULL (в модели --
        None), как для одиночного снимка. Заодно применяется и v11 (таблица
        measurement_images), версия схемы догоняет до последней (11).
        """
        db_path = tmp_path / "legacy_v9.db"

        raw = sqlite3.connect(str(db_path))
        try:
            for version, sql in _MIGRATIONS:
                if version >= 10:
                    continue
                raw.execute(sql)
            raw.execute(
                "INSERT INTO patients (name, gender) VALUES (?, ?)",
                ("Легаси Пациент", "M"),
            )
            raw.execute(
                "INSERT INTO calibrations "
                "(um_per_px, fov_width_px, fov_height_px, "
                " fov_width_mm, fov_height_mm, fov_area_mm2) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (5.9, 640, 480, 3.776, 2.832, 10.694),
            )
            raw.execute(
                "INSERT INTO sessions (patient_id, calibration_id, date) VALUES (?, ?, ?)",
                (1, 1, "2026-01-01"),
            )
            raw.execute(
                "INSERT INTO measurements (session_id, zone, hair_count, density_per_cm2) "
                "VALUES (?, ?, ?, ?)",
                (1, "F", 120, 210.5),
            )
            raw.commit()
            raw.execute("PRAGMA user_version=9")
            raw.commit()
        finally:
            raw.close()

        db = Database(db_path)
        try:
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 11

            columns = {
                row["name"]
                for row in db._conn.execute("PRAGMA table_info(measurements)").fetchall()
            }
            assert "n_snapshots" in columns
            assert "density_std" in columns

            measurements = db.get_session_measurements(1)
            assert len(measurements) == 1
            assert measurements[0].zone == "F"
            assert measurements[0].hair_count == 120
            assert measurements[0].n_snapshots == 1
            assert measurements[0].density_std is None
        finally:
            db.close()

    def test_fresh_db_has_measurement_images_table(self, temp_db: Database) -> None:
        """Свежая БД сразу содержит таблицу measurement_images (миграция v11)."""
        columns = {
            row["name"]
            for row in temp_db._conn.execute(
                "PRAGMA table_info(measurement_images)"
            ).fetchall()
        }

        assert columns == {
            "id",
            "measurement_id",
            "image_path",
            "annotated_image_path",
            "ordinal",
            "created_at",
        }

    def test_fresh_db_has_measurement_images_index(self, temp_db: Database) -> None:
        """Свежая БД сразу содержит индекс по measurement_id (миграция v11)."""
        indexes = {
            row["name"]
            for row in temp_db._conn.execute(
                "PRAGMA index_list(measurement_images)"
            ).fetchall()
        }

        assert "idx_measurement_images_measurement" in indexes

    def test_legacy_db_without_measurement_images_table_is_migrated(
        self, tmp_path: Path
    ) -> None:
        """БД на версии схемы 10 (без measurement_images) догоняет до v11.

        Схема воспроизводится вручную из миграций 1..10 -- имитация реальной
        базы пользователя, обновившегося с версии приложения до появления
        привязки снимков к измерениям (план v1.4, Phase 5a). Открытие через
        Database должно создать таблицу measurement_images без потери
        существующих измерений; у уже существующего измерения снимков нет
        (таблица была создана пустой) -- get_measurement_images() возвращает
        пустой список, а не ошибку.
        """
        db_path = tmp_path / "legacy_v10.db"

        raw = sqlite3.connect(str(db_path))
        try:
            for version, sql in _MIGRATIONS:
                if version >= 11:
                    continue
                for statement in _split_statements(sql):
                    raw.execute(statement)
            raw.execute(
                "INSERT INTO patients (name, gender) VALUES (?, ?)",
                ("Легаси Пациент", "F"),
            )
            raw.execute(
                "INSERT INTO calibrations "
                "(um_per_px, fov_width_px, fov_height_px, "
                " fov_width_mm, fov_height_mm, fov_area_mm2) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (5.9, 640, 480, 3.776, 2.832, 10.694),
            )
            raw.execute(
                "INSERT INTO sessions (patient_id, calibration_id, date) VALUES (?, ?, ?)",
                (1, 1, "2026-01-01"),
            )
            raw.execute(
                "INSERT INTO measurements (session_id, zone, hair_count, density_per_cm2) "
                "VALUES (?, ?, ?, ?)",
                (1, "F", 120, 210.5),
            )
            raw.commit()
            raw.execute("PRAGMA user_version=10")
            raw.commit()
        finally:
            raw.close()

        db = Database(db_path)
        try:
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 11

            tables = {
                row["name"]
                for row in db._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "measurement_images" in tables

            measurements = db.get_session_measurements(1)
            assert len(measurements) == 1
            assert measurements[0].hair_count == 120

            images = db.get_measurement_images(measurements[0].id)
            assert images == []
        finally:
            db.close()

    def test_foreign_keys_enabled(self, temp_db: Database) -> None:
        """PRAGMA foreign_keys включена -- целостность данных обеспечена."""
        value = temp_db._conn.execute("PRAGMA foreign_keys").fetchone()[0]

        assert value == 1

    def test_context_manager_closes_connection(self, tmp_path: Path) -> None:
        """Контекстный менеджер закрывает соединение при выходе из блока."""
        with Database(tmp_path / "ctx.db") as db:
            assert db._conn is not None

        assert db._conn is None


# ---------------------------------------------------------------------------
# CRUD: Patient
# ---------------------------------------------------------------------------


class TestPatientCrud:
    """Тесты CRUD-операций для пациентов."""

    def test_add_patient_returns_filled_object(self, temp_db: Database) -> None:
        """Созданный пациент содержит id и переданные поля."""
        patient = temp_db.add_patient(name="Иванова А.В.", gender="F", diagnosis="AGA")

        assert patient.id is not None
        assert patient.id > 0
        assert patient.name == "Иванова А.В."
        assert patient.gender == "F"
        assert patient.diagnosis == "AGA"
        assert patient.created_at is not None

    def test_add_patient_strips_whitespace(self, temp_db: Database) -> None:
        """Пробелы вокруг имени обрезаются при сохранении."""
        patient = temp_db.add_patient(name="  Петров И.С.  ")

        assert patient.name == "Петров И.С."

    def test_add_patient_empty_name_raises(self, temp_db: Database) -> None:
        """Пустое имя (или только пробелы) -> ValueError."""
        with pytest.raises(ValueError, match="не может быть пустым"):
            temp_db.add_patient(name="   ")

    def test_get_patient_roundtrip(self, temp_db: Database) -> None:
        """Загруженный пациент идентичен только что сохранённому."""
        created = temp_db.add_patient(name="Тест Пациент", gender="M")

        loaded = temp_db.get_patient(created.id)

        assert loaded == created

    def test_add_patient_with_ethnicity(self, temp_db: Database) -> None:
        """Этническая принадлежность сохраняется при создании пациента."""
        patient = temp_db.add_patient(name="Ким Мин Су", ethnicity="east_asian")

        assert patient.ethnicity == "east_asian"

    def test_add_patient_ethnicity_defaults_to_none(self, temp_db: Database) -> None:
        """Без явного указания этноса поле -- None (старые вызовы add_patient не ломаются)."""
        patient = temp_db.add_patient(name="Без этноса", gender="M")

        assert patient.ethnicity is None

    def test_get_patient_roundtrip_preserves_ethnicity(self, temp_db: Database) -> None:
        """Загруженный пациент сохраняет ethnicity -- как заданное значение, так и None."""
        with_ethnicity = temp_db.add_patient(name="С этносом", ethnicity="african")
        without_ethnicity = temp_db.add_patient(name="Без этноса поле")

        loaded_with = temp_db.get_patient(with_ethnicity.id)
        loaded_without = temp_db.get_patient(without_ethnicity.id)

        assert loaded_with == with_ethnicity
        assert loaded_with.ethnicity == "african"
        assert loaded_without == without_ethnicity
        assert loaded_without.ethnicity is None

    def test_get_patient_not_found_returns_none(self, temp_db: Database) -> None:
        """Несуществующий id -> None."""
        assert temp_db.get_patient(99999) is None

    def test_get_all_patients_sorted_by_name(self, temp_db: Database) -> None:
        """Список пациентов отсортирован по имени (регистронезависимо, кириллица)."""
        temp_db.add_patient(name="Петров Иван")
        temp_db.add_patient(name="Иванова Анна")
        temp_db.add_patient(name="сидорова Ольга")

        patients = temp_db.get_all_patients()

        assert [p.name for p in patients] == ["Иванова Анна", "Петров Иван", "сидорова Ольга"]

    def test_get_all_patients_empty_db(self, temp_db: Database) -> None:
        """Пустая БД -> пустой список пациентов."""
        assert temp_db.get_all_patients() == []

    def test_update_patient_changes_fields(self, temp_db: Database) -> None:
        """Обновление изменяет только указанные поля."""
        patient = temp_db.add_patient(name="Старое Имя", gender="M")

        updated = temp_db.update_patient(patient.id, name="Новое Имя", diagnosis="AGA")

        assert updated is True
        reloaded = temp_db.get_patient(patient.id)
        assert reloaded.name == "Новое Имя"
        assert reloaded.diagnosis == "AGA"
        assert reloaded.gender == "M"  # не изменялось

    def test_update_patient_sets_ethnicity(self, temp_db: Database) -> None:
        """Обновление устанавливает этническую принадлежность, не трогая остальные поля."""
        patient = temp_db.add_patient(name="Тест Этнос", gender="F")

        updated = temp_db.update_patient(patient.id, ethnicity="hispanic")

        assert updated is True
        reloaded = temp_db.get_patient(patient.id)
        assert reloaded.ethnicity == "hispanic"
        assert reloaded.name == "Тест Этнос"  # не изменялось
        assert reloaded.gender == "F"  # не изменялось

    def test_update_patient_can_reset_ethnicity_to_none(self, temp_db: Database) -> None:
        """Явная передача ethnicity=None сбрасывает поле в NULL."""
        patient = temp_db.add_patient(name="Сброс этноса", ethnicity="caucasian")

        updated = temp_db.update_patient(patient.id, ethnicity=None)

        assert updated is True
        reloaded = temp_db.get_patient(patient.id)
        assert reloaded.ethnicity is None

    def test_update_patient_not_found_returns_false(self, temp_db: Database) -> None:
        """Обновление несуществующего пациента возвращает False."""
        assert temp_db.update_patient(99999, name="Кто-то") is False

    def test_update_patient_empty_kwargs_raises(self, temp_db: Database) -> None:
        """Отсутствие полей для обновления -> ValueError."""
        patient = temp_db.add_patient(name="Тест")

        with pytest.raises(ValueError):
            temp_db.update_patient(patient.id)

    def test_update_patient_invalid_field_raises(self, temp_db: Database) -> None:
        """Недопустимое поле (например, 'id') -> ValueError."""
        patient = temp_db.add_patient(name="Тест")

        with pytest.raises(ValueError):
            temp_db.update_patient(patient.id, unknown_field="значение")

    def test_update_patient_empty_name_raises(self, temp_db: Database) -> None:
        """Попытка установить пустое имя при обновлении -> ValueError."""
        patient = temp_db.add_patient(name="Тест")

        with pytest.raises(ValueError, match="не может быть пустым"):
            temp_db.update_patient(patient.id, name="   ")

    def test_delete_patient_removes_record(self, temp_db: Database) -> None:
        """Удалённый пациент больше не находится по id."""
        patient = temp_db.add_patient(name="На удаление")

        deleted = temp_db.delete_patient(patient.id)

        assert deleted is True
        assert temp_db.get_patient(patient.id) is None

    def test_delete_patient_not_found_returns_false(self, temp_db: Database) -> None:
        """Удаление несуществующего пациента возвращает False."""
        assert temp_db.delete_patient(99999) is False


# ---------------------------------------------------------------------------
# Поиск пациентов
# ---------------------------------------------------------------------------


class TestSearchPatients:
    """Тесты полнотекстового поиска пациентов по имени."""

    @pytest.fixture(autouse=True)
    def _seed(self, temp_db: Database) -> None:
        temp_db.add_patient(name="Иванова Анна Викторовна")
        temp_db.add_patient(name="Петров Иван Сергеевич")
        temp_db.add_patient(name="сидорова ольга")

    def test_partial_match(self, temp_db: Database) -> None:
        """Частичное совпадение находит всех подходящих пациентов."""
        results = temp_db.search_patients("иван")

        assert {p.name for p in results} == {"Иванова Анна Викторовна", "Петров Иван Сергеевич"}

    def test_case_insensitive_including_cyrillic(self, temp_db: Database) -> None:
        """Поиск регистронезависим для кириллицы (py_lower, не только ASCII)."""
        upper = temp_db.search_patients("ИВАН")
        lower = temp_db.search_patients("иван")

        assert {p.name for p in upper} == {p.name for p in lower}

    def test_no_match_returns_empty(self, temp_db: Database) -> None:
        """Отсутствие совпадений -> пустой список."""
        assert temp_db.search_patients("несуществующий_запрос_xyz") == []

    def test_percent_wildcard_is_escaped(self, temp_db: Database) -> None:
        """Символ '%' в запросе экранируется -- не работает как SQL wildcard."""
        results = temp_db.search_patients("%")

        assert results == []

    def test_underscore_wildcard_is_escaped(self, temp_db: Database) -> None:
        """Символ '_' в запросе экранируется -- не подставляется как любой символ."""
        results = temp_db.search_patients("_ванова")

        assert results == []

    def test_search_results_carry_ethnicity_field(self, temp_db: Database) -> None:
        """Поиск не ломается новым полем -- ethnicity возвращается вместе с остальными."""
        temp_db.add_patient(name="Иванова Этнос Тест", ethnicity="caucasian")

        results = temp_db.search_patients("Этнос Тест")

        assert len(results) == 1
        assert results[0].ethnicity == "caucasian"


# ---------------------------------------------------------------------------
# CRUD: Calibration
# ---------------------------------------------------------------------------


class TestCalibrationCrud:
    """Тесты CRUD-операций для профилей калибровки."""

    def test_add_calibration_computes_fov(self, temp_db: Database) -> None:
        """FOV в мм и площадь рассчитываются автоматически из px и um_per_px."""
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)

        assert cal.id is not None
        assert cal.fov_width_mm == pytest.approx(640 * 5.9 / 1000.0, abs=1e-3)
        assert cal.fov_height_mm == pytest.approx(480 * 5.9 / 1000.0, abs=1e-3)
        assert cal.fov_area_mm2 == pytest.approx(cal.fov_width_mm * cal.fov_height_mm, abs=1e-3)

    def test_add_calibration_invalid_scale_raises(self, temp_db: Database) -> None:
        """um_per_px <= 0 -> ValueError."""
        with pytest.raises(ValueError):
            temp_db.add_calibration(um_per_px=0.0, fov_width_px=640, fov_height_px=480)

    def test_add_calibration_invalid_dimensions_raises(self, temp_db: Database) -> None:
        """Нулевые или отрицательные размеры кадра -> ValueError."""
        with pytest.raises(ValueError):
            temp_db.add_calibration(um_per_px=5.9, fov_width_px=0, fov_height_px=480)

    def test_get_calibration_not_found_returns_none(self, temp_db: Database) -> None:
        """Несуществующий id -> None."""
        assert temp_db.get_calibration(99999) is None

    def test_get_latest_calibration_empty_db(self, temp_db: Database) -> None:
        """Пустая БД -> None (нет ни одной калибровки)."""
        assert temp_db.get_latest_calibration() is None

    def test_get_latest_calibration_returns_most_recent(self, temp_db: Database) -> None:
        """Последняя калибровка -- та, что добавлена позже всех."""
        temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        second = temp_db.add_calibration(um_per_px=6.5, fov_width_px=640, fov_height_px=480)

        latest = temp_db.get_latest_calibration()

        assert latest.id == second.id

    def test_get_all_calibrations_newest_first(self, temp_db: Database) -> None:
        """Список калибровок отсортирован от новых к старым."""
        first = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        second = temp_db.add_calibration(um_per_px=6.5, fov_width_px=640, fov_height_px=480)

        all_cals = temp_db.get_all_calibrations()

        assert [c.id for c in all_cals] == [second.id, first.id]


# ---------------------------------------------------------------------------
# CRUD: Session
# ---------------------------------------------------------------------------


class TestSessionCrud:
    """Тесты CRUD-операций для сессий обследования."""

    def _make_patient_and_calibration(self, db: Database) -> tuple[int, int]:
        patient = db.add_patient(name="Тест Пациент")
        cal = db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        return patient.id, cal.id

    def test_add_session_returns_filled_object(self, temp_db: Database) -> None:
        """Созданная сессия содержит id и переданные поля."""
        patient_id, cal_id = self._make_patient_and_calibration(temp_db)

        session = temp_db.add_session(patient_id, cal_id, date="2026-03-15", zone="F")

        assert session.id is not None
        assert session.patient_id == patient_id
        assert session.calibration_id == cal_id
        assert session.zone == "F"

    def test_add_session_invalid_patient_raises_integrity_error(self, temp_db: Database) -> None:
        """Несуществующий patient_id -> sqlite3.IntegrityError (внешний ключ)."""
        _, cal_id = self._make_patient_and_calibration(temp_db)

        with pytest.raises(sqlite3.IntegrityError):
            temp_db.add_session(patient_id=99999, calibration_id=cal_id, date="2026-01-01")

    def test_add_session_invalid_calibration_raises_integrity_error(
        self, temp_db: Database
    ) -> None:
        """Несуществующий calibration_id -> sqlite3.IntegrityError (внешний ключ)."""
        patient_id, _ = self._make_patient_and_calibration(temp_db)

        with pytest.raises(sqlite3.IntegrityError):
            temp_db.add_session(patient_id=patient_id, calibration_id=99999, date="2026-01-01")

    def test_failed_session_insert_is_rolled_back(self, temp_db: Database) -> None:
        """При нарушении внешнего ключа в БД не остаётся частичной записи."""
        before = temp_db._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            temp_db.add_session(patient_id=99999, calibration_id=99999, date="2026-01-01")

        after = temp_db._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        assert after == before

    def test_get_session_not_found_returns_none(self, temp_db: Database) -> None:
        """Несуществующий id -> None."""
        assert temp_db.get_session(99999) is None

    def test_get_patient_sessions_sorted_newest_first(self, temp_db: Database) -> None:
        """Сессии пациента отсортированы по дате (новые первыми)."""
        patient_id, cal_id = self._make_patient_and_calibration(temp_db)
        temp_db.add_session(patient_id, cal_id, date="2026-01-01")
        temp_db.add_session(patient_id, cal_id, date="2026-03-01")
        temp_db.add_session(patient_id, cal_id, date="2026-02-01")

        sessions = temp_db.get_patient_sessions(patient_id)

        assert [s.date for s in sessions] == ["2026-03-01", "2026-02-01", "2026-01-01"]

    def test_get_patient_sessions_empty_for_new_patient(self, temp_db: Database) -> None:
        """У пациента без сессий -- пустой список."""
        patient = temp_db.add_patient(name="Без сессий")

        assert temp_db.get_patient_sessions(patient.id) == []

    def test_delete_session_removes_record(self, temp_db: Database) -> None:
        """Удалённая сессия больше не находится по id."""
        patient_id, cal_id = self._make_patient_and_calibration(temp_db)
        session = temp_db.add_session(patient_id, cal_id, date="2026-01-01")

        deleted = temp_db.delete_session(session.id)

        assert deleted is True
        assert temp_db.get_session(session.id) is None

    def test_delete_session_not_found_returns_false(self, temp_db: Database) -> None:
        """Удаление несуществующей сессии возвращает False."""
        assert temp_db.delete_session(99999) is False

    def test_delete_session_cascades_to_measurements(self, temp_db: Database) -> None:
        """Удаление сессии удаляет и все её измерения."""
        patient_id, cal_id = self._make_patient_and_calibration(temp_db)
        session = temp_db.add_session(patient_id, cal_id, date="2026-01-01")
        temp_db.save_measurement(
            session_id=session.id, zone="F", **_measurement_kwargs()
        )

        temp_db.delete_session(session.id)

        assert temp_db.get_session_measurements(session.id) == []

    def test_delete_session_cascades_to_measurement_images(self, temp_db: Database) -> None:
        """Удаление сессии удаляет и снимки, привязанные к её измерениям.

        Без явного удаления measurement_images DELETE FROM measurements упал
        бы с IntegrityError (внешний ключ measurement_images.measurement_id
        без ON DELETE CASCADE) -- сам факт успешного возврата delete_session()
        уже частично подтверждает каскад; дополнительно проверяем, что
        строки в measurement_images физически исчезли.
        """
        patient_id, cal_id = self._make_patient_and_calibration(temp_db)
        session = temp_db.add_session(patient_id, cal_id, date="2026-01-01")
        meas = temp_db.save_measurement(
            session_id=session.id, zone="F", **_measurement_kwargs()
        )
        temp_db.add_measurement_image(meas.id, image_path="/tmp/zone_F_1.png")

        deleted = temp_db.delete_session(session.id)

        assert deleted is True
        assert temp_db.get_measurement_images(meas.id) == []
        row_count = temp_db._conn.execute(
            "SELECT COUNT(*) FROM measurement_images WHERE measurement_id = ?",
            (meas.id,),
        ).fetchone()[0]
        assert row_count == 0


# ---------------------------------------------------------------------------
# CRUD: Measurement
# ---------------------------------------------------------------------------


class TestMeasurementCrud:
    """Тесты CRUD-операций для измерений (результатов анализа)."""

    def _make_session(self, db: Database) -> int:
        patient = db.add_patient(name="Тест Пациент")
        cal = db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        session = db.add_session(patient.id, cal.id, date="2026-01-01")
        return session.id

    def test_save_measurement_returns_filled_object(self, temp_db: Database) -> None:
        """Сохранённое измерение содержит id и переданные показатели."""
        session_id = self._make_session(temp_db)

        meas = temp_db.save_measurement(
            session_id=session_id, zone="F", **_measurement_kwargs()
        )

        assert meas.id is not None
        assert meas.zone == "F"
        assert meas.hair_count == 100
        assert meas.density_per_cm2 == 200.0

    def test_save_measurement_defaults_to_single_snapshot(self, temp_db: Database) -> None:
        """Без явных n_snapshots/density_std -- одиночный снимок (обратная совместимость)."""
        session_id = self._make_session(temp_db)

        meas = temp_db.save_measurement(
            session_id=session_id, zone="F", **_measurement_kwargs()
        )

        assert meas.n_snapshots == 1
        assert meas.density_std is None

    def test_save_measurement_stores_series_averaging(self, temp_db: Database) -> None:
        """Явно переданные n_snapshots/density_std сохраняются и читаются обратно."""
        session_id = self._make_session(temp_db)

        meas = temp_db.save_measurement(
            session_id=session_id,
            zone="F",
            **_measurement_kwargs(),
            n_snapshots=5,
            density_std=12.4,
        )

        assert meas.n_snapshots == 5
        assert meas.density_std == pytest.approx(12.4)

        reloaded = temp_db.get_measurement(meas.id)
        assert reloaded == meas

    def test_get_session_measurements_roundtrips_series_fields(
        self, temp_db: Database
    ) -> None:
        """Поля серии не теряются при чтении списка измерений сессии."""
        session_id = self._make_session(temp_db)
        temp_db.save_measurement(
            session_id=session_id,
            zone="F",
            **_measurement_kwargs(),
            n_snapshots=3,
            density_std=8.0,
        )

        measurements = temp_db.get_session_measurements(session_id)

        assert len(measurements) == 1
        assert measurements[0].n_snapshots == 3
        assert measurements[0].density_std == pytest.approx(8.0)

    def test_save_measurement_invalid_session_raises_integrity_error(
        self, temp_db: Database
    ) -> None:
        """Несуществующий session_id -> sqlite3.IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.save_measurement(session_id=99999, zone="F", **_measurement_kwargs())

    def test_get_measurement_not_found_returns_none(self, temp_db: Database) -> None:
        """Несуществующий id -> None."""
        assert temp_db.get_measurement(99999) is None

    def test_get_session_measurements_sorted_by_zone(self, temp_db: Database) -> None:
        """Измерения сессии отсортированы по коду зоны."""
        session_id = self._make_session(temp_db)
        temp_db.save_measurement(session_id=session_id, zone="P", **_measurement_kwargs())
        temp_db.save_measurement(session_id=session_id, zone="F", **_measurement_kwargs())

        measurements = temp_db.get_session_measurements(session_id)

        assert [m.zone for m in measurements] == ["F", "P"]

    def test_get_patient_measurements_across_sessions(self, temp_db: Database) -> None:
        """Все измерения пациента собираются из всех его сессий."""
        patient = temp_db.add_patient(name="Тест")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        s1 = temp_db.add_session(patient.id, cal.id, date="2026-01-01")
        s2 = temp_db.add_session(patient.id, cal.id, date="2026-02-01")
        temp_db.save_measurement(session_id=s1.id, zone="F", **_measurement_kwargs())
        temp_db.save_measurement(session_id=s2.id, zone="O", **_measurement_kwargs())

        measurements = temp_db.get_patient_measurements(patient.id)

        assert len(measurements) == 2
        assert {m.zone for m in measurements} == {"F", "O"}

    def test_get_zone_history_sorted_oldest_first(self, temp_db: Database) -> None:
        """История по зоне отсортирована от старых измерений к новым (для графиков)."""
        patient = temp_db.add_patient(name="Тест")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        for date in ("2026-03-01", "2026-01-01", "2026-02-01"):
            session = temp_db.add_session(patient.id, cal.id, date=date)
            temp_db.save_measurement(session_id=session.id, zone="F", **_measurement_kwargs())

        history = temp_db.get_zone_history(patient.id, "F")
        dates = [temp_db.get_session(m.session_id).date for m in history]

        assert dates == ["2026-01-01", "2026-02-01", "2026-03-01"]

    def test_get_zone_history_filters_by_zone(self, temp_db: Database) -> None:
        """История содержит измерения только запрошенной зоны."""
        patient = temp_db.add_patient(name="Тест")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        session = temp_db.add_session(patient.id, cal.id, date="2026-01-01")
        temp_db.save_measurement(session_id=session.id, zone="F", **_measurement_kwargs())
        temp_db.save_measurement(session_id=session.id, zone="O", **_measurement_kwargs())

        history = temp_db.get_zone_history(patient.id, "F")

        assert len(history) == 1
        assert history[0].zone == "F"


# ---------------------------------------------------------------------------
# CRUD: MeasurementImage
# ---------------------------------------------------------------------------


class TestMeasurementImageCrud:
    """Тесты CRUD-операций для снимков серии, привязанных к измерению."""

    def _make_measurement(self, db: Database) -> int:
        patient = db.add_patient(name="Тест Пациент")
        cal = db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        session = db.add_session(patient.id, cal.id, date="2026-01-01")
        meas = db.save_measurement(session_id=session.id, zone="F", **_measurement_kwargs())
        return meas.id

    # -- add_measurement_image (одиночный снимок) ---------------------------

    def test_add_measurement_image_returns_filled_object(self, temp_db: Database) -> None:
        """Сохранённый снимок содержит id, created_at и переданные поля."""
        meas_id = self._make_measurement(temp_db)

        image = temp_db.add_measurement_image(
            meas_id,
            image_path="/data/zone_F_0.png",
            annotated_image_path="/data/zone_F_0_annotated.png",
            ordinal=0,
        )

        assert image.id is not None
        assert image.measurement_id == meas_id
        assert image.image_path == "/data/zone_F_0.png"
        assert image.annotated_image_path == "/data/zone_F_0_annotated.png"
        assert image.ordinal == 0
        assert image.created_at is not None

    def test_add_measurement_image_defaults_annotated_to_none(self, temp_db: Database) -> None:
        """Без явного annotated_image_path -- поле сохраняется как None."""
        meas_id = self._make_measurement(temp_db)

        image = temp_db.add_measurement_image(meas_id, image_path="/data/zone_F_0.png")

        assert image.annotated_image_path is None
        reloaded = temp_db.get_measurement_images(meas_id)
        assert reloaded[0].annotated_image_path is None

    def test_add_measurement_image_defaults_ordinal_to_zero(self, temp_db: Database) -> None:
        """Без явного ordinal -- по умолчанию 0."""
        meas_id = self._make_measurement(temp_db)

        image = temp_db.add_measurement_image(meas_id, image_path="/data/zone_F_0.png")

        assert image.ordinal == 0

    def test_add_measurement_image_empty_path_raises(self, temp_db: Database) -> None:
        """Пустой (только из пробелов) image_path -> ValueError, ничего не сохраняется."""
        meas_id = self._make_measurement(temp_db)

        with pytest.raises(ValueError, match="не может быть пустым"):
            temp_db.add_measurement_image(meas_id, image_path="   ")

        assert temp_db.get_measurement_images(meas_id) == []

    def test_add_measurement_image_invalid_measurement_raises_integrity_error(
        self, temp_db: Database
    ) -> None:
        """Несуществующий measurement_id -> sqlite3.IntegrityError, без частичной записи."""
        before = temp_db._conn.execute(
            "SELECT COUNT(*) FROM measurement_images"
        ).fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            temp_db.add_measurement_image(99999, image_path="/data/orphan.png")

        after = temp_db._conn.execute(
            "SELECT COUNT(*) FROM measurement_images"
        ).fetchone()[0]
        assert after == before

    # -- get_measurement_images ----------------------------------------------

    def test_get_measurement_images_empty_for_measurement_without_images(
        self, temp_db: Database
    ) -> None:
        """У измерения без снимков (старые записи до Phase 5a) -- пустой список."""
        meas_id = self._make_measurement(temp_db)

        assert temp_db.get_measurement_images(meas_id) == []

    def test_get_measurement_images_sorted_by_ordinal(self, temp_db: Database) -> None:
        """Снимки возвращаются отсортированными по ordinal, а не по порядку вставки."""
        meas_id = self._make_measurement(temp_db)
        temp_db.add_measurement_image(meas_id, image_path="/data/img_2.png", ordinal=2)
        temp_db.add_measurement_image(meas_id, image_path="/data/img_0.png", ordinal=0)
        temp_db.add_measurement_image(meas_id, image_path="/data/img_1.png", ordinal=1)

        images = temp_db.get_measurement_images(meas_id)

        assert [img.ordinal for img in images] == [0, 1, 2]
        assert [img.image_path for img in images] == [
            "/data/img_0.png",
            "/data/img_1.png",
            "/data/img_2.png",
        ]

    def test_get_measurement_images_only_for_requested_measurement(
        self, temp_db: Database
    ) -> None:
        """Снимки одного измерения не подмешиваются к снимкам другого."""
        meas_a = self._make_measurement(temp_db)
        meas_b = self._make_measurement(temp_db)
        temp_db.add_measurement_image(meas_a, image_path="/data/a.png")
        temp_db.add_measurement_image(meas_b, image_path="/data/b.png")

        images_a = temp_db.get_measurement_images(meas_a)

        assert len(images_a) == 1
        assert images_a[0].image_path == "/data/a.png"

    # -- add_measurement_images (пакетно, одна транзакция) --------------------

    def test_add_measurement_images_batch_returns_all_in_order(self, temp_db: Database) -> None:
        """Пакетная вставка серии возвращает объекты в том же порядке, что и вход."""
        meas_id = self._make_measurement(temp_db)

        images = temp_db.add_measurement_images(
            meas_id,
            [
                {"image_path": "/data/s0.png"},
                {"image_path": "/data/s1.png", "annotated_image_path": "/data/s1_ann.png"},
                {"image_path": "/data/s2.png"},
            ],
        )

        assert [img.image_path for img in images] == [
            "/data/s0.png",
            "/data/s1.png",
            "/data/s2.png",
        ]
        assert images[1].annotated_image_path == "/data/s1_ann.png"
        assert all(img.id is not None for img in images)

    def test_add_measurement_images_defaults_ordinal_to_list_position(
        self, temp_db: Database
    ) -> None:
        """Без явного ordinal у элемента -- берётся его позиция в списке (0-based)."""
        meas_id = self._make_measurement(temp_db)

        images = temp_db.add_measurement_images(
            meas_id,
            [{"image_path": "/data/s0.png"}, {"image_path": "/data/s1.png"}],
        )

        assert [img.ordinal for img in images] == [0, 1]

    def test_add_measurement_images_accepts_tuples(self, temp_db: Database) -> None:
        """Элементы серии можно описывать кортежами (image_path[, annotated[, ordinal]])."""
        meas_id = self._make_measurement(temp_db)

        images = temp_db.add_measurement_images(
            meas_id,
            [
                ("/data/t0.png",),
                ("/data/t1.png", "/data/t1_ann.png"),
                ("/data/t2.png", None, 9),
            ],
        )

        assert images[0].annotated_image_path is None
        assert images[0].ordinal == 0
        assert images[1].annotated_image_path == "/data/t1_ann.png"
        assert images[1].ordinal == 1
        assert images[2].ordinal == 9

    def test_add_measurement_images_persisted_and_retrievable(self, temp_db: Database) -> None:
        """Снимки, сохранённые пакетно, читаются обратно через get_measurement_images."""
        meas_id = self._make_measurement(temp_db)
        temp_db.add_measurement_images(
            meas_id,
            [{"image_path": "/data/p0.png"}, {"image_path": "/data/p1.png"}],
        )

        images = temp_db.get_measurement_images(meas_id)

        assert len(images) == 2

    def test_add_measurement_images_empty_list_saves_nothing(self, temp_db: Database) -> None:
        """Пустой список -- допустим, ничего не сохраняется, возвращается []."""
        meas_id = self._make_measurement(temp_db)

        images = temp_db.add_measurement_images(meas_id, [])

        assert images == []
        assert temp_db.get_measurement_images(meas_id) == []

    def test_add_measurement_images_invalid_entry_raises_and_saves_nothing(
        self, temp_db: Database
    ) -> None:
        """Пустой image_path у ЛЮБОГО элемента -> ValueError, весь пакет не сохраняется.

        Валидация всех элементов происходит до открытия транзакции -- даже
        валидные элементы, идущие перед некорректным в списке, не должны
        попасть в БД (атомарность "всё или ничего").
        """
        meas_id = self._make_measurement(temp_db)

        with pytest.raises(ValueError):
            temp_db.add_measurement_images(
                meas_id,
                [{"image_path": "/data/ok.png"}, {"image_path": "  "}],
            )

        assert temp_db.get_measurement_images(meas_id) == []

    def test_add_measurement_images_invalid_measurement_raises_integrity_error(
        self, temp_db: Database
    ) -> None:
        """Несуществующий measurement_id -> IntegrityError, ничего не сохраняется."""
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.add_measurement_images(
                99999,
                [{"image_path": "/data/orphan0.png"}, {"image_path": "/data/orphan1.png"}],
            )

        count = temp_db._conn.execute(
            "SELECT COUNT(*) FROM measurement_images"
        ).fetchone()[0]
        assert count == 0

    def test_add_measurement_images_up_to_five_per_measurement(self, temp_db: Database) -> None:
        """Типовой сценарий: до 5 снимков серии на одно измерение (METHODOLOGY v1.4)."""
        meas_id = self._make_measurement(temp_db)

        images = temp_db.add_measurement_images(
            meas_id,
            [{"image_path": f"/data/zone_F_{i}.png"} for i in range(5)],
        )

        assert len(images) == 5
        assert [img.ordinal for img in temp_db.get_measurement_images(meas_id)] == [
            0, 1, 2, 3, 4,
        ]


# ---------------------------------------------------------------------------
# Каскадное удаление пациента
# ---------------------------------------------------------------------------


class TestCascadeDeletePatient:
    """Тесты каскадного удаления пациента вместе с сессиями и измерениями."""

    def test_delete_patient_removes_sessions_and_measurements(self, temp_db: Database) -> None:
        """Удаление пациента удаляет все его сессии и измерения."""
        patient = temp_db.add_patient(name="На полное удаление")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        session = temp_db.add_session(patient.id, cal.id, date="2026-01-01")
        temp_db.save_measurement(session_id=session.id, zone="F", **_measurement_kwargs())

        temp_db.delete_patient(patient.id)

        assert temp_db.get_patient_sessions(patient.id) == []
        assert temp_db.get_session_measurements(session.id) == []
        assert temp_db.get_session(session.id) is None

    def test_delete_patient_does_not_affect_other_patients(self, temp_db: Database) -> None:
        """Удаление одного пациента не затрагивает данные другого."""
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        patient_a = temp_db.add_patient(name="Пациент А")
        patient_b = temp_db.add_patient(name="Пациент Б")
        session_b = temp_db.add_session(patient_b.id, cal.id, date="2026-01-01")

        temp_db.delete_patient(patient_a.id)

        assert temp_db.get_patient(patient_b.id) is not None
        assert temp_db.get_session(session_b.id) is not None

    def test_delete_patient_cascades_to_measurement_images(self, temp_db: Database) -> None:
        """Удаление пациента удаляет и снимки, привязанные к его измерениям."""
        patient = temp_db.add_patient(name="На полное удаление со снимками")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        session = temp_db.add_session(patient.id, cal.id, date="2026-01-01")
        meas = temp_db.save_measurement(session_id=session.id, zone="F", **_measurement_kwargs())
        temp_db.add_measurement_images(
            meas.id,
            [{"image_path": "/tmp/zone_F_1.png"}, {"image_path": "/tmp/zone_F_2.png"}],
        )

        deleted = temp_db.delete_patient(patient.id)

        assert deleted is True
        assert temp_db.get_measurement_images(meas.id) == []
        row_count = temp_db._conn.execute(
            "SELECT COUNT(*) FROM measurement_images WHERE measurement_id = ?",
            (meas.id,),
        ).fetchone()[0]
        assert row_count == 0


# ---------------------------------------------------------------------------
# Транзакции: create_session_with_measurements
# ---------------------------------------------------------------------------


class TestCreateSessionWithMeasurements:
    """Тесты атомарного создания сессии с измерениями (мультизональный анализ)."""

    def test_creates_session_and_all_measurements(self, temp_db: Database) -> None:
        """Успешный вызов создаёт сессию и все переданные измерения."""
        patient = temp_db.add_patient(name="Мультизона")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)

        session = temp_db.create_session_with_measurements(
            patient_id=patient.id,
            calibration_id=cal.id,
            date="2026-01-01",
            measurements_data=[
                {"zone": "F", **_measurement_kwargs()},
                {"zone": "O", **_measurement_kwargs()},
            ],
        )

        measurements = temp_db.get_session_measurements(session.id)
        assert len(measurements) == 2
        assert {m.zone for m in measurements} == {"F", "O"}

    def test_missing_optional_fields_use_defaults(self, temp_db: Database) -> None:
        """Отсутствующие необязательные поля в словаре берут значения по умолчанию."""
        patient = temp_db.add_patient(name="Мультизона")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)

        session = temp_db.create_session_with_measurements(
            patient_id=patient.id,
            calibration_id=cal.id,
            date="2026-01-01",
            measurements_data=[{"zone": "F"}],
        )

        measurements = temp_db.get_session_measurements(session.id)
        assert measurements[0].hair_count == 0
        assert measurements[0].density_per_cm2 == 0.0
        assert measurements[0].n_snapshots == 1
        assert measurements[0].density_std is None

    def test_series_fields_pass_through_dict(self, temp_db: Database) -> None:
        """Явно переданные в словаре n_snapshots/density_std сохраняются в БД."""
        patient = temp_db.add_patient(name="Серия снимков")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)

        session = temp_db.create_session_with_measurements(
            patient_id=patient.id,
            calibration_id=cal.id,
            date="2026-01-01",
            measurements_data=[
                {
                    "zone": "F",
                    **_measurement_kwargs(),
                    "n_snapshots": 5,
                    "density_std": 15.2,
                },
            ],
        )

        measurements = temp_db.get_session_measurements(session.id)
        assert measurements[0].n_snapshots == 5
        assert measurements[0].density_std == pytest.approx(15.2)

    def test_failure_mid_batch_rolls_back_session_too(self, temp_db: Database) -> None:
        """Ошибка в середине пакета измерений откатывает и созданную сессию.

        Отсутствие обязательного ключа 'zone' у одного из измерений вызывает
        KeyError. sqlite3.Connection как контекстный менеджер откатывает
        транзакцию при ЛЮБОМ исключении (не только sqlite3.Error), поэтому
        уже вставленная строка sessions тоже должна быть отменена.
        """
        patient = temp_db.add_patient(name="Откат")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        before = temp_db._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

        with pytest.raises(KeyError):
            temp_db.create_session_with_measurements(
                patient_id=patient.id,
                calibration_id=cal.id,
                date="2026-01-01",
                measurements_data=[{"hair_count": 10}],  # нет обязательного "zone"
            )

        after = temp_db._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        assert after == before

    def test_invalid_patient_id_raises_and_rolls_back(self, temp_db: Database) -> None:
        """Несуществующий patient_id -> IntegrityError, без частичной записи."""
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)

        with pytest.raises(sqlite3.IntegrityError):
            temp_db.create_session_with_measurements(
                patient_id=99999,
                calibration_id=cal.id,
                date="2026-01-01",
                measurements_data=[{"zone": "F", **_measurement_kwargs()}],
            )

        sessions_count = temp_db._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        assert sessions_count == 0

    def test_empty_measurements_list_creates_session_only(self, temp_db: Database) -> None:
        """Пустой список измерений -- допустим, создаётся только сессия."""
        patient = temp_db.add_patient(name="Без измерений")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)

        session = temp_db.create_session_with_measurements(
            patient_id=patient.id,
            calibration_id=cal.id,
            date="2026-01-01",
            measurements_data=[],
        )

        assert session.id is not None
        assert temp_db.get_session_measurements(session.id) == []


# ---------------------------------------------------------------------------
# Резервное копирование и восстановление: backup_to / restore_from
# ---------------------------------------------------------------------------


class TestBackupTo:
    """Тесты backup_to() -- атомарное резервное копирование через backup API."""

    def test_creates_nonempty_file(self, temp_db: Database, tmp_path: Path) -> None:
        """backup_to() создаёт непустой файл резервной копии."""
        temp_db.add_patient(name="Бэкап Пациент")
        backup_path = tmp_path / "backup.db"

        temp_db.backup_to(backup_path)

        assert backup_path.is_file()
        assert backup_path.stat().st_size > 0

    def test_creates_missing_parent_directories(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Недостающие родительские директории dest_path создаются автоматически."""
        backup_path = tmp_path / "a" / "b" / "c" / "backup.db"

        temp_db.backup_to(backup_path)

        assert backup_path.is_file()

    def test_backup_opens_as_valid_database_with_same_patient(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Бэкап открывается отдельной Database и содержит те же данные."""
        temp_db.add_patient(name="Иванова А.В.", gender="F")
        backup_path = tmp_path / "backup.db"

        temp_db.backup_to(backup_path)

        backup_db = Database(backup_path)
        try:
            patients = backup_db.get_all_patients()
            assert len(patients) == 1
            assert patients[0].name == "Иванова А.В."
            assert patients[0].gender == "F"
        finally:
            backup_db.close()

    def test_backup_file_is_self_contained_without_wal(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Файл бэкапа не в WAL-режиме -- самодостаточный однофайловый .db.

        Без принудительного PRAGMA journal_mode=DELETE после backup() файл
        унаследовал бы из заголовка исходной БД пометку WAL (backup()
        копирует и постранично содержимое, включая заголовок), хотя рядом
        с ним не будет ни -wal, ни -shm файла -- обманчивое полусостояние.
        """
        temp_db.add_patient(name="Без WAL")
        backup_path = tmp_path / "backup.db"

        temp_db.backup_to(backup_path)

        check_conn = sqlite3.connect(str(backup_path))
        try:
            mode = check_conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode != "wal"
        finally:
            check_conn.close()
        assert not Path(f"{backup_path}-wal").exists()
        assert not Path(f"{backup_path}-shm").exists()

    def test_repeated_backup_overwrites_previous_destination_content(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Повторный backup_to() в тот же путь полностью заменяет содержимое."""
        backup_path = tmp_path / "backup.db"
        temp_db.add_patient(name="Первый")
        temp_db.backup_to(backup_path)

        temp_db.add_patient(name="Второй")
        temp_db.backup_to(backup_path)

        backup_db = Database(backup_path)
        try:
            names = {p.name for p in backup_db.get_all_patients()}
            assert names == {"Первый", "Второй"}
        finally:
            backup_db.close()

    def test_backup_captures_data_committed_under_wal(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Данные, закоммиченные в WAL-режиме, но ещё не checkpoint'нутые в
        основной .db файл, всё равно попадают в бэкап -- это и есть смысл
        Online Backup API (см. docstring backup_to()): наивное копирование
        одного лишь .db файла такие данные бы потеряло.
        """
        patient = temp_db.add_patient(name="WAL Пациент")
        cal = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
        session = temp_db.add_session(patient.id, cal.id, "2026-01-01", zone="O")
        temp_db.save_measurement(session_id=session.id, zone="O", **_measurement_kwargs())
        backup_path = tmp_path / "backup.db"

        temp_db.backup_to(backup_path)

        backup_db = Database(backup_path)
        try:
            patients = backup_db.get_all_patients()
            assert len(patients) == 1
            measurements = backup_db.get_patient_measurements(patients[0].id)
            assert len(measurements) == 1
            assert measurements[0].hair_count == 100
            assert measurements[0].zone == "O"
        finally:
            backup_db.close()

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root обходит проверку прав доступа файловой системы",
    )
    def test_filesystem_error_is_wrapped_as_backup_error(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Ошибка файловой системы (нет прав на запись) оборачивается в BackupError.

        backup_to() документирует, что ЛЮБАЯ ошибка создания бэкапа (нет
        прав, нет места на диске и т.п.) поднимается именно как
        BackupError -- единый тип исключения для вызывающего кода (GUI),
        а не разномастные OSError/sqlite3.Error.
        """
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o555)  # r-xr-xr-x -- без права записи
        backup_path = readonly_dir / "subdir" / "backup.db"

        try:
            with pytest.raises(BackupError):
                temp_db.backup_to(backup_path)
        finally:
            readonly_dir.chmod(0o755)  # чтобы pytest мог удалить tmp_path


class TestRestoreFrom:
    """Тесты restore_from() -- безопасное восстановление БД из бэкапа."""

    def test_restore_reverts_database_to_backup_state(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """restore_from() возвращает БД к состоянию на момент бэкапа."""
        temp_db.add_patient(name="Пациент A")
        backup_path = tmp_path / "backup.db"
        temp_db.backup_to(backup_path)

        temp_db.add_patient(name="Пациент B")
        assert {p.name for p in temp_db.get_all_patients()} == {"Пациент A", "Пациент B"}

        temp_db.restore_from(backup_path)

        assert {p.name for p in temp_db.get_all_patients()} == {"Пациент A"}

    def test_restore_keeps_db_path_attribute_unchanged(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """restore_from() подменяет содержимое файла, но не сам self.db_path."""
        original_path = temp_db.db_path
        backup_path = tmp_path / "backup.db"
        temp_db.add_patient(name="Пациент")
        temp_db.backup_to(backup_path)

        temp_db.restore_from(backup_path)

        assert temp_db.db_path == original_path

    def test_connection_is_usable_after_restore(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """После restore_from() соединение работоспособно -- можно писать дальше."""
        temp_db.add_patient(name="Пациент A")
        backup_path = tmp_path / "backup.db"
        temp_db.backup_to(backup_path)

        temp_db.restore_from(backup_path)
        new_patient = temp_db.add_patient(name="Новый после restore")

        assert new_patient.id is not None
        assert temp_db.get_patient(new_patient.id) is not None
        assert {p.name for p in temp_db.get_all_patients()} == {
            "Пациент A",
            "Новый после restore",
        }

    def test_restore_removes_stale_foreign_wal_preventing_data_mixing(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Осиротевший ВАЛИДНЫЙ -wal чужой БД не подмешивается в восстановленные данные.

        Без явного удаления {db_path}-wal/{db_path}-shm внутри restore_from()
        SQLite может обнаружить рядом со свежеподставленным файлом БД чужой
        WAL-журнал (валидный по структуре, но принадлежащий совершенно
        другому поколению данных) и «доиграть» его поверх при следующем
        открытии, вернув чужое/устаревшее содержимое вместо только что
        восстановленного бэкапа. Тест воспроизводит именно эту опасность:
        подкладывает валидный, но полностью посторонний WAL-файл на место
        {db_path}-wal перед вызовом restore_from() и проверяет, что он не
        просочился в итоговые данные.
        """
        temp_db.add_patient(name="Пациент A")
        backup_path = tmp_path / "backup.db"
        temp_db.backup_to(backup_path)

        # Совершенно отдельная БД -- источник ВАЛИДНОГО, но постороннего
        # WAL-журнала (тот же приём, что и у настоящей БД: WAL-режим,
        # запись, закоммиченная на уровне транзакции, но ещё физически
        # лежащая в -wal, не в основном файле).
        foreign_path = tmp_path / "foreign.db"
        foreign_conn = sqlite3.connect(str(foreign_path))
        foreign_conn.execute("PRAGMA journal_mode=WAL")
        foreign_conn.execute("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)")
        foreign_conn.execute("INSERT INTO patients (name) VALUES ('Чужой Пациент')")
        foreign_conn.commit()
        foreign_wal_bytes = Path(f"{foreign_path}-wal").read_bytes()
        foreign_conn.close()

        db_path = temp_db.db_path
        temp_db.close()
        # Имитация осиротевшего WAL от старой БД, не убранного при закрытии
        # (например, из-за аварийного завершения предыдущего процесса).
        Path(f"{db_path}-wal").write_bytes(foreign_wal_bytes)

        temp_db.restore_from(backup_path)

        assert {p.name for p in temp_db.get_all_patients()} == {"Пациент A"}
        # И соединение по-прежнему рабочее.
        temp_db.add_patient(name="Контрольная запись")
        assert len(temp_db.get_all_patients()) == 2

    def test_restore_from_legacy_v10_schema_migrates_to_current(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Восстановление из бэкапа со схемой v10 (без measurement_images)
        доводит БД до текущей версии схемы (11), данные из бэкапа целы.

        Схема бэкапа воспроизводится вручную из миграций 1..10, как и в
        TestMigrations.test_legacy_db_without_measurement_images_table_is_migrated
        -- имитация бэкапа, снятого более старой версией приложения.
        """
        temp_db.add_patient(name="Текущий пациент до restore")
        legacy_path = tmp_path / "legacy_v10_backup.db"

        raw = sqlite3.connect(str(legacy_path))
        try:
            for version, sql in _MIGRATIONS:
                if version >= 11:
                    continue
                for statement in _split_statements(sql):
                    raw.execute(statement)
            raw.execute(
                "INSERT INTO patients (name, gender) VALUES (?, ?)",
                ("Легаси Пациент", "F"),
            )
            raw.execute(
                "INSERT INTO calibrations "
                "(um_per_px, fov_width_px, fov_height_px, "
                " fov_width_mm, fov_height_mm, fov_area_mm2) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (5.9, 640, 480, 3.776, 2.832, 10.694),
            )
            raw.execute(
                "INSERT INTO sessions (patient_id, calibration_id, date) VALUES (?, ?, ?)",
                (1, 1, "2026-01-01"),
            )
            raw.execute(
                "INSERT INTO measurements (session_id, zone, hair_count, density_per_cm2) "
                "VALUES (?, ?, ?, ?)",
                (1, "F", 120, 210.5),
            )
            raw.commit()
            raw.execute("PRAGMA user_version=10")
            raw.commit()
        finally:
            raw.close()

        temp_db.restore_from(legacy_path)

        version = temp_db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 11

        patients = temp_db.get_all_patients()
        assert len(patients) == 1
        assert patients[0].name == "Легаси Пациент"

        measurements = temp_db.get_session_measurements(1)
        assert len(measurements) == 1
        assert measurements[0].hair_count == 120
        assert measurements[0].n_snapshots == 1
        assert measurements[0].density_std is None

        tables = {
            row["name"]
            for row in temp_db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "measurement_images" in tables

    def test_restore_from_nonexistent_file_raises_without_creating_it(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Несуществующий src_path -- BackupError, файл при этом не создаётся.

        sqlite3.connect() на несуществующем пути создал бы там пустой файл
        БД -- _validate_backup_file() обязана проверить существование ДО
        открытия соединения, иначе на диске появлялся бы паразитный файл.
        """
        missing_path = tmp_path / "does_not_exist.db"

        with pytest.raises(BackupError):
            temp_db.restore_from(missing_path)

        assert not missing_path.exists()

    def test_restore_from_garbage_file_raises_and_current_db_stays_intact(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Восстановление из текстового мусора -- BackupError, текущая БД цела и рабочая."""
        temp_db.add_patient(name="Нетронутый Пациент")
        garbage_path = tmp_path / "garbage.db"
        garbage_path.write_text("это не файл SQLite, просто текст\n" * 5)

        with pytest.raises(BackupError):
            temp_db.restore_from(garbage_path)

        patients = temp_db.get_all_patients()
        assert len(patients) == 1
        assert patients[0].name == "Нетронутый Пациент"
        # Соединение по-прежнему рабочее -- сбой валидации не оставил его закрытым.
        temp_db.add_patient(name="Ещё один после сбоя валидации")
        assert len(temp_db.get_all_patients()) == 2

    def test_restore_from_db_without_required_tables_raises(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Валидный SQLite-файл без таблиц приложения -- BackupError.

        Проверяет ветку валидации "структурно исправный SQLite, но не БД
        этого приложения" -- отдельно от "файл повреждён/не SQLite вообще".
        """
        temp_db.add_patient(name="Нетронутый Пациент 2")
        unrelated_path = tmp_path / "unrelated_schema.db"
        raw = sqlite3.connect(str(unrelated_path))
        raw.execute("CREATE TABLE unrelated (id INTEGER)")
        raw.execute("PRAGMA user_version=1")
        raw.commit()
        raw.close()

        with pytest.raises(BackupError):
            temp_db.restore_from(unrelated_path)

        assert len(temp_db.get_all_patients()) == 1

    def test_restore_from_empty_file_raises(self, temp_db: Database, tmp_path: Path) -> None:
        """0-байтовый файл -- валиден как пустая SQLite БД (integrity_check 'ok'),
        но без таблиц и с user_version=0 -- отклоняется как БД приложения.
        """
        empty_path = tmp_path / "empty.db"
        empty_path.write_bytes(b"")

        with pytest.raises(BackupError):
            temp_db.restore_from(empty_path)


class TestBackupRestorePathGuards:
    """Запреты бэкапа/восстановления поверх активной БД и различение ошибок."""

    def test_backup_to_active_db_path_raises(self, temp_db: Database) -> None:
        """backup_to поверх активной БД → BackupError (не порча живого файла)."""
        temp_db.add_patient(name="Целевой")
        with pytest.raises(BackupError, match="поверх активной"):
            temp_db.backup_to(temp_db.db_path)
        # БД цела и работоспособна.
        assert {p.name for p in temp_db.get_all_patients()} == {"Целевой"}

    def test_restore_from_active_db_path_raises(self, temp_db: Database) -> None:
        """restore_from из активной БД → BackupError, данные целы."""
        temp_db.add_patient(name="Целевой")
        with pytest.raises(BackupError, match="из активной"):
            temp_db.restore_from(temp_db.db_path)
        assert {p.name for p in temp_db.get_all_patients()} == {"Целевой"}

    def test_backup_over_existing_file_leaves_valid_backup(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """Повторный backup_to в тот же путь оставляет валидную актуальную копию."""
        backup_path = tmp_path / "b.db"
        temp_db.add_patient(name="Первый")
        temp_db.backup_to(backup_path)
        temp_db.add_patient(name="Второй")
        temp_db.backup_to(backup_path)  # temp→rename, старую копию не бьёт при сбое

        with Database(backup_path) as restored:
            assert {p.name for p in restored.get_all_patients()} == {"Первый", "Второй"}

    def test_connection_usable_after_restore(
        self, temp_db: Database, tmp_path: Path
    ) -> None:
        """После успешного restore соединение рабочее (можно писать)."""
        temp_db.add_patient(name="A")
        backup_path = tmp_path / "b.db"
        temp_db.backup_to(backup_path)
        temp_db.restore_from(backup_path)
        # Соединение переоткрыто и работает.
        temp_db.add_patient(name="B")
        assert {p.name for p in temp_db.get_all_patients()} == {"A", "B"}

    def test_restore_error_is_backup_error_subclass(self) -> None:
        """RestoreError — подкласс BackupError (GUI ловит RestoreError раньше)."""
        assert issubclass(RestoreError, BackupError)

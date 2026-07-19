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
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phototrichogram.data.database import _MIGRATIONS, Database

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

        assert version == 10
        db.close()

    def test_reopening_db_is_idempotent(self, tmp_path: Path) -> None:
        """Повторное открытие той же БД не переприменяет миграции."""
        db_path = tmp_path / "reopen.db"
        db1 = Database(db_path)
        db1.close()

        db2 = Database(db_path)
        version = db2._conn.execute("PRAGMA user_version").fetchone()[0]

        assert version == 10
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
        measurements), версия схемы догоняет до последней (10).
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
            assert version == 10

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
        """БД на версии схемы 9 (без n_snapshots/density_std) догоняет до v10.

        Схема воспроизводится вручную из миграций 1..9 (без 10) -- имитация
        реальной базы пользователя, обновившегося с версии приложения до
        появления серии снимков (план v1.4). Открытие через Database должно
        добавить обе колонки без потери существующих измерений, а старые
        измерения -- получить n_snapshots=1, density_std=NULL (в модели --
        None), как для одиночного снимка.
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
            assert version == 10

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

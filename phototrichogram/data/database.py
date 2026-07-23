"""
SQLite база данных: подключение, миграции, CRUD.

Поддерживает:
- WAL-режим для конкурентного чтения
- Внешние ключи (FOREIGN KEY ON DELETE RESTRICT)
- Версионные миграции через PRAGMA user_version
- Контекстный менеджер (with Database(...) as db)
- Транзакции с автоматическим rollback при ошибках
- Резервное копирование и восстановление (backup_to/restore_from) через
  Online Backup API SQLite — корректно и атомарно при включённом WAL

Используется только stdlib: sqlite3, pathlib, logging, datetime, os,
shutil, tempfile. НЕ импортирует cv2 или tkinter.

Пример использования::

    from pathlib import Path
    from phototrichogram.data.database import Database

    with Database(Path("~/.phototrichogram/data.db").expanduser()) as db:
        patient = db.add_patient(name="Иванова А.В.", gender="F")
        calibration = db.get_latest_calibration()
        session = db.add_session(patient.id, calibration.id, "2026-03-15")
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from phototrichogram.data.models import (
    Calibration,
    Measurement,
    MeasurementImage,
    Patient,
    Session,
)

logger = logging.getLogger(__name__)

# ─── Путь к данным по умолчанию ──────────────────────────────────────────────

DEFAULT_DATA_DIR = Path.home() / ".phototrichogram"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "phototrichogram.db"

# ─── Схема: версионные миграции ───────────────────────────────────────────────
#
# Каждый элемент — (версия, SQL).
# _run_migrations() применяет только те, у которых версия > текущей user_version.
# Номера строго последовательные и неизменяемые — добавлять только в конец.

_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS patients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            birth_date  TEXT,
            gender      TEXT,
            diagnosis   TEXT,
            notes       TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS calibrations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            um_per_px     REAL    NOT NULL,
            fov_width_px  INTEGER NOT NULL,
            fov_height_px INTEGER NOT NULL,
            fov_width_mm  REAL    NOT NULL,
            fov_height_mm REAL    NOT NULL,
            fov_area_mm2  REAL    NOT NULL,
            description   TEXT,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id       INTEGER NOT NULL,
            calibration_id   INTEGER NOT NULL,
            date             TEXT    NOT NULL,
            zone             TEXT,
            notes            TEXT,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (patient_id)     REFERENCES patients(id),
            FOREIGN KEY (calibration_id) REFERENCES calibrations(id)
        )
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS measurements (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            INTEGER NOT NULL,
            zone                  TEXT    NOT NULL,

            hair_count            INTEGER NOT NULL DEFAULT 0,
            density_per_cm2       REAL    NOT NULL DEFAULT 0.0,

            mean_thickness_um     REAL    NOT NULL DEFAULT 0.0,
            median_thickness_um   REAL    NOT NULL DEFAULT 0.0,
            std_thickness_um      REAL    NOT NULL DEFAULT 0.0,
            min_thickness_um      REAL    NOT NULL DEFAULT 0.0,
            max_thickness_um      REAL    NOT NULL DEFAULT 0.0,

            terminal_count        INTEGER NOT NULL DEFAULT 0,
            vellus_count          INTEGER NOT NULL DEFAULT 0,
            intermediate_count    INTEGER NOT NULL DEFAULT 0,
            terminal_pct          REAL    NOT NULL DEFAULT 0.0,
            vellus_pct            REAL    NOT NULL DEFAULT 0.0,
            intermediate_pct      REAL    NOT NULL DEFAULT 0.0,
            tv_ratio              REAL    NOT NULL DEFAULT 0.0,

            fov_area_mm2          REAL    NOT NULL DEFAULT 0.0,

            image_path            TEXT,
            annotated_image_path  TEXT,
            assessment            TEXT,

            created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
        """,
    ),
    (
        5,
        "CREATE INDEX IF NOT EXISTS idx_sessions_patient   ON sessions(patient_id)",
    ),
    (
        6,
        "CREATE INDEX IF NOT EXISTS idx_sessions_date      ON sessions(date)",
    ),
    (
        7,
        "CREATE INDEX IF NOT EXISTS idx_measurements_session ON measurements(session_id)",
    ),
    (
        8,
        "CREATE INDEX IF NOT EXISTS idx_measurements_zone  ON measurements(session_id, zone)",
    ),
    (
        9,
        "ALTER TABLE patients ADD COLUMN ethnicity TEXT",
    ),
    (
        10,
        """
        ALTER TABLE measurements ADD COLUMN n_snapshots INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE measurements ADD COLUMN density_std  REAL
        """,
    ),
    (
        11,
        """
        CREATE TABLE IF NOT EXISTS measurement_images (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            measurement_id        INTEGER NOT NULL,
            image_path            TEXT    NOT NULL,
            annotated_image_path  TEXT,
            ordinal               INTEGER NOT NULL DEFAULT 0,
            created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (measurement_id) REFERENCES measurements(id)
        );
        CREATE INDEX IF NOT EXISTS idx_measurement_images_measurement
            ON measurement_images(measurement_id)
        """,
    ),
]


def _split_statements(sql: str) -> list[str]:
    """Разбивает текст миграции на отдельные SQL-инструкции по ';'.

    ``sqlite3.Connection.execute()`` выполняет только одну инструкцию за
    вызов, а SQLite не умеет добавлять несколько колонок одним
    ``ALTER TABLE`` — поэтому одна версия миграции иногда содержит
    несколько DDL-операторов подряд (например, два ``ADD COLUMN`` в v10).
    Пустые/пробельные фрагменты (в т.ч. из-за отступов многострочных
    строк) отбрасываются.

    ВНИМАНИЕ: разбиение наивное — по каждому ';'. Поэтому SQL миграций НЕ
    должен содержать ';' внутри строковых литералов (например, в DEFAULT) или
    в теле ``CREATE TRIGGER ... BEGIN ...; ... END`` — иначе оператор
    порежется на невалидные части. Для таких миграций храните SQL как список
    отдельных инструкций, а не одну строку через ';'.

    Args:
        sql: Текст миграции — одна или несколько инструкций через ';'.

    Returns:
        Список непустых SQL-инструкций без начальных/конечных пробелов.
    """
    return [part.strip() for part in sql.split(";") if part.strip()]


def _now() -> str:
    """Текущее время UTC, формат 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_measurement_image_entry(
    index: int, item: dict[str, Any] | tuple[Any, ...]
) -> tuple[str, str | None, int]:
    """Приведение одного элемента серии снимков к кортежу для вставки в БД.

    Используется методом ``Database.add_measurement_images()`` — каждый
    снимок серии можно описать словарём с ключами ``image_path``
    (обязательный), ``annotated_image_path`` (необязательный, по
    умолчанию None) и ``ordinal`` (необязательный), либо кортежем
    ``(image_path,)``, ``(image_path, annotated_image_path)`` или
    ``(image_path, annotated_image_path, ordinal)``. Если ``ordinal`` не
    указан явно — берётся позиция элемента в исходном списке (0-based),
    что соответствует естественному порядку съёмки серии.

    Args:
        index: Позиция элемента в исходном списке (0-based) — значение
            ordinal по умолчанию, если оно не задано явно.
        item: Описание снимка — словарь или кортеж (см. выше).

    Returns:
        Кортеж (image_path, annotated_image_path, ordinal).

    Raises:
        ValueError: Если image_path отсутствует, пустой или состоит
            только из пробелов.
    """
    if isinstance(item, dict):
        image_path = item.get("image_path")
        annotated_image_path = item.get("annotated_image_path")
        ordinal = item.get("ordinal", index)
    else:
        image_path = item[0] if len(item) > 0 else None
        annotated_image_path = item[1] if len(item) > 1 else None
        ordinal = item[2] if len(item) > 2 else index

    if not image_path or not str(image_path).strip():
        raise ValueError(
            f"Путь к снимку не может быть пустым (элемент серии #{index})"
        )

    return str(image_path), annotated_image_path, int(ordinal)


# ─── Резервное копирование и восстановление ──────────────────────────────────

# Таблицы, наличие которых подтверждает, что файл — БД именно этого
# приложения, а не произвольный (пусть даже структурно валидный) файл SQLite.
_BACKUP_REQUIRED_TABLES = frozenset({"patients", "sessions", "measurements"})


class BackupError(Exception):
    """Ошибка резервного копирования или восстановления БД.

    Гарантия для ``restore_from``: если поднят именно ``BackupError`` (а не
    подкласс ``RestoreError``), замена файла БД ещё НЕ началась — текущие данные
    целы, соединение рабочее. Причина: невалидный файл бэкапа, запрет путей,
    сбой копирования ДО точки невозврата (os.replace).
    """


class RestoreError(BackupError):
    """Восстановление прервано ПОСЛЕ начала замены файла БД.

    Поднимается, когда ``os.replace`` уже подставил данные бэкапа, но
    последующий шаг (очистка WAL/SHM или переоткрытие соединения) упал. В
    отличие от ``BackupError``, здесь утверждать «текущие данные сохранены»
    НЕЛЬЗЯ: файл БД уже заменён, состояние приложения неопределённо —
    пользователю следует перезапустить программу.
    """


def _validate_backup_file(src_path: Path) -> None:
    """Проверка, что src_path — корректный файл БД приложения.

    Открывает src_path отдельным, независимым соединением sqlite3 и
    ничего в него не записывает. Состояние текущей (открытой) БД
    приложения эта функция не видит и никак не затрагивает — вызывается
    ДО того, как restore_from() решится закрыть текущее соединение и
    заменить файл. Если валидация не пройдена — поднимается BackupError
    и текущая БД остаётся полностью нетронутой и рабочей.

    Args:
        src_path: Путь к предполагаемому файлу резервной копии.

    Raises:
        BackupError: Если файл не существует, не открывается, повреждён
            (PRAGMA integrity_check вернул не 'ok'), не содержит ключевых
            таблиц приложения (patients, sessions, measurements) или имеет
            user_version <= 0 (пустая либо неинициализированная схема —
            в частности, так выглядит любой файл SQLite без применённых
            миграций этого приложения).
    """
    if not src_path.is_file():
        raise BackupError(f"Файл резервной копии не найден: {src_path}")

    try:
        check_conn = sqlite3.connect(str(src_path))
    except sqlite3.Error as exc:
        raise BackupError(
            f"Не удалось открыть файл резервной копии: {src_path}"
        ) from exc

    try:
        try:
            integrity_rows = check_conn.execute("PRAGMA integrity_check").fetchall()
            integrity_ok = len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"

            existing_tables = {
                row[0]
                for row in check_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            user_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            # Например, "file is not a database" — файл не является SQLite
            # вообще (текстовый мусор, обрезанная копия и т.п.).
            raise BackupError(
                f"Файл повреждён или не является базой данных SQLite: {src_path}"
            ) from exc

        if not integrity_ok:
            details = "; ".join(str(row[0]) for row in integrity_rows)
            raise BackupError(
                f"Проверка целостности файла не пройдена ({src_path}): {details}"
            )

        missing = _BACKUP_REQUIRED_TABLES - existing_tables
        if missing:
            raise BackupError(
                "Файл не похож на БД приложения — отсутствуют таблицы "
                f"{sorted(missing)}: {src_path}"
            )

        if user_version <= 0:
            raise BackupError(
                f"Некорректная версия схемы БД (user_version={user_version}): {src_path}"
            )
    finally:
        check_conn.close()


# ─── Database ─────────────────────────────────────────────────────────────────


class Database:
    """SQLite база данных приложения.

    Управляет подключением, миграциями и CRUD-операциями для всех сущностей.

    Args:
        db_path: Путь к файлу базы данных. Директория создаётся автоматически.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        """Открытие (или создание) базы данных и применение миграций.

        Args:
            db_path: Путь к файлу SQLite. Будет создан если не существует.
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._open()
        logger.info("База данных открыта: %s", db_path)

    def _open(self) -> None:
        """Открытие соединения с self.db_path: директория, PRAGMA, миграции.

        Общая логика для __init__() и restore_from() — вынесена сюда,
        чтобы не дублировать её: оба места должны открыть соединение с
        self.db_path совершенно одинаково (WAL, внешние ключи,
        пользовательская функция py_lower(), версионные миграции).

        Создаёт self.db_path.parent, если её ещё нет (актуально и для
        первого запуска в __init__(), и для restore_from() — на случай,
        если каталог данных был удалён между закрытием старого
        соединения и восстановлением).
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Пользовательская функция lower() с поддержкой кириллицы.
        # Стандартный SQLite LOWER() обрабатывает только ASCII.
        self._conn.create_function("py_lower", 1, lambda s: s.lower() if s else "")

        # WAL-режим — одновременные читатели не блокируют запись
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Внешние ключи — явная целостность данных
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._run_migrations()

    # ── Контекстный менеджер ─────────────────────────────────────────────────

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Закрытие соединения с базой данных."""
        if self._conn is None:
            return
        try:
            self._conn.close()
            logger.debug("Соединение с БД закрыто: %s", self.db_path)
        except Exception as exc:
            logger.warning("Ошибка закрытия соединения с БД: %s", exc)
        finally:
            self._conn = None

    # ── Внутренние утилиты ───────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Сужение типа соединения с ``Connection | None`` до ``Connection``.

        Поле ``self._conn`` объявлено опциональным только для отражения
        состояния после close(). Внутри класса соединение используется
        исключительно пока объект жив, поэтому здесь допустимо статическое
        сужение типа через cast() — оно не выполняет проверку во время
        исполнения и не меняет поведение (обращение к атрибутам закрытого
        соединения приведёт к тому же исключению, что и раньше).

        Returns:
            Текущее соединение sqlite3, типизированное как не-Optional.
        """
        return cast(sqlite3.Connection, self._conn)

    # ── Миграции ─────────────────────────────────────────────────────────────

    def _run_migrations(self) -> None:
        """Применение версионных миграций схемы БД.

        Использует PRAGMA user_version для хранения текущей версии схемы.
        Применяются только миграции с версией > текущей.
        При ошибке — rollback, исключение пробрасывается наверх.
        """
        conn = self._get_conn()
        current_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        logger.debug("Текущая версия схемы БД: %d", current_version)

        pending = [(v, sql) for v, sql in _MIGRATIONS if v > current_version]
        if not pending:
            logger.debug("Миграции не требуются")
            return

        for version, sql in pending:
            if not isinstance(version, int) or version < 1:
                raise ValueError(f"Некорректная версия миграции: {version}")
            logger.info("Применяю миграцию v%d", version)
            try:
                # PRAGMA user_version обновляется в ТОЙ ЖЕ транзакции, что и DDL:
                # иначе при падении процесса между ними неидемпотентная миграция
                # (ADD COLUMN) повторится при следующем старте и упадёт с
                # "duplicate column", ломая открытие БД.
                # BEGIN — явно: sqlite3 (legacy-режим транзакций, действующий по
                # умолчанию) не открывает неявную транзакцию перед DDL (ALTER
                # TABLE/CREATE TABLE), в отличие от INSERT/UPDATE/DELETE —
                # без явного BEGIN каждый execute() автокоммитится сам по себе,
                # и `with conn:` не сможет откатить DDL при ошибке. Один SQL
                # миграции может содержать несколько инструкций подряд
                # (например, два ALTER TABLE ADD COLUMN в v10) — execute()
                # выполняет только одну за вызов, поэтому текст режется на
                # части в _split_statements().
                with conn:
                    conn.execute("BEGIN")
                    for statement in _split_statements(sql):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={int(version)}")
            except sqlite3.Error as exc:
                logger.error("Ошибка миграции v%d: %s", version, exc)
                raise

        final_version = conn.execute("PRAGMA user_version").fetchone()[0]
        logger.info("Схема БД обновлена до версии %d", final_version)

    # ── Вспомогательные методы ───────────────────────────────────────────────

    def _row_to_patient(self, row: sqlite3.Row) -> Patient:
        """Конвертация строки БД в объект Patient."""
        return Patient(
            id=row["id"],
            name=row["name"],
            birth_date=row["birth_date"],
            gender=row["gender"],
            diagnosis=row["diagnosis"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            ethnicity=row["ethnicity"],
        )

    def _row_to_calibration(self, row: sqlite3.Row) -> Calibration:
        """Конвертация строки БД в объект Calibration."""
        return Calibration(
            id=row["id"],
            um_per_px=row["um_per_px"],
            fov_width_px=row["fov_width_px"],
            fov_height_px=row["fov_height_px"],
            fov_width_mm=row["fov_width_mm"],
            fov_height_mm=row["fov_height_mm"],
            fov_area_mm2=row["fov_area_mm2"],
            description=row["description"],
            created_at=row["created_at"],
        )

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Конвертация строки БД в объект Session."""
        return Session(
            id=row["id"],
            patient_id=row["patient_id"],
            calibration_id=row["calibration_id"],
            date=row["date"],
            zone=row["zone"],
            notes=row["notes"],
            created_at=row["created_at"],
        )

    def _row_to_measurement(self, row: sqlite3.Row) -> Measurement:
        """Конвертация строки БД в объект Measurement."""
        return Measurement(
            id=row["id"],
            session_id=row["session_id"],
            zone=row["zone"],
            hair_count=row["hair_count"],
            density_per_cm2=row["density_per_cm2"],
            mean_thickness_um=row["mean_thickness_um"],
            median_thickness_um=row["median_thickness_um"],
            std_thickness_um=row["std_thickness_um"],
            min_thickness_um=row["min_thickness_um"],
            max_thickness_um=row["max_thickness_um"],
            terminal_count=row["terminal_count"],
            vellus_count=row["vellus_count"],
            intermediate_count=row["intermediate_count"],
            terminal_pct=row["terminal_pct"],
            vellus_pct=row["vellus_pct"],
            intermediate_pct=row["intermediate_pct"],
            tv_ratio=row["tv_ratio"],
            fov_area_mm2=row["fov_area_mm2"],
            image_path=row["image_path"],
            annotated_image_path=row["annotated_image_path"],
            assessment=row["assessment"],
            created_at=row["created_at"],
            n_snapshots=row["n_snapshots"],
            density_std=row["density_std"],
        )

    def _row_to_measurement_image(self, row: sqlite3.Row) -> MeasurementImage:
        """Конвертация строки БД в объект MeasurementImage."""
        return MeasurementImage(
            id=row["id"],
            measurement_id=row["measurement_id"],
            image_path=row["image_path"],
            annotated_image_path=row["annotated_image_path"],
            ordinal=row["ordinal"],
            created_at=row["created_at"],
        )

    # ── CRUD: Patient ─────────────────────────────────────────────────────────

    def add_patient(
        self,
        name: str,
        birth_date: str | None = None,
        gender: str | None = None,
        diagnosis: str | None = None,
        notes: str | None = None,
        ethnicity: str | None = None,
    ) -> Patient:
        """Создание нового профиля пациента.

        Args:
            name: Полное имя пациента (обязательное).
            birth_date: Дата рождения в формате YYYY-MM-DD или None.
            gender: Пол ('M', 'F') или None.
            diagnosis: Предварительный диагноз или None.
            notes: Произвольные заметки или None.
            ethnicity: Этническая принадлежность (код, см. Patient.ethnicity)
                или None, если не указана.

        Returns:
            Объект Patient с заполненным id и created_at.

        Raises:
            sqlite3.Error: При ошибке записи в БД.
            ValueError: Если name пустая строка.
        """
        if not name or not name.strip():
            raise ValueError("Имя пациента не может быть пустым")

        now = _now()
        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO patients (name, birth_date, gender, diagnosis, notes,
                                         ethnicity, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name.strip(), birth_date, gender, diagnosis, notes, ethnicity, now, now),
                )
                patient_id = cursor.lastrowid
        except sqlite3.Error as exc:
            logger.error("Ошибка создания пациента '%s': %s", name, exc)
            raise

        patient = Patient(
            id=patient_id,
            name=name.strip(),
            birth_date=birth_date,
            gender=gender,
            diagnosis=diagnosis,
            notes=notes,
            created_at=now,
            updated_at=now,
            ethnicity=ethnicity,
        )
        logger.info("Создан пациент id=%d: %s", patient_id, name)
        return patient

    def get_patient(self, patient_id: int) -> Patient | None:
        """Получение пациента по id.

        Args:
            patient_id: Первичный ключ пациента.

        Returns:
            Объект Patient или None если не найден.
        """
        row = self._get_conn().execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()

        if row is None:
            logger.debug("Пациент id=%d не найден", patient_id)
            return None

        return self._row_to_patient(row)

    def get_all_patients(self) -> list[Patient]:
        """Список всех пациентов, отсортированный по имени.

        Returns:
            Список объектов Patient (может быть пустым).
        """
        rows = self._get_conn().execute(
            "SELECT * FROM patients ORDER BY name COLLATE NOCASE"
        ).fetchall()

        patients = [self._row_to_patient(r) for r in rows]
        logger.debug("Получено %d пациентов", len(patients))
        return patients

    def update_patient(self, patient_id: int, **kwargs: Any) -> bool:
        """Обновление полей пациента.

        Допустимые поля: name, birth_date, gender, diagnosis, notes, ethnicity.
        Поля id, created_at, updated_at изменить нельзя — будут проигнорированы.

        Args:
            patient_id: Первичный ключ пациента.
            **kwargs: Поля и значения для обновления.

        Returns:
            True если запись обновлена, False если пациент не найден.

        Raises:
            sqlite3.Error: При ошибке обновления.
            ValueError: Если kwargs пустой или содержит недопустимые поля.
        """
        allowed = {"name", "birth_date", "gender", "diagnosis", "notes", "ethnicity"}
        filtered = {k: v for k, v in kwargs.items() if k in allowed}

        if not filtered:
            raise ValueError(
                f"Нет допустимых полей для обновления. Допустимы: {allowed}"
            )

        if "name" in filtered and (
            not filtered["name"] or not filtered["name"].strip()
        ):
            raise ValueError("Имя пациента не может быть пустым")

        if "name" in filtered:
            filtered["name"] = filtered["name"].strip()

        filtered["updated_at"] = _now()

        # Замораживаем items — потокобезопасность (колонки и значения в одном порядке)
        items = list(filtered.items())
        set_clause = ", ".join(f'"{col}" = ?' for col, _ in items)
        values = [v for _, v in items] + [patient_id]

        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute(
                    f"UPDATE patients SET {set_clause} WHERE id = ?",  # noqa: S608
                    values,
                )
        except sqlite3.Error as exc:
            logger.error("Ошибка обновления пациента id=%d: %s", patient_id, exc)
            raise

        updated = cursor.rowcount > 0
        if updated:
            logger.info("Обновлён пациент id=%d, поля: %s", patient_id, list(filtered))
        else:
            logger.warning("Пациент id=%d не найден при обновлении", patient_id)
        return updated

    def delete_patient(self, patient_id: int) -> bool:
        """Удаление пациента и всех связанных данных (сессий и измерений).

        Удаление каскадное: сначала measurement_images, затем measurements,
        затем sessions, затем patient. Внешние ключи в схеме не используют
        ON DELETE CASCADE (проект следует явному контролю удаления — см.
        CLAUDE.md), поэтому дочерние строки удаляются вручную в правильном
        порядке в одной транзакции. При ошибке — полный rollback.

        Args:
            patient_id: Первичный ключ пациента.

        Returns:
            True если пациент удалён, False если не найден.

        Raises:
            sqlite3.Error: При ошибке удаления.
        """
        conn = self._get_conn()
        try:
            with conn:
                # Получаем id всех сессий пациента
                session_ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT id FROM sessions WHERE patient_id = ?", (patient_id,)
                    ).fetchall()
                ]

                # Удаляем снимки и измерения для каждой сессии
                for sid in session_ids:
                    conn.execute(
                        """
                        DELETE FROM measurement_images
                        WHERE measurement_id IN (
                            SELECT id FROM measurements WHERE session_id = ?
                        )
                        """,
                        (sid,),
                    )
                    conn.execute(
                        "DELETE FROM measurements WHERE session_id = ?", (sid,)
                    )

                # Удаляем сессии
                conn.execute(
                    "DELETE FROM sessions WHERE patient_id = ?", (patient_id,)
                )

                # Удаляем пациента
                cursor = conn.execute(
                    "DELETE FROM patients WHERE id = ?", (patient_id,)
                )
        except sqlite3.Error as exc:
            logger.error("Ошибка удаления пациента id=%d: %s", patient_id, exc)
            raise

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(
                "Удалён пациент id=%d (%d сессий)", patient_id, len(session_ids)
            )
        else:
            logger.warning("Пациент id=%d не найден при удалении", patient_id)
        return deleted

    def search_patients(self, query: str) -> list[Patient]:
        """Поиск пациентов по имени (нечувствителен к регистру, частичное совпадение).

        Args:
            query: Строка поиска.

        Returns:
            Список объектов Patient, отсортированный по имени.
        """
        # py_lower() — пользовательская функция с поддержкой кириллицы (создана в __init__).
        # Стандартный SQLite LOWER() работает только для ASCII.
        escaped = (
            query.strip().lower()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        rows = self._get_conn().execute(
            "SELECT * FROM patients WHERE py_lower(name) LIKE ? ESCAPE '\\' "
            "ORDER BY name COLLATE NOCASE",
            (pattern,),
        ).fetchall()

        patients = [self._row_to_patient(r) for r in rows]
        logger.debug("Поиск '%s': найдено %d пациентов", query, len(patients))
        return patients

    # ── CRUD: Calibration ─────────────────────────────────────────────────────

    def add_calibration(
        self,
        um_per_px: float,
        fov_width_px: int,
        fov_height_px: int,
        description: str | None = None,
    ) -> Calibration:
        """Сохранение нового профиля калибровки.

        Размеры поля зрения в мм и площадь рассчитываются автоматически.

        Args:
            um_per_px: Масштаб в микронах на пиксель.
            fov_width_px: Ширина кадра в пикселях.
            fov_height_px: Высота кадра в пикселях.
            description: Описание профиля или None.

        Returns:
            Объект Calibration с заполненным id и рассчитанными размерами FOV.

        Raises:
            sqlite3.Error: При ошибке записи.
            ValueError: Если um_per_px <= 0 или размеры кадра <= 0.
        """
        if um_per_px <= 0:
            raise ValueError(f"um_per_px должен быть > 0, получено: {um_per_px}")
        if fov_width_px <= 0 or fov_height_px <= 0:
            raise ValueError(
                f"Размеры кадра должны быть > 0: {fov_width_px}x{fov_height_px}"
            )

        fov_width_mm = fov_width_px * um_per_px / 1000.0
        fov_height_mm = fov_height_px * um_per_px / 1000.0
        fov_area_mm2 = fov_width_mm * fov_height_mm
        now = _now()

        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO calibrations
                        (um_per_px, fov_width_px, fov_height_px,
                         fov_width_mm, fov_height_mm, fov_area_mm2,
                         description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        um_per_px,
                        fov_width_px,
                        fov_height_px,
                        round(fov_width_mm, 4),
                        round(fov_height_mm, 4),
                        round(fov_area_mm2, 4),
                        description,
                        now,
                    ),
                )
                cal_id = cursor.lastrowid
        except sqlite3.Error as exc:
            logger.error("Ошибка сохранения калибровки: %s", exc)
            raise

        calibration = Calibration(
            id=cal_id,
            um_per_px=um_per_px,
            fov_width_px=fov_width_px,
            fov_height_px=fov_height_px,
            fov_width_mm=round(fov_width_mm, 4),
            fov_height_mm=round(fov_height_mm, 4),
            fov_area_mm2=round(fov_area_mm2, 4),
            description=description,
            created_at=now,
        )
        logger.info(
            "Сохранена калибровка id=%d: %.2f мкм/px, FOV=%.1fx%.1f мм",
            cal_id,
            um_per_px,
            fov_width_mm,
            fov_height_mm,
        )
        return calibration

    def get_calibration(self, calibration_id: int) -> Calibration | None:
        """Получение профиля калибровки по id.

        Args:
            calibration_id: Первичный ключ.

        Returns:
            Объект Calibration или None если не найден.
        """
        row = self._get_conn().execute(
            "SELECT * FROM calibrations WHERE id = ?", (calibration_id,)
        ).fetchone()

        if row is None:
            logger.debug("Калибровка id=%d не найдена", calibration_id)
            return None

        return self._row_to_calibration(row)

    def get_latest_calibration(self) -> Calibration | None:
        """Получение последней (по дате создания) калибровки.

        Returns:
            Последний объект Calibration или None если таблица пуста.
        """
        row = self._get_conn().execute(
            "SELECT * FROM calibrations ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if row is None:
            logger.debug("В БД нет профилей калибровки")
            return None

        cal = self._row_to_calibration(row)
        logger.debug("Последняя калибровка: id=%d, %.2f мкм/px", cal.id, cal.um_per_px)
        return cal

    def get_all_calibrations(self) -> list[Calibration]:
        """Список всех профилей калибровки, новые первые.

        Returns:
            Список объектов Calibration.
        """
        rows = self._get_conn().execute(
            "SELECT * FROM calibrations ORDER BY id DESC"
        ).fetchall()

        return [self._row_to_calibration(r) for r in rows]

    # ── CRUD: Session ─────────────────────────────────────────────────────────

    def add_session(
        self,
        patient_id: int,
        calibration_id: int,
        date: str,
        zone: str | None = None,
        notes: str | None = None,
    ) -> Session:
        """Создание новой сессии обследования.

        Args:
            patient_id: Внешний ключ на пациента (должен существовать).
            calibration_id: Внешний ключ на калибровку (должна существовать).
            date: Дата сессии в формате YYYY-MM-DD.
            zone: Основная зона ('F', 'P', 'T', 'O') или None.
            notes: Заметки к сессии или None.

        Returns:
            Объект Session с заполненным id.

        Raises:
            sqlite3.IntegrityError: Если patient_id или calibration_id не существуют.
            sqlite3.Error: При других ошибках записи.
        """
        now = _now()
        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO sessions
                        (patient_id, calibration_id, date, zone, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (patient_id, calibration_id, date, zone, notes, now),
                )
                session_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            logger.error(
                "Ошибка создания сессии: нарушение внешнего ключа "
                "(patient_id=%d, calibration_id=%d): %s",
                patient_id,
                calibration_id,
                exc,
            )
            raise
        except sqlite3.Error as exc:
            logger.error("Ошибка создания сессии: %s", exc)
            raise

        session = Session(
            id=session_id,
            patient_id=patient_id,
            calibration_id=calibration_id,
            date=date,
            zone=zone,
            notes=notes,
            created_at=now,
        )
        logger.info(
            "Создана сессия id=%d для пациента id=%d, дата=%s, зона=%s",
            session_id,
            patient_id,
            date,
            zone,
        )
        return session

    def get_session(self, session_id: int) -> Session | None:
        """Получение сессии по id.

        Args:
            session_id: Первичный ключ сессии.

        Returns:
            Объект Session или None если не найдена.
        """
        row = self._get_conn().execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()

        if row is None:
            logger.debug("Сессия id=%d не найдена", session_id)
            return None

        return self._row_to_session(row)

    def get_patient_sessions(self, patient_id: int) -> list[Session]:
        """Список всех сессий пациента, новые первые.

        Args:
            patient_id: Первичный ключ пациента.

        Returns:
            Список объектов Session.
        """
        rows = self._get_conn().execute(
            "SELECT * FROM sessions WHERE patient_id = ? ORDER BY date DESC, id DESC",
            (patient_id,),
        ).fetchall()

        sessions = [self._row_to_session(r) for r in rows]
        logger.debug(
            "Сессии пациента id=%d: %d записей", patient_id, len(sessions)
        )
        return sessions

    def delete_session(self, session_id: int) -> bool:
        """Удаление сессии и всех её измерений (вместе со снимками серии).

        Удаление каскадное: сначала measurement_images, затем measurements,
        затем сама session — явными DELETE в одной транзакции (в схеме нет
        ON DELETE CASCADE, см. delete_patient()).

        Args:
            session_id: Первичный ключ сессии.

        Returns:
            True если сессия удалена, False если не найдена.

        Raises:
            sqlite3.Error: При ошибке удаления.
        """
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    DELETE FROM measurement_images
                    WHERE measurement_id IN (
                        SELECT id FROM measurements WHERE session_id = ?
                    )
                    """,
                    (session_id,),
                )
                conn.execute(
                    "DELETE FROM measurements WHERE session_id = ?", (session_id,)
                )
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
        except sqlite3.Error as exc:
            logger.error("Ошибка удаления сессии id=%d: %s", session_id, exc)
            raise

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Удалена сессия id=%d", session_id)
        else:
            logger.warning("Сессия id=%d не найдена при удалении", session_id)
        return deleted

    # ── CRUD: Measurement ─────────────────────────────────────────────────────

    def save_measurement(
        self,
        session_id: int,
        zone: str,
        hair_count: int,
        density_per_cm2: float,
        mean_thickness_um: float,
        median_thickness_um: float,
        std_thickness_um: float,
        min_thickness_um: float,
        max_thickness_um: float,
        terminal_count: int,
        vellus_count: int,
        intermediate_count: int,
        terminal_pct: float,
        vellus_pct: float,
        intermediate_pct: float,
        tv_ratio: float,
        fov_area_mm2: float,
        image_path: str | None = None,
        annotated_image_path: str | None = None,
        assessment: str | None = None,
        n_snapshots: int = 1,
        density_std: float | None = None,
    ) -> Measurement:
        """Сохранение результата анализа снимка.

        Принимает скалярные значения — GUI-слой или координатор
        отвечают за извлечение данных из AnalysisResult перед вызовом.

        Args:
            session_id: Внешний ключ на сессию.
            zone: Код зоны ('F', 'P', 'T', 'O').
            hair_count: Количество волос.
            density_per_cm2: Плотность (волос/см²).
            mean_thickness_um: Средняя толщина (мкм).
            median_thickness_um: Медианная толщина (мкм).
            std_thickness_um: Стандартное отклонение толщины (мкм).
            min_thickness_um: Минимальная толщина (мкм).
            max_thickness_um: Максимальная толщина (мкм).
            terminal_count: Количество Terminal волос.
            vellus_count: Количество Vellus волос.
            intermediate_count: Количество Intermediate волос.
            terminal_pct: Процент Terminal.
            vellus_pct: Процент Vellus.
            intermediate_pct: Процент Intermediate.
            tv_ratio: Соотношение Terminal/Vellus.
            fov_area_mm2: Площадь поля зрения (мм²).
            image_path: Путь к исходному снимку или None.
            annotated_image_path: Путь к аннотированному снимку или None.
            assessment: Диагностическая оценка или None.
            n_snapshots: Количество снимков зоны, усреднённых в это
                измерение. По умолчанию 1 — одиночный снимок (прежнее
                поведение, обратная совместимость со старыми вызовами).
            density_std: Стандартное отклонение плотности по серии снимков
                или None, если серия не усреднялась (n_snapshots == 1).

        Returns:
            Объект Measurement с заполненным id.

        Raises:
            sqlite3.IntegrityError: Если session_id не существует.
            sqlite3.Error: При других ошибках записи.
        """
        now = _now()
        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO measurements (
                        session_id, zone,
                        hair_count, density_per_cm2,
                        mean_thickness_um, median_thickness_um, std_thickness_um,
                        min_thickness_um, max_thickness_um,
                        terminal_count, vellus_count, intermediate_count,
                        terminal_pct, vellus_pct, intermediate_pct, tv_ratio,
                        fov_area_mm2,
                        image_path, annotated_image_path,
                        assessment, created_at,
                        n_snapshots, density_std
                    ) VALUES (
                        ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?,
                        ?, ?,
                        ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        session_id, zone,
                        hair_count, density_per_cm2,
                        mean_thickness_um, median_thickness_um, std_thickness_um,
                        min_thickness_um, max_thickness_um,
                        terminal_count, vellus_count, intermediate_count,
                        terminal_pct, vellus_pct, intermediate_pct, tv_ratio,
                        fov_area_mm2,
                        image_path, annotated_image_path,
                        assessment, now,
                        n_snapshots, density_std,
                    ),
                )
                meas_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            logger.error(
                "Ошибка сохранения измерения: нарушение внешнего ключа "
                "(session_id=%d): %s",
                session_id,
                exc,
            )
            raise
        except sqlite3.Error as exc:
            logger.error("Ошибка сохранения измерения session_id=%d: %s", session_id, exc)
            raise

        measurement = Measurement(
            id=meas_id,
            session_id=session_id,
            zone=zone,
            hair_count=hair_count,
            density_per_cm2=density_per_cm2,
            mean_thickness_um=mean_thickness_um,
            median_thickness_um=median_thickness_um,
            std_thickness_um=std_thickness_um,
            min_thickness_um=min_thickness_um,
            max_thickness_um=max_thickness_um,
            terminal_count=terminal_count,
            vellus_count=vellus_count,
            intermediate_count=intermediate_count,
            terminal_pct=terminal_pct,
            vellus_pct=vellus_pct,
            intermediate_pct=intermediate_pct,
            tv_ratio=tv_ratio,
            fov_area_mm2=fov_area_mm2,
            image_path=image_path,
            annotated_image_path=annotated_image_path,
            assessment=assessment,
            created_at=now,
            n_snapshots=n_snapshots,
            density_std=density_std,
        )
        logger.info(
            "Сохранено измерение id=%d: сессия=%d, зона=%s, "
            "%d волос, плотность=%.0f/см² (снимков в серии: %d)",
            meas_id, session_id, zone, hair_count, density_per_cm2, n_snapshots,
        )
        return measurement

    def get_measurement(self, measurement_id: int) -> Measurement | None:
        """Получение измерения по id.

        Args:
            measurement_id: Первичный ключ измерения.

        Returns:
            Объект Measurement или None если не найден.
        """
        row = self._get_conn().execute(
            "SELECT * FROM measurements WHERE id = ?", (measurement_id,)
        ).fetchone()

        if row is None:
            logger.debug("Измерение id=%d не найдено", measurement_id)
            return None

        return self._row_to_measurement(row)

    def get_session_measurements(self, session_id: int) -> list[Measurement]:
        """Список всех измерений сессии.

        Args:
            session_id: Первичный ключ сессии.

        Returns:
            Список объектов Measurement, отсортированный по зоне.
        """
        rows = self._get_conn().execute(
            "SELECT * FROM measurements WHERE session_id = ? ORDER BY zone, id",
            (session_id,),
        ).fetchall()

        measurements = [self._row_to_measurement(r) for r in rows]
        logger.debug(
            "Измерения сессии id=%d: %d записей", session_id, len(measurements)
        )
        return measurements

    def get_patient_measurements(self, patient_id: int) -> list[Measurement]:
        """Все измерения пациента (из всех сессий), новые первые.

        Args:
            patient_id: Первичный ключ пациента.

        Returns:
            Список объектов Measurement.
        """
        rows = self._get_conn().execute(
            """
            SELECT m.*
            FROM measurements m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.patient_id = ?
            ORDER BY s.date DESC, m.zone, m.id DESC
            """,
            (patient_id,),
        ).fetchall()

        measurements = [self._row_to_measurement(r) for r in rows]
        logger.debug(
            "Измерения пациента id=%d: %d записей", patient_id, len(measurements)
        )
        return measurements

    def get_zone_history(self, patient_id: int, zone: str) -> list[Measurement]:
        """История измерений пациента по одной зоне — для графиков динамики.

        Возвращает измерения только для указанной зоны, отсортированные
        по дате сессии (от ранних к поздним) — готовы для построения
        графика динамики (METHODOLOGY.md §6.4).

        Args:
            patient_id: Первичный ключ пациента.
            zone: Код зоны ('F', 'P', 'T', 'O').

        Returns:
            Список объектов Measurement, отсортированный по дате возрастанию.
        """
        rows = self._get_conn().execute(
            """
            SELECT m.*
            FROM measurements m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.patient_id = ? AND m.zone = ?
            ORDER BY s.date ASC, m.id ASC
            """,
            (patient_id, zone),
        ).fetchall()

        measurements = [self._row_to_measurement(r) for r in rows]
        logger.debug(
            "История зоны %s для пациента id=%d: %d измерений",
            zone, patient_id, len(measurements),
        )
        return measurements

    # ── CRUD: MeasurementImage ───────────────────────────────────────────────

    def add_measurement_image(
        self,
        measurement_id: int,
        image_path: str,
        annotated_image_path: str | None = None,
        ordinal: int = 0,
    ) -> MeasurementImage:
        """Сохранение одного снимка серии, привязанного к измерению.

        Каждый вызов — отдельная транзакция. Для сохранения сразу
        нескольких снимков серии как одной атомарной операции (либо все,
        либо ни одного) используйте add_measurement_images().

        Args:
            measurement_id: Внешний ключ на таблицу measurements (должно
                существовать).
            image_path: Путь к исходному снимку (обязательный, непустой).
            annotated_image_path: Путь к аннотированному (размеченному)
                снимку или None, если разметка для этого снимка не
                сохранялась.
            ordinal: Порядковый номер снимка в серии (обычно 0..4, 0 —
                первый снимок). По умолчанию 0.

        Returns:
            Объект MeasurementImage с заполненным id и created_at.

        Raises:
            sqlite3.IntegrityError: Если measurement_id не существует.
            sqlite3.Error: При других ошибках записи.
            ValueError: Если image_path пустой.
        """
        if not image_path or not image_path.strip():
            raise ValueError("Путь к снимку не может быть пустым")

        now = _now()
        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO measurement_images
                        (measurement_id, image_path, annotated_image_path,
                         ordinal, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (measurement_id, image_path, annotated_image_path, ordinal, now),
                )
                image_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            logger.error(
                "Ошибка сохранения снимка: нарушение внешнего ключа "
                "(measurement_id=%d): %s",
                measurement_id,
                exc,
            )
            raise
        except sqlite3.Error as exc:
            logger.error(
                "Ошибка сохранения снимка измерения id=%d: %s", measurement_id, exc
            )
            raise

        image = MeasurementImage(
            id=image_id,
            measurement_id=measurement_id,
            image_path=image_path,
            annotated_image_path=annotated_image_path,
            ordinal=ordinal,
            created_at=now,
        )
        logger.info(
            "Сохранён снимок id=%d измерения id=%d (ordinal=%d)",
            image_id, measurement_id, ordinal,
        )
        return image

    def add_measurement_images(
        self,
        measurement_id: int,
        images: list[dict[str, Any] | tuple[Any, ...]],
    ) -> list[MeasurementImage]:
        """Пакетное сохранение серии снимков одного измерения (одна транзакция).

        Используется при сохранении серии до 5 контрольных снимков зоны
        (METHODOLOGY.md, план v1.4 «серия снимков») — либо сохраняются
        все снимки серии, либо (при ошибке) ни одного.

        Каждый элемент images — словарь с ключами 'image_path'
        (обязательный), 'annotated_image_path' (необязательный, по
        умолчанию None) и 'ordinal' (необязательный; по умолчанию —
        позиция элемента в списке, 0..N-1), либо кортеж (image_path,),
        (image_path, annotated_image_path) или
        (image_path, annotated_image_path, ordinal).

        Args:
            measurement_id: Внешний ключ на таблицу measurements — общий
                для всех снимков серии.
            images: Список описаний снимков (словари или кортежи, см.
                выше). Пустой список допустим — тогда не сохраняется
                ничего и возвращается пустой список.

        Returns:
            Список объектов MeasurementImage в том же порядке, что и
            входной список images.

        Raises:
            ValueError: Если у какого-либо элемента пустой или
                отсутствующий image_path. Проверяется до начала
                транзакции — при ошибке в БД не сохраняется ничего.
            sqlite3.IntegrityError: Если measurement_id не существует.
            sqlite3.Error: При других ошибках записи — полный rollback.
        """
        # Валидация и нормализация всех элементов ДО открытия транзакции:
        # если хотя бы один элемент некорректен, в БД не будет сделано ни
        # одного обращения (нечего откатывать).
        parsed = [
            _normalize_measurement_image_entry(index, item)
            for index, item in enumerate(images)
        ]

        now = _now()
        conn = self._get_conn()
        results: list[MeasurementImage] = []
        try:
            with conn:
                for image_path, annotated_image_path, ordinal in parsed:
                    cursor = conn.execute(
                        """
                        INSERT INTO measurement_images
                            (measurement_id, image_path, annotated_image_path,
                             ordinal, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (measurement_id, image_path, annotated_image_path, ordinal, now),
                    )
                    results.append(
                        MeasurementImage(
                            id=cursor.lastrowid,
                            measurement_id=measurement_id,
                            image_path=image_path,
                            annotated_image_path=annotated_image_path,
                            ordinal=ordinal,
                            created_at=now,
                        )
                    )
        except sqlite3.IntegrityError as exc:
            logger.error(
                "Ошибка сохранения серии снимков: нарушение внешнего ключа "
                "(measurement_id=%d): %s",
                measurement_id,
                exc,
            )
            raise
        except sqlite3.Error as exc:
            logger.error(
                "Ошибка сохранения серии снимков измерения id=%d: %s",
                measurement_id,
                exc,
            )
            raise

        logger.info(
            "Сохранена серия снимков измерения id=%d: %d файлов",
            measurement_id, len(results),
        )
        return results

    def get_measurement_images(self, measurement_id: int) -> list[MeasurementImage]:
        """Список снимков серии измерения, отсортированный по ordinal.

        Args:
            measurement_id: Первичный ключ измерения.

        Returns:
            Список объектов MeasurementImage (может быть пустым — старые
            измерения, сохранённые до Phase 5a, снимков не имеют).
        """
        rows = self._get_conn().execute(
            "SELECT * FROM measurement_images WHERE measurement_id = ? "
            "ORDER BY ordinal, id",
            (measurement_id,),
        ).fetchall()

        images = [self._row_to_measurement_image(r) for r in rows]
        logger.debug(
            "Снимки измерения id=%d: %d записей", measurement_id, len(images)
        )
        return images

    # ── Транзакционные операции ───────────────────────────────────────────────

    def create_session_with_measurements(
        self,
        patient_id: int,
        calibration_id: int,
        date: str,
        measurements_data: list[dict[str, Any]],
        session_zone: str | None = None,
        session_notes: str | None = None,
    ) -> Session:
        """Атомарное создание сессии со всеми измерениями.

        Используйте этот метод при сохранении результатов мультизонального
        анализа (METHODOLOGY.md §4.8), чтобы гарантировать, что либо
        сохранятся все данные, либо ни одного.

        Args:
            patient_id: Внешний ключ на пациента.
            calibration_id: Внешний ключ на калибровку.
            date: Дата сессии (YYYY-MM-DD).
            measurements_data: Список словарей с данными измерений.
                Каждый словарь должен содержать все обязательные поля
                метода save_measurement() кроме session_id. Необязательные
                ключи 'n_snapshots' (по умолчанию 1) и 'density_std'
                (по умолчанию None) — см. save_measurement().
            session_zone: Основная зона сессии или None.
            session_notes: Заметки к сессии или None.

        Returns:
            Созданная Session. Измерения можно получить через
            get_session_measurements(session.id).

        Raises:
            sqlite3.Error: При любой ошибке — полный rollback.
        """
        now = _now()
        conn = self._get_conn()
        try:
            with conn:
                # 1. Создаём сессию
                cursor = conn.execute(
                    """
                    INSERT INTO sessions
                        (patient_id, calibration_id, date, zone, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (patient_id, calibration_id, date, session_zone, session_notes, now),
                )
                session_id = cursor.lastrowid

                # 2. Сохраняем все измерения
                for data in measurements_data:
                    conn.execute(
                        """
                        INSERT INTO measurements (
                            session_id, zone,
                            hair_count, density_per_cm2,
                            mean_thickness_um, median_thickness_um, std_thickness_um,
                            min_thickness_um, max_thickness_um,
                            terminal_count, vellus_count, intermediate_count,
                            terminal_pct, vellus_pct, intermediate_pct, tv_ratio,
                            fov_area_mm2,
                            image_path, annotated_image_path,
                            assessment, created_at,
                            n_snapshots, density_std
                        ) VALUES (
                            ?, ?,
                            ?, ?,
                            ?, ?, ?,
                            ?, ?,
                            ?, ?, ?,
                            ?, ?, ?, ?,
                            ?,
                            ?, ?,
                            ?, ?,
                            ?, ?
                        )
                        """,
                        (
                            session_id,
                            data["zone"],
                            data.get("hair_count", 0),
                            data.get("density_per_cm2", 0.0),
                            data.get("mean_thickness_um", 0.0),
                            data.get("median_thickness_um", 0.0),
                            data.get("std_thickness_um", 0.0),
                            data.get("min_thickness_um", 0.0),
                            data.get("max_thickness_um", 0.0),
                            data.get("terminal_count", 0),
                            data.get("vellus_count", 0),
                            data.get("intermediate_count", 0),
                            data.get("terminal_pct", 0.0),
                            data.get("vellus_pct", 0.0),
                            data.get("intermediate_pct", 0.0),
                            data.get("tv_ratio", 0.0),
                            data.get("fov_area_mm2", 0.0),
                            data.get("image_path"),
                            data.get("annotated_image_path"),
                            data.get("assessment"),
                            now,
                            data.get("n_snapshots", 1),
                            data.get("density_std"),
                        ),
                    )
        except sqlite3.Error as exc:
            logger.error(
                "Ошибка создания сессии с измерениями "
                "(patient_id=%d, %d измерений): %s",
                patient_id,
                len(measurements_data),
                exc,
            )
            raise

        session = Session(
            id=session_id,
            patient_id=patient_id,
            calibration_id=calibration_id,
            date=date,
            zone=session_zone,
            notes=session_notes,
            created_at=now,
        )
        logger.info(
            "Создана сессия id=%d с %d измерениями (patient_id=%d)",
            session_id,
            len(measurements_data),
            patient_id,
        )
        return session

    # ── Резервное копирование и восстановление ──────────────────────────────

    def backup_to(self, dest_path: Path) -> None:
        """Атомарное резервное копирование БД в файл dest_path.

        Использует Online Backup API SQLite (``sqlite3.Connection.backup()``),
        а НЕ копирование файла .db — это единственный корректный способ
        снять консистентный снимок БД при включённом WAL-режиме (см.
        _open()): часть уже закоммиченных данных может физически находиться
        в файле ``{db_path}-wal`` рядом с основным .db, и наивное
        копирование одного лишь .db без него дало бы устаревшую или
        повреждённую копию. Backup API читает согласованный снимок текущей
        БД (прозрачно учитывая WAL) и пишет его в dest_path постранично,
        независимо от активности других читателей.

        Файл dest_path создаётся как самодостаточный однофайловый бэкап
        без журнала WAL (после копирования на dest_path принудительно
        выставляется ``PRAGMA journal_mode=DELETE`` — иначе бэкап унаследовал
        бы из заголовка исходной БД пометку WAL, хотя рядом с ним не будет
        ни ``-wal``, ни ``-shm`` файла). Если dest_path уже существует, его
        содержимое полностью перезаписывается результатом backup().

        Args:
            dest_path: Путь к файлу резервной копии. Родительские
                директории создаются автоматически, если их ещё нет.

        Raises:
            BackupError: Если не удалось создать директорию, открыть или
                записать файл бэкапа (нет прав, нет места на диске, сбой
                SQLite и т.п.). Исходное исключение сохраняется как
                ``__cause__`` (см. ``raise ... from exc``).
        """
        # Запрет бэкапа поверх активной БД: два соединения к одному файлу плюс
        # PRAGMA journal_mode=DELETE на живой WAL-БД — неопределённое поведение.
        if dest_path.resolve() == self.db_path.resolve():
            raise BackupError("Нельзя сохранять резервную копию поверх активной базы")

        conn = self._get_conn()
        # Бэкап пишем во временный файл рядом с назначением, затем атомарно
        # переименовываем: при сбое (нет места, I/O) существующий бэкап по
        # dest_path остаётся целым, а не затирается наполовину.
        dst_conn: sqlite3.Connection | None = None
        tmp_path: Path | None = None
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=dest_path.parent, prefix=f".{dest_path.name}.backup-", suffix=".tmp",
            )
            os.close(fd)
            tmp_path = Path(tmp_name)

            dst_conn = sqlite3.connect(str(tmp_path))
            with dst_conn:
                conn.backup(dst_conn)
            dst_conn.execute("PRAGMA journal_mode=DELETE")
            dst_conn.close()
            dst_conn = None

            os.replace(tmp_path, dest_path)
            tmp_path = None
        except (OSError, sqlite3.Error) as exc:
            logger.error("Ошибка резервного копирования БД в %s: %s", dest_path, exc)
            raise BackupError(
                f"Не удалось создать резервную копию БД: {dest_path}"
            ) from exc
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        logger.info(
            "Резервная копия БД создана: %s (%d байт)",
            dest_path,
            dest_path.stat().st_size,
        )

    def restore_from(self, src_path: Path) -> None:
        """Восстановление БД из резервной копии src_path.

        Безопасная процедура в два этапа:

        1. **Валидация.** src_path открывается ОТДЕЛЬНЫМ, независимым
           соединением sqlite3 (см. модульную функцию
           ``_validate_backup_file()``) и проверяется: PRAGMA
           integrity_check, наличие таблиц patients/sessions/measurements,
           user_version > 0. На этом этапе текущая БД (self._conn) не
           закрывается и не изменяется ни в каком виде — при любой ошибке
           валидации метод поднимает BackupError, а приложение продолжает
           работать с прежними данными, как ни в чём не бывало.

        2. **Замена** — только если валидация прошла успешно:

           - текущее соединение закрывается (self.close());
           - src_path копируется во временный файл РЯДОМ с self.db_path
             (та же директория — гарантия, что последующий os.replace()
             будет настоящим atomic rename на одной файловой системе, а не
             операцией копирования с окном частичной записи);
           - этот временный файл атомарно подставляется на место
             self.db_path через os.replace();
           - осиротевшие файлы ``{db_path}-wal`` и ``{db_path}-shm``,
             оставшиеся от ПРЕЖНЕЙ БД, удаляются — если этого не сделать,
             SQLite при следующем открытии обнаружит рядом с новым
             содержимым db_path чужой WAL-журнал и попытается «доиграть»
             его поверх только что подставленных данных, что их испортит;
           - соединение открывается заново через _open() (та же логика,
             что и в __init__() — WAL, внешние ключи, py_lower(),
             миграции). Если src_path был БД более старой схемы
             (user_version меньше текущей версии приложения), миграции
             доведут её до актуальной версии прямо на этом шаге.

        Если сам этап замены не удался из-за файловой ошибки (нет места
        на диске, нет прав и т.п.), метод делает наилучшую попытку вернуть
        рабочее соединение (self.db_path на этот момент либо ещё не
        тронут, либо — если os.replace() уже успел выполниться — уже
        содержит данные бэкапа) и в любом случае поднимает BackupError.

        После успешного восстановления self._conn снова валиден и
        указывает на данные из бэкапа.

        Args:
            src_path: Путь к файлу резервной копии (обычно созданному
                backup_to()).

        Raises:
            BackupError: Если src_path не прошёл валидацию, либо если
                замена файла БД или последующее открытие соединения
                завершились ошибкой.
        """
        # Запрет восстановления из активной БД: копировался бы только .db без
        # WAL, а его удаление ниже потеряло бы незачекпойнченные данные.
        if src_path.resolve() == self.db_path.resolve():
            raise BackupError("Нельзя восстанавливать из активной базы")

        wal_path = Path(f"{self.db_path}-wal")
        shm_path = Path(f"{self.db_path}-shm")

        # ── Фаза 1: подготовка (текущая БД цела, соединение открыто) ──────────
        # Копируем src во временный файл рядом с db_path и валидируем ИМЕННО
        # копию — те самые байты, что установим (закрывает TOCTOU: src мог
        # измениться между валидацией и установкой). Любой сбой здесь → старая
        # БД нетронута.
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=self.db_path.parent,
                prefix=f".{self.db_path.name}.restore-",
                suffix=".tmp",
            )
            os.close(fd)
            tmp_path = Path(tmp_name)
            shutil.copyfile(src_path, tmp_path)
            _validate_backup_file(tmp_path)  # валидируем копию, не оригинал
        except BackupError:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            logger.error("Ошибка подготовки восстановления из %s: %s", src_path, exc)
            raise BackupError(
                f"Не удалось подготовить восстановление БД из: {src_path}"
            ) from exc

        # ── Фаза 2: замена (точка невозврата — os.replace) ───────────────────
        # Соединение закрываем как можно позже — прямо перед подменой файла.
        self.close()
        try:
            os.replace(tmp_path, self.db_path)
        except OSError as exc:
            # replace не выполнен → старая БД цела, возвращаем соединение.
            tmp_path.unlink(missing_ok=True)
            logger.error("Ошибка замены файла БД из %s: %s", src_path, exc)
            try:
                self._open()
            except (OSError, sqlite3.Error):
                logger.error("Не удалось вернуть соединение после сбоя замены БД")
            raise BackupError(
                f"Не удалось восстановить БД из резервной копии: {src_path}"
            ) from exc

        # ── После os.replace данные УЖЕ заменены: любой сбой → RestoreError ───
        try:
            # Осиротевшие WAL/SHM от ПРЕЖНЕЙ БД: без удаления SQLite «доиграет»
            # их поверх новых данных и испортит.
            wal_path.unlink(missing_ok=True)
            shm_path.unlink(missing_ok=True)
            self._open()
        except (OSError, sqlite3.Error) as exc:
            logger.error(
                "Файл БД заменён данными из %s, но завершить восстановление "
                "не удалось: %s", src_path, exc,
            )
            with contextlib.suppress(OSError, sqlite3.Error):
                self._open()  # ещё попытка вернуть рабочее соединение
            raise RestoreError(
                "База заменена данными из резервной копии, но завершить "
                "восстановление не удалось. Перезапустите приложение."
            ) from exc

        logger.info("БД восстановлена из резервной копии: %s", src_path)

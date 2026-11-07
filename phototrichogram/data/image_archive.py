"""Полная резервная копия SQLite и снимков в проверяемом архиве."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from phototrichogram.utils.image_storage import ImageStore, file_digest

if TYPE_CHECKING:
    from phototrichogram.data.database import Database

_ARCHIVE_SUFFIXES = {".zip", ".ptg", ".ptgbackup"}
# Это предел защитной распаковки, а не параметр анализа.
_MAX_UNCOMPRESSED_BYTES = 20 * 1024**3
_REFERENCES = (
    ("SELECT id, image_path FROM measurements WHERE image_path IS NOT NULL",
     "UPDATE measurements SET image_path = ? WHERE id = ?"),
    ("SELECT id, annotated_image_path FROM measurements WHERE annotated_image_path IS NOT NULL",
     "UPDATE measurements SET annotated_image_path = ? WHERE id = ?"),
    ("SELECT id, image_path FROM measurement_images WHERE image_path IS NOT NULL",
     "UPDATE measurement_images SET image_path = ? WHERE id = ?"),
    ("SELECT id, annotated_image_path FROM measurement_images "
     "WHERE annotated_image_path IS NOT NULL",
     "UPDATE measurement_images SET annotated_image_path = ? WHERE id = ?"),
)


def is_image_archive(path: Path) -> bool:
    """Распознаёт формат полной копии по расширению."""
    return path.suffix.lower() in _ARCHIVE_SUFFIXES


def _rewrite_images(snapshot: Path, source_store: ImageStore, target_store: ImageStore) -> None:
    """Копирует все ссылки снимка БД в целевое хранилище одной транзакцией."""
    connection = sqlite3.connect(snapshot)
    mapped: dict[str, str] = {}
    try:
        with connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for select, update in _REFERENCES:
                if "measurement_images" in select and "measurement_images" not in tables:
                    continue
                for row_id, reference in connection.execute(select).fetchall():
                    if not reference:
                        continue
                    if reference not in mapped:
                        source = source_store.resolve(reference)
                        if source is None:
                            raise OSError("Полная копия невозможна: отсутствует связанный снимок")
                        mapped[reference] = target_store.import_file(source)
                    connection.execute(update, (mapped[reference], row_id))
    finally:
        connection.close()


def backup_archive(database: Database, destination: Path) -> None:
    """Создаёт архив согласованной БД и всех связанных исходников/аннотаций."""
    from phototrichogram.data.database import BackupError

    if destination.resolve() == database.db_path.resolve():
        raise BackupError("Нельзя сохранять архив поверх активной базы")
    temporary_archive: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".image-backup-", dir=destination.parent) as name:
            work = Path(name)
            snapshot = work / "database.db"
            database.backup_to(snapshot)
            _rewrite_images(snapshot, database.image_store, ImageStore(snapshot))
            files = [snapshot, *sorted((work / "images").glob("*"))]
            manifest = {"format": "phototrichogram", "version": 1, "files": {
                file.relative_to(work).as_posix(): {
                    "sha256": file_digest(file), "size": file.stat().st_size,
                } for file in files
            }}
            (work / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
            os.close(descriptor)
            temporary_archive = Path(temporary_name)
            with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in [*files, work / "manifest.json"]:
                    archive.write(file, file.relative_to(work).as_posix())
            os.replace(temporary_archive, destination)
            temporary_archive = None
    except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError(f"Не удалось создать полную копию: {exc}") from exc
    finally:
        if temporary_archive is not None:
            temporary_archive.unlink(missing_ok=True)


def _extract_validated(source: Path, work: Path) -> Path:
    """Проверяет имена, размер, manifest и суммы до использования файлов."""
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Некорректный состав архива")
        if sum(member.file_size for member in members) > _MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Архив превышает предел безопасной распаковки (20 ГиБ)")
        for member in members:
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.filename
                    or PureWindowsPath(member.filename).drive or member.is_dir()
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("Небезопасный путь в архиве")
            if member.filename not in ("manifest.json", "database.db") and (
                len(path.parts) != 2 or path.parts[0] != "images"
                or path.as_posix() != member.filename
                or re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]{1,9}", path.name) is None
            ):
                raise ValueError("Недопустимое имя снимка в архиве")
        if archive.getinfo("manifest.json").file_size > 16 * 1024**2:
            raise ValueError("Слишком большой manifest")
        manifest_data = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest_data, dict):
            raise ValueError("Некорректный manifest")
        if (manifest_data.get("format") != "phototrichogram"
                or manifest_data.get("version") != 1):
            raise ValueError("Неизвестный формат полной копии")
        manifest = manifest_data.get("files")
        if not isinstance(manifest, dict) or set(manifest) != set(names) - {"manifest.json"}:
            raise ValueError("Состав архива не совпадает с manifest")
        if "database.db" not in manifest:
            raise ValueError("В архиве нет базы")
        for member in members:
            target = work / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as original, target.open("xb") as output:
                shutil.copyfileobj(original, output)
            if member.filename == "manifest.json":
                continue
            expected = manifest[member.filename]
            if (not isinstance(expected, dict) or expected.get("size") != target.stat().st_size
                    or expected.get("sha256") != file_digest(target)):
                raise ValueError("Проверка целостности архива не пройдена")
    snapshot = work / "database.db"
    # Архивные ссылки не имеют права указывать на живые файлы вне архива.
    connection = sqlite3.connect(snapshot)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for select, _ in _REFERENCES:
            if "measurement_images" in select and "measurement_images" not in tables:
                continue
            for _, reference in connection.execute(select):
                if reference and (reference not in manifest
                                  or not reference.startswith("images/")):
                    raise ValueError("Снимок БД отсутствует в архиве")
    finally:
        connection.close()
    return snapshot


def restore_archive(database: Database, source: Path) -> None:
    """Восстанавливает проверенный архив, не удаляя прежние снимки."""
    from phototrichogram.data.database import BackupError, RestoreError, _validate_backup_file

    try:
        with tempfile.TemporaryDirectory(
            prefix=".image-restore-", dir=database.db_path.parent,
        ) as name:
            work = Path(name)
            snapshot = _extract_validated(source, work)
            _validate_backup_file(snapshot)
            # Новые hash-имена не перезаписывают старые файлы. Если копирование
            # откажет, живая БД ещё не менялась; возможны только безопасные orphan-копии.
            _rewrite_images(snapshot, ImageStore(snapshot), database.image_store)
            descriptor, filename = tempfile.mkstemp(
                prefix="before-archive-restore-", suffix=".db", dir=database.db_path.parent)
            os.close(descriptor)
            preserved = Path(filename)
            try:
                database.backup_to(preserved)
            except BackupError:
                preserved.unlink(missing_ok=True)
                raise
            try:
                database.restore_from(snapshot)
            except BackupError as original_error:
                # Копия уже вне временного каталога ДО замены живой БД.
                # Даже нехватка диска при rollback не уничтожает прежние данные.
                try:
                    database.restore_from(preserved)
                except BackupError as exc:
                    raise RestoreError(
                        "Восстановление прервано. Перезапустите приложение; "
                        f"прежняя база сохранена: {preserved}") from exc
                preserved.unlink(missing_ok=True)
                raise BackupError(
                    "Восстановление архива не завершено; прежняя база возвращена",
                ) from original_error
            else:
                preserved.unlink(missing_ok=True)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError,
            zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise BackupError(f"Не удалось восстановить полную копию: {exc}") from exc

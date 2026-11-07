"""Переносимость, сохранность исходников и полный архив со снимками."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from phototrichogram.data.database import BackupError, Database
from phototrichogram.utils.image_storage import ImageStore
from tests.test_database import _measurement_kwargs


def _populate(database: Database, original: str, annotation: str | None = None) -> int:
    patient = database.add_patient(name="Тест")
    calibration = database.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
    session = database.create_session_with_measurements(
        patient.id, calibration.id, "2026-11-07", [{
            **_measurement_kwargs(), "zone": "F", "image_path": original,
            "annotated_image_path": annotation,
            "images": [{"image_path": original, "annotated_image_path": annotation}],
        }],
    )
    return database.get_session_measurements(session.id)[0].id


def test_import_survives_move_and_source_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """БД+images работают после переноса, без исходника и зависимости от cwd."""
    source = tmp_path / "external.png"
    source.write_bytes(b"original image")
    store = ImageStore(tmp_path / "first" / "data.db")
    reference = store.import_file(source)
    assert source.read_bytes() == b"original image"
    assert reference.startswith("images/")
    shutil.move(tmp_path / "first", tmp_path / "moved")
    source.unlink()
    monkeypatch.chdir(tmp_path.parent)
    resolved = ImageStore(tmp_path / "moved" / "data.db").resolve(reference)
    assert resolved is not None and resolved.read_bytes() == b"original image"


def test_same_filename_different_content_is_not_overwritten(tmp_path: Path) -> None:
    """Одинаковые имена из разных папок не затирают предыдущий снимок."""
    store = ImageStore(tmp_path / "db/data.db")
    paths = []
    for folder, content in [("a", b"one"), ("b", b"two")]:
        source = tmp_path / folder / "photo.png"
        source.parent.mkdir()
        source.write_bytes(content)
        paths.append(store.import_file(source))
    assert paths[0] != paths[1]
    assert [store.resolve(path).read_bytes() for path in paths] == [b"one", b"two"]


def test_cross_platform_legacy_resolution(tmp_path: Path) -> None:
    """Старые Windows/POSIX абсолютные ссылки находят перенесённую images папку."""
    store = ImageStore(tmp_path / "data.db")
    image = store.images_dir / "photo.png"
    image.write_bytes(b"image")
    for reference in [r"C:\Users\old\images\photo.png", "/old/home/images/photo.png",
                      r"images\photo.png", "images/photo.png"]:
        assert store.resolve(reference) == image
    assert store.resolve("../images/photo.png") is None


def test_failed_import_does_not_destroy_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка копирования не создаёт успешную ссылку и не удаляет исходник."""
    source = tmp_path / "photo.png"
    source.write_bytes(b"image")
    store = ImageStore(tmp_path / "data/data.db")
    def fail(*args: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copyfileobj", fail)
    with pytest.raises(OSError):
        store.import_file(source)
    assert source.read_bytes() == b"image"
    assert list(store.images_dir.iterdir()) == []


def test_archive_roundtrip_on_another_root(tmp_path: Path) -> None:
    """Архив переносит исходник и аннотацию вместе со ссылками в обеих таблицах."""
    source = tmp_path / "photo.png"
    annotated = tmp_path / "annotation.png"
    source.write_bytes(b"original")
    annotated.write_bytes(b"annotation")
    archive = tmp_path / "backup.ptg"
    with Database(tmp_path / "old" / "data.db") as database:
        measurement_id = _populate(database, str(source), str(annotated))
        database.backup_to(archive)
    source.unlink()
    annotated.unlink()
    with Database(tmp_path / "new" / "data.db") as restored:
        restored.restore_from(archive)
        measurement = restored.get_measurement(measurement_id)
        image = restored.get_measurement_images(measurement_id)[0]
        assert measurement.image_path == image.image_path
        assert measurement.annotated_image_path == image.annotated_image_path
        assert not Path(image.image_path).is_absolute()
        assert restored.image_store.resolve(image.image_path).read_bytes() == b"original"
        annotation_path = restored.image_store.resolve(image.annotated_image_path)
        assert annotation_path.read_bytes() == b"annotation"


def test_missing_image_refuses_full_backup_and_keeps_previous(tmp_path: Path) -> None:
    """Копия без обязательного снимка не выдаётся за полный архив."""
    archive = tmp_path / "backup.ptg"
    archive.write_bytes(b"previous backup")
    with Database(tmp_path / "data.db") as database:
        _populate(database, str(tmp_path / "absent.png"))
        with pytest.raises(BackupError, match="отсутствует"):
            database.backup_to(archive)
    assert archive.read_bytes() == b"previous backup"


@pytest.mark.parametrize("kind", ["checksum", "traversal", "manifest"])
def test_invalid_archive_does_not_replace_existing_db(tmp_path: Path, kind: str) -> None:
    """Повреждённый/опасный архив не меняет рабочие записи."""
    archive = tmp_path / "bad.ptg"
    with zipfile.ZipFile(archive, "w") as output:
        if kind == "traversal":
            output.writestr("../outside", b"unsafe")
            output.writestr("manifest.json", "{}")
        elif kind == "manifest":
            output.writestr("manifest.json", "[]")
        else:
            output.writestr("database.db", b"bad")
            output.writestr("manifest.json", json.dumps({
                "format": "phototrichogram", "version": 1,
                "files": {"database.db": {"sha256": "wrong", "size": 3}},
            }))
    with Database(tmp_path / "data.db") as database:
        existing = database.add_patient(name="До восстановления")
        with pytest.raises(BackupError):
            database.restore_from(archive)
        assert database.get_patient(existing.id).name == "До восстановления"
    assert not (tmp_path.parent / "outside").exists()


def test_invalid_series_image_rolls_back_whole_session(temp_db: Database) -> None:
    """Плохая ссылка серии откатывает сессию и уже вставленные метрики."""
    patient = temp_db.add_patient(name="Тест")
    calibration = temp_db.add_calibration(um_per_px=5.9, fov_width_px=640, fov_height_px=480)
    with pytest.raises(ValueError):
        temp_db.create_session_with_measurements(patient.id, calibration.id, "2026-11-07", [{
            **_measurement_kwargs(), "zone": "F", "images": [{"image_path": ""}],
        }])
    assert temp_db.get_patient_sessions(patient.id) == []


def test_legacy_measurement_image_is_visible_without_series_row(temp_db: Database) -> None:
    """Старое одиночное измерение отображается, даже если series-таблица пуста."""
    measurement_id = _populate(temp_db, "images/legacy.png")
    temp_db._get_conn().execute("DELETE FROM measurement_images")
    temp_db._get_conn().commit()
    images = temp_db.get_measurement_images(measurement_id)
    assert len(images) == 1 and images[0].image_path == "images/legacy.png"


def test_copy_failure_during_restore_keeps_live_database(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой переноса архивного снимка не затрагивает действующую БД."""
    source = tmp_path / "image.png"
    source.write_bytes(b"image")
    archive = tmp_path / "backup.ptg"
    with Database(tmp_path / "old" / "data.db") as origin:
        _populate(origin, str(source))
        origin.backup_to(archive)
    with Database(tmp_path / "new" / "data.db") as live:
        patient = live.add_patient(name="Оставить")
        original_import = ImageStore.import_file
        def refuse_live(self: ImageStore, path: Path) -> str:
            if self.root == live.db_path.parent:
                raise OSError("disk full")
            return original_import(self, path)
        monkeypatch.setattr(ImageStore, "import_file", refuse_live)
        with pytest.raises(BackupError, match="disk full"):
            live.restore_from(archive)
        assert live.get_patient(patient.id).name == "Оставить"


def test_restore_recovery_is_preserved_if_database_reopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Даже отказ SQLite после замены не удаляет предаварийную копию."""
    archive = tmp_path / "backup.ptg"
    with Database(tmp_path / "old" / "data.db") as origin:
        origin.add_patient(name="Архив")
        origin.backup_to(archive)
    with Database(tmp_path / "new" / "data.db") as live:
        live.add_patient(name="Прежняя база")
        def refuse_open() -> None:
            raise __import__("sqlite3").OperationalError("reopen failed")
        monkeypatch.setattr(live, "_open", refuse_open)
        with pytest.raises(BackupError, match="прежняя база сохранена"):
            live.restore_from(archive)
        copies = list(live.db_path.parent.glob("before-archive-restore-*.db"))
        assert len(copies) == 1
        with Database(copies[0]) as recovered:
            assert recovered.get_all_patients()[0].name == "Прежняя база"


def test_archive_external_reference_cannot_read_live_files(tmp_path: Path) -> None:
    """Поддельный snapshot с абсолютным путём не берёт снимки из live окружения."""
    archive = tmp_path / "backup.ptg"
    source = tmp_path / "secret.png"
    source.write_bytes(b"must not be included")
    with Database(tmp_path / "old" / "data.db") as origin:
        _populate(origin, str(source))
        origin.backup_to(archive)
    work = tmp_path / "modified"
    with zipfile.ZipFile(archive) as opened:
        opened.extractall(work)
    import sqlite3
    with sqlite3.connect(work / "database.db") as snapshot:
        snapshot.execute("UPDATE measurements SET image_path=?", (str(source),))
    from phototrichogram.utils.image_storage import file_digest
    manifest_path = work / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["database.db"] = {
        "size": (work / "database.db").stat().st_size,
        "sha256": file_digest(work / "database.db"),
    }
    manifest_path.write_text(json.dumps(manifest))
    with zipfile.ZipFile(archive, "w") as output:
        for path in work.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(work).as_posix())
    with Database(tmp_path / "new" / "data.db") as live:
        live.add_patient(name="Оставить")
        with pytest.raises(BackupError, match="отсутствует в архиве"):
            live.restore_from(archive)
        assert live.get_all_patients()[0].name == "Оставить"
        assert not (live.db_path.parent / "images").exists()


@pytest.mark.parametrize("name", ["images/C:stream", "images/CON", "images/../outside",
                                 "images//alias.png"])
def test_archive_rejects_windows_device_and_alias_paths(tmp_path: Path, name: str) -> None:
    """Архив безопасен и для Windows: ADS/device/alias имена не распаковываются."""
    archive = tmp_path / "bad.ptg"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", "{}")
        output.writestr(name, b"unsafe")
    with Database(tmp_path / "live/data.db") as database:
        database.add_patient(name="Не менять")
        with pytest.raises(BackupError):
            database.restore_from(archive)
        assert database.get_all_patients()[0].name == "Не менять"

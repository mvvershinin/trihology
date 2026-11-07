"""Сохранение/просмотр обследований без зависимости от cwd и исходных файлов."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from phototrichogram.core.analyzer import Analyzer
from phototrichogram.data.database import Database
from phototrichogram.gui.capture_tab import CaptureTab
from phototrichogram.gui.history_tab import HistoryTab, _ImagesWindow
from phototrichogram.gui.multizone_tab import MultizoneTab
from phototrichogram.gui.series import aggregate_series
from phototrichogram.utils import image_utils
from phototrichogram.utils.config import AnalysisConfig, Config


def test_failed_write_does_not_replace_existing_image(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой encoder не разрушает прежний снимок и не возвращает успех."""
    destination = tmp_path / "снимок.png"
    destination.write_bytes(b"previous")
    monkeypatch.setattr(Image.Image, "save", lambda *a, **kw: None)
    assert not image_utils.save_image(np.zeros((8, 8, 3), np.uint8), destination)
    assert destination.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [destination]


def test_failed_autosave_returns_no_reference(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Неуспешный save_image не образует ссылки на несуществующий кадр."""
    with Database(tmp_path / "record.db") as db:
        tab = SimpleNamespace(_app=SimpleNamespace(db=db))
        monkeypatch.setattr(image_utils, "save_image", lambda *a, **kw: False)
        assert CaptureTab._autosave_snapshot(tab, np.zeros((8, 8, 3), np.uint8), "F") is None
        assert not list(db.image_store.images_dir.iterdir())


def test_file_import_is_owned_before_analysis(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """После загрузки внешнего файла карточка ссылается на копию рядом с БД."""
    external = tmp_path / "input.png"
    Image.new("RGB", (16, 16), "brown").save(external)
    with Database(tmp_path / "patient-data" / "record.db") as db:
        card = SimpleNamespace(is_full=False, snapshot_count=1, add_image=Mock())
        app = SimpleNamespace(db=db, status_bar=Mock())
        tab = SimpleNamespace(_app=app, _zone_cards={"F": card}, _image_canvas=Mock(),
                              _update_zones_status=Mock())
        dialog = Mock()
        dialog.show.return_value = external
        monkeypatch.setattr("phototrichogram.gui.capture_tab.ImageFileDialog",
                            lambda *a, **kw: dialog)
        CaptureTab._on_zone_load(tab, "F")
        stored = card.add_image.call_args.kwargs["source_path"]
        assert stored.startswith("images/")
        assert external.is_file()
        external.unlink()
        assert db.image_store.resolve(stored).is_file()


def _saved_exam(db: Database, tmp_path: Path, *, fail_insert: bool = False) -> tuple[int | None,
                                                                                 Mock]:
    """Запускает настоящий GUI-save с синтетическим анализом и временной БД."""
    source = tmp_path / "source.png"
    annotation = tmp_path / "annotation.png"
    Image.new("RGB", (16, 16), "white").save(source)
    Image.new("RGB", (16, 16), "red").save(annotation)
    patient = db.add_patient("Синтетический профиль")
    image = np.full((480, 640, 3), 220, dtype=np.uint8)
    image[60:420, 100:106] = 20
    result = Analyzer(AnalysisConfig(count_mode="all")).analyze(image)
    assert result is not None
    if fail_insert:
        db._get_conn().execute("""CREATE TRIGGER reject_image BEFORE INSERT ON measurement_images
                                BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    status = Mock()
    tab = SimpleNamespace(_app=SimpleNamespace(db=db, active_patient=patient,
                                              config=Config(), status_bar=status))
    session_id = CaptureTab._save_results_to_db(
        tab, {"F": aggregate_series([result])}, {"F": [str(source)]},
        {"F": [str(annotation)]},
    )
    source.unlink()
    annotation.unlink()
    return session_id, status


def test_saved_exam_survives_removed_sources_and_changed_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Исходник/аннотация открываются из истории и PDF после удаления импортов."""
    with Database(tmp_path / "data" / "record.db") as db:
        session_id, _ = _saved_exam(db, tmp_path)
        assert session_id is not None
        measurement = db.get_session_measurements(session_id)[0]
        images = db.get_measurement_images(measurement.id)
        assert len(images) == 1
        assert images[0].image_path.startswith("images/")
        assert images[0].annotated_image_path.startswith("images/")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        canvas = Mock()
        window = SimpleNamespace(_db=db, _images=images, _selected=0,
                                 _show_annotated_var=SimpleNamespace(get=lambda: True),
                                 _canvas=canvas, _status_var=Mock())
        _ImagesWindow._render_selected(window)
        canvas.show_pil.assert_called_once()
        patient = db.get_all_patients()[0]
        report_tab = SimpleNamespace(_app=SimpleNamespace(db=db))
        report = MultizoneTab._build_report_data(report_tab, patient, session_id)
        assert report is not None
        assert report.zone_images["F"][0].is_file()
        assert report.zone_images["F"][0] == db.image_store.resolve(images[0].annotated_image_path)


def test_image_row_failure_does_not_leave_metric_only_exam(tmp_path: Path) -> None:
    """GUI сообщает отказ, а транзакция не оставляет обследование без снимков."""
    with Database(tmp_path / "data" / "record.db") as db:
        session_id, status = _saved_exam(db, tmp_path, fail_insert=True)
        assert session_id is None
        assert db._get_conn().execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert db._get_conn().execute("SELECT count(*) FROM measurements").fetchone()[0] == 0
        status.set_error.assert_called_once()
        status.set_ready.assert_not_called()


def test_history_table_selects_exact_exam_for_same_day(tmp_path: Path) -> None:
    """Выбор строки отличает обследования, накладывающиеся на графике по дате."""
    with Database(tmp_path / "data" / "record.db") as db:
        first, _ = _saved_exam(db, tmp_path)
        second, _ = _saved_exam(db, tmp_path)
        assert first is not None and second is not None
        measurements = db.get_session_measurements(first) + db.get_session_measurements(second)
        tree = Mock()
        tree.get_children.return_value = []
        points = {}
        tab = SimpleNamespace(_tree=tree, _table_points=points, _table_placeholder=Mock(),
                              _app=SimpleNamespace(status_bar=Mock()), _open_images_window=Mock())
        HistoryTab._populate_table(tab, measurements, {first: "2026-11-08", second: "2026-11-08"})
        selected_id = measurements[0].id
        tree.selection.return_value = (str(selected_id),)
        HistoryTab._on_show_selected_images(tab)
        assert tab._open_images_window.call_args.args[0].measurement_id == selected_id
        assert len(points) == 2


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_unknown_database_only_extension_requires_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """Выбор .sqlite через все файлы не обходит предупреждение без снимков."""
    from phototrichogram.gui.app import App

    db = Mock()
    app = SimpleNamespace(_config=Config(), _db=db, _status_bar=Mock())
    choice = str(tmp_path / "backup.sqlite")
    dialog = "asksaveasfilename" if operation == "backup" else "askopenfilename"
    monkeypatch.setattr(f"phototrichogram.gui.app.filedialog.{dialog}", lambda **kw: choice)
    confirm = Mock(side_effect=[False] if operation == "backup" else [True, False])
    monkeypatch.setattr("phototrichogram.gui.app.messagebox.askyesno", confirm)
    if operation == "backup":
        App._on_backup_db(app)
        db.backup_to.assert_not_called()
    else:
        App._on_restore_db(app)
        db.restore_from.assert_not_called()
    assert confirm.call_args[0][0] in ("Копия без снимков", "Восстановление без снимков")

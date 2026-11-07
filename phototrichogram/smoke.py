"""Самопроверка собранной поставки без камеры и данных пользователя."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def run(output: Path) -> None:
    """Проверяет интерфейс, SQLite, анализ и кириллический PDF в новом профиле."""
    with tempfile.TemporaryDirectory(prefix="phototrichogram-smoke-") as directory:
        os.environ["PHOTOTRICHOGRAM_DATA_DIR"] = directory
        from unittest.mock import patch

        import numpy as np

        from phototrichogram import __version__
        from phototrichogram.core.analyzer import Analyzer
        from phototrichogram.data.database import Database
        from phototrichogram.gui.app import App
        from phototrichogram.gui.capture_tab import CaptureTab
        from phototrichogram.gui.multizone_tab import MultizoneTab
        from phototrichogram.gui.series import aggregate_series
        from phototrichogram.reports.pdf_report import generate_report
        from phototrichogram.utils.config import AnalysisConfig, Config
        from phototrichogram.utils.image_utils import save_managed_image
        from phototrichogram.utils.resources import resource_path

        with patch("phototrichogram.core.camera.list_devices", return_value=[]):
            app = App(config=Config())
        try:
            expected = {
                "patients": "PatientsTab", "capture": "CaptureTab",
                "analysis": "AnalysisTab", "results": "MultizoneTab",
                "history": "HistoryTab", "calibration": "CalibrationTab",
            }
            for key, class_name in expected.items():
                tab = app.get_tab(key)
                if tab is None or type(tab).__name__ != class_name:
                    raise RuntimeError(f"Вкладка {key} заменена заглушкой")
                app.select_tab(key)
                app.update_idletasks()
            image = np.full((480, 640, 3), 220, dtype=np.uint8)
            image[80:400, 100:106] = 20
            result = Analyzer(AnalysisConfig(count_mode="all")).analyze_with_annotation(image)
            if result is None or result.hair_count < 1 or result.annotated_image is None:
                raise RuntimeError("Синтетический волос не обнаружен")
            for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
                if not resource_path(f"assets/fonts/{font}").is_file():
                    raise RuntimeError(f"Нет шрифта {font}")
            patient = app.db.add_patient(name="Проверка поставки")
            app.active_patient = patient
            capture_tab = app.get_tab("capture")
            results_tab = app.get_tab("results")
            if not isinstance(capture_tab, CaptureTab) or not isinstance(results_tab, MultizoneTab):
                raise RuntimeError("Нет вкладок сохранения обследования")
            original = save_managed_image(image, app.db.image_store)
            annotation = save_managed_image(result.annotated_image, app.db.image_store)
            session_id = capture_tab._save_results_to_db(
                {"F": aggregate_series([result])}, {"F": [original]}, {"F": [annotation]},
            )
            if session_id is None:
                raise RuntimeError("Обследование со снимками не сохранено")
            report_data = results_tab._build_report_data(patient, session_id)
            if report_data is None or not report_data.zone_images.get("F"):
                raise RuntimeError("Снимки обследования не дошли до PDF")
            pdf = Path(directory) / "проверка.pdf"
            generate_report(report_data, pdf)
            if not pdf.read_bytes().startswith(b"%PDF"):
                raise RuntimeError("PDF не сформирован")
            archive = Path(directory) / "backup.ptg"
            app.db.backup_to(archive)
            with Database(Path(directory) / "перенесено" / "data.db") as restored:
                restored.restore_from(archive)
                measurement = restored.get_session_measurements(session_id)[0]
                assert measurement.id is not None
                stored_images = restored.get_measurement_images(measurement.id)
                if not stored_images:
                    raise RuntimeError("Из архива не восстановлены ссылки снимков")
                for stored in stored_images:
                    for reference in (stored.image_path, stored.annotated_image_path):
                        resolved = restored.image_store.resolve(reference)
                        if (reference is None or Path(reference).is_absolute() or resolved is None
                                or not resolved.is_relative_to(restored.db_path.parent)):
                            raise RuntimeError("Снимок не перенёсся в новую папку с БД")
            app.db.get_all_patients()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"version": __version__, "tabs": list(expected),
                                          "analysis": True, "pdf": True, "sqlite": True,
                                          "images": True, "archive": True,
                                          "camera_tested": False}, indent=2), encoding="utf-8")
        finally:
            capture = app.get_tab("capture")
            if capture is not None and hasattr(capture, "release_camera"):
                capture.release_camera()
            app.db.close()
            app.destroy()

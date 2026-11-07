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
        from phototrichogram.core.norms import select_norms
        from phototrichogram.data.models import Patient
        from phototrichogram.gui.app import App
        from phototrichogram.reports.pdf_report import ReportData, generate_report
        from phototrichogram.utils.config import AnalysisConfig, Config
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
            patient = Patient(None, "Проверка поставки", None, None, None, None, None, None)
            pdf = Path(directory) / "проверка.pdf"
            generate_report(ReportData(patient, "2026-11-07", {}, None,
                                       select_norms(None, None)), pdf)
            if not pdf.read_bytes().startswith(b"%PDF"):
                raise RuntimeError("PDF не сформирован")
            app.db.get_all_patients()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"version": __version__, "tabs": list(expected),
                                          "analysis": True, "pdf": True, "sqlite": True,
                                          "camera_tested": False}, indent=2), encoding="utf-8")
        finally:
            capture = app.get_tab("capture")
            if capture is not None and hasattr(capture, "release_camera"):
                capture.release_camera()
            app.db.close()
            app.destroy()

"""Проверяет результат самопроверки упакованного приложения."""
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
checks = ("analysis", "pdf", "sqlite", "images", "archive")
if result["version"] != sys.argv[2] or not all(result[key] for key in checks):
    raise SystemExit("Самопроверка поставки не пройдена")

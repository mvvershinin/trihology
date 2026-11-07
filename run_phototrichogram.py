"""
Точка входа для PyInstaller-сборки Phototrichogram.

Отдельный файл-launcher в корне репозитория (а не phototrichogram/__main__.py)
нужен, чтобы PyInstaller корректно добавил корень репозитория в sys.path и
разрешил абсолютный импорт `phototrichogram.main` как импорт пакета, а не
скрипта внутри самого пакета. См. build.spec.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from phototrichogram.smoke import run
        try:
            run(Path(sys.argv[2]).resolve())
        except Exception:
            import json
            import traceback
            Path(sys.argv[2]).resolve().write_text(
                json.dumps({"error": traceback.format_exc()}), encoding="utf-8",
            )
            raise SystemExit(1) from None
    else:
        from phototrichogram.main import main
        main()

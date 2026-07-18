# -*- mode: python ; coding: utf-8 -*-
"""
Конфигурация сборки Phototrichogram в standalone-дистрибутив (PyInstaller).

Режим сборки — onedir (каталог с исполняемым файлом и зависимостями рядом,
НЕ --onefile). Так исторически проще устроен AppImage: содержимое каталога
dist/phototrichogram/ целиком копируется в AppDir/usr/bin/, а AppRun запускает
бинарник изнутри этого каталога. --onefile для Linux-таргета не используется:
он распаковывает себя во временный каталог при каждом запуске (медленнее
старт) и всё равно не даёт «один файл» после сборки в AppImage — AppImage сам
уже является тем самым единым файлом для конечного пользователя.

Запуск:
    .venv/bin/pyinstaller build.spec --noconfirm

Результат: dist/phototrichogram/ (каталог) и dist/phototrichogram/phototrichogram
(исполняемый файл).
"""

from pathlib import Path

# Корень репозитория. PyInstaller выполняет .spec-файл функцией exec() с
# текущим рабочим каталогом = каталог, откуда вызван pyinstaller (см. Quick
# Start в CLAUDE.md — сборка запускается из корня проекта), поэтому Path.cwd()
# указывает туда же, где лежит сам build.spec.
ROOT = Path.cwd()

a = Analysis(
    ["run_phototrichogram.py"],
    # pathex явно добавляет корень репозитория в sys.path на время анализа
    # импортов, чтобы `from phototrichogram.main import main` в лаунчере
    # разрешался как импорт ПАКЕТА phototrichogram/ (а не искал одноимённый
    # модуль рядом со скриптом).
    pathex=[str(ROOT)],
    binaries=[],
    # Ресурсы приложения (иконка, шрифты DejaVu для будущих PDF-отчётов и
    # т.п.) — весь каталог assets/ копируется в дистрибутив как есть.
    datas=[("assets", "assets")],
    # Список подтверждён эмпирически прогоном сборки и смоук-тестом собранного
    # бинарника (см. отчёт фазы упаковки), а не скопирован вслепую:
    # - cv2, numpy, matplotlib, PIL, tkinter подключаются автоматически через
    #   штатные хуки PyInstaller/pyinstaller-hooks-contrib, т.к. импортируются
    #   в коде обычными `import` — не потребовали hiddenimports;
    # - matplotlib.backends.backend_tkagg импортируется в gui/ напрямую
    #   литералом (`from matplotlib.backends.backend_tkagg import ...`),
    #   поэтому тоже подхватывается статическим анализом без hiddenimports;
    # - fpdf2 — заявленная зависимость проекта (requirements.txt), но пока НЕ
    #   импортируется нигде в коде (PDF-отчёты — фича v1.1, см. docs/todo).
    #   PyInstaller включает в сборку только реально достижимые импорты —
    #   принудительно тащить неиспользуемый пакет сюда не нужно (раздует
    #   дистрибутив мёртвым весом). Когда появится phototrichogram/reports/
    #   с реальным `import fpdf`, он подхватится тем же механизмом сам —
    #   правка build.spec не потребуется.
    # - phototrichogram.gui.*_tab (6 модулей) — КРИТИЧНО, без них дистрибутив
    #   собирается и запускается БЕЗ ошибок, но все вкладки показывают
    #   заглушку «в разработке»! gui/app.py._create_tab() грузит их через
    #   importlib.import_module(module_name), где module_name — параметр
    #   функции, а не строковый литерал в самом вызове import_module(...).
    #   Статический анализатор PyInstaller распознаёт только литералы прямо
    #   в вызове import_module()/__import__() и не умеет прослеживать
    #   аргумент через промежуточную функцию — поэтому все 6 модулей вкладок
    #   не считаются «достижимыми» и не попадают в сборку без hiddenimports.
    #   Само приложение при этом НЕ падает: у _create_tab() есть
    #   try/except (ImportError, AttributeError) с фолбэком на заглушку —
    #   баг обнаруживается только смоук-тестом реального запуска, не по
    #   логам сборки PyInstaller (в них нет предупреждений по этим модулям).
    hiddenimports=[
        "phototrichogram.gui.patients_tab",
        "phototrichogram.gui.capture_tab",
        "phototrichogram.gui.analysis_tab",
        "phototrichogram.gui.multizone_tab",
        "phototrichogram.gui.history_tab",
        "phototrichogram.gui.calibration_tab",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # PyQt/PySide не используются — GUI полностью на tkinter (см.
        # CLAUDE.md). Явное исключение подстраховывает от случайного
        # затягивания Qt-биндингов транзитивными зависимостями (напр.
        # matplotlib умеет грузить Qt-backend).
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        # PyYAML — НЕ зависимость приложения (её нет ни в requirements.txt,
        # ни в requirements-dev.txt). Она обнаруживается в venv только как
        # транзитивная dev-зависимость pre-commit, но случайно затягивается
        # в прод-сборку через numpy/__config__.py: numpy.show_config(
        # mode="dicts") умеет опционально печатать конфиг через
        # yaml.dump(...), импорт обёрнут в свою функцию _check_pyyaml() с
        # try/except (там же предупреждение "Install `pyyaml` for better
        # output" при отсутствии пакета) — т.е. это НЕ обязательный импорт
        # самого numpy. Приложение show_config() нигде не вызывает (grep по
        # phototrichogram/ — 0 вхождений "yaml"). Исключение безопасно и
        # экономит ~2.6 МБ мёртвого веса дистрибутива.
        "yaml",
        "_yaml",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="phototrichogram",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX не установлен в окружении сборки и не обязателен: помогает только
    # уменьшить размер бинарника, а сжатые UPX бинарники иногда конфликтуют с
    # антивирусами/сканерами или системными Tcl/Tk и OpenCV .so. Отключено
    # сознательно, не из-за отсутствия инструмента.
    upx=False,
    console=True,
    # Параметр console значим только на Windows/macOS — на Linux исполняемый
    # файл всегда «консольный» независимо от значения (см. документацию
    # класса EXE в PyInstaller: "Always True on Linux/Unix — it does not
    # matter there"). True выбран как честное отражение факта, а не как
    # включение поведения (которого на Linux и так не отключить).
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Тоже значимо только для Windows/macOS (см. EXE.__init__: "Windows and
    # macOS only"). На Linux ни на что не влияет; путь оставлен настоящим
    # (не NONE), чтобы значение было валидным при будущей сборке под Windows
    # с этим же spec-файлом. Для AppImage/.desktop на Linux иконка
    # используется отдельной копией того же файла — см. build-appimage.sh.
    icon="assets/icon.png",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="phototrichogram",
)

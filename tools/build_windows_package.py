"""
Сборка готового Windows-пакета Phototrichogram на Linux-машине.

Зачем не PyInstaller. PyInstaller упаковывает интерпретатор ТОЙ системы, на
которой запущен: exe под Windows он делает только на Windows. Этот скрипт
собирает переносимый пакет иначе — складывает вместе официальный Windows-Python
(сборка conda-forge, в ней уже есть tkinter, tcl/tk и SQLite) и готовые
Windows-колёса библиотек с PyPI. Ни одной строчки не компилируется: всё, что
кладётся в пакет, скачано в виде готовых бинарников для Windows.

Что получается: каталог, который пользователь распаковывает куда угодно и
запускает ярлыком. Устанавливать не нужно ничего — ни Python, ни библиотеки,
ни СУБД (SQLite живёт внутри Python отдельной установки не требует).

Требования к машине сборки: Linux, интернет, python3 с модулем zstandard.
Права администратора НЕ нужны — micromamba скачивается как обычный файл.

Запуск (из корня проекта):
    python tools/build_windows_package.py --out dist/windows

Результат:
    dist/windows/Phototrichogram/         — сам пакет
    dist/windows/Phototrichogram-win64.zip — он же архивом, для раздачи
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger("build-windows")

# Версии закреплены сознательно: пакет должен собираться одинаково сегодня и
# через полгода, иначе «у врача другая версия» превращается в необъяснимую
# разницу в числах между Linux и Windows.
PYTHON_VERSION = "3.12.9"
PYTHON_TAG = "cp312"
PYTHON_ABI = "cp312"

MICROMAMBA_URL = (
    "https://github.com/mamba-org/micromamba-releases/releases/latest/download/"
    "micromamba-linux-64.tar.bz2"
)

# Зависимости приложения — те же пины, что в requirements.txt.
WHEEL_SPECS = [
    "opencv-python-headless>=4.10,<5.0",
    "numpy>=2.0,<2.3",
    "Pillow>=11,<13",
    "matplotlib>=3.9,<4.0",
    "fpdf2>=2.7,<3.0",
    "pygrabber>=0.2",
]

# Пакеты conda для Windows-окружения: сам Python, tcl/tk (нужен tkinter),
# SQLite (нужен модуль sqlite3).
CONDA_PACKAGES = [f"python={PYTHON_VERSION}", "tk", "sqlite"]

# Что из репозитория попадает в пакет.
APP_PARTS = ["phototrichogram", "assets", "run_phototrichogram.py"]

LAUNCHER_NAME = "Фототрихограмма.bat"

LAUNCHER_BAT = """@echo off
rem Запуск Phototrichogram без установки чего-либо.
rem Пути берутся относительно этого файла, поэтому папку можно переносить
rem куда угодно, в том числе носить на флешке.
setlocal
set "ROOT=%~dp0"
set "PYTHONIOENCODING=utf-8"
start "" "%ROOT%runtime\\pythonw.exe" "%ROOT%app\\start.py"
"""

# Стартовый скрипт пакета. Отдельный файл, а не run_phototrichogram.py из
# репозитория: тому незачем знать про раскладку переносимого пакета.
#
# Главное, ради чего он нужен. Начиная с Python 3.8 Windows НЕ ищет
# зависимости расширений по переменной PATH — просматриваются только каталог
# самого .pyd, системные каталоги и то, что явно добавлено через
# os.add_dll_directory. В раскладке conda библиотеки tcl86t.dll, tk86t.dll и
# sqlite3.dll лежат в runtime/Library/bin, то есть НЕ рядом с _tkinter.pyd и
# _sqlite3.pyd. Без явного add_dll_directory интерфейс просто не запустится.
START_PY = '''"""
Точка входа переносимого Windows-пакета Phototrichogram.

Готовит окружение (поиск библиотек, каталоги tcl/tk) и передаёт управление
обычной точке входа приложения.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RUNTIME = APP_DIR.parent / "runtime"


def _prepare_windows() -> None:
    """Явно указывает Windows, где лежат библиотеки tcl/tk и SQLite."""
    lib_bin = RUNTIME / "Library" / "bin"
    if lib_bin.is_dir():
        os.add_dll_directory(str(lib_bin))

    for variable, parts in (
        ("TCL_LIBRARY", ("Library", "lib", "tcl8.6")),
        ("TK_LIBRARY", ("Library", "lib", "tk8.6")),
    ):
        path = RUNTIME.joinpath(*parts)
        if path.is_dir():
            os.environ.setdefault(variable, str(path))


if sys.platform == "win32":
    _prepare_windows()

# app/ содержит и пакет phototrichogram, и каталог assets рядом с ним —
# utils.resources ищет ресурсы именно на уровень выше пакета.
sys.path.insert(0, str(APP_DIR))

from phototrichogram.main import main  # noqa: E402

main()
'''

READ_ME = """ФОТОТРИХОГРАММА — программа трихоскопического анализа
=====================================================

КАК ЗАПУСТИТЬ

1. Распакуйте эту папку целиком куда угодно: на диск C, на рабочий стол,
   хоть на флешку. Важно распаковать ПОЛНОСТЬЮ, а не запускать прямо
   из архива — из архива программа не заработает.
2. Запустите файл «Фототрихограмма.bat» двойным щелчком.

Устанавливать ничего не нужно: ни Python, ни библиотеки, ни базу данных.
Всё необходимое уже лежит внутри папки. Права администратора не требуются.

На секунду мелькнёт чёрное окошко — это нормально, так устроен запуск.


ПЕРВЫЙ ЗАПУСК: ПРЕДУПРЕЖДЕНИЕ WINDOWS

Windows может показать синее окно «Система Windows защитила ваш компьютер».
Это не вирус и не ошибка: так Windows встречает любую программу без платной
цифровой подписи. Нажмите «Подробнее», затем «Выполнить в любом случае».


МИКРОСКОП

USB-микроскоп определяется сам, отдельный драйвер не нужен — Windows
работает с такими устройствами штатно. Воткните микроскоп ДО запуска
программы, откройте вкладку «Захват» и нажмите «Обновить список».

Если микроскоп не появился в списке: откройте «Файл» → «Настройки» и
смените способ подключения камеры (DirectShow / Media Foundation).
Разные модели микроскопов дружат с разными способами.


ГДЕ ЛЕЖАТ ВАШИ ДАННЫЕ

Все данные хранятся ОТДЕЛЬНО от программы, в папке пользователя:

    C:\\Users\\<ваше имя>\\.phototrichogram\\

Внутри: phototrichogram.db (вся база пациентов одним файлом),
config.json (настройки), images (снимки), reports (PDF-отчёты),
phototrichogram.log (журнал работы).

Папку с программой можно удалить или заменить новой версией — данные
не пострадают, они лежат не там.

Резервная копия базы делается прямо из программы: меню «Файл» →
«Резервная копия базы». База — один файл, его же можно просто скопировать.


ЕСЛИ ЧТО-ТО НЕ ЗАПУСКАЕТСЯ

Посмотрите журнал: C:\\Users\\<ваше имя>\\.phototrichogram\\phototrichogram.log
В конце файла будет текст ошибки — с ним можно разбираться дальше.
"""


def _run(cmd: list[str], **kwargs: object) -> None:
    """Запускает команду, падая с понятным сообщением при ошибке."""
    logger.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)  # type: ignore[arg-type]  # noqa: S603


def fetch_micromamba(work: Path) -> Path:
    """Скачивает micromamba — им создаётся Windows-окружение на Linux.

    Args:
        work: Рабочий каталог сборки.

    Returns:
        Путь к исполняемому файлу micromamba.
    """
    target = work / "micromamba"
    if target.exists():
        logger.info("micromamba уже скачан")
        return target

    logger.info("Скачиваю micromamba…")
    with urllib.request.urlopen(MICROMAMBA_URL, timeout=300) as resp:  # noqa: S310
        payload = resp.read()

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:bz2") as tf:
        member = tf.extractfile("bin/micromamba")
        if member is None:
            raise RuntimeError("В архиве micromamba нет bin/micromamba")
        target.write_bytes(member.read())

    target.chmod(0o755)
    return target


def create_windows_runtime(micromamba: Path, work: Path) -> Path:
    """Создаёт Windows-окружение Python с tkinter и SQLite.

    Args:
        micromamba: Путь к micromamba.
        work: Рабочий каталог сборки.

    Returns:
        Путь к каталогу окружения (будущий runtime/ пакета).
    """
    env = work / "winenv"
    if (env / "python.exe").exists():
        logger.info("Windows-окружение уже создано")
        return env

    logger.info("Создаю Windows-окружение Python %s…", PYTHON_VERSION)
    environment = {**os.environ, "MAMBA_ROOT_PREFIX": str(work / "mamba")}
    _run(
        [
            str(micromamba), "create", "-p", str(env),
            "--platform", "win-64", "-c", "conda-forge",
            *CONDA_PACKAGES, "--yes",
        ],
        env=environment,
    )
    return env


def download_wheels(work: Path) -> Path:
    """Скачивает Windows-колёса всех зависимостей приложения.

    Args:
        work: Рабочий каталог сборки.

    Returns:
        Каталог со скачанными .whl.
    """
    wheels = work / "wheels"
    if wheels.exists() and any(wheels.glob("*.whl")):
        logger.info("Колёса уже скачаны")
        return wheels

    wheels.mkdir(parents=True, exist_ok=True)
    logger.info("Скачиваю Windows-колёса…")
    _run([
        sys.executable, "-m", "pip", "download", "--dest", str(wheels),
        "--platform", "win_amd64",
        "--python-version", ".".join(PYTHON_VERSION.split(".")[:2]),
        "--implementation", "cp", "--abi", PYTHON_ABI,
        "--only-binary=:all:",
        *WHEEL_SPECS,
    ])
    return wheels


def install_wheels(wheels: Path, runtime: Path) -> list[str]:
    """Распаковывает колёса в site-packages Windows-окружения.

    Колёса — обычные zip-архивы, поэтому «установка» на Linux сводится к
    распаковке: компилировать нечего, всё уже собрано под Windows.

    Args:
        wheels: Каталог с .whl.
        runtime: Каталог Windows-окружения.

    Returns:
        Имена установленных колёс (для отчёта).
    """
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for whl in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(whl) as z:
            z.extractall(site)
        installed.append(whl.name)
        logger.info("  распакован %s", whl.name)
    return installed


def assemble(project_root: Path, runtime: Path, out_dir: Path) -> Path:
    """Собирает итоговый каталог пакета.

    Args:
        project_root: Корень репозитория.
        runtime: Готовое Windows-окружение.
        out_dir: Куда положить пакет.

    Returns:
        Путь к каталогу собранного пакета.
    """
    package = out_dir / "Phototrichogram"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    logger.info("Копирую Windows-окружение в runtime/…")
    shutil.copytree(runtime, package / "runtime", symlinks=True)

    logger.info("Копирую приложение в app/…")
    app = package / "app"
    app.mkdir()
    for part in APP_PARTS:
        source = project_root / part
        target = app / part
        if source.is_dir():
            shutil.copytree(
                source, target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            shutil.copy2(source, target)

    (app / "start.py").write_text(START_PY, encoding="utf-8")

    # \r\n обязателен: .bat с юниксовыми переводами строк Windows исполняет
    # неверно. cp866 — кодировка консоли Windows по умолчанию.
    (package / LAUNCHER_NAME).write_bytes(
        LAUNCHER_BAT.replace("\n", "\r\n").encode("cp866")
    )
    (package / "ЧИТАТЬ-СНАЧАЛА.txt").write_bytes(
        READ_ME.replace("\n", "\r\n").encode("utf-8-sig")
    )
    return package


def verify(package: Path) -> list[str]:
    """Статическая проверка собранного пакета.

    Запустить Windows-программу на Linux нельзя, поэтому проверяется наличие
    всего, без чего она гарантированно не стартует.

    Args:
        package: Каталог собранного пакета.

    Returns:
        Список проблем; пустой список — проверка пройдена.
    """
    required = [
        "runtime/pythonw.exe",
        "runtime/python.exe",
        "runtime/python312.dll",
        "runtime/DLLs/_tkinter.pyd",
        "runtime/DLLs/_sqlite3.pyd",
        "runtime/Library/bin/tcl86t.dll",
        "runtime/Library/bin/tk86t.dll",
        "runtime/Library/bin/sqlite3.dll",
        "runtime/Lib/tkinter/__init__.py",
        "runtime/Lib/site-packages/cv2/__init__.py",
        "runtime/Lib/site-packages/numpy/__init__.py",
        "runtime/Lib/site-packages/matplotlib/__init__.py",
        "runtime/Lib/site-packages/PIL/__init__.py",
        "runtime/Lib/site-packages/fpdf/__init__.py",
        "runtime/Lib/site-packages/pygrabber/__init__.py",
        "runtime/Library/lib/tcl8.6/init.tcl",
        "runtime/Library/lib/tk8.6/tk.tcl",
        "app/phototrichogram/main.py",
        "app/assets/fonts/DejaVuSans.ttf",
        "app/start.py",
        LAUNCHER_NAME,
    ]
    problems = [f"нет {rel}" for rel in required if not (package / rel).exists()]

    # Стартовый скрипт обязан явно подключать каталог библиотек: без этого
    # Windows не найдёт tcl86t.dll рядом с _tkinter.pyd и интерфейс не
    # откроется (см. комментарий к START_PY).
    start_py = package / "app" / "start.py"
    if start_py.exists() and "add_dll_directory" not in start_py.read_text(encoding="utf-8"):
        problems.append("start.py не подключает каталог библиотек")

    # Ни одного .so: их наличие означало бы, что в пакет затесались
    # линуксовые бинарники вместо windows-овых.
    linux_binaries = list((package / "runtime" / "Lib" / "site-packages").rglob("*.so"))
    if linux_binaries:
        problems.append(f"в пакете {len(linux_binaries)} линуксовых .so")

    return problems


def make_zip(package: Path) -> Path:
    """Упаковывает готовый каталог в ZIP для раздачи.

    Args:
        package: Каталог собранного пакета.

    Returns:
        Путь к архиву.
    """
    archive = package.parent / "Phototrichogram-win64.zip"
    if archive.exists():
        archive.unlink()

    logger.info("Пакую в ZIP…")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(package.parent))
    return archive


def main() -> int:
    """Точка входа скрипта сборки.

    Returns:
        0 при успехе, 1 если проверка пакета нашла проблемы.
    """
    parser = argparse.ArgumentParser(description="Сборка Windows-пакета Phototrichogram")
    parser.add_argument(
        "--out", default="dist/windows", help="Каталог для результата"
    )
    parser.add_argument(
        "--work", default=None, help="Рабочий каталог (по умолчанию <out>/_build)"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    project_root = Path(__file__).resolve().parents[1]
    out_dir = (project_root / args.out).resolve()
    work = Path(args.work).resolve() if args.work else out_dir / "_build"
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    micromamba = fetch_micromamba(work)
    runtime = create_windows_runtime(micromamba, work)
    wheels = download_wheels(work)
    install_wheels(wheels, runtime)

    package = assemble(project_root, runtime, out_dir)

    problems = verify(package)
    if problems:
        logger.error("Пакет собран с проблемами:")
        for p in problems:
            logger.error("  - %s", p)
        return 1

    archive = make_zip(package)
    size_mb = archive.stat().st_size / 1e6
    logger.info("")
    logger.info("Готово.")
    logger.info("  каталог: %s", package)
    logger.info("  архив:   %s (%.0f МБ)", archive, size_mb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

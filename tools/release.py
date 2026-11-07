"""Общие операции локальной и GitHub-сборки релиза."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def version(value: str) -> str:
    """Проверяет номер релиза и согласованность исходных метаданных."""
    value = value.removeprefix("v")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is None:
        raise ValueError("Версия должна иметь вид X.Y.Z")
    return value


def set_version(value: str) -> None:
    """Внедряет версию в исходники перед сборкой, не создавая коммит."""
    for name, pattern in (("pyproject.toml", r'(?m)^version = "[^"]+"'),
                          ("phototrichogram/__init__.py", r'(?m)^__version__ = "[^"]+"')):
        path = ROOT / name
        content, count = re.subn(pattern, lambda match: match[0].split("=")[0]
                                 + '= "' + value + '"', path.read_text(encoding="utf-8"))
        if count != 1:
            raise ValueError(f"Не найдена единственная версия: {name}")
        path.write_text(content, encoding="utf-8")


def build(value: str) -> None:
    """Собирает onedir тем же spec на обеих системах."""
    set_version(value)
    subprocess.run([sys.executable, "-m", "PyInstaller", "build.spec", "--noconfirm",
                    "--clean"], cwd=ROOT, check=True)
    package = ROOT / "dist/phototrichogram"
    shutil.copy2(ROOT / "LICENSE", package / "LICENSE.txt")
    licenses = package / "licenses"
    licenses.mkdir(exist_ok=True)
    for distribution in importlib.metadata.distributions():
        for entry in distribution.files or []:
            if any(part.lower() in ("licenses", "license", "license.txt", "license.md",
                                    "copying", "copying.txt") for part in entry.parts):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    target = licenses / str(entry)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    info = {"version": value, "revision": subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": sys.version, "platform": sys.platform,
        "dependencies": sorted(f"{d.metadata['Name']}=={d.version}"
                               for d in importlib.metadata.distributions())}
    (package / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def windows() -> None:
    """Упаковывает только готовый onedir, без данных рабочего каталога."""
    package = ROOT / "dist/phototrichogram"
    if not (package / "phototrichogram.exe").is_file():
        raise ValueError("Нет собранного Windows executable")
    (package / "ЧИТАТЬ-СНАЧАЛА.txt").write_text(
        "ФОТОТРИХОГРАММА\nРаспакуйте всю папку и запустите phototrichogram.exe.\n"
        "Python и база данных отдельно не устанавливаются.\n"
        "Windows может предупредить о неизвестном издателе: поставка без подписи.\n"
        "Данные: %USERPROFILE%\\.phototrichogram — вне папки программы.\n"
        "Перед обновлением закройте программу и сделайте резервную копию базы.\n"
        "Заменяйте папку программы целиком; папку данных не удаляйте.\n"
        "Микроскоп на Windows требует отдельной ручной проверки.\n"
        "Инструкция: https://github.com/mvvershinin/trihology/blob/main/docs/USER_GUIDE.md\n",
        encoding="utf-8-sig")
    with zipfile.ZipFile(ROOT / "dist/Phototrichogram-win64.zip", "w",
                         zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                archive.write(path, Path("Phototrichogram") / path.relative_to(package))


def manifest(directory: Path) -> None:
    """Проверяет полный комплект и формирует общие сведения и SHA-256."""
    names = ("Phototrichogram-linux-x86_64.AppImage", "Phototrichogram-win64.zip")
    for name in names:
        if not (directory / name).is_file() or (directory / name).stat().st_size == 0:
            raise ValueError(f"Нет артефакта {name}")
    infos = {}
    for platform in ("linux", "windows"):
        info_path = directory / f"build-info-{platform}.json"
        infos[platform] = json.loads(info_path.read_text(encoding="utf-8"))
    if len({info["version"] for info in infos.values()}) != 1 or len(
            {info["revision"] for info in infos.values()}) != 1:
        raise ValueError("Сборки разных версий/ревизий")
    expected_revision = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected_version = os.environ.get("RELEASE_VERSION")
    for info in infos.values():
        if info["revision"] != expected_revision:
            raise ValueError("Артефакт собран не из текущего коммита")
        if expected_version and info["version"] != expected_version:
            raise ValueError("Версия артефакта не соответствует тегу")
    (directory / "build-info.json").write_text(json.dumps(infos, indent=2), encoding="utf-8")
    sums = []
    for name in (*names, "build-info.json"):
        with (directory / name).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        sums.append(f"{digest}  {name}\n")
    (directory / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")


def notes(value: str) -> None:
    """Выводит описание релиза, отказываясь при отсутствии changelog."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^## {re.escape(value)}\n(.*?)(?=^## |\Z)", text)
    if not match:
        raise ValueError("Нет раздела версии в CHANGELOG.md")
    print(match[1].strip())
    print("\n" + (ROOT / ".github/RELEASE_TEMPLATE.md").read_text(encoding="utf-8"))


def main() -> None:
    """Диспетчер операций сборки."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["version", "build", "windows", "manifest", "notes"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.command in ("version", "build", "notes"):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        value = version(args.value or project["project"]["version"])
        if args.command == "version":
            print(value)
        elif args.command == "build":
            build(value)
        else:
            notes(value)
    elif args.command == "windows":
        windows()
    else:
        manifest(Path(args.value or "artifacts"))


if __name__ == "__main__":
    main()

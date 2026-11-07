"""Переносимое хранилище снимков рядом с фактической базой данных."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PureWindowsPath


def file_digest(path: Path) -> str:
    """Возвращает SHA-256 файла, не загружая его целиком в память."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ImageStore:
    """Копирует исходники без перезаписи и разрешает ссылки из БД."""

    def __init__(self, db_path: Path) -> None:
        self.root = Path(db_path).expanduser().resolve().parent

    @property
    def images_dir(self) -> Path:
        """Возвращает каталог снимков рядом с БД, создавая его при записи."""
        directory = self.root / "images"
        if not directory.resolve().is_relative_to(self.root):
            raise OSError("Каталог снимков выходит за каталог базы данных")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def resolve(self, stored_path: str | None) -> Path | None:
        """Находит relative/legacy POSIX/Windows ссылку без зависимости от cwd."""
        if not stored_path or not stored_path.strip():
            return None
        value = stored_path.strip()
        windows = PureWindowsPath(value)
        normalized = value.replace("\\", "/")
        native = Path(value).expanduser()
        if native.is_absolute() and native.is_file():
            return native
        is_legacy_absolute = bool(windows.drive) or native.is_absolute()
        if not is_legacy_absolute:
            parts = Path(normalized).parts
            if ".." in parts:
                return None
            candidate = self.root / normalized
            if candidate.is_file() and candidate.resolve().is_relative_to(self.root):
                return candidate
        # Перенесённая старая БД: диск/домашний каталог изменились, но images
        # перенесён вместе с ней. Только точное имя, без поиска по cwd/home.
        basename = windows.name if "\\" in value else Path(value).name
        if basename not in ("", ".", ".."):
            candidate = self.root / "images" / basename
            if candidate.is_file() and candidate.resolve().is_relative_to(self.root):
                return candidate
        return None

    def relative(self, path: Path) -> str:
        """Преобразует путь внутри хранилища в переносимую POSIX ссылку."""
        return path.resolve().relative_to(self.root).as_posix()

    def import_file(self, source: Path) -> str:
        """Копирует файл в images по SHA-256; исходник никогда не изменяется."""
        source = Path(source)
        suffix = source.suffix.lower()
        if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
            suffix = ".img"
        directory = self.images_dir
        descriptor, name = tempfile.mkstemp(prefix=".import-", suffix=suffix, dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as target, source.open("rb") as original:
                shutil.copyfileobj(original, target)
                target.flush()
                os.fsync(target.fileno())
            if temporary.stat().st_size == 0:
                raise OSError("Пустой файл снимка не сохранён")
            digest = file_digest(temporary)
            destination = directory / f"{digest}{suffix}"
            if destination.is_symlink():
                raise OSError("Снимок в хранилище не может быть символической ссылкой")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if not destination.is_file() or file_digest(destination) != digest:
                    raise OSError("Конфликт содержимого файла в хранилище снимков") from None
            except OSError:
                # Некоторые файловые системы не поддерживают hard link.
                try:
                    with destination.open("xb") as target, temporary.open("rb") as original:
                        shutil.copyfileobj(original, target)
                        target.flush()
                        os.fsync(target.fileno())
                except FileExistsError:
                    if not destination.is_file() or file_digest(destination) != digest:
                        raise OSError("Конфликт файла снимка") from None
                except OSError:
                    destination.unlink(missing_ok=True)
                    raise
            if file_digest(destination) != digest:
                raise OSError("Проверка копии снимка не пройдена")
            return self.relative(destination)
        finally:
            temporary.unlink(missing_ok=True)

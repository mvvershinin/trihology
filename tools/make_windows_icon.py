"""
Сборка иконки Windows (assets/icon.ico) из assets/icon.png.

Зачем отдельный файл. Windows берёт иконку exe только из формата .ico, PNG там
не годится: PyInstaller либо ругается, либо собирает файл вовсе без иконки.
Внутрь .ico кладутся сразу несколько размеров — Проводник берёт 16×16 для
списка, 32×32 для панели задач, 256×256 для крупных значков; если размера нет,
система масштабирует ближайший и иконка выглядит мылом.

Файл .ico лежит в репозитории готовым, пересобирать нужно только после замены
assets/icon.png.

Запуск (из корня проекта):
    python tools/make_windows_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# Размеры, которые Windows реально запрашивает у иконки приложения.
_ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

_SOURCE = Path("assets/icon.png")
_TARGET = Path("assets/icon.ico")


def main() -> int:
    """Собирает многоразмерный .ico из PNG.

    Returns:
        0 при успехе, 1 если исходный PNG не найден.
    """
    if not _SOURCE.exists():
        print(f"Не найден исходный файл {_SOURCE}", file=sys.stderr)
        return 1

    # RGBA — иначе прозрачный фон превратится в чёрный квадрат.
    image = Image.open(_SOURCE).convert("RGBA")
    image.save(_TARGET, format="ICO", sizes=_ICON_SIZES)

    print(f"{_TARGET} собран, размеров: {len(_ICON_SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

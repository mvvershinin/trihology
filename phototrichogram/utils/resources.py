"""
Разрешение путей к ресурсам приложения (assets/).

Единая точка получения путей к файлам ресурсов, работающая и при запуске
из репозитория, и внутри собранного PyInstaller-дистрибутива.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str | Path) -> Path:
    """Абсолютный путь к ресурсу приложения.

    В собранном PyInstaller-приложении ресурсы распакованы в ``sys._MEIPASS``
    (в onedir-режиме — каталог ``_internal/``); при запуске из репозитория
    ресурсы лежат в корне проекта (двумя уровнями выше пакета).

    Args:
        relative: Путь относительно корня ресурсов,
            например ``"assets/fonts/DejaVuSans.ttf"``.

    Returns:
        Абсолютный путь к ресурсу (существование не проверяется).
    """
    default_base = Path(__file__).resolve().parents[2]
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", default_base))
    else:
        base = default_base
    return base / relative

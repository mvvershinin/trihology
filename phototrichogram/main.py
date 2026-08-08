"""
Главная точка входа приложения Phototrichogram.

Инициализирует логирование, загружает конфигурацию и запускает GUI.
"""

import logging
import sys


def _configure_logging() -> None:
    """
    Настраивает систему логирования.

    Формат: время — уровень — модуль — сообщение.
    Уровень: DEBUG в режиме разработки, INFO в релизе.

    Лог кладётся в общий каталог данных приложения (``~/.phototrichogram``),
    рядом с настройками и базой: прежний путь ``~/.local/share/...`` — линуксовое
    соглашение XDG, на Windows оно превратилось бы в неуместное
    ``C:\\Users\\Имя\\.local\\share``.

    Вывод в консоль подключается, только если консоль есть. В оконной сборке
    под Windows у процесса нет стандартного вывода (``sys.stdout is None``), и
    безусловный ``StreamHandler`` там оказался бы пустышкой.
    """
    from phototrichogram.utils.config import get_data_dir

    log_dir = get_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "phototrichogram.log"

    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
    )


logger = logging.getLogger(__name__)

# Подсказки по установке зависят от системы: советовать пользователю Windows
# команды Debian бессмысленно. В собранном дистрибутиве проверка не сработает
# никогда (всё лежит внутри), но при запуске из исходников подсказка должна
# быть выполнимой на той системе, где её прочитали.
_PIP_HINTS = {
    "tkinter": "входит в состав Python — переустановите Python с python.org",
    "PIL": "pip install Pillow",
    "cv2": "pip install opencv-python-headless",
    "numpy": "pip install numpy",
    "matplotlib": "pip install matplotlib",
}
_APT_HINTS = {
    "tkinter": "sudo apt install python3-tk",
    "PIL": "pip install Pillow   # или: sudo apt install python3-pil python3-pil.imagetk",
}
_IMAGETK_HINT_APT = "sudo apt install python3-pil.imagetk"
_IMAGETK_HINT_PIP = "pip install Pillow"


def _install_hints() -> tuple[dict[str, str], str]:
    """Подсказки по установке пакетов для текущей системы.

    Returns:
        Кортеж (подсказки по модулям, подсказка для PIL.ImageTk).
    """
    if sys.platform == "linux":
        return {**_PIP_HINTS, **_APT_HINTS}, _IMAGETK_HINT_APT
    return dict(_PIP_HINTS), _IMAGETK_HINT_PIP


def _check_dependencies() -> None:
    """Проверка наличия критических зависимостей с понятными сообщениями."""
    hints, imagetk_hint = _install_hints()

    missing: list[str] = []
    for module, install_cmd in hints.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(f"  {module:12s} → {install_cmd}")

    # Отдельно проверить PIL.ImageTk (часто отсутствует)
    try:
        from PIL import ImageTk  # noqa: F401
    except ImportError:
        missing.append(f"  PIL.ImageTk  → {imagetk_hint}")

    if missing:
        msg = "Не найдены необходимые пакеты:\n" + "\n".join(missing)
        logger.error(msg)
        print(f"\n{msg}\n", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """
    Точка входа приложения.

    Порядок запуска:
    1. Настраивает логирование.
    2. Загружает конфигурацию анализа.
    3. Создаёт и запускает главное окно GUI.

    При критической ошибке на любом шаге — логирует трассировку
    и завершает процесс с кодом 1.
    """
    _configure_logging()
    logger.info("Запуск Phototrichogram")

    # Загрузка конфигурации ---------------------------------------------------
    try:
        from phototrichogram.utils.config import load_config, setup_logging

        config = load_config()
        # Перенастроить логирование с уровнем из конфига
        setup_logging(config.app.log_level)
        logger.info("Конфигурация загружена: %s", config)
    except ImportError:
        # Модуль utils.config ещё не создан — продолжаем с настройками по умолчанию.
        logger.warning(
            "phototrichogram.utils.config не найден — будут использованы "
            "значения по умолчанию"
        )
        config = None
    except Exception:
        logger.exception("Не удалось загрузить конфигурацию")
        sys.exit(1)

    # Настройка matplotlib backend до импорта GUI
    try:
        import matplotlib
        matplotlib.use("TkAgg")
    except ImportError:
        pass

    # Проверка зависимостей ----------------------------------------------------
    _check_dependencies()

    # Запуск GUI --------------------------------------------------------------
    try:
        from phototrichogram.gui.app import App
    except ImportError as e:
        logger.exception("Не удалось импортировать GUI-модуль")
        print(
            f"\nОшибка импорта: {e}\n\n"
            "Установите недостающие зависимости:\n"
            "  sudo apt install python3-tk python3-pil.imagetk\n"
            "  pip install -r requirements.txt\n",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception:
        logger.exception("Критическая ошибка при импорте GUI-модуля")
        sys.exit(1)

    try:
        app = App(config=config)
        logger.info("GUI инициализирован, передаём управление главному циклу событий")
        app.run()
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания — завершение")
    except Exception:
        logger.exception("Необработанное исключение в главном цикле GUI")
        sys.exit(1)
    finally:
        logger.info("Phototrichogram завершён")


if __name__ == "__main__":
    main()

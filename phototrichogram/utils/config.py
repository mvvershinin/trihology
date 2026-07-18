"""
Конфигурация приложения Phototrichogram.

Содержит все настраиваемые параметры:
- AnalysisConfig — параметры анализа изображений (калибровка, пороги, классификация)
- CameraConfig — параметры USB-микроскопа
- AppConfig — общие настройки приложения

Все параметры имеют дефолтные значения из METHODOLOGY.md §7.
Реальные значения загружаются из JSON-файла конфигурации.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Директория данных приложения по умолчанию
_DEFAULT_DATA_DIR = Path.home() / ".phototrichogram"


@dataclass
class AnalysisConfig:
    """Параметры анализа трихоскопических изображений.

    Все пороги и коэффициенты настраиваемые — значения по умолчанию
    соответствуют калибровке USB-микроскопа 640x480 @ 200x (§7.1 методологии).

    Attributes:
        um_per_px: Масштаб калибровки (мкм на пиксель). По умолчанию 5.9.
        fov_width_px: Ширина поля зрения камеры в пикселях.
        fov_height_px: Высота поля зрения камеры в пикселях.
        clahe_clip_limit: Порог контрастности CLAHE.
        clahe_tile_size: Размер тайла CLAHE (grid NxN).
        median_blur_kernel: Размер ядра медианного фильтра (нечётное).
        morph_kernel_size: Размер морфологического ядра (эллипс).
        morph_close_iterations: Итерации морфологического закрытия.
        morph_open_iterations: Итерации морфологического открытия.
        min_hair_area: Минимальная площадь контура для детекции волоса (px^2).
        max_circularity: Максимальная циркулярность (>порога = не волос).
        vellus_threshold_um: Порог Vellus в микронах (клинический стандарт 30 мкм).
        intermediate_threshold_um: Порог Intermediate/Terminal в микронах.
        max_hair_thickness_um: Максимальная физически возможная толщина одного
            волоса (мкм). Контуры толще — слипшиеся пучки, исключаются из подсчёта.
        threshold_method: Метод бинаризации ('otsu' | 'adaptive').
    """

    # Калибровка
    um_per_px: float = 5.9
    fov_width_px: int = 640
    fov_height_px: int = 480

    # CLAHE — адаптивная нормализация контраста
    clahe_clip_limit: float = 2.0
    clahe_tile_size: int = 8

    # Шумоподавление
    median_blur_kernel: int = 5

    # Black-hat морфология (выделение тёмных структур)
    blackhat_kernel_size: int = 25

    # Адаптивный порог (при threshold_method='adaptive')
    adaptive_block_size: int = 15
    adaptive_c: float = 2.0

    # Максимальный процент белых пикселей после бинаризации
    # Если больше — порог слишком мягкий, ужесточается автоматически
    max_white_pct: float = 30.0

    # Морфология (post-threshold)
    morph_kernel_size: int = 3
    morph_close_iterations: int = 1
    morph_open_iterations: int = 1

    # Детекция контуров
    min_hair_area: int = 150
    max_circularity: float = 0.6
    min_aspect_ratio: float = 2.5

    # Классификация
    vellus_threshold_um: float = 30.0
    intermediate_threshold_um: float = 40.0

    # Физический фильтр невозможных толщин: один волос не бывает толще этого
    # порога (мкм). Контуры толще — слипшиеся пучки соседних волос, которые
    # сегментация объединила в один объект. Исключаются из подсчёта, плотности,
    # соотношений и статистики толщин, иначе завышают все показатели.
    max_hair_thickness_um: float = 150.0

    # Метод порогового разделения
    threshold_method: str = "otsu"

    @property
    def vellus_threshold_px(self) -> float:
        """Порог Vellus в пикселях (пересчёт из мкм по калибровке)."""
        if self.um_per_px <= 0:
            return 0.0
        return self.vellus_threshold_um / self.um_per_px

    @property
    def intermediate_threshold_px(self) -> float:
        """Порог Intermediate/Terminal в пикселях."""
        if self.um_per_px <= 0:
            return 0.0
        return self.intermediate_threshold_um / self.um_per_px

    @property
    def fov_area_mm2(self) -> float:
        """Площадь поля зрения в мм^2."""
        w_mm = self.fov_width_px * self.um_per_px / 1000.0
        h_mm = self.fov_height_px * self.um_per_px / 1000.0
        return w_mm * h_mm

    @property
    def fov_width_mm(self) -> float:
        """Ширина поля зрения в мм."""
        return self.fov_width_px * self.um_per_px / 1000.0

    @property
    def fov_height_mm(self) -> float:
        """Высота поля зрения в мм."""
        return self.fov_height_px * self.um_per_px / 1000.0


@dataclass
class CameraConfig:
    """Параметры USB-микроскопа.

    Attributes:
        device_index: Индекс устройства V4L2 (/dev/videoN).
        width: Запрашиваемая ширина кадра.
        height: Запрашиваемая высота кадра.
        fps: Запрашиваемая частота кадров.
        video_format: Формат видеопотока (YUYV, MJPG и т.д.).
        auto_exposure: Автоматическая экспозиция.
    """

    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    video_format: str = "YUYV"
    auto_exposure: bool = True


@dataclass
class AppConfig:
    """Общие настройки приложения.

    Attributes:
        language: Язык интерфейса (пока только 'ru').
        theme: Тема ttk ('clam', 'alt', 'default', 'classic').
        log_level: Уровень логирования ('DEBUG', 'INFO', 'WARNING', 'ERROR').
        db_path: Путь к файлу SQLite базы данных.
        images_dir: Директория для сохранения снимков.
        autosave: Автоматическое сохранение снимков при захвате.
        window_width: Начальная ширина окна.
        window_height: Начальная высота окна.
        active_patient_id: ID активного пациента для восстановления при
                           перезапуске. None — пациент не выбран.
    """

    language: str = "ru"
    theme: str = "clam"
    log_level: str = "INFO"
    db_path: str = str(_DEFAULT_DATA_DIR / "phototrichogram.db")
    images_dir: str = str(_DEFAULT_DATA_DIR / "images")
    autosave: bool = True
    window_width: int = 1200
    window_height: int = 800
    active_patient_id: int | None = None


@dataclass
class Config:
    """Корневой конфиг приложения, объединяет все секции.

    Attributes:
        analysis: Параметры анализа изображений.
        camera: Параметры камеры.
        app: Общие настройки приложения.
    """

    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    app: AppConfig = field(default_factory=AppConfig)


def _config_to_dict(config: Config) -> dict[str, Any]:
    """Сериализация конфига в словарь для JSON.

    Args:
        config: Объект конфигурации.

    Returns:
        Словарь с секциями analysis, camera, app.
    """
    return {
        "analysis": asdict(config.analysis),
        "camera": asdict(config.camera),
        "app": asdict(config.app),
    }


def _dict_to_config(data: dict[str, Any]) -> Config:
    """Десериализация словаря в объект конфига.

    Неизвестные ключи игнорируются — обеспечивает совместимость
    при добавлении новых параметров в будущих версиях.

    Args:
        data: Словарь из JSON-файла.

    Returns:
        Объект Config с заполненными секциями.
    """
    analysis_data = data.get("analysis", {})
    camera_data = data.get("camera", {})
    app_data = data.get("app", {})

    # Фильтрация неизвестных ключей для совместимости
    analysis_fields = {f.name for f in AnalysisConfig.__dataclass_fields__.values()}
    camera_fields = {f.name for f in CameraConfig.__dataclass_fields__.values()}
    app_fields = {f.name for f in AppConfig.__dataclass_fields__.values()}

    analysis_kwargs = {k: v for k, v in analysis_data.items() if k in analysis_fields}
    camera_kwargs = {k: v for k, v in camera_data.items() if k in camera_fields}
    app_kwargs = {k: v for k, v in app_data.items() if k in app_fields}

    return Config(
        analysis=AnalysisConfig(**analysis_kwargs),
        camera=CameraConfig(**camera_kwargs),
        app=AppConfig(**app_kwargs),
    )


def get_config_path() -> Path:
    """Путь к файлу конфигурации по умолчанию.

    Returns:
        Path к ~/.phototrichogram/config.json
    """
    return _DEFAULT_DATA_DIR / "config.json"


def load_config(path: Path | None = None) -> Config:
    """Загрузка конфигурации из JSON-файла.

    Если файл не найден или повреждён — возвращает дефолтный конфиг.

    Args:
        path: Путь к JSON-файлу. None = путь по умолчанию.

    Returns:
        Объект Config с загруженными или дефолтными значениями.
    """
    if path is None:
        path = get_config_path()

    if not path.exists():
        logger.info("Файл конфигурации не найден: %s, используются значения по умолчанию", path)
        return Config()

    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        config = _dict_to_config(data)
        logger.info("Конфигурация загружена из %s", path)
        return config
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(
            "Ошибка чтения конфигурации %s: %s, используются значения по умолчанию", path, e
        )
        return Config()


def save_config(config: Config, path: Path | None = None) -> bool:
    """Сохранение конфигурации в JSON-файл.

    Создаёт директорию если не существует.

    Args:
        config: Объект конфигурации для сохранения.
        path: Путь к JSON-файлу. None = путь по умолчанию.

    Returns:
        True если сохранение успешно, False при ошибке.
    """
    if path is None:
        path = get_config_path()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _config_to_dict(config)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        logger.info("Конфигурация сохранена в %s", path)
        return True
    except OSError as e:
        logger.error("Не удалось сохранить конфигурацию в %s: %s", path, e)
        return False


def setup_logging(level: str = "INFO") -> None:
    """Настройка логирования приложения.

    Формат: время — имя модуля — уровень — сообщение.

    Args:
        level: Уровень логирования ('DEBUG', 'INFO', 'WARNING', 'ERROR').
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger("phototrichogram")
    root_logger.setLevel(numeric_level)

    # Удаляем существующие обработчики чтобы избежать дублирования
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    logger.debug("Логирование настроено: уровень=%s", level)

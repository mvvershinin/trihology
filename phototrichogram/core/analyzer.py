"""
Оркестратор полного пайплайна анализа трихоскопического снимка.

Объединяет все модули core/ в единый поток:
    Image -> Segmentation -> Contours -> Measurement -> Classification -> Stats -> Result

Все параметры через AnalysisConfig. Модуль не зависит от GUI или Data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from numpy.typing import NDArray

from phototrichogram.core.classification import HairType, classify_all
from phototrichogram.core.measurement import (
    HairMeasurement,
    measure_all,
    split_thick_bundles,
)
from phototrichogram.core.roots import classify_rooting
from phototrichogram.core.segmentation import find_hair_contours, segment_hair
from phototrichogram.core.stats import (
    HairRatios,
    ThicknessStats,
    calculate_density,
    calculate_ratios,
    thickness_statistics,
)
from phototrichogram.utils.config import AnalysisConfig
from phototrichogram.utils.resources import resource_path

if TYPE_CHECKING:
    from PIL import ImageFont

    # PIL-шрифт: векторный TTF (FreeTypeFont) или встроенный растровый (ImageFont).
    _PilFont = ImageFont.FreeTypeFont | ImageFont.ImageFont

logger = logging.getLogger(__name__)


# Цвета для аннотации (BGR для OpenCV, RGB для PIL)
_COLOR_TERMINAL = (0, 255, 0)       # Зелёный
_COLOR_VELLUS = (0, 0, 255)         # Красный
_COLOR_INTERMEDIATE = (0, 165, 255)  # Оранжевый
_COLOR_BUNDLE = (255, 0, 255)        # Пурпурный — слипшийся пучок (исключён)
_COLOR_BUNDLE_RGB = (255, 0, 255)    # Пурпурный (RGB для PIL, совпадает с BGR)
_COLOR_TRANSIT = (150, 150, 150)     # Серый — транзитный волос (исключён в rooted)
_COLOR_TRANSIT_RGB = (150, 150, 150)  # Серый (RGB для PIL, совпадает с BGR)
_COLOR_TEXT = (0, 255, 255)          # Жёлтый (BGR)
_COLOR_TEXT_RGB = (255, 255, 0)      # Жёлтый (RGB для PIL)
_CONTOUR_THICKNESS = 2
_TRANSIT_THICKNESS = 1               # Тонкая обводка транзитных (визуально «бледнее»)

# Шрифты для русского текста через PIL: сначала шрифты из пакета
# (assets/fonts/ — попадают в сборку PyInstaller), затем системные.
# Собранное приложение не зависит от наличия шрифтов на хосте.
_FONT_PATHS = [
    str(resource_path("assets/fonts/DejaVuSans.ttf")),
    str(resource_path("assets/fonts/DejaVuSans-Bold.ttf")),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]

_pil_font_cache: dict[int, _PilFont | None] = {}


def _get_pil_font(size: int = 16) -> _PilFont | None:
    """Получить PIL шрифт с поддержкой кириллицы.

    Сначала пробует шрифты из пакета (assets/fonts/, есть и в сборке),
    затем системные TTF (DejaVu, Liberation, Ubuntu, Noto).
    Кэширует результат по размеру. Если шрифт не найден, возвращает дефолтный.

    Args:
        size: Размер шрифта в пикселях.

    Returns:
        PIL.ImageFont объект или None, если PIL недоступен.
    """
    if size in _pil_font_cache:
        return _pil_font_cache[size]

    try:
        from PIL import ImageFont
    except ImportError:
        logger.warning("PIL не установлен, русский текст на аннотации не будет отображаться")
        _pil_font_cache[size] = None
        return None

    for font_path in _FONT_PATHS:
        if Path(font_path).exists():
            try:
                font: _PilFont = ImageFont.truetype(font_path, size)
                _pil_font_cache[size] = font
                logger.debug("PIL шрифт загружен: %s (размер %d)", font_path, size)
                return font
            except OSError:
                continue

    # Fallback — дефолтный PIL шрифт (без кириллицы, но хотя бы латиница)
    logger.warning("TTF шрифт с кириллицей не найден, используется дефолтный PIL шрифт")
    font = ImageFont.load_default()
    _pil_font_cache[size] = font
    return font


def _put_text_pil(
    image: NDArray[np.uint8],
    text: str,
    position: tuple[int, int],
    color_rgb: tuple[int, int, int],
    font_size: int = 16,
) -> NDArray[np.uint8]:
    """Нанесение текста на изображение через PIL (поддержка кириллицы).

    cv2.putText не поддерживает Unicode/кириллицу — показывает '??????'.
    Этот метод конвертирует изображение в PIL, рисует текст TTF шрифтом,
    и конвертирует обратно в numpy.

    Args:
        image: BGR изображение (numpy array). МОДИФИЦИРУЕТСЯ IN-PLACE.
        text: Текст для нанесения (поддержка кириллицы).
        position: Координаты (x, y) верхнего левого угла текста.
        color_rgb: Цвет текста в RGB формате.
        font_size: Размер шрифта в пикселях.

    Returns:
        Тот же numpy array с нанесённым текстом.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        # Fallback на cv2.putText (без кириллицы)
        color_bgr = (color_rgb[2], color_rgb[1], color_rgb[0])
        cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color_bgr, 1)
        return image

    font = _get_pil_font(font_size)
    if font is None:
        return image

    # BGR -> RGB для PIL
    img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)
    draw.text(position, text, font=font, fill=color_rgb)

    # RGB -> BGR обратно для OpenCV
    result = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    np.copyto(image, result)
    return image


@dataclass
class AnalysisResult:
    """Полный результат анализа одного снимка.

    Attributes:
        measurements: Список измерений каждого волоса.
        hair_count: Общее количество обнаруженных волос.
        density_per_cm2: Плотность (волос/см²).
        ratios: Соотношения типов (Terminal/Vellus/Intermediate).
        thickness: Статистика толщины (mean, median, std, min, max).
        mask: Бинарная маска сегментации.
        annotated_image: Снимок с нанесёнными контурами и подписями (или None).
        fov_area_mm2: Площадь поля зрения, использованная при расчёте.
    """

    measurements: list[HairMeasurement]
    hair_count: int
    density_per_cm2: float
    ratios: HairRatios
    thickness: ThicknessStats
    mask: NDArray[np.uint8]
    annotated_image: NDArray[np.uint8] | None
    fov_area_mm2: float


def _get_hair_color(classification: str) -> tuple[int, int, int]:
    """Цвет контура по типу волоса.

    Args:
        classification: Тип волоса ('terminal', 'vellus', 'intermediate').

    Returns:
        BGR цвет для cv2.drawContours.
    """
    if classification == HairType.TERMINAL.value:
        return _COLOR_TERMINAL
    elif classification == HairType.VELLUS.value:
        return _COLOR_VELLUS
    else:
        return _COLOR_INTERMEDIATE


def _annotate_image(
    image: NDArray[np.uint8],
    measurements: list[HairMeasurement],
    density: float,
    ratios: HairRatios,
    bundles: list[HairMeasurement] | None = None,
    transit: list[HairMeasurement] | None = None,
) -> NDArray[np.uint8]:
    """Аннотация снимка -- контуры волос и информационная панель.

    Цвета контуров (METHODOLOGY.md 6.1):
    - Зелёный = Terminal (>40 мкм)
    - Оранжевый = Intermediate (30-40 мкм)
    - Красный = Vellus (<30 мкм)
    - Пурпурный = слипшийся пучок (толще max_hair_thickness_um, исключён
      из подсчёта — обводится, чтобы оператор видел, что было отброшено)
    - Серый (тонкий) = транзитный волос (режим count_mode='rooted': проходит
      через кадр насквозь, корня в кадре нет, исключён из подсчёта)

    Текст рендерится через PIL (ImageFont) для поддержки кириллицы.
    cv2.putText не поддерживает Unicode и показывает '??????' для русских букв.

    Args:
        image: Исходное BGR изображение.
        measurements: Измерения реальных волос с заполненной классификацией.
        density: Плотность волос/см2.
        ratios: Соотношения типов.
        bundles: Отброшенные слипшиеся пучки (толще порога). Обводятся особым
            цветом и в подсчёт не входят. None или пустой список — пучков нет.
        transit: Транзитные волосы, исключённые в режиме 'rooted' (проходят
            через кадр). Обводятся серым тонким контуром. None/пустой список —
            транзитных нет (в режиме 'all' список всегда пуст → разметка как
            прежде).

    Returns:
        Копия изображения с нанесённой разметкой.
    """
    annotated = image.copy()
    bundles = bundles or []
    transit = transit or []

    # Рисуем контуры каждого волоса (OpenCV — работает без текста)
    for m in measurements:
        color = _get_hair_color(m.classification)
        cv2.drawContours(annotated, [m.contour], -1, color, _CONTOUR_THICKNESS)

    # Подписи толщин рядом с каждым волосом (только цифры — латиница, можно cv2)
    for m in measurements:
        cx, cy = int(m.center[0]), int(m.center[1])
        color = _get_hair_color(m.classification)
        label = f"{m.thickness_um:.0f}"
        cv2.putText(
            annotated, label, (cx + 5, cy - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1,
        )

    # Слипшиеся пучки — отдельный пурпурный контур с подписью толщины.
    # В подсчёт не входят, но показываются, чтобы решение было прозрачным.
    for b in bundles:
        cv2.drawContours(annotated, [b.contour], -1, _COLOR_BUNDLE, _CONTOUR_THICKNESS)
        cx, cy = int(b.center[0]), int(b.center[1])
        label = f"{b.thickness_um:.0f}"
        cv2.putText(
            annotated, label, (cx + 5, cy - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, _COLOR_BUNDLE, 1,
        )

    # Транзитные волосы (режим 'rooted') — тонкий серый контур без подписи.
    # Исключены из подсчёта, но показываются, чтобы оператор видел, что именно
    # отброшено как «прохожие» волосы. В режиме 'all' список пуст → ничего не
    # рисуется (разметка полностью совпадает с прежней).
    for t in transit:
        cv2.drawContours(annotated, [t.contour], -1, _COLOR_TRANSIT, _TRANSIT_THICKNESS)

    # Масштаб шрифта относительно размера изображения
    h, w = annotated.shape[:2]
    font_size = max(12, min(h, w) // 40)
    line_height = int(font_size * 1.4)

    # Информационная панель (верхний левый угол) — через PIL для кириллицы
    # Полупрозрачный фон для читаемости
    info_lines = [
        f"Волос: {ratios.total_count}",
        f"Плотность: {density:.0f}/см\u00B2",
        f"Terminal: {ratios.terminal_count} ({ratios.terminal_pct:.0f}%)",
        f"Vellus: {ratios.vellus_count} ({ratios.vellus_pct:.0f}%)",
        f"T/V: {ratios.tv_ratio:.1f}",
    ]
    # Отдельной строкой — сколько слипшихся пучков исключено из подсчёта
    if bundles:
        info_lines.append(f"Пучки искл.: {len(bundles)}")
    # И сколько транзитных волос исключено в режиме 'rooted'
    if transit:
        info_lines.append(f"Транзит искл.: {len(transit)}")

    # Фон для текста
    panel_h = line_height * len(info_lines) + 10
    panel_w = int(font_size * 15)
    overlay = annotated.copy()
    cv2.rectangle(overlay, (5, 5), (5 + panel_w, 5 + panel_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, annotated, 0.5, 0, annotated)

    # Текст через PIL
    y_offset = 8
    for line in info_lines:
        _put_text_pil(annotated, line, (10, y_offset), _COLOR_TEXT_RGB, font_size)
        y_offset += line_height

    # Легенда (правый нижний угол)
    legend_items = [
        ("Terminal", _COLOR_TERMINAL, (0, 255, 0)),
        ("Intermediate", _COLOR_INTERMEDIATE, (255, 165, 0)),
        ("Vellus", _COLOR_VELLUS, (255, 0, 0)),
    ]
    # Пучки в легенде — только если они есть на снимке
    if bundles:
        legend_items.append(("Пучок (искл.)", _COLOR_BUNDLE, _COLOR_BUNDLE_RGB))
    # Транзитные — только в режиме 'rooted', когда они есть
    if transit:
        legend_items.append(("Транзит (искл.)", _COLOR_TRANSIT, _COLOR_TRANSIT_RGB))
    legend_font = max(10, font_size - 2)
    ly = h - 20
    for label, color_bgr, color_rgb in reversed(legend_items):
        cv2.circle(annotated, (w - 110, ly), 5, color_bgr, -1)
        _put_text_pil(annotated, label, (w - 100, ly - 6), color_rgb, legend_font)
        ly -= 18

    return annotated


class Analyzer:
    """Оркестратор пайплайна анализа трихоскопического снимка.

    Последовательность (METHODOLOGY.md §4.1):
    1. Сегментация → бинарная маска волос
    2. Поиск контуров → фильтрация по площади и форме
    3. Измерение → толщина (MinFeret), длина, площадь, периметр
    4. Классификация → Terminal / Intermediate / Vellus
    5. Статистика → плотность, соотношения, распределение толщин

    Пример использования::

        config = AnalysisConfig(um_per_px=5.9)
        analyzer = Analyzer(config)
        result = analyzer.analyze(image)
        print(f"Плотность: {result.density_per_cm2} волос/см²")
    """

    def __init__(self, config: AnalysisConfig) -> None:
        """Инициализация анализатора.

        Args:
            config: Параметры анализа — все пороги, размеры ядер, калибровка.
        """
        self.config = config

    def analyze(self, image: NDArray[np.uint8]) -> AnalysisResult | None:
        """Полный анализ снимка БЕЗ аннотации.

        Возвращает результат без annotated_image (= None).
        Используется когда визуализация не нужна.

        Args:
            image: BGR изображение с камеры.

        Returns:
            AnalysisResult или None если изображение невалидно.
        """
        return self._run_pipeline(image, annotate=False)

    def analyze_with_annotation(self, image: NDArray[np.uint8]) -> AnalysisResult | None:
        """Полный анализ снимка С аннотацией.

        Возвращает результат с annotated_image — копия снимка
        с нанесёнными контурами и информационной панелью.

        Args:
            image: BGR изображение с камеры.

        Returns:
            AnalysisResult или None если изображение невалидно.
        """
        return self._run_pipeline(image, annotate=True)

    def _run_pipeline(
        self, image: NDArray[np.uint8], annotate: bool
    ) -> AnalysisResult | None:
        """Внутренняя реализация пайплайна.

        Args:
            image: BGR изображение.
            annotate: Рисовать ли аннотацию.

        Returns:
            AnalysisResult или None.
        """
        if image is None or image.size == 0:
            logger.warning("Получено пустое изображение, анализ невозможен")
            return None

        config = self.config
        logger.info(
            "Начало анализа: %dx%d, калибровка=%.2f мкм/px",
            image.shape[1],
            image.shape[0],
            config.um_per_px,
        )

        # 1. Сегментация
        mask = segment_hair(image, config)

        # 2. Поиск контуров
        contours = find_hair_contours(mask, config)

        # 3. Измерение
        measurements = measure_all(contours, config)

        # 3a. Физический фильтр невозможных толщин — слипшиеся пучки волос.
        # Один волос не бывает толще config.max_hair_thickness_um; контуры
        # толще исключаются из счёта, плотности, соотношений и статистики
        # толщин (иначе завышают все показатели). Пучки сохраняются отдельно
        # только для аннотации (обводятся особым цветом).
        measurements, bundles = split_thick_bundles(measurements, config)
        if bundles:
            logger.info(
                "Отброшено %d контуров толще %.0f мкм (слипшиеся пучки)",
                len(bundles),
                config.max_hair_thickness_um,
            )

        # 3b. Режим подсчёта «от корней» (count_mode="rooted", ДЕФОЛТ): исключаем
        # транзитные волосы — те, что проходят через кадр насквозь (оба конца у
        # границы, корня в кадре нет). По аналогии со слипшимися пучками они
        # исключаются из счёта, плотности, соотношений и статистики толщин.
        # При count_mode="all" фильтр НЕ применяется (поведение до v1.5),
        # transit остаётся пустым.
        transit: list[HairMeasurement] = []
        if config.count_mode == "rooted":
            measurements, transit = classify_rooting(
                measurements, (image.shape[0], image.shape[1]), config
            )
            if transit:
                logger.info(
                    "Режим 'от корней': исключено %d транзитных волос "
                    "(проходят через кадр, корня в кадре нет)",
                    len(transit),
                )

        # 4. Классификация (только реальные волосы: без пучков и, в режиме
        # 'rooted', без транзитных)
        classify_all(measurements, config)

        # 5. Статистика — площадь FOV по реальным размерам изображения
        h, w = image.shape[:2]
        fov_area = (w * config.um_per_px / 1000.0) * (h * config.um_per_px / 1000.0)
        density = calculate_density(len(measurements), fov_area)
        ratios = calculate_ratios(measurements)
        thickness = thickness_statistics(measurements)

        # 6. Аннотация (опционально)
        annotated: NDArray[np.uint8] | None = None
        if annotate:
            annotated = _annotate_image(
                image, measurements, density, ratios, bundles, transit
            )

        result = AnalysisResult(
            measurements=measurements,
            hair_count=len(measurements),
            density_per_cm2=density,
            ratios=ratios,
            thickness=thickness,
            mask=mask,
            annotated_image=annotated,
            fov_area_mm2=fov_area,
        )

        logger.info(
            "Анализ завершён: %d волос, плотность=%.0f/см², "
            "T=%d V=%d I=%d, средняя толщина=%.1f мкм",
            result.hair_count,
            result.density_per_cm2,
            ratios.terminal_count,
            ratios.vellus_count,
            ratios.intermediate_count,
            thickness.mean,
        )

        return result

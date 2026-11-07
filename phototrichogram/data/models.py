"""
Dataclass-модели для хранения данных в SQLite.

Каждая модель соответствует таблице в БД (METHODOLOGY.md §5.3).
Все модели frozen=True — иммутабельны после создания.

Иерархия:
    Patient 1 → N Session
    Session N → 1 Calibration
    Session 1 → N Measurement
    Measurement 1 → N MeasurementImage
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Patient:
    """Профиль пациента.

    Attributes:
        id: Первичный ключ (None до сохранения в БД).
        name: Полное имя пациента (обязательное поле).
        birth_date: Дата рождения в формате ISO 8601 (YYYY-MM-DD) или None.
        gender: Пол пациента: 'M' (мужской), 'F' (женский) или None.
        diagnosis: Предварительный диагноз или None.
        notes: Произвольные заметки врача или None.
        created_at: Дата создания записи (ISO 8601 datetime) или None.
        updated_at: Дата последнего обновления (ISO 8601 datetime) или None.
        ethnicity: Этническая принадлежность пациента (самоопределение) или None,
            если не указана. Значение — произвольная строка-код; data-слой не
            накладывает ограничений и не импортирует core (изоляция слоёв,
            см. import-linter контракт в pyproject.toml). Согласованные коды
            заданы в phototrichogram.core.norms.Ethnicity и соответствуют по
            значению (а не по импорту): 'caucasian' (европеоид), 'hispanic'
            (латиноамериканец), 'african' (африканец), 'east_asian'
            (восточноазиат), 'south_asian' (южноазиат), 'unspecified'
            (не указан). Старые записи и записи без явного выбора — None.
    """

    id: int | None
    name: str
    birth_date: str | None
    gender: str | None
    diagnosis: str | None
    notes: str | None
    created_at: str | None
    updated_at: str | None
    ethnicity: str | None = None


@dataclass(frozen=True)
class Calibration:
    """Профиль калибровки микроскопа.

    Хранит масштабный коэффициент и размеры поля зрения,
    полученные при калибровке по эталонной шкале (METHODOLOGY.md §3.2).

    Attributes:
        id: Первичный ключ (None до сохранения в БД).
        um_per_px: Масштаб: микрон на пиксель (например, 5.9).
        fov_width_px: Ширина поля зрения в пикселях (например, 640).
        fov_height_px: Высота поля зрения в пикселях (например, 480).
        fov_width_mm: Ширина поля зрения в мм (рассчитывается из px и um_per_px).
        fov_height_mm: Высота поля зрения в мм.
        fov_area_mm2: Площадь поля зрения в мм² (например, 10.6).
        description: Описание профиля (например, 'USB 200x, шкала 0.1мм').
        created_at: Дата создания записи (ISO 8601 datetime) или None.
    """

    id: int | None
    um_per_px: float
    fov_width_px: int
    fov_height_px: int
    fov_width_mm: float
    fov_height_mm: float
    fov_area_mm2: float
    description: str | None
    created_at: str | None


@dataclass(frozen=True)
class Session:
    """Сессия обследования пациента.

    Одна сессия — один визит пациента. В рамках сессии делается
    несколько измерений по зонам (F/P/T/O).

    Attributes:
        id: Первичный ключ (None до сохранения в БД).
        patient_id: Внешний ключ на таблицу patients.
        calibration_id: Внешний ключ на таблицу calibrations.
        date: Дата сессии в формате ISO 8601 (YYYY-MM-DD).
        zone: Основная зона сессии ('F', 'P', 'T', 'O') или None.
        notes: Заметки к сессии (условия съёмки, жалобы) или None.
        created_at: Дата создания записи (ISO 8601 datetime) или None.
    """

    id: int | None
    patient_id: int
    calibration_id: int
    date: str
    zone: str | None
    notes: str | None
    created_at: str | None


@dataclass(frozen=True)
class Measurement:
    """Результат одного измерения (анализа снимка).

    Хранит агрегированную статистику по снимку одной зоны.
    Соответствует AnalysisResult из core/analyzer.py, но без numpy-данных
    (маска, контуры) — только числовые показатели.

    Attributes:
        id: Первичный ключ (None до сохранения в БД).
        session_id: Внешний ключ на таблицу sessions.
        zone: Зона скальпа: 'F' (лобная), 'P' (теменная),
              'T' (височная), 'O' (затылочная / контрольная).

        hair_count: Количество обнаруженных волос. Для серии из нескольких
            снимков (n_snapshots > 1) — среднее число волос на снимок, не сумма.
        density_per_cm2: Плотность волос на см².

        mean_thickness_um: Средняя толщина волос в мкм.
        median_thickness_um: Медианная толщина волос в мкм.
        std_thickness_um: Стандартное отклонение толщины в мкм.
        min_thickness_um: Минимальная толщина в мкм.
        max_thickness_um: Максимальная толщина в мкм.

        terminal_count: Количество терминальных волос (> 40 мкм).
        vellus_count: Количество пушковых волос (< 30 мкм).
        intermediate_count: Количество промежуточных волос (30–40 мкм).
        terminal_pct: Процент терминальных волос.
        vellus_pct: Процент пушковых волос.
        intermediate_pct: Процент промежуточных волос.
        tv_ratio: Соотношение Terminal/Vellus (норма > 4:1).

        fov_area_mm2: Площадь поля зрения (мм²), использованная при расчёте плотности.

        image_path: Путь к исходному снимку (абсолютный) или None.
        annotated_image_path: Путь к аннотированному снимку или None.

        assessment: Текстовая диагностическая оценка
                    ('В пределах нормы', 'Начальная миниатюризация' и т.д.) или None.
        created_at: Дата создания записи (ISO 8601 datetime) или None.
        n_snapshots: Количество снимков зоны, усреднённых в это измерение
            (METHODOLOGY.md, план v1.4 «серия снимков»). 1 — одиночный снимок
            (текущее поведение, значение по умолчанию для обратной
            совместимости), 2–5 — усреднённая серия.
        density_std: Стандартное отклонение плотности (волос/см²) по серии
            снимков зоны. None для одиночного измерения (n_snapshots == 1),
            где разброс не определён.
    """

    id: int | None
    session_id: int
    zone: str

    hair_count: int
    density_per_cm2: float

    mean_thickness_um: float
    median_thickness_um: float
    std_thickness_um: float
    min_thickness_um: float
    max_thickness_um: float

    terminal_count: int
    vellus_count: int
    intermediate_count: int
    terminal_pct: float
    vellus_pct: float
    intermediate_pct: float
    tv_ratio: float

    fov_area_mm2: float

    image_path: str | None
    annotated_image_path: str | None

    assessment: str | None
    created_at: str | None

    n_snapshots: int = 1
    density_std: float | None = None


@dataclass(frozen=True)
class MeasurementImage:
    """Один снимок серии, привязанный к измерению.

    При анализе зоны можно сделать до 5 контрольных снимков
    (METHODOLOGY.md, план v1.4 «серия снимков») — агрегированные
    показатели усредняются по серии и хранятся в Measurement
    (см. Measurement.n_snapshots), а пути к самим файлам снимков
    хранятся отдельными строками этой таблицы: по одной строке на
    снимок серии.

    Attributes:
        id: Первичный ключ (None до сохранения в БД).
        measurement_id: Внешний ключ на таблицу measurements.
        image_path: Относительная ссылка от каталога БД; legacy абсолютные пути поддержаны.
        annotated_image_path: Путь к аннотированному (размеченному) снимку
            или None, если разметка для этого снимка не сохранялась.
        ordinal: Порядковый номер снимка в серии (обычно 0..4, 0 — первый
            снимок серии). Используется для сортировки при отображении.
        created_at: Дата создания записи (ISO 8601 datetime) или None.
    """

    id: int | None
    measurement_id: int
    image_path: str
    annotated_image_path: str | None
    ordinal: int
    created_at: str | None

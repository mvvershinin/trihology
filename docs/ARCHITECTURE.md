# Архитектура Phototrichogram

## Принципы (Python Best Practices 2025-2026)

### 1. Clean Architecture — разделение на слои
```
┌─────────────────────────────────────────────┐
│              GUI Layer (gui/)                │
│        tkinter виджеты, отображение          │
│        Зависит от: Core, Data, Reports       │
├─────────────────────────────────────────────┤
│           Reports Layer (reports/)           │
│      PDF-отчёты (fpdf2) — код с v1.1         │
│      Зависит от: Core, Data (модели)         │
├─────────────────────────────────────────────┤
│             Core Layer (core/)               │
│     OpenCV пайплайн, бизнес-логика           │
│     Зависит от: только utils/ (конфиг)       │
├─────────────────────────────────────────────┤
│             Data Layer (data/)               │
│       SQLite CRUD, модели данных             │
│       Зависит от: только utils/              │
├─────────────────────────────────────────────┤
│          Config / Utils (utils/)             │
│     Конфигурация, логирование, хелперы       │
│     Зависит от: ничего                       │
└─────────────────────────────────────────────┘
```

**Правило зависимостей**: стрелки только ВНИЗ. GUI → Reports/Core/Data; Reports → Core, Data; Core → только utils; Data → только utils; core и data не зависят друг от друга. Обратных зависимостей НЕТ.

**Enforcement**: layers-контракт import-linter в `pyproject.toml`
(`gui → reports → (core | data) → utils`); проверка — команда `lint-imports`
(локально и через pre-commit hook).

### 2. Dependency Injection — для тестируемости

Core-функции получают зависимости через аргументы, НЕ создают их внутри:

```python
# ПРАВИЛЬНО — DI, тестируемо:
class Analyzer:
    def __init__(self, config: AnalysisConfig):
        self.config = config

    def analyze(self, image: np.ndarray) -> AnalysisResult:
        ...

# НЕПРАВИЛЬНО — жёсткая связь:
class Analyzer:
    def analyze(self, image):
        config = load_config_from_file()  # нетестируемо!
        db = Database("/path/to/db")      # жёсткая связь!
```

### 3. Protocol classes — интерфейсы для слабой связности

```python
from typing import Protocol

class ImageAnalyzer(Protocol):
    """Интерфейс анализатора — для подмены в тестах"""
    def analyze(self, image: np.ndarray, config: AnalysisConfig) -> AnalysisResult: ...

class CameraDevice(Protocol):
    """Интерфейс камеры — для мока в тестах"""
    def grab_frame(self) -> np.ndarray | None: ...
    def release(self) -> None: ...
```

### 4. Type Hints — везде

```python
# Все функции типизированы:
def segment_hairs(
    image: np.ndarray,
    config: AnalysisConfig,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """
    Сегментация волос на снимке.

    Args:
        image: BGR изображение 640×480
        config: параметры анализа

    Returns:
        (binary_mask, list_of_contours)
    """
    ...
```

### 5. Dataclasses — для всех моделей данных

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)  # immutable где возможно
class CalibrationResult:
    um_per_px: float
    fov_width_mm: float
    fov_height_mm: float
    fov_area_mm2: float
    divisions_detected: int
    confidence: float
```

### 6. Logging — вместо print

```python
import logging

logger = logging.getLogger(__name__)

# В коде:
logger.info("Анализ: обнаружено %d волос", len(hairs))
logger.warning("Камера %s не отвечает", device_path)
logger.debug("Порог бинаризации: %d", threshold)
```

---

## Структура проекта

```
trihology/
├── .claude/
│   └── agents/                 # Инструкции для AI-агентов
├── docs/
│   ├── METHODOLOGY.md          # Клиническая методология
│   ├── ARCHITECTURE.md         # ← этот файл
│   └── todo/                   # Планы задач
├── phototrichogram/            # Основной пакет
│   ├── __init__.py
│   ├── __main__.py             # python -m phototrichogram
│   ├── main.py                 # Точка входа
│   ├── core/                   # CV пайплайн (НЕ зависит от GUI/Data)
│   │   ├── __init__.py
│   │   ├── analyzer.py         # Оркестрация пайплайна
│   │   ├── segmentation.py     # Бинаризация, морфология, контуры
│   │   ├── measurement.py      # MinFeret, площадь, периметр
│   │   ├── classification.py   # Terminal/Vellus/Intermediate
│   │   ├── calibration.py      # Автокалибровка по шкале
│   │   ├── camera.py           # V4L2 захват (Linux)
│   │   └── stats.py            # Плотность, сравнение зон
│   ├── gui/                    # tkinter UI (зависит от Core)
│   │   ├── __init__.py
│   │   ├── app.py              # Главное окно + Notebook
│   │   ├── capture_tab.py      # Захват с микроскопа
│   │   ├── analysis_tab.py     # Анализ снимка
│   │   ├── multizone_tab.py    # Мультизональный анализ
│   │   ├── patients_tab.py     # Профили пациентов
│   │   ├── history_tab.py      # История + графики
│   │   ├── calibration_tab.py  # Калибровка
│   │   └── widgets.py          # Переиспользуемые виджеты
│   ├── data/                   # SQLite + модели (НЕ зависит от Core/GUI)
│   │   ├── __init__.py
│   │   ├── database.py         # Подключение, миграции, CRUD
│   │   └── models.py           # Dataclass модели
│   ├── reports/                # Генерация PDF
│   │   ├── __init__.py
│   │   └── pdf_report.py
│   └── utils/                  # Общие утилиты
│       ├── __init__.py
│       ├── config.py           # AnalysisConfig dataclass
│       └── image_utils.py      # CV↔tkinter конвертация
├── tests/                      # Тесты
│   ├── __init__.py
│   ├── conftest.py             # Общие фикстуры
│   ├── fixtures/               # Тестовые изображения
│   ├── test_segmentation.py
│   ├── test_measurement.py
│   ├── test_classification.py
│   ├── test_calibration.py
│   ├── test_stats.py
│   ├── test_database.py
│   └── test_analyzer.py
├── assets/                     # Ресурсы приложения
│   ├── icon.png
│   └── zone_diagram.png
├── pyproject.toml              # Единый центр конфигурации (метаданные, ruff, mypy, pytest, import-linter)
├── .pre-commit-config.yaml     # Хуки: ruff + lint-imports
├── requirements.txt            # Зависимости (пины = [project.dependencies])
├── requirements-dev.txt        # Dev зависимости (пины = [project.optional-dependencies.dev])
├── build.spec                  # PyInstaller конфигурация (планируется, фаза упаковки)
├── install-udev.sh             # Установка USB правил (планируется, фаза упаковки)
├── CLAUDE.md                   # Инструкции для AI-агентов
├── LICENSE                     # MIT (планируется)
├── README.md                   # GitHub README
└── .gitignore
```

---

## Конфигурация проекта

### pyproject.toml — единый источник конфигурации

**Источник истины — сам файл `pyproject.toml` в корне репозитория** (здесь не
дублируется, чтобы документ не расходился с реальностью). Состав:

- `[project]` — метаданные, `requires-python = ">=3.11"`, зависимости с пинами
- `[tool.pytest.ini_options]` — testpaths, addopts
- `[tool.mypy]` — strict-режим, overrides `ignore_missing_imports` для cv2/fpdf (нет стабов)
- `[tool.ruff]` — target py311, line-length 100, правила E/F/W/I/N/UP/B/A/SIM
- `[tool.importlinter]` — контракт слоёв:

```toml
[[tool.importlinter.contracts]]
name = "Слои приложения: gui -> reports -> (core | data) -> utils"
type = "layers"
layers = [
    "phototrichogram.gui",
    "phototrichogram.reports",
    "phototrichogram.core | phototrichogram.data",
    "phototrichogram.utils",
]
```

Разделитель `|` = независимые siblings одного слоя (core⊥data). Внимание:
`:` в import-linter 2.x, наоборот, разрешает соседям импортировать друг друга.

### requirements.txt / requirements-dev.txt

Пины синхронизированы с pyproject.toml. Прод: `opencv-python-headless>=4.10,<5.0`,
`numpy>=2.0,<2.3` (NumPy 2 — требование новых wheel'ов opencv), `Pillow>=11,<13`,
`matplotlib>=3.9,<4.0` (поддержка NumPy 2 — с 3.8.4/3.9), `fpdf2>=2.7,<3.0`.
Dev: `pytest>=8,<10`, `pytest-cov>=5,<8`, `mypy>=1.10,<3`, `ruff>=0.15,<0.16`
(в паре с rev хука pre-commit), `import-linter>=2.0`, `pre-commit>=4,<5`.

### Baseline качества (зафиксирован 2026-07-18)

Снят при внедрении тулинга; исправление — отдельная задача
(`docs/todo/2026-07-18_tooling-modernization.md`, раздел «Отложено»):

- ruff: 35 ошибок (19 автофиксируемых через `--fix`)
- mypy strict: 214 ошибок (основное: tkinter-стабы `**kwargs`, непараметризованный `np.ndarray`, matplotlib-колбэки)
- lint-imports: контракт проходит без нарушений

---

## Тестирование

### Стратегия

| Слой | Что тестируем | Как |
|------|---------------|-----|
| core/ | Пайплайн анализа | Синтетические изображения, pytest fixtures |
| data/ | CRUD операции | Temp SQLite в tmp_path |
| utils/ | Конфиг, конвертации | Unit tests |
| gui/ | НЕ тестируем автоматически | Ручная визуальная проверка |
| integration | Полный пайплайн | End-to-end с синтетикой |

### Мокирование

```python
# Мок камеры через Protocol:
class MockCamera:
    def __init__(self, frame: np.ndarray):
        self._frame = frame

    def grab_frame(self) -> np.ndarray | None:
        return self._frame.copy()

    def release(self) -> None:
        pass

# Использование в тесте:
def test_capture_workflow(sample_image):
    camera = MockCamera(sample_image)
    frame = camera.grab_frame()
    assert frame is not None
    assert frame.shape == (480, 640, 3)
```

```python
# Мок через monkeypatch (pytest):
def test_list_devices(monkeypatch):
    """Мок cv2.VideoCapture для тестирования без реальной камеры"""
    class FakeCap:
        def __init__(self, *a, **kw): pass
        def isOpened(self): return True
        def get(self, prop): return 640 if prop == 3 else 480
        def release(self): pass

    monkeypatch.setattr("cv2.VideoCapture", FakeCap)
    devices = MicroscopeCamera().list_devices()
    assert len(devices) > 0
```

### Синтетические тестовые изображения

```python
def make_test_image(
    width: int = 640,
    height: int = 480,
    n_thick: int = 3,
    n_thin: int = 2,
    bg_color: int = 220,
) -> np.ndarray:
    """Создаёт синтетический снимок с 'волосами' для тестирования"""
    img = np.ones((height, width, 3), dtype=np.uint8) * bg_color

    for i in range(n_thick):
        x = 80 + i * 120
        cv2.line(img, (x, 30), (x + 10, height - 30), (30, 30, 30), 4)

    for i in range(n_thin):
        x = 140 + i * 120
        cv2.line(img, (x, 60), (x + 5, height - 60), (50, 50, 50), 1)

    return img
```

---

## Обработка ошибок

### Иерархия исключений

```python
class PhototrichogramError(Exception):
    """Базовое исключение приложения"""
    pass

class CameraError(PhototrichogramError):
    """Ошибка работы с камерой"""
    pass

class CalibrationError(PhototrichogramError):
    """Ошибка калибровки"""
    pass

class AnalysisError(PhototrichogramError):
    """Ошибка анализа изображения"""
    pass
```

### Стратегия обработки

| Слой | Стратегия |
|------|-----------|
| core/ | Raise специфичные исключения, НЕ ловить |
| data/ | Raise на ошибках БД, транзакции с rollback |
| gui/ | Ловить исключения, показывать пользователю (statusbar) |

---

## Дорожная карта

### v1.0 — MVP (текущая цель)
- [x] Архитектура и инструкции для агентов
- [ ] Core: пайплайн анализа (сегментация → измерение → классификация)
- [ ] Core: калибровка микроскопа
- [ ] Core: камера V4L2
- [ ] Data: SQLite схема, CRUD
- [ ] GUI: захват + анализ + калибровка
- [ ] Tests: core + data
- [ ] Сборка: PyInstaller

### v1.1 — Профили и история
- [ ] GUI: пациенты (CRUD)
- [ ] GUI: история измерений
- [ ] Reports: PDF-отчёт

### v1.2 — Мультизональный анализ
- [ ] Core: анализ 4 зон + сравнение с контролем
- [ ] GUI: вкладка мультизонального анализа
- [ ] Reports: сводный отчёт

### v1.3 — Мониторинг
- [ ] GUI: графики динамики
- [ ] Core: Anagen/Telogen (два снимка)
- [ ] Data: экспорт CSV

### v2.0 — Улучшения
- [ ] Core: Watershed для слипшихся волос
- [ ] Platform: Windows support (DirectShow)
- [ ] Core: ML-классификация
- [ ] GitHub: CI/CD, releases

---

## Платформа

### Текущая: Linux
- Камера: V4L2 через `cv2.VideoCapture(id, cv2.CAP_V4L2)`
- Сборка: PyInstaller → AppImage
- USB: udev rules для работы без sudo

### Планируется: Windows
- Камера: DirectShow через `cv2.VideoCapture(id, cv2.CAP_DSHOW)`
- Сборка: PyInstaller → .exe (--onefile)
- USB: UVC plug & play (обычно работает из коробки)
- SQLite: встроен в Python stdlib — работает без доп. зависимостей
- tkinter: встроен в Python — работает на Windows
- Кириллица: проверить шрифты tkinter на Windows

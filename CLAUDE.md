# Phototrichogram — Desktop Trichoscopy App

## Агенты (КРИТИЧЕСКИЕ ПРАВИЛА)

### 1. ВСЕ СЛОЖНЫЕ ЗАДАЧИ ТОЛЬКО ЧЕРЕЗ КООРДИНАТОРА
- Для сложных задач (> 3 файлов, новая фича, рефакторинг) → вызвать **coordinator**
- Координатор анализирует задачу и запускает нужных агентов
- Прямой вызов агентов разрешён только для простых задач

### 2. ОБЯЗАТЕЛЬНЫЕ АГЕНТЫ
После написания/изменения кода **ВСЕГДА** вызывать:
- **architecture-validator** → проверка разделения слоёв
- **code-critic** → поиск багов, security issues

### 3. ВЫБОР АГЕНТА
| Задача | Агент |
|--------|-------|
| Планирование сложной фичи | coordinator |
| Анализ изображений, OpenCV | cv-expert |
| Интерфейс tkinter | gui-expert |
| SQLite, модели данных | db-expert |
| Сборка, PyInstaller, деплой | packaging-expert |
| Проверка архитектуры | architecture-validator |
| Code review, баги, security | code-critic |
| Тесты pytest | test-expert |

### 4. ДОКУМЕНТАЦИЯ АГЕНТОВ
- Полное описание: `.claude/agents/README.md`
- Slash-команды: `/orchestrate`, `/develop-cv`, `/develop-gui`, `/develop-db`, `/review`, `/test-code`, `/validate`, `/package`

---

## Overview

Десктопное приложение для трихоскопического анализа волос с USB-микроскопа.
Замена коммерческого ПО стоимостью 50-150к рублей.

- **Язык**: Python 3.11+
- **GUI**: tkinter (ttk)
- **CV**: OpenCV + NumPy
- **БД**: SQLite3 (stdlib)
- **Графики**: matplotlib
- **Отчёты**: fpdf2
- **Сборка**: PyInstaller → AppImage (Linux)
- **Платформа**: Linux (Windows — в дорожной карте)
- **Интерфейс**: полностью на русском
- **Конфигурация**: `pyproject.toml` — единый источник (ruff, mypy, pytest, import-linter)

**Методология**: `docs/METHODOLOGY.md`
**Архитектура**: `docs/ARCHITECTURE.md`
**Задачи**: `docs/todo/*.md`

---

## Архитектурные правила

### Модульная структура (4 слоя)

```
GUI (gui/)
 ├──→ Reports (reports/)   PDF-отчёты, fpdf2 (код с v1.1)
 ├──→ Core (core/)         OpenCV/NumPy
 └──→ Data (data/)         SQLite3
Reports ──→ Core, Data (модели)

core/ и data/ независимы друг от друга; utils/ — нижний слой, доступен всем.
Контракт import-linter: gui → reports → (core | data) → utils.
Проверка: `lint-imports` (контракт в pyproject.toml).
```

### СТРОГО ЗАПРЕЩЕНО

| Запрет | Почему |
|--------|--------|
| cv2 вызовы в gui/ | Нарушение слоёв |
| tkinter в core/ | Core должен быть testable без GUI |
| sqlite3 в core/ | Core не знает о хранилище |
| Хардкод параметров анализа | Все пороги — через конфиг |
| cv2.imshow() | Показ через tkinter |
| print() вместо logging | Не отключается в релизе |
| f-string в SQL | SQL injection |
| Английский текст в UI | Интерфейс на русском. Исключение: клинические термины Terminal/Vellus/T/V — общепринятая трихологическая номенклатура |

### ОБЯЗАТЕЛЬНО

| Правило | Зачем |
|---------|-------|
| Все параметры анализа — настраиваемые | Разные микроскопы, разные пороги |
| dataclasses для моделей | Типизация, автокомплит |
| pathlib.Path для путей | Кроссплатформенность |
| logging вместо print | Уровни, отключение |
| Type hints везде | Автокомплит, mypy |
| Docstrings на русском | Понятность |
| pytest тесты | Качество кода |

---

## Конфигурация — ВСЕ настраиваемое

```python
@dataclass
class AnalysisConfig:
    # Калибровка
    um_per_px: float = 5.9
    fov_width_px: int = 640
    fov_height_px: int = 480

    # Пороги сегментации
    clahe_clip_limit: float = 2.0
    clahe_tile_size: int = 8
    median_blur_kernel: int = 5
    morph_kernel_size: int = 3
    morph_close_iterations: int = 1
    morph_open_iterations: int = 1

    # Пороги детекции (эмпирически подстроены, см. METHODOLOGY.md §7.1)
    min_hair_area: int = 150
    max_circularity: float = 0.6

    # Классификация
    vellus_threshold_um: float = 30.0
    intermediate_threshold_um: float = 40.0

    # Расчётные (автоматически)
    @property
    def vellus_threshold_px(self) -> float:
        return self.vellus_threshold_um / self.um_per_px

    @property
    def fov_area_mm2(self) -> float:
        w_mm = self.fov_width_px * self.um_per_px / 1000
        h_mm = self.fov_height_px * self.um_per_px / 1000
        return w_mm * h_mm
```

---

## Quick Start (разработка)

```bash
# Виртуальное окружение
cd /home/mvv/Projects/trihology
python3 -m venv .venv
source .venv/bin/activate

# Зависимости (dev-набор включает прод-зависимости через -r)
pip install -r requirements-dev.txt

# Запуск
python -m phototrichogram

# Тесты
pytest tests/ -v --cov=phototrichogram

# Линтеры и контракт слоёв
ruff check phototrichogram/ tests/
lint-imports

# Сборка (standalone-дистрибутив, onedir; см. build.spec)
pyinstaller build.spec
```

---

## Права USB

Для работы микроскопа без sudo нужны udev-правила:
```bash
sudo bash install-udev.sh
```
Скрипт ставит правило `/etc/udev/rules.d/99-phototrichogram.rules`
(`SUBSYSTEM=="video4linux", GROUP="video", MODE="0660"`) и подсказывает
добавить пользователя в группу `video` (`sudo usermod -aG video $USER` +
перевход в сессию), если он ещё не состоит в ней.

Временное решение — без установки правила, до перезагрузки:
```bash
sudo chmod a+rw /dev/video*
```

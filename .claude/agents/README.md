# Phototrichogram Agent Team

Мульти-агентная система для разработки десктопного приложения трихоскопии.

**Дата**: 2026-03-15
**Проект**: Phototrichogram (Desktop Trichoscopy App)
**Стек**: Python 3.11+ / OpenCV / tkinter / SQLite / PyInstaller

---

## КРИТИЧЕСКИЕ ПРАВИЛА

### 1. ВСЕ СЛОЖНЫЕ ЗАДАЧИ ЧЕРЕЗ КООРДИНАТОРА

Для задач > 3 файлов, новых фич, рефакторинга:
```bash
/orchestrate
```

### 2. ОБЯЗАТЕЛЬНЫЕ ПРОВЕРКИ ПОСЛЕ КОДА

После написания/изменения кода **ВСЕГДА**:
```bash
/validate   # проверка архитектуры
/review     # code review, баги, security
```

---

## Slash-команды

| Команда | Агент | Когда использовать |
|---------|-------|-------------------|
| `/orchestrate` | coordinator | Сложные задачи, планирование |
| `/develop-cv` | cv-expert | Компьютерное зрение, анализ изображений |
| `/develop-gui` | gui-expert | tkinter интерфейс |
| `/develop-db` | db-expert | SQLite, модели данных |
| `/package` | packaging-expert | Сборка, PyInstaller, AppImage |
| `/validate` | architecture-validator | Проверка паттернов |
| `/review` | code-critic | Code review, баги |
| `/test-code` | test-expert | Написание тестов |

---

## Доступные агенты

| # | Имя | Модель | Роль | Когда использовать |
|---|-----|--------|------|-------------------|
| 0 | **coordinator** | Sonnet | Планировщик, координатор | Планирование задач, выбор стратегии |
| 1 | **cv-expert** | **Opus** | Computer Vision | OpenCV пайплайн, сегментация, калибровка |
| 2 | **gui-expert** | **Opus** | GUI разработка | tkinter интерфейс, вкладки, виджеты |
| 3 | **db-expert** | Sonnet | SQLite/данные | Схема БД, миграции, запросы, модели |
| 4 | **packaging-expert** | Sonnet | Сборка/деплой | PyInstaller, AppImage, зависимости |
| 5 | **architecture-validator** | Sonnet | Валидация архитектуры | Проверка соблюдения паттернов |
| 6 | **code-critic** | **Opus** | Code review | Adversarial review, баги, security |
| 7 | **test-expert** | Sonnet | Тестирование | pytest, coverage |

---

## Архитектура проекта

### Модульная структура (4 слоя)

```
GUI (gui/)
 ├──→ Reports (reports/)   PDF-отчёты, fpdf2 (код с v1.1)
 ├──→ Core (core/)         OpenCV/NumPy
 └──→ Data (data/)         SQLite3
Reports ──→ Core, Data (модели)

core/ и data/ независимы друг от друга; utils/ — нижний слой.
Контракт import-linter: gui → reports → (core | data) → utils (`lint-imports`).
```

**Критические правила:**
- GUI слой НЕ содержит бизнес-логику — только отображение и ввод
- Core слой НЕ знает о GUI, Data и Reports — возвращает данные, не виджеты
- Data слой НЕ знает о Core — только CRUD операции
- Камера (V4L2) изолирована в отдельном модуле
- Все параметры анализа вынесены в конфиг — не хардкод
- Калибровка хранится в БД — не в коде

### Платформа
- **Текущая**: Linux (V4L2 для камеры)
- **Планируется**: Windows (DirectShow fallback) — в дорожной карте

---

## Workflow паттерны

### Actor-Critic Pattern

```
Actor (cv-expert/gui-expert) создаёт код
    ↓
Critic (code-critic) ревьюит код
    ↓
Если BLOCK/REQUEST_CHANGES → Actor исправляет
    ↓
Если APPROVE → Код готов
```

### Quality Gates

1. **После implementation** → `architecture-validator` проверяет разделение слоёв
2. **Перед commit** → `code-critic` делает adversarial review
3. **Перед release** → `test-expert` проверяет coverage

---

## Сценарии использования

### 1. Новый модуль анализа
```
coordinator → cv-expert (реализация) → test-expert (тесты)
→ architecture-validator + code-critic (параллельно)
```

### 2. Новая вкладка GUI
```
coordinator → gui-expert (UI) → cv-expert (интеграция с core)
→ code-critic (review)
```

### 3. Сборка релиза
```
test-expert (все тесты) → packaging-expert (сборка)
→ code-critic (финальная проверка)
```

---

## Референсы

| Документ | Путь | Описание |
|----------|------|----------|
| Методология | `docs/METHODOLOGY.md` | Клиническая методология, алгоритмы, ТЗ |
| Архитектура | `docs/ARCHITECTURE.md` | Структура модулей, зависимости |
| TODO планы | `docs/todo/*.md` | Текущие задачи |

---
name: orchestrate
description: "Координатор задач. Анализирует задачу, разбивает на фазы, назначает агентов. Для сложных задач (> 3 файлов, новая фича, рефакторинг)."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob, Bash
---

# Координатор задач

Ты — координатор мульти-агентной разработки приложения трихоскопии.

## Алгоритм

1. **Прочитай документацию**:
   - `docs/METHODOLOGY.md` — методология, алгоритмы, параметры
   - `docs/ARCHITECTURE.md` — структура, слои, правила
   - `docs/todo/` — существующие планы

2. **Проанализируй задачу**:
   - Какие модули затронуты (core, gui, data, utils)?
   - Сложность (simple / moderate / complex)?
   - Зависимости между подзадачами?

3. **Составь план**:
   ```markdown
   ## Task: [название]

   ### Фазы

   #### Phase 1: [название]
   **Agent**: cv-expert / gui-expert / db-expert
   **Task**: описание
   **Files**: список файлов

   #### Phase 2: ...

   ### Quality Gates
   1. architecture-validator после реализации
   2. code-critic перед коммитом
   3. test-expert — тесты
   ```

4. **Дождись одобрения** — покажи план пользователю, жди OK

5. **Запусти агентов** — последовательно или параллельно по плану:
   - Независимые фазы → параллельно (несколько Agent в одном ответе)
   - Зависимые фазы → последовательно

## Выбор агента

| Задача | Агент | Модель |
|--------|-------|--------|
| OpenCV, анализ, калибровка, камера | cv-expert | Opus |
| tkinter UI, виджеты, графики | gui-expert | Opus |
| SQLite, модели данных | db-expert | Sonnet |
| PyInstaller, AppImage | packaging-expert | Sonnet |
| Проверка архитектуры | architecture-validator | Sonnet |
| Code review | code-critic | Sonnet |
| Тесты pytest | test-expert | Sonnet |

## Правила

- Core-задачи (CV) выполняет cv-expert на Opus — это сложная работа
- GUI-задачи выполняет gui-expert на Opus — тоже сложная работа
- После КАЖДОЙ реализации → обязательно validate + review
- План сохранять в `docs/todo/YYYY-MM-DD_название.md`

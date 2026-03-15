---
name: develop-db
description: "Database разработка. SQLite схема, CRUD, модели данных. Запускает db-expert агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob
---

# Database Development

Запусти агента db-expert для задачи базы данных.

## Действия

1. Прочитай задачу пользователя
2. Прочитай `.docs/METHODOLOGY.md` секцию 5.3 (схема БД)
3. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: sonnet
   - Передай в prompt: задачу + контекст + правила из `.claude/agents/03-db-expert.md`

## Контекст для агента

Агент работает в `phototrichogram/data/`:
- `database.py` — подключение, миграции, CRUD
- `models.py` — dataclass модели (Patient, Session, Measurement, Calibration)

**Только параметризованные SQL! Модели — dataclasses!**

---
name: test-code
description: "Написание и запуск тестов. pytest, моки камеры, синтетические изображения, SQLite fixtures. Запускает test-expert агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob, Bash
---

# Testing

Запусти агента test-expert для написания/запуска тестов.

## Действия

1. Определи что тестировать (модуль, функция, или всё)
2. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: sonnet
   - Передай в prompt: что тестировать + правила из `.claude/agents/07-test-expert.md`

## Стратегия тестирования

| Слой | Как тестируем |
|------|--------------|
| core/ | Синтетические изображения, pytest fixtures, моки камеры через Protocol |
| data/ | Temp SQLite в tmp_path, CRUD проверки |
| utils/ | Unit tests конфигов, конвертаций |
| integration | Полный пайплайн с синтетикой |
| gui/ | НЕ тестируем автоматически |

## Запуск тестов

```bash
# Все тесты
pytest tests/ -v --cov=phototrichogram

# Конкретный модуль
pytest tests/test_segmentation.py -v

# С покрытием
pytest tests/ --cov=phototrichogram --cov-report=html
```

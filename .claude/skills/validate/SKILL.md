---
name: validate
description: "Проверка архитектуры. Валидация разделения слоёв, импортов, хардкода. Запускает architecture-validator агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob
---

# Architecture Validation

Запусти агента architecture-validator для проверки кода.

## Действия

1. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: sonnet
   - Передай в prompt правила из `.claude/agents/05-architecture-validator.md`
   - Укажи какие файлы/модули проверить (или все)

## Что проверяет

- gui/ НЕ импортирует cv2 (кроме конвертации) и sqlite3
- core/ НЕ импортирует tkinter и sqlite3
- data/ НЕ импортирует cv2 и tkinter
- Параметры анализа из конфига, не хардкод
- Нет cv2.imshow(), нет print() в core/data
- Параметризованные SQL запросы
- Type hints на всех функциях

## Когда запускать

- После реализации любой фичи
- Перед коммитом
- При рефакторинге

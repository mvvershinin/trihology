---
name: package
description: "Сборка и упаковка. PyInstaller, AppImage, requirements. Запускает packaging-expert агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob
---

# Packaging

Запусти агента packaging-expert для задачи сборки.

## Действия

1. Прочитай задачу пользователя
2. Прочитай `docs/ARCHITECTURE.md` — зависимости, структура
3. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: sonnet
   - Передай в prompt: задачу + правила из `.claude/agents/04-packaging-expert.md`

## Что может делать агент

- Создать/обновить `requirements.txt`, `pyproject.toml`
- Настроить PyInstaller spec
- Создать AppImage структуру
- Написать скрипты сборки
- Создать `install-udev.sh`
- Подготовить `.gitignore`, `LICENSE`, `README.md`

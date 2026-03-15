---
name: develop-gui
description: "GUI разработка. tkinter интерфейс, вкладки, виджеты, matplotlib. Запускает gui-expert агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob
---

# GUI Development

Запусти агента gui-expert для задачи интерфейса.

## Действия

1. Прочитай задачу пользователя
2. Прочитай `.docs/METHODOLOGY.md` секцию 5.5 (GUI экраны)
3. Прочитай `docs/ARCHITECTURE.md` — правила gui/ слоя
4. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: opus
   - Передай в prompt: задачу + контекст + правила из `.claude/agents/02-gui-expert.md`

## Контекст для агента

Агент работает в `phototrichogram/gui/`:
- `app.py` — главное окно + Notebook
- `capture_tab.py` — захват с микроскопа
- `analysis_tab.py` — анализ снимка
- `calibration_tab.py` — калибровка
- `patients_tab.py` — профили пациентов
- `history_tab.py` — история + графики
- `widgets.py` — переиспользуемые компоненты

**Интерфейс на русском! GUI НЕ содержит бизнес-логику!**

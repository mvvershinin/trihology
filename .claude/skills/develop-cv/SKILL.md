---
name: develop-cv
description: "Computer Vision разработка. OpenCV пайплайн, сегментация, калибровка, камера. Запускает cv-expert агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob
---

# CV Development

Запусти агента cv-expert для задачи компьютерного зрения.

## Действия

1. Прочитай задачу пользователя
2. Прочитай `docs/METHODOLOGY.md` секции 3-4 (оборудование, алгоритм) и 7 (параметры)
3. Прочитай `docs/ARCHITECTURE.md` — правила core/ слоя
4. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: opus
   - Передай в prompt: задачу + контекст из документации + правила из `.claude/agents/01-cv-expert.md`

## Контекст для агента

Агент работает в `phototrichogram/core/`:
- `analyzer.py` — оркестрация пайплайна
- `segmentation.py` — бинаризация, морфология
- `measurement.py` — MinFeret, площадь
- `classification.py` — Terminal/Vellus
- `calibration.py` — автокалибровка
- `camera.py` — V4L2 через OpenCV
- `stats.py` — плотность, сравнение зон

**Все параметры через AnalysisConfig** — не хардкод!

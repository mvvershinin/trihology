# План: Модернизация тулинга и синхронизация архитектуры

**Дата**: 2026-07-18
**Основание**: аудит актуальности архитектурных знаний (best practices 2026).

## Найденные проблемы (все исправлены 2026-07-18)

✅ **Конфигурация проекта**
✅ `pyproject.toml` создан — единый центр конфигурации
✅ mypy 2.3, ruff 0.15, pytest-cov, import-linter, pre-commit установлены в venv
✅ Enforcement слоёв: layers-контракт import-linter, `lint-imports` проходит

✅ **Устаревшие пины версий**
✅ `numpy>=2.0,<2.3` — обновлено до 2.2.6, opencv 4.13, breakage нет (полный pytest)
✅ `Pillow>=11,<13` — обновлено до 12.3
✅ `requires-python >=3.11`

✅ **Противоречия в документах**
✅ `05-architecture-validator.md`: диаграмма исправлена (Core standalone), добавлен раздел 7 Tooling
✅ Исключение про cv2.cvtColor убрано
✅ CLAUDE.md: ссылки на `build.spec` / `install-udev.sh` помечены как задачи фазы упаковки

---

## Task: Модернизация тулинга

### Фазы

#### Phase 1: pyproject.toml + обновление зависимостей
**Agent**: packaging-expert
**Сложность**: moderate
**Files**: `pyproject.toml` (новый), `requirements.txt`, `requirements-dev.txt`

- Создать `pyproject.toml` — единый центр конфигурации:
  - `[project]`: метаданные, `requires-python = ">=3.11"`, зависимости
  - `[tool.pytest.ini_options]`, `[tool.mypy]` (strict), `[tool.ruff]` (py311, line-length 100, правила E/F/W/I/N/UP/B/A/SIM)
  - `[tool.importlinter]` — layers-контракт: gui — верхний слой; core и data — независимые siblings; utils — нижний (точный контракт по факту импортов, включая reports/)
- Обновить пины: `numpy>=2.0,<2.3`, `opencv-python-headless>=4.10,<5.0`, `Pillow>=11,<13`
- requirements-dev.txt: добавить `import-linter>=2.0`
- Установить всё в venv (включая отсутствующие mypy/ruff), проверить импорты пакета

#### Phase 2: Тесты на NumPy 2
**Agent**: test-expert
**Сложность**: simple (если нет breakage) / moderate
**Files**: `tests/`, при необходимости `phototrichogram/core/*.py`

- Полный прогон pytest на обновлённых зависимостях
- NumPy 2 убрал алиасы (`np.float_` и др.), изменил promotion-правила — при падениях зафиксировать список
- Если есть breakage в core → передать cv-expert точечные фиксы, повторный прогон

#### Phase 3: Enforcement слоёв
**Agent**: packaging-expert
**Сложность**: simple
**Files**: `pyproject.toml`, `.pre-commit-config.yaml` (новый, опционально)

- Прогнать `lint-imports` — контракт должен пройти на текущем коде (слои реально чистые)
- Прогнать `ruff check` и `mypy phototrichogram/` — зафиксировать baseline; если ошибок много, оформить как отдельный todo, не чинить в этой задаче
- pre-commit hook: ruff + lint-imports (mypy — опционально, по времени прогона)

#### Phase 4: Синхронизация документов
**Agent**: coordinator (напрямую — только правки документации)
**Files**: `.claude/agents/05-architecture-validator.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`

- `05-architecture-validator.md`:
  - Диаграмма: Core ни от чего не зависит; GUI → Core, GUI → Data (как в ARCHITECTURE.md и в коде)
  - Убрать исключение про cv2.cvtColor в gui/
  - Добавить в чеклист: pyproject.toml существует и является источником конфигурации; `lint-imports` проходит
- `CLAUDE.md`: убрать ссылки на несуществующие build.spec / install-udev.sh (пометить «создаётся на фазе сборки»), обновить Quick Start
- `ARCHITECTURE.md`: секция pyproject.toml = фактическому файлу, пины = фактическим, `>=3.11`

### Quality Gates (пройдены 2026-07-18)

✅ architecture-validator — пункты 1–4 чисто; найденный рассинхрон диаграмм по слою reports (CLAUDE.md, инструкция валидатора, README агентов) исправлен
✅ code-critic — 11 находок: High (старые пины в 04-packaging-expert.md) и все Medium/Low исправлены; находка про разделитель `:` import-linter отклонена с доказательством (layers.py:20-21 установленного пакета: `|` = independent, `:` = non-independent — комментарий в pyproject был верен)
✅ pytest — NumPy-2-breakage отсутствует, 40/44 passed; 4 падения — предсуществующий баг терминологии зон (заведён отдельно, см. ниже)
✅ lint-imports — контракт KEPT (28 файлов, 43 зависимости), перепроверен после всех правок

### Отложено (отдельное решение, не в этой задаче)

⬜ Миграция на uv (замена pip+venv) — стандарт 2026, но трогает workflow разработчика
⬜ src-layout (`src/phototrichogram/`) — для приложения под PyInstaller не критично
⬜ Исправление baseline: ruff 35 ошибок (19 автофиксируемых), mypy strict 214

### Найдено при выполнении (новые задачи)

⬜ **Баг терминологии зон** (предсуществующий, 4 падающих теста): `core/diagnosis.py:27-32` использует «Лобная»/«Теменная», а `docs/METHODOLOGY.md` §2.3 и `tests/test_diagnosis.py` — «Фронтальная»/«Париетальная». Дубли также в `gui/capture_tab.py:42-43`, `gui/multizone_tab.py:37-38`. Нужно product-решение: канон — методичка (тогда правится код в 3 файлах) или UI-термины (тогда методичка + тесты)
⬜ **Покрытие тестами 12%**: существует только `tests/test_diagnosis.py` (44 теста); из чартера test-expert не написаны test_segmentation, test_measurement, test_classification, test_calibration, test_stats, test_database, test_analyzer, conftest, fixtures

⬜ **Дрейф дефолтов конфига от методички** (нашёл architecture-validator): `utils/config.py:81-82` — `min_hair_area=150`, `max_circularity=0.6`, а `docs/METHODOLOGY.md` §7.1 и пример в CLAUDE.md фиксируют `200`/`0.4`. Похоже на эмпирическую подстройку; нужно product-решение — обновить методичку или вернуть значения

⬜ **Англицизмы в UI** (Terminal/Vellus/«T/V» в history_tab, analysis_tab, multizone_tab): соответствуют терминологии METHODOLOGY.md, но формально нарушают правило CLAUDE.md «Английский текст в UI запрещён». Задокументировать как согласованное исключение либо перевести

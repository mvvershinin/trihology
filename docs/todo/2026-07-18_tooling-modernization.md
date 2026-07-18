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
⬜ v1.1 PDF-отчёты + v1.2 бэкап БД — отложено решением пользователя 2026-07-18; задел готов: шрифты DejaVu (кириллица) уже в `assets/fonts/` — PDF делать полностью из пакета, без системных зависимостей
⬜ v1.3 дерматоскопические признаки, v2.0 (Windows, watershed, CI/CD) — по дорожной карте

### Follow-up (реальная эксплуатация 2026-07-18, вечер)

⬜ **Дробление волоса на фрагменты** завышает счёт и плотность: длинный волос, уходящий из фокуса, рвётся на 2–4 контура, каждый считается отдельным волосом (видно на разметке zone_F). Нужна склейка коллинеарных фрагментов (продолжение направления, малый зазор) — связано с watershed из v2.0, но отдельная задача
⬜ **Волосы, лежащие поперёк кадра «транзитом»**, считаются наравне с растущими в зоне — систематическое завышение плотности; методологический вопрос (настоящая фототрихограмма считает фолликулы на выбритом участке)

### Follow-up (нашёл финальный гейт 2026-07-18)

⬜ **Дубль клинической логики статусов в GUI**: `gui/multizone_tab.py:64-123` (`_assess_status` + пороги) — третья параллельная реализация рядом с `core/diagnosis.get_zone_status` и `core/stats._assess_zone`. Вынести единый классификатор в core/, GUI должен его вызывать (правило «GUI без бизнес-логики»)
⬜ **METHODOLOGY §4 устарел**: описывает Intermodes + fill_holes вместо фактического black-hat пайплайна; supersession-примечание добавлено 2026-07-18, нужна полная актуализация раздела под код
✅ Baseline ruff: 0 ошибок (2026-07-18) — попутно исправлены 3 реальных F821-бага: `lambda: ...str(e)` в except-блоках analysis_tab/calibration_tab/capture_tab падали бы с NameError при показе ошибки (переменная `e` удаляется при выходе из except)
✅ Baseline mypy strict 214 → 0 по всему проекту (2026-07-18): data/ 47→0 (cast-helper), core/+utils/ 35→0 (6 cast на границах cv2), gui/+main 135→0 (19 targeted ignore на дырах стабов matplotlib/tkinter, удалено 18 устаревших ignore)

### Найдено при выполнении (новые задачи)

✅ **Баг терминологии зон** — решение пользователя: канон «Лобная/Теменная» (как в коде). Методичка §2.3 и тесты выровнены, 44/44 зелёные (2026-07-18)
✅ **Покрытие тестами 12% → 33%** (2026-07-18): добавлено 9 файлов / 214 тестов (segmentation, measurement, classification, calibration, stats, database, analyzer, camera через monkeypatch, conftest с синтетикой). Всего 258 зелёных; core/ и data/ покрыты на 88–100%. GUI не тестируется по стратегии ARCHITECTURE.md (ручная проверка)

✅ **Баг валидации калибровки** (нашёл test-expert, исправлено): `calibrate_from_ruler` при `division_um<=0` тихо возвращал нулевой/отрицательный масштаб — теперь CalibrationError + 2 теста

✅ **Дрейф дефолтов конфига** — решение: канон — код (эмпирическая подстройка 150/0.6). METHODOLOGY §7.1 и пример CLAUDE.md обновлены (2026-07-18)

✅ **Англицизмы в UI** — задокументированы как согласованное исключение в CLAUDE.md (клинические термины Terminal/Vellus/T/V) (2026-07-18)

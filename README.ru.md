# Фототрихограмма

Десктопное приложение для трихоскопического анализа волос с USB-микроскопа.
Замена коммерческого ПО стоимостью 50-150к рублей.

## Скачать готовую программу

Откройте [GitHub Releases](https://github.com/mvvershinin/trihology/releases/latest)
и выберите файл своей системы:

- **Windows 10/11 x64:** `Phototrichogram-win64.zip` — распакуйте папку целиком
  и запустите `phototrichogram.exe`. Python и SQLite устанавливать не нужно.
- **Linux x86_64:** `Phototrichogram-linux-x86_64.AppImage` — разрешите запуск
  (`chmod +x Phototrichogram-linux-x86_64.AppImage`) и откройте файл.

Рядом публикуются `SHA256SUMS` и сведения о сборке. Проверка сборки в CI не
означает проверку USB-микроскопа: ручная приёмка Windows с ним пока не проведена.
Инструкции первого запуска, обновления и резервного копирования — в
[руководстве пользователя](docs/USER_GUIDE.md).

## Возможности

- Захват снимков с USB-микроскопа (Linux V4L2 / Windows DirectShow, MSMF)
- Загрузка снимков из файлов
- Мультизональный анализ (лобная, теменная, височная, затылочная зоны)
- Автоматическое определение: плотность волос, толщина, соотношение Terminal/Vellus
- Сравнение зон с контрольной (затылочной)
- Профили пациентов, история измерений, графики динамики
- Автокалибровка по микрометрической шкале
- Визуализация: аннотированные снимки, гистограммы, сравнительные графики

## Хранение данных и обновления

AppImage на Linux, ZIP на Windows и запуск из исходников используют одну папку
данных текущего пользователя ОС — независимо от версии и расположения программы:

| Система | Папка по умолчанию |
|---|---|
| Linux | `~/.phototrichogram/` |
| Windows | `%USERPROFILE%\.phototrichogram\` (обычно `C:\Users\Имя\.phototrichogram\`) |

Содержимое: `phototrichogram.db` (SQLite: пациенты, обследования, результаты),
`config.json` (настройки), `images/` (снимки), `reports/` (папка PDF по умолчанию).
Пути к базе, снимкам и отчётам можно изменить в настройках. Данные хранятся
локально на компьютере и не входят в скачиваемую сборку.

Новая версия открывает записи от прежней версии или запуска из исходников.
Замена или удаление файла программы не удаляет данные. Перед обновлением
создайте резервную копию базы средствами приложения. Не запускайте одновременно
несколько копий программы с одной папкой данных.

Для отдельного профиля с изначально пустой базой задайте переменную
`PHOTOTRICHOGRAM_DATA_DIR` перед запуском. Она работает и для готовых пакетов,
и для запуска из исходников.

Linux:

```bash
PHOTOTRICHOGRAM_DATA_DIR="$HOME/.phototrichogram-test" ./Phototrichogram-linux-x86_64.AppImage
```

Windows PowerShell, из распакованной папки программы:

```powershell
$env:PHOTOTRICHOGRAM_DATA_DIR = "$env:USERPROFILE\.phototrichogram-test"
.\phototrichogram.exe
```

Основные данные останутся на месте. Повторный запуск с той же тестовой папкой
откроет её записи; для ещё одного пустого профиля выберите новую папку.
Для возврата к основной базе запустите программу обычным способом из нового
терминала, без этой переменной окружения.

## Требования

**Для разработки (Linux):**

- Linux (Ubuntu/Debian)
- Python 3.11+
- USB-микроскоп (UVC-совместимый)

**Для готового Windows-пакета:** ничего устанавливать не нужно — ни Python, ни
библиотек, ни SQLite, ни прав администратора. Windows 10/11 64-бит, ~350 МБ на
диске, UVC-микроскоп (драйвер в Windows встроенный). См.
[docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md).

## Установка

```bash
# Системные пакеты
sudo apt install python3-tk python3-pil.imagetk python3.12-venv

# Клонирование
git clone <url> && cd trihology

# Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Права USB-микроскопа (без sudo при последующих запусках)
sudo bash install-udev.sh
```

## Запуск

```bash
source .venv/bin/activate
python -m phototrichogram
```

## Использование

См. [docs/USER_GUIDE.md](docs/USER_GUIDE.md)

## Сборка

### Linux

Релизный AppImage собирается в чистом Ubuntu 22.04 тем же рецептом, что и в CI:

```bash
docker run --rm -v "$PWD:/project" -w /project \
  -e PHOTOTRICHOGRAM_VERSION=1.0.1 ubuntu:22.04 \
  bash tools/build_linux_release.sh
```

Результат — `dist/Phototrichogram-linux-x86_64.AppImage`. Скрипт устанавливает
зависимости внутри контейнера, проверяет готовую поставку без камеры и не
требует установки Python на компьютере получателя.

Для разработки standalone-каталог можно собрать отдельно:

```bash
.venv/bin/python -m PyInstaller build.spec --noconfirm
```

Файлы внутри `dist/phototrichogram/` переносите вместе.

### Windows

Основная релизная сборка выполняется нативно на Windows в GitHub Actions:
PyInstaller собирает standalone-каталог, который целиком упаковывается в ZIP.
Локальная сборка возможна на Windows тем же `build.spec`.

Прежний способ собрать Windows runtime-пакет на Linux остаётся резервным:

```bash
python tools/build_windows_package.py --out dist/windows
```

Подробности — [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md).
Автоматический выпуск запускается тегом `vX.Y.Z`: проверки, сборка обеих
платформ и публикация файлов в GitHub Releases. Ручной запуск workflow
собирает пробные артефакты без публикации релиза.

## Стек

| Компонент | Технология |
|-----------|-----------|
| GUI | tkinter (ttk) |
| CV | OpenCV + NumPy |
| БД | SQLite3 |
| Графики | matplotlib |
| Отчёты | fpdf2 |
| Сборка | PyInstaller |

## Структура

```
phototrichogram/
├── core/           # CV-пайплайн (сегментация, измерение, классификация)
├── gui/            # tkinter интерфейс (вкладки, виджеты)
├── data/           # SQLite (модели, CRUD, миграции)
├── utils/          # Конфигурация, утилиты изображений
└── reports/        # PDF-отчёты
```

## AI-агенты: Claude и Codex

Адаптеры Codex ниже относятся к локальному рабочему окружению владельца;
они пока не входят в опубликованный исходный код и готовые сборки.
Исходные инструкции Claude находятся в репозитории.

Для обеих сред указан общий текущий план в docs/todo; отдельные копии плана
не создаются. В остальном инструкции Claude сохранены. Для Codex добавлена
отдельная копия: `AGENTS.md` (правила проекта и адаптер инструментов),
`.codex/agents/` (8 ролей и дословные Markdown-копии), `.agents/skills/`
(8 дословных копий команд), `.codex/config.toml` (подагенты включены).
Копии самостоятельные: изменения Claude не синхронизируются автоматически.

Запускайте Codex из корня проекта в новой сессии:

```bash
cd /home/mvv/Projects/trihology
codex
```

В Codex команды вызываются через skills:

| Claude | Codex | Назначение |
|--------|-------|------------|
| `/orchestrate` | `$orchestrate` | Координатор сложной задачи |
| `/develop-cv` | `$develop-cv` | OpenCV, анализ и калибровка |
| `/develop-gui` | `$develop-gui` | tkinter и графики |
| `/develop-db` | `$develop-db` | SQLite и модели |
| `/package` | `$package` | Сборка и поставка |
| `/validate` | `$validate` | Проверка архитектуры |
| `/review` | `$review` | Проверка кода |
| `/test-code` | `$test-code` | Тесты |

Пример запроса: `$orchestrate Добавь экспорт результатов исследования`.
Можно также попросить: «Запусти cv-expert для анализа сегментации».
Список skills доступен через `/skills`.

Роли сохраняют исходные инструкции, включая порядок проверок и согласование
плана. Модель и уровень рассуждений наследуются от текущей сессии Codex;
Opus/Sonnet из Claude не назначаются. Хуки Claude не перенесены в исполнение
Codex, а `allowed-tools` Claude не являются ограничениями безопасности Codex.
Нужна версия Codex с поддержкой пользовательских агентов и доверенный проект:
в недоверенном проекте локальная конфигурация может не применяться.

Формат подключения: [агенты Codex](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[skills](https://learn.chatgpt.com/docs/build-skills).

## Лицензия

MIT

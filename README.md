# Фототрихограмма

Десктопное приложение для трихоскопического анализа волос с USB-микроскопа.
Замена коммерческого ПО стоимостью 50-150к рублей.

## Возможности

- Захват снимков с USB-микроскопа (V4L2)
- Загрузка снимков из файлов
- Мультизональный анализ (лобная, теменная, височная, затылочная зоны)
- Автоматическое определение: плотность волос, толщина, соотношение Terminal/Vellus
- Сравнение зон с контрольной (затылочной)
- Профили пациентов, история измерений, графики динамики
- Автокалибровка по микрометрической шкале
- Визуализация: аннотированные снимки, гистограммы, сравнительные графики

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

Standalone-дистрибутив (PyInstaller, режим onedir):

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt   # добавляет pyinstaller
pyinstaller build.spec --noconfirm
```

Результат — каталог `dist/phototrichogram/` с исполняемым файлом
`phototrichogram` и всеми зависимостями рядом (запускать можно только из
этого каталога, файлы `_internal/` переносить/удалять по отдельности нельзя).

AppImage (нужен `appimagetool` в PATH — не устанавливается автоматически):
```bash
bash build-appimage.sh
```

### Windows

Собирается **на Linux**, Windows-машина не нужна:

```bash
python tools/build_windows_package.py --out dist/windows
```

Результат — `dist/windows/Phototrichogram-win64.zip` (~122 МБ): распаковал и
запустил, устанавливать нечего. PyInstaller здесь неприменим — он пакует
интерпретатор той системы, где запущен; скрипт вместо этого складывает готовые
Windows-бинарники (Python из conda-forge с tkinter и SQLite + колёса win_amd64).

Подробности, разбор зависимостей и разбор проблем:
[docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md)

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
└── reports/        # PDF-отчёты (в разработке)
```

## Лицензия

MIT

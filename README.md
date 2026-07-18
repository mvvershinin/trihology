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

- Linux (Ubuntu/Debian)
- Python 3.11+
- USB-микроскоп (UVC-совместимый)

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

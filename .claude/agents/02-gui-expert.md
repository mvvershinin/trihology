---
name: gui-expert
description: tkinter/ttk GUI expert for desktop trichoscopy application
tools: Read, Grep, Glob, Edit, Write, Bash
model: opus
---

CRITICAL: Read `.docs/METHODOLOGY.md` section 5.5 (GUI screens) before any GUI work.

## Role: GUI Developer

Отвечает за весь пользовательский интерфейс: tkinter окно, вкладки, виджеты, видеопоток, графики.

## Primary Responsibilities

### 1. Main Application Window
- `gui/app.py` — главное окно с вкладками (ttk.Notebook)
- Меню, статусбар, глобальные хоткеи

### 2. Tab Modules
- `gui/capture_tab.py` — захват с микроскопа (live preview, snapshot)
- `gui/analysis_tab.py` — анализ снимка (результат + разметка)
- `gui/multizone_tab.py` — мультизональный анализ (карта зон)
- `gui/patients_tab.py` — профили пациентов (CRUD)
- `gui/history_tab.py` — история и динамика (графики)
- `gui/calibration_tab.py` — калибровка микроскопа

### 3. Reusable Widgets
- `gui/widgets.py` — переиспользуемые компоненты (ZoneSelector, ImageViewer, ResultsTable)

### 4. Matplotlib Integration
- Встраивание графиков (FigureCanvasTkAgg) в tkinter
- Гистограммы толщин, столбчатые диаграммы по зонам, графики динамики

## Technical Standards

### Зависимости
```python
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk  # pillow — для отображения OpenCV кадров
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
```

### GUI НЕ содержит бизнес-логику
```python
# ПРАВИЛЬНО — GUI вызывает core, отображает результат:
class AnalysisTab(ttk.Frame):
    def on_analyze(self):
        result = self.analyzer.analyze(self.current_image, self.config)
        self.display_result(result)

# НЕПРАВИЛЬНО — GUI сама анализирует:
class AnalysisTab(ttk.Frame):
    def on_analyze(self):
        gray = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)  # CV в GUI!
        contours = cv2.findContours(...)  # логика в GUI!
```

### Видеопоток — через after(), НЕ threading
```python
# ПРАВИЛЬНО — tkinter event loop:
def update_preview(self):
    frame = self.camera.grab_frame()
    if frame is not None:
        self.show_frame(frame)
    self.after(66, self.update_preview)  # ~15 fps

# НЕПРАВИЛЬНО — thread напрямую меняет GUI:
def camera_thread(self):
    while True:
        frame = self.camera.grab_frame()
        self.canvas.create_image(...)  # thread-unsafe!
```

### OpenCV → tkinter конвертация
```python
def cv2_to_tk(cv_image: np.ndarray) -> ImageTk.PhotoImage:
    """Конвертация BGR OpenCV → RGB PIL → tkinter PhotoImage"""
    rgb = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    return ImageTk.PhotoImage(pil_image)
```

### Интерфейс — ПОЛНОСТЬЮ на русском
```python
# Все надписи, кнопки, заголовки, подсказки — на русском:
ttk.Label(self, text="Плотность (волос/см²):")
ttk.Button(self, text="Сделать снимок", command=self.capture)
notebook.add(tab, text="Калибровка")
```

### Layout — grid, НЕ pack для сложных экранов
```python
# grid даёт предсказуемый layout:
self.image_label.grid(row=0, column=0, rowspan=3, sticky="nsew")
self.results_frame.grid(row=0, column=1, sticky="nsew")
self.controls_frame.grid(row=1, column=1, sticky="ew")
```

### Отзывчивый UI — тяжёлые операции в thread с callback
```python
import threading

def on_analyze(self):
    self.status_var.set("Анализ...")
    self.analyze_btn.config(state="disabled")

    def do_analysis():
        result = self.analyzer.analyze(self.current_image, self.config)
        # Вернуться в GUI thread:
        self.after(0, lambda: self.on_analysis_complete(result))

    threading.Thread(target=do_analysis, daemon=True).start()

def on_analysis_complete(self, result):
    self.display_result(result)
    self.status_var.set("Готово")
    self.analyze_btn.config(state="normal")
```

## Tab Specifications

### Capture Tab
- Dropdown выбора устройства (/dev/video0..N)
- Canvas с live preview (~15 fps)
- Выбор зоны (F/P/T/O) — radio buttons
- Кнопка "Снимок" + hotkey (Space)
- Thumbnail-лента последних снимков

### Analysis Tab
- Загрузка снимка (файл или из захвата)
- Canvas с разметкой (контуры волос цветом по классификации)
- Таблица результатов (плотность, толщина, T/V)
- Matplotlib гистограмма толщин
- Кнопка "Добавить в сессию"

### Calibration Tab
- Загрузка снимка шкалы
- Preview с обнаруженными делениями
- Поле "Цена деления (мкм)" — default 100
- Вычисленный масштаб (мкм/px)
- Ручная коррекция
- Кнопка "Сохранить калибровку"

### Patients Tab
- Treeview список пациентов
- Форма создания/редактирования
- Поля: ФИО, дата рождения, пол, диагноз, заметки
- Кнопки: Добавить, Редактировать, Удалить

### History Tab
- Выбор пациента
- Treeview список сессий
- Matplotlib графики динамики (плотность, vellus% по датам)
- Кнопка "Экспорт PDF"

## Anti-Patterns

```
NEVER:
- import cv2 в gui/ модулях (кроме конвертации cv2→tk)
- SQL запросы в gui/ модулях
- Блокирующие операции в main thread (freeze UI)
- messagebox вместо inline status (раздражает пользователя)
- Английский текст в UI (всё на русском!)
- Hardcoded размеры окон (используй sticky + weight)
```

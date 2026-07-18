# Фототрихограмма — Методология и ТЗ на Desktop-приложение

## 1. Цель проекта

Автономное десктопное приложение (Linux, один бинарник) для анализа трихоскопических снимков с USB-микроскопа. Замена коммерческого ПО (Nautilus PhTG, TrichoSciencePro и аналогов стоимостью 50 000–150 000 ₽).

Приложение должно:
- Захватывать изображения напрямую с USB-микроскопа
- Автоматически подсчитывать и классифицировать волосы
- Вести профили пациентов с историей измерений
- Генерировать отчёты в PDF
- Работать без установки зависимостей (единый бинарник / AppImage)
- Полностью русскоязычный интерфейс

---

## 2. Клиническая методология

### 2.1 Что такое фототрихограмма

Количественный метод оценки состояния волос по микроскопическим снимкам кожи головы. Измеряемые параметры:

| Параметр | Что измеряет | Клиническое значение |
|----------|-------------|---------------------|
| Плотность (волос/см²) | Количество волос на единицу площади | Общее состояние волосяного покрова |
| Толщина волоса (мкм) | Диаметр стержня | Степень миниатюризации |
| Terminal / Vellus (%) | Соотношение терминальных и пушковых | Маркер андрогенетической алопеции |
| Anagen / Telogen (%) | Соотношение фаз роста и покоя | Активность выпадения |

### 2.2 Референсные нормы (общие)

| Показатель | Норма | Пограничное | Патология |
|-----------|-------|-------------|-----------|
| Плотность | 150–300 волос/см² | 100–150 | < 100 |
| Vellus % | < 20% | 20–30% | > 30% |
| Anagen % | 80–90% | 70–80% | < 70% |
| Telogen % | 10–20% | 20–30% | > 30% |
| Толщина Terminal | > 40 мкм | 30–40 мкм | < 30 мкм |
| Толщина Vellus | < 30 мкм | — | — |
| T/V соотношение | > 4:1 | 2–4:1 | < 2:1 |
| Снижение плотности vs затылок | < 15% | 15–30% | > 30% |

Это общие ориентиры (fallback, когда демография пациента не указана).

### 2.2a Персональные нормы по демографии

Плотность и толщина волос значимо зависят от этноса, возраста и пола.
Программа выбирает норму по этим параметрам пациента (`core/norms.py`,
`select_norms`). Значения справочные, НЕ диагноз; итоговую оценку даёт
трихолог, сравнивая зоны с контрольной затылочной.

**Плотность (волос/см², здоровые взрослые):**

| Этнос | Норма | Погранично | Источник |
|-------|-------|-----------|----------|
| Европеоид | 180–250 | 130–180 | лоб 230, макушка 226, затылок 214 [PMC6219221] |
| Латиноамериканец | 150–200 | 100–150 | 169–178 [PMC6219221] |
| Африканец | 130–175 | 80–130 | 148–160 [PMC6219221] |
| Восточноазиат | 120–170 | 70–120 | ханьцы ~143, тайцы [PMC7035527] |
| Южноазиат | 140–190 | 90–140 | индийские мужчины [PMC11753574] |

**Толщина терминального волоса (мкм):** восточноазиат 80–120 · европеоид
60–90 · африканец 55–90 · прочие 50–90 (поперечник: азиат — круглый и самый
толстый, африканец — уплощённый).

**Vellus:** реальная норма ~6–11% по зонам (затылок ~6,6%, лоб ~10,7%)
[PMC3157785] — уже норма ≤15%, погранично 15–25% (прежнее «<20» было грубым).

**Возраст:** плотность значимо снижается после ~35 лет [PMC6219221,
PMC11497558]; программа снижает нижнюю границу нормы плотности на ~10% за
каждые 15 лет после 35 (не глубже −40%). Толщину/vellus/T-V возраст не меняет.

**Пол:** плотность фолликулов близка (жен. ~108 против муж. ~105/см²); при
андрогенетической алопеции важнее паттерн и сравнение с затылком, поэтому пол
влияет только на подпись нормы, не на числа.

**Источники** (PubMed/PMC):
- [PMC6219221] Оценка плотности по этносам, здоровая популяция США.
- [PMC7035527] Плотность и диаметр, взрослые тайцы.
- [PMC11753574] Трихоскопия здоровых индийских мужчин.
- [PMC3157785] Нормы у здоровых женщин, стандартизация метода.
- [PMC6830029] Трихоскопия у детей до 17 лет (T/V сильно зависит от возраста).
- [PMC11497558] Плотность и возраст, кросс-секционное исследование.

Пороговые полосы (пограничные зоны, возрастной коэффициент) — консервативные
эвристики поверх опорных значений литературы; точные границы в источниках не
заданы. Задокументированы в `core/norms.py`.

### 2.3 Стандартные зоны скальпа

```
              ┌──────────────┐
             /   F (лобная)    \
            /                    \
           │   ┌──────────────┐   │
           │   │  P (макушка)  │   │
  T (L) ←──│   │   (vertex)   │   │──→ T (R)
  висок    │   └──────────────┘   │    висок
           │                      │
            \   O (затылок)      /
             \   (контроль)     /
              └──────────────┘
```

| Код | Зона | Координаты на голове | Роль |
|-----|------|---------------------|------|
| F | Лобная (frontal) | Лобная линия роста, 2 см от края | Первая зона истончения при АГА |
| P | Теменная (parietal) | Макушка (vertex) | Основная зона облысения у мужчин |
| T | Височная (temporal) | Виски (L/R) | Рецессия височной линии |
| O | Затылочная (occipital) | Затылок, 2 см выше нижней линии роста | Контрольная зона, устойчива к АГА |

Затылочная зона (O) — эталон. Волосы здесь генетически устойчивы к дигидротестостерону (DHT), поэтому используются как базовое значение для сравнения.

### 2.4 Протокол съёмки

**Одиночный анализ (плотность + толщина + T/V):**
1. Вымыть и высушить волосы
2. Опционально: окрасить контрастным красителем участок ~1 см²
3. Плотно прижать микроскоп к коже (без давления)
4. Снять 2–3 кадра на каждую зону
5. Сохранить изображения напрямую (не фото экрана)

**Двойной анализ (Anagen/Telogen):**
1. День 0: выбрить участок ~1 см², отметить точку маркером/татуажем
2. Сделать снимок сразу после бритья
3. День 2–3: снять тот же участок
4. Выросшие волосы = Anagen (фаза роста), без изменений = Telogen (покой)

**Мониторинг лечения:**
- Интервал: 3–6 месяцев
- Те же зоны, те же условия съёмки
- Использовать татуажные точки для точного позиционирования
- Сравнивать: рост плотности, снижение % Vellus, стабильность показателей

### 2.5 Паттерны заболеваний

**Мужской тип (шкала Norwood):**
- F↓ + P↓, O = норма → Андрогенетическая алопеция (АГА)
- Все зоны ↓ равномерно → Диффузное выпадение (Telogen effluvium)
- Локальные зоны = 0 → Alopecia areata (гнёздная)

**Женский тип (шкала Ludwig):**
- P↓, F ≈ норма, O = норма → АГА по женскому типу
- Расширение центрального пробора при сохранении лобной линии
- Диффузное истончение без чёткой локализации → Хронический TE

---

## 3. Оборудование

### 3.1 USB-микроскоп

**Текущее оборудование пользователя:**
- Китайский USB-микроскоп
- Vendor ID: 05e3, Product ID: f12b
- Протокол: UVC (USB Video Class) — работает на Linux без драйверов
- Формат видео: YUYV 4:2:2
- Разрешение: 640×480 @ 30fps
- Подсветка: LED кольцо
- Кнопка захвата на корпусе (генерирует клавишу 0x21ff8f)

**Особенности:**
- Работает как стандартная вебкамера (/dev/videoN)
- Нестабильное USB-соединение при движении (типичная проблема бюджетных моделей)
- Требуется отключение USB autosuspend: `echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend`
- При наличии встроенной вебкамеры — микроскоп обычно на /dev/video2

**Рекомендуемые характеристики для будущих моделей:**
- Разрешение: 2 Мп+ (1600×1200)
- Увеличение: 200× (рабочий диапазон для трихоскопии)
- USB-подключение к ПК (не только OTG)
- Прямое сохранение изображений

### 3.2 Калибровка

**Калибровочный инструмент:** микрометрическая шкала 0.1 мм/деление (AliExpress, ~300 ₽)

**Результаты калибровки текущего микроскопа:**

| Параметр | Значение |
|----------|----------|
| Расстояние между делениями | 17 пикселей |
| Цена деления | 0.1 мм (100 мкм) |
| **Масштаб: 1 пиксель** | **5.9 мкм** |
| Поле зрения (ширина) | 640 × 5.9 = 3776 мкм = 3.8 мм |
| Поле зрения (высота) | 480 × 5.9 = 2832 мкм = 2.8 мм |
| **Площадь поля зрения** | **10.6 мм²** |

**Алгоритм автокалибровки (для встраивания в приложение):**
1. Пользователь фотографирует калибровочную шкалу
2. Перевод в grayscale
3. Порог бинаризации ~128 (тёмные линии на светлом фоне)
4. Построчный анализ (горизонтальные линии пикселей)
5. Обнаружение тёмных полос: нахождение rising/falling edge
6. Фильтр по ширине полосы < 12 px (только мелкие деления)
7. Вычисление центров полос: (start + end) / 2
8. Расстояния между соседними центрами
9. Медианная фильтрация: отброс выбросов за пределами ±30% от медианы
10. Итоговый масштаб: цена_деления_мкм / медиана_расстояний_px
11. Усреднение по множеству строк (y=260..300 в текущей конфигурации)

**Бюджетная альтернатива:** монета 1 рубль — расстояние между рифлениями = 0.5 мм

**Формула пересчёта:**
```
1 пиксель (мкм) = цена_деления (мкм) / расстояние_между_делениями (px)
площадь_поля (мм²) = (ширина_кадра × мкм/px / 1000) × (высота_кадра × мкм/px / 1000)
порог_vellus (px) = 30 мкм / мкм_на_пиксель
```

---

## 4. Алгоритм анализа изображений

> **Примечание (2026-07-18)**: описание пайплайна в этом разделе частично
> устарело. Фактическая реализация (`core/segmentation.py`) использует
> **Black-hat морфологию + Otsu/Adaptive порог** вместо Intermodes и
> **не применяет** fill_holes (заполнение дыр сливает тонкие волосы).
> Актуальные значения параметров — §7.1 и `utils/config.py` (источник истины —
> код). Полная актуализация раздела — в списке задач.

### 4.1 Общая схема пайплайна

```
Исходное изображение (RGB, 640×480)
        │
        ▼
[1] Предобработка
        │  → Grayscale (8-bit)
        │  → Нормализация контраста (CLAHE или saturated=2%)
        │  → Медианный фильтр (radius=2) — удаление шума
        │
        ▼
[2] Сегментация волос
        │  → Автопорог (метод Intermodes)
        │  → Морфологическое закрытие (Close) — заполнение разрывов
        │  → Морфологическое открытие (Open) — удаление мусора
        │  → Заполнение дыр (Fill Holes)
        │
        ▼
[3] Детекция отдельных волос (Particle Analysis)
        │  → Фильтр по площади: > MIN_HAIR_AREA (200 px²)
        │  → Фильтр по форме: circularity < MAX_CIRCULARITY (0.4)
        │  → Результат: контуры (ROI) каждого волоса
        │
        ▼
[4] Измерение параметров каждого волоса
        │  → MinFeret (минимальный диаметр) → толщина
        │  → Area → площадь сечения
        │  → Perimeter → периметр
        │
        ▼
[5] Классификация
        │  → Terminal: MinFeret ≥ порог (5 px = 30 мкм)
        │  → Vellus: MinFeret < порог
        │
        ▼
[6] Статистика и отчёт
        → Плотность = кол-во / площадь_поля × 100 (волос/см²)
        → Средняя толщина, мин, макс
        → % Terminal, % Vellus
        → Гистограмма распределения толщин
        → Сравнение с контрольной зоной
```

### 4.2 Предобработка (подробно)

**Вход:** RGB изображение 640×480 (YUYV с камеры → BGR через OpenCV)

**Шаг 1 — Grayscale:**
```python
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
```

**Шаг 2 — Нормализация контраста:**
```python
# Вариант A: CLAHE (адаптивный, предпочтительный)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
enhanced = clahe.apply(gray)

# Вариант B: Линейная нормализация (saturated=2%)
# Обрезаем 2% самых тёмных и светлых пикселей, растягиваем на 0-255
p_low, p_high = np.percentile(gray, [2, 98])
enhanced = np.clip((gray - p_low) * 255.0 / (p_high - p_low), 0, 255).astype(np.uint8)
```

**Шаг 3 — Шумоподавление:**
```python
# Медианный фильтр — сохраняет края (контуры волос)
denoised = cv2.medianBlur(enhanced, 5)  # kernel=5 (radius≈2)
```

### 4.3 Сегментация (подробно)

**Цель:** бинарная маска, где белый = волос, чёрный = кожа/фон.

**Автопорог — метод Intermodes:**
```python
# OpenCV не имеет встроенного Intermodes, реализация:
def threshold_intermodes(image):
    hist = cv2.calcHist([image], [0], None, [256], [0, 256]).flatten()
    
    # Сглаживание гистограммы до бимодальности
    smoothed = hist.copy()
    while count_modes(smoothed) > 2:
        smoothed = np.convolve(smoothed, [0.25, 0.5, 0.25], mode='same')
    
    # Порог = среднее между двумя модами
    modes = find_modes(smoothed)
    threshold = int((modes[0] + modes[1]) / 2)
    
    _, binary = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY_INV)
    return binary

# Альтернатива: Otsu (проще, часто не хуже)
_, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
```

**Почему THRESH_BINARY_INV:** волосы тёмные на светлом фоне кожи → инвертируем, чтобы волосы стали белыми.

**Морфологические операции:**
```python
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

# Закрытие (Close) — соединяет разрывы в контурах волос
closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)

# Открытие (Open) — удаляет мелкий мусор (артефакты < ядра)
opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)

# Заполнение дыр внутри контуров
# (floodFill от краёв, потом инвертировать)
filled = fill_holes(opened)
```

**Реализация fill_holes:**
```python
def fill_holes(binary):
    h, w = binary.shape
    mask = np.zeros((h + 2, w + 2), np.uint8)
    flood = binary.copy()
    cv2.floodFill(flood, mask, (0, 0), 255)
    flood_inv = cv2.bitwise_not(flood)
    return binary | flood_inv
```

### 4.4 Детекция волос (Particle Analysis)

**В OpenCV — через contours:**
```python
contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

hairs = []
for cnt in contours:
    area = cv2.contourArea(cnt)
    perimeter = cv2.arcLength(cnt, True)
    
    # Фильтр по площади
    if area < MIN_HAIR_AREA:  # 200 px²
        continue
    
    # Circularity = 4π × area / perimeter²
    # Круг = 1.0, длинный объект → 0
    if perimeter > 0:
        circularity = 4 * np.pi * area / (perimeter * perimeter)
    else:
        continue
    
    # Фильтр по форме — волосы вытянутые (circularity < 0.4)
    if circularity > MAX_CIRCULARITY:  # 0.4
        continue
    
    hairs.append(cnt)
```

**Параметры фильтрации:**

| Параметр | Значение | Почему |
|----------|----------|--------|
| MIN_HAIR_AREA | 200 px² | Отсекает мусор, пыль, артефакты |
| MAX_CIRCULARITY | 0.4 | Волос вытянут; круглые объекты = не волос |

Эти параметры калибруются под конкретный микроскоп и увеличение. При другом разрешении/масштабе — пересчитать.

### 4.5 Измерение толщины (MinFeret)

**MinFeret** — минимальная ширина описанного прямоугольника повёрнутого на произвольный угол. Для вытянутого объекта (волос) это и есть толщина.

```python
def measure_hair(contour):
    # MinAreaRect → повёрнутый прямоугольник
    rect = cv2.minAreaRect(contour)
    (cx, cy), (w, h), angle = rect
    
    # MinFeret = меньшая сторона прямоугольника
    min_feret = min(w, h)
    max_feret = max(w, h)  # длина волоса в кадре
    
    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, True)
    circularity = 4 * np.pi * area / (perimeter ** 2) if perimeter > 0 else 0
    
    return {
        'min_feret': min_feret,        # толщина (px)
        'max_feret': max_feret,        # длина в кадре (px)
        'area': area,                   # площадь (px²)
        'perimeter': perimeter,         # периметр (px)
        'circularity': circularity,     # форм-фактор
        'center': (cx, cy),            # центр масс
        'angle': angle,                 # угол наклона
        'contour': contour             # контур для визуализации
    }
```

**Пересчёт в микроны:**
```python
thickness_um = min_feret * CALIBRATION_UM_PER_PX  # 5.9 мкм/px
```

### 4.6 Классификация Terminal / Vellus

```python
VELLUS_THRESHOLD_UM = 30  # мкм (клинический стандарт)
VELLUS_THRESHOLD_PX = VELLUS_THRESHOLD_UM / CALIBRATION_UM_PER_PX  # 30 / 5.9 ≈ 5 px

def classify_hair(hair):
    if hair['min_feret'] >= VELLUS_THRESHOLD_PX:
        return 'terminal'
    else:
        return 'vellus'
```

Некоторые системы используют 3 категории:
- Terminal: > 40 мкм (толстый, пигментированный)
- Intermediate: 30–40 мкм (промежуточный, ранняя миниатюризация)
- Vellus: < 30 мкм (пушковый, тонкий)

### 4.7 Анализ Anagen/Telogen

Требует двух снимков одной точки с интервалом 2–3 дня после бритья.

```python
def anagen_telogen(count_day0, count_day2):
    """
    Day 0: сразу после бритья — видны все стержни (обрезанные)
    Day 2-3: выросшие стержни = anagen, без изменений = telogen
    
    Волосы в фазе anagen растут ~0.3 мм/день → за 2-3 дня вырастут на 0.6-0.9 мм,
    что заметно при 200× увеличении.
    Волосы в telogen не растут → длина не изменилась.
    """
    if count_day2 > count_day0:
        anagen = count_day2 - count_day0
        telogen = count_day0
    else:
        # Если count_day2 <= count_day0, все видимые = telogen
        anagen = 0
        telogen = count_day2
    
    total = anagen + telogen
    anagen_pct = anagen / total * 100 if total > 0 else 0
    telogen_pct = telogen / total * 100 if total > 0 else 0
    
    return anagen, telogen, anagen_pct, telogen_pct
```

**Улучшенный алгоритм (для будущей версии):**
Вместо простого подсчёта — совмещение двух изображений и попиксельное сравнение длин волос. Требует выравнивания (image registration) по ключевым точкам кожи.

### 4.8 Мультизональный анализ

```python
def multizone_analysis(zones_data, calibration):
    """
    zones_data = {
        'F': [image1, image2, ...],  # несколько снимков на зону
        'P': [...],
        'T': [...],
        'O': [...]  # контрольная
    }
    """
    results = {}
    
    for zone_code, images in zones_data.items():
        zone_results = []
        for img in images:
            r = analyze_single_image(img, calibration)
            zone_results.append(r)
        
        # Усреднение по снимкам зоны
        results[zone_code] = average_results(zone_results)
    
    # Сравнение с контролем (O)
    if 'O' in results:
        ref = results['O']
        for code in ['F', 'P', 'T']:
            if code in results:
                z = results[code]
                z['density_diff_pct'] = (z['density'] - ref['density']) / ref['density'] * 100
                z['vellus_diff_pp'] = z['vellus_pct'] - ref['vellus_pct']
                
                # Диагностическая оценка
                if z['density_diff_pct'] < -30 and z['vellus_diff_pp'] > 10:
                    z['assessment'] = 'Выраженная миниатюризация'
                elif z['density_diff_pct'] < -15 or z['vellus_diff_pp'] > 5:
                    z['assessment'] = 'Начальная миниатюризация'
                else:
                    z['assessment'] = 'В пределах нормы'
    
    return results
```

---

## 5. Архитектура приложения

### 5.1 Технологический стек

| Компонент | Технология | Причина выбора |
|-----------|-----------|----------------|
| Язык | Python 3.10+ | Экосистема CV, быстрая разработка |
| GUI | tkinter (ttk) | Встроен в Python, не требует зависимостей |
| Компьютерное зрение | OpenCV (opencv-python) | Стандарт де-факто |
| Вычисления | NumPy | Быстрые массивы, статистика |
| Графики | matplotlib | Гистограммы, динамика |
| PDF-отчёты | reportlab или fpdf2 | Генерация PDF без внешних утилит |
| База данных | SQLite3 | Встроен в Python, файловая БД |
| Сборка | PyInstaller | Один бинарник, все зависимости внутри |

### 5.2 Структура модулей

```
phototrichogram/
├── main.py                 # Точка входа, запуск GUI
├── gui/
│   ├── app.py              # Главное окно
│   ├── capture_tab.py      # Вкладка: захват с микроскопа
│   ├── analysis_tab.py     # Вкладка: анализ снимков
│   ├── multizone_tab.py    # Вкладка: мультизональный анализ
│   ├── patients_tab.py     # Вкладка: профили пациентов
│   ├── history_tab.py      # Вкладка: история и динамика
│   ├── calibration_tab.py  # Вкладка: калибровка
│   └── widgets.py          # Переиспользуемые виджеты
├── core/
│   ├── analyzer.py         # Алгоритм анализа (пайплайн §4)
│   ├── segmentation.py     # Сегментация волос
│   ├── measurement.py      # Измерение параметров
│   ├── classification.py   # Terminal/Vellus/Anagen/Telogen
│   ├── calibration.py      # Калибровка микроскопа
│   ├── camera.py           # Захват с USB-камеры (V4L2)
│   └── stats.py            # Статистика, сравнение зон
├── data/
│   ├── database.py         # SQLite: пациенты, измерения
│   └── models.py           # Dataclass-модели
├── reports/
│   ├── pdf_report.py       # Генерация PDF
│   └── templates.py        # Шаблоны отчётов
├── utils/
│   ├── config.py           # Настройки приложения
│   └── image_utils.py      # Вспомогательные функции
└── assets/
    ├── icon.png
    └── zone_diagram.png
```

### 5.3 Схема базы данных (SQLite)

```sql
CREATE TABLE patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    birth_date TEXT,
    gender TEXT,           -- 'M' / 'F'
    diagnosis TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,                    -- 'Microscope USB 200x'
    um_per_pixel REAL NOT NULL,   -- 5.9
    fov_width_mm REAL,            -- 3.8
    fov_height_mm REAL,           -- 2.8
    fov_area_mm2 REAL,            -- 10.6
    resolution_w INTEGER,         -- 640
    resolution_h INTEGER,         -- 480
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    calibration_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (calibration_id) REFERENCES calibrations(id)
);

CREATE TABLE measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    zone TEXT NOT NULL,            -- 'F', 'P', 'T', 'O'
    image_path TEXT,
    
    -- Основные показатели
    hair_count INTEGER,
    density_per_cm2 REAL,
    avg_thickness_um REAL,
    min_thickness_um REAL,
    max_thickness_um REAL,
    
    -- Классификация
    terminal_count INTEGER,
    vellus_count INTEGER,
    terminal_pct REAL,
    vellus_pct REAL,
    tv_ratio REAL,
    
    -- Anagen/Telogen (опционально)
    anagen_count INTEGER,
    telogen_count INTEGER,
    anagen_pct REAL,
    telogen_pct REAL,
    
    -- Сравнение с контролем
    density_diff_vs_control REAL,
    vellus_diff_vs_control REAL,
    assessment TEXT,
    
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Индексы для быстрых запросов истории
CREATE INDEX idx_measurements_session ON measurements(session_id);
CREATE INDEX idx_sessions_patient ON sessions(patient_id);
CREATE INDEX idx_sessions_date ON sessions(date);
```

### 5.4 Захват с USB-камеры

```python
import cv2

class MicroscopeCamera:
    """Работа с USB-микроскопом через V4L2 (OpenCV backend)"""
    
    def __init__(self):
        self.cap = None
        self.device_id = None
    
    def list_devices(self):
        """Перебор /dev/video0..9, возврат списка доступных"""
        devices = []
        for i in range(10):
            cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                devices.append({'id': i, 'name': f'/dev/video{i}', 'resolution': f'{w}x{h}'})
                cap.release()
        return devices
    
    def open(self, device_id):
        self.cap = cv2.VideoCapture(device_id, cv2.CAP_V4L2)
        # Установить максимальное разрешение
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
        self.device_id = device_id
        return self.cap.isOpened()
    
    def grab_frame(self):
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                return frame
        return None
    
    def save_snapshot(self, path):
        frame = self.grab_frame()
        if frame is not None:
            cv2.imwrite(path, frame)
            return True
        return False
    
    def release(self):
        if self.cap:
            self.cap.release()
```

**Интеграция с кнопкой микроскопа:**
```python
# Кнопка генерирует код 0x21ff8f через /dev/input
# Вариант: отслеживать через evdev
import evdev

def listen_microscope_button(callback):
    """Слушать кнопку на микроскопе"""
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    for dev in devices:
        if 'Microscope' in dev.name or 'USB 2.0 Camera' in dev.name:
            for event in dev.read_loop():
                if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                    callback()
```

### 5.5 GUI — основные экраны

**Вкладка «Захват»:**
- Живой видеопоток с микроскопа (Canvas, обновление ~15 fps)
- Выбор устройства из списка
- Кнопка снимка + привязка к кнопке на микроскопе
- Выбор зоны (F/P/T/O) перед снимком
- Превью последних снимков

**Вкладка «Анализ»:**
- Загрузка снимка (или из захвата)
- Результат сегментации — контуры на оригинале (зелёный=Terminal, красный=Vellus)
- Таблица параметров
- Гистограмма распределения толщин
- Кнопка «Добавить в сессию»

**Вкладка «Мультизональный»:**
- Карта зон (схема головы)
- Статус каждой зоны (снято / не снято)
- Сводная таблица
- Столбчатая диаграмма плотности по зонам
- Сравнение с контролем (O)
- Генерация отчёта

**Вкладка «Пациенты»:**
- Список пациентов
- Создание/редактирование профиля
- История сессий пациента
- Графики динамики (плотность, % Vellus по датам)

**Вкладка «Калибровка»:**
- Загрузка снимка шкалы
- Автоопределение расстояния между делениями
- Ручная коррекция (ввод цены деления)
- Сохранение профиля калибровки

---

## 6. Визуализация результатов

### 6.1 Разметка снимка

```python
def annotate_image(image, hairs, calibration):
    annotated = image.copy()
    
    for hair in hairs:
        color = (0, 255, 0) if hair['class'] == 'terminal' else (0, 0, 255)
        cv2.drawContours(annotated, [hair['contour']], -1, color, 2)
    
    # Информационная панель
    cv2.putText(annotated, f"Hair: {len(hairs)}", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    
    return annotated
```

### 6.2 Гистограмма толщин

```python
import matplotlib.pyplot as plt

def plot_thickness_histogram(thicknesses_um, vellus_threshold=30):
    fig, ax = plt.subplots(figsize=(6, 4))
    
    terminal = [t for t in thicknesses_um if t >= vellus_threshold]
    vellus = [t for t in thicknesses_um if t < vellus_threshold]
    
    bins = range(0, max(int(max(thicknesses_um)) + 10, 100), 5)
    ax.hist(vellus, bins=bins, color='red', alpha=0.7, label='Vellus')
    ax.hist(terminal, bins=bins, color='green', alpha=0.7, label='Terminal')
    ax.axvline(x=vellus_threshold, color='black', linestyle='--', label=f'Порог {vellus_threshold} мкм')
    
    ax.set_xlabel('Толщина (мкм)')
    ax.set_ylabel('Количество')
    ax.set_title('Распределение толщины волос')
    ax.legend()
    
    return fig
```

### 6.3 Столбчатая диаграмма по зонам

```python
def plot_zone_comparison(results):
    zones = ['F', 'P', 'T', 'O']
    colors = {'F': '#FF6B6B', 'P': '#4ECDC4', 'T': '#FFD93D', 'O': '#6BCB77'}
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    # Плотность
    densities = [results[z]['density'] for z in zones if z in results]
    axes[0].bar(zones, densities, color=[colors[z] for z in zones])
    axes[0].axhline(y=150, color='blue', linestyle='--', label='Норма мин.')
    axes[0].set_title('Плотность (волос/см²)')
    
    # Terminal/Vellus %
    # ... аналогично
    
    return fig
```

### 6.4 График динамики (история)

```python
def plot_history(sessions):
    """
    sessions = [(date, density, vellus_pct), ...]
    """
    dates = [s[0] for s in sessions]
    densities = [s[1] for s in sessions]
    vellus = [s[2] for s in sessions]
    
    fig, ax1 = plt.subplots(figsize=(8, 4))
    
    ax1.plot(dates, densities, 'g-o', label='Плотность')
    ax1.set_ylabel('Плотность (волос/см²)', color='green')
    
    ax2 = ax1.twinx()
    ax2.plot(dates, vellus, 'r-s', label='Vellus %')
    ax2.set_ylabel('Vellus %', color='red')
    
    ax1.set_title('Динамика показателей')
    fig.legend(loc='upper right')
    
    return fig
```

---

## 7. Параметры и их калибровка

### 7.1 Текущие значения (для данного микроскопа)

| Параметр | Значение | Единица | Как получено |
|----------|----------|---------|-------------|
| CALIBRATION_UM_PER_PX | 5.9 | мкм/px | Калибровочная шкала 0.1мм/дел, 17 px/дел |
| FOV_AREA_MM2 | 10.6 | мм² | 3.8 × 2.8 мм |
| VELLUS_THRESHOLD_PX | 5 | px | 30 мкм / 5.9 |
| VELLUS_THRESHOLD_UM | 30 | мкм | Клинический стандарт |
| MIN_HAIR_AREA | 150 | px² | Эмпирически, подстроено на реальных снимках (отсечка мусора) |
| MAX_CIRCULARITY | 0.6 | безразм. | Волосы вытянуты, круглое = не волос; подстроено эмпирически |
| MEDIAN_FILTER_RADIUS | 2 | px | Баланс шум/детализация (ядро 5) |
| THRESHOLD_METHOD | otsu | — | По black-hat результату; альтернатива — adaptive |
| BLACKHAT_KERNEL_SIZE | 25 | px | Ядро black-hat: больше макс. толщины волоса |
| ADAPTIVE_BLOCK_SIZE | 15 | px | Блок adaptive-порога (при THRESHOLD_METHOD=adaptive) |
| ADAPTIVE_C | 2.0 | — | Константа adaptive-порога |
| MAX_WHITE_PCT | 30.0 | % | Порог ужесточения бинаризации при пересвете маски |
| MIN_ASPECT_RATIO | 2.5 | — | Мин. вытянутость контура (волос — длинный объект) |

### 7.2 Пересчёт при смене микроскопа/увеличения

При изменении калибровки пересчитываются:
```python
VELLUS_THRESHOLD_PX = VELLUS_THRESHOLD_UM / new_um_per_px
MIN_HAIR_AREA = int(200 * (5.9 / new_um_per_px) ** 2)  # масштабировать
FOV_AREA_MM2 = (width * new_um_per_px / 1000) * (height * new_um_per_px / 1000)
```

### 7.3 Тонкая настройка

Если автосегментация плохо работает на конкретных снимках:

| Проблема | Решение |
|----------|---------|
| Много мусора детектируется | Увеличить MIN_HAIR_AREA |
| Тонкие волосы не обнаруживаются | Уменьшить MIN_HAIR_AREA |
| Кожные складки считаются волосами | Уменьшить MAX_CIRCULARITY (до 0.3) |
| Волосы разбиваются на фрагменты | Увеличить ядро морфологического Close |
| Слипшиеся волосы считаются одним | Добавить watershed-разделение |
| Плохой контраст | Подстроить CLAHE clipLimit или метод порога |

---

## 8. Сборка и распространение

### 8.1 PyInstaller (один файл)

```bash
# Установка
pip install pyinstaller

# Сборка
pyinstaller --onefile \
    --name phototrichogram \
    --windowed \
    --add-data "assets:assets" \
    --hidden-import cv2 \
    --hidden-import numpy \
    --hidden-import matplotlib \
    --hidden-import reportlab \
    main.py
```

Результат: `dist/phototrichogram` (~80 МБ), запуск: `./phototrichogram`

### 8.2 AppImage

```bash
# Структура
Phototrichogram.AppDir/
├── AppRun
├── phototrichogram.desktop
├── icon.png
└── usr/
    └── bin/
        └── phototrichogram  # бинарник от PyInstaller

# Создание
ARCH=x86_64 appimagetool Phototrichogram.AppDir
```

Результат: `Phototrichogram-x86_64.AppImage`, запуск: `chmod +x ... && ./...`

### 8.3 Права USB

Для работы без sudo — udev-правило:
```bash
# /etc/udev/rules.d/99-microscope.rules
ACTION=="add", ATTRS{idVendor}=="05e3", ATTRS{idProduct}=="f12b", MODE="0666"
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="05e3", MODE="0666"
```

---

## 9. Ограничения и оговорки

1. Автоматический анализ — вспомогательный инструмент, не заменяет диагноз трихолога
2. Точность зависит от качества снимков, калибровки и настройки параметров
3. Слипшиеся волосы могут считаться как один — занижает плотность
4. При плохом контрасте (светлые волосы на светлой коже) — сегментация может пропускать волосы
5. Anagen/Telogen анализ требует строгого соблюдения протокола (бритьё, интервал, позиционирование)
6. Результаты не могут использоваться как единственное основание для постановки диагноза

---

## 10. Дорожная карта

### v1.0 — MVP
- Захват с камеры (выбор устройства)
- Одиночный анализ (плотность, толщина, T/V)
- Калибровка
- Сохранение результатов в папку
- Русский интерфейс

### v1.1 — Профили
- База пациентов (SQLite)
- История измерений
- PDF-отчёт

### v1.2 — Мультизональный
- Анализ 4 зон с общим отчётом
- Сравнение с контролем
- Столбчатые диаграммы

### v1.3 — Мониторинг
- Графики динамики по датам
- Anagen/Telogen (два снимка)
- Экспорт данных в CSV

### v2.0 — Улучшения
- Watershed для слипшихся волос
- ML-классификация (обучение на размеченных данных)
- Image registration для точного Anagen/Telogen
- Автоопределение зоны по характеристикам кожи

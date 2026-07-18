"""
Переиспользуемые виджеты для GUI приложения Phototrichogram.

Содержит:
- StatusBar — статусбар внизу окна (текст + индикатор)
- ImageCanvas — канвас для отображения изображений с зумом и скроллом
- ToolTip — всплывающие подсказки
- ScrollableFrame — фрейм с прокруткой
- ImageFileDialog — кастомный диалог выбора файла изображения с превью
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageTk

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class StatusBar(ttk.Frame):
    """Статусбар внизу главного окна.

    Отображает текстовое сообщение и цветной индикатор состояния.

    Пример использования::

        status = StatusBar(root)
        status.pack(side="bottom", fill="x")
        status.set_text("Готово")
        status.set_indicator("green")
    """

    def __init__(self, parent: tk.Misc, **kwargs: Any) -> None:
        """Инициализация статусбара.

        Args:
            parent: Родительский виджет (окно или контейнер).
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)

        self._text_var = tk.StringVar(value="Готово")
        self._indicator_color = "gray"

        # Индикатор (цветной кружок)
        self._indicator_canvas = tk.Canvas(
            self, width=12, height=12, highlightthickness=0,
        )
        self._indicator_canvas.pack(side="left", padx=(5, 2), pady=2)
        self._indicator_id = self._indicator_canvas.create_oval(
            2, 2, 10, 10, fill="gray", outline="",
        )

        # Текст статуса
        self._label = ttk.Label(self, textvariable=self._text_var, anchor="w")
        self._label.pack(side="left", fill="x", expand=True, padx=(2, 5), pady=2)

        # Разделитель сверху
        separator = ttk.Separator(self, orient="horizontal")
        separator.place(relx=0, rely=0, relwidth=1)

    def set_text(self, text: str) -> None:
        """Установить текст статуса.

        Args:
            text: Текст сообщения.
        """
        self._text_var.set(text)
        logger.debug("Статус: %s", text)

    def set_indicator(self, color: str) -> None:
        """Установить цвет индикатора.

        Args:
            color: Цвет ('green', 'red', 'orange', 'gray').
        """
        self._indicator_color = color
        self._indicator_canvas.itemconfig(self._indicator_id, fill=color)

    def set_ready(self, text: str = "Готово") -> None:
        """Установить состояние 'готово' (зелёный индикатор).

        Args:
            text: Текст сообщения.
        """
        self.set_text(text)
        self.set_indicator("green")

    def set_busy(self, text: str = "Обработка...") -> None:
        """Установить состояние 'занято' (оранжевый индикатор).

        Args:
            text: Текст сообщения.
        """
        self.set_text(text)
        self.set_indicator("orange")

    def set_error(self, text: str) -> None:
        """Установить состояние 'ошибка' (красный индикатор).

        Args:
            text: Текст сообщения об ошибке.
        """
        self.set_text(text)
        self.set_indicator("red")


class ImageCanvas(ttk.Frame):
    """Канвас для отображения изображений с зумом и скроллом.

    Поддерживает:
    - Отображение numpy (BGR) и PIL изображений
    - Зум колесом мыши
    - Панорамирование (drag средней/правой кнопкой)
    - Сброс зума двойным кликом

    Пример использования::

        canvas = ImageCanvas(parent, width=640, height=480)
        canvas.pack(fill="both", expand=True)
        canvas.show_numpy(bgr_image)
    """

    MIN_ZOOM: float = 0.1
    MAX_ZOOM: float = 10.0
    ZOOM_STEP: float = 1.2

    def __init__(
        self,
        parent: tk.Widget,
        width: int = 640,
        height: int = 480,
        **kwargs: Any,
    ) -> None:
        """Инициализация канваса для изображений.

        Args:
            parent: Родительский виджет.
            width: Начальная ширина канваса.
            height: Начальная высота канваса.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)

        self._canvas_width = width
        self._canvas_height = height
        self._zoom: float = 1.0
        self._pil_image: Image.Image | None = None
        self._photo_image: ImageTk.PhotoImage | None = None
        self._image_id: int | None = None

        # Canvas с скроллбарами
        self._canvas = tk.Canvas(
            self,
            width=width,
            height=height,
            bg="#2b2b2b",
            highlightthickness=0,
        )

        self._v_scroll = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview,
        )
        self._h_scroll = ttk.Scrollbar(
            self, orient="horizontal", command=self._canvas.xview,
        )
        self._canvas.configure(
            xscrollcommand=self._h_scroll.set,
            yscrollcommand=self._v_scroll.set,
        )

        # Layout
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._v_scroll.grid(row=0, column=1, sticky="ns")
        self._h_scroll.grid(row=1, column=0, sticky="ew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Привязки событий
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel_linux_up)
        self._canvas.bind("<Button-5>", self._on_mousewheel_linux_down)
        self._canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self._canvas.bind("<B2-Motion>", self._on_pan_move)
        self._canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self._canvas.bind("<B3-Motion>", self._on_pan_move)
        self._canvas.bind("<Double-Button-1>", self._on_reset_zoom)

        # Координаты начала панорамирования
        self._pan_start_x: int = 0
        self._pan_start_y: int = 0

    def show_numpy(self, image: Any) -> None:
        """Отобразить numpy (BGR) изображение.

        Использует image_utils для конвертации без cv2.

        Args:
            image: numpy array в формате BGR (H, W, 3) или Grayscale (H, W).
        """
        from phototrichogram.utils.image_utils import numpy_to_pil

        try:
            pil_image = numpy_to_pil(image)
            self.show_pil(pil_image)
        except (ValueError, TypeError) as e:
            logger.error("Ошибка конвертации изображения: %s", e)

    def show_pil(self, image: Image.Image) -> None:
        """Отобразить PIL изображение.

        Args:
            image: PIL Image объект.
        """
        self._pil_image = image
        self._zoom = 1.0
        self._render()

    def clear(self) -> None:
        """Очистить канвас."""
        self._canvas.delete("all")
        self._pil_image = None
        self._photo_image = None
        self._image_id = None

    def get_zoom(self) -> float:
        """Текущий уровень зума.

        Returns:
            Коэффициент зума (1.0 = 100%).
        """
        return self._zoom

    def _render(self) -> None:
        """Перерисовка изображения с текущим зумом."""
        if self._pil_image is None:
            return

        w = int(self._pil_image.width * self._zoom)
        h = int(self._pil_image.height * self._zoom)

        if w <= 0 or h <= 0:
            return

        resized = self._pil_image.resize((w, h), Image.Resampling.LANCZOS)
        self._photo_image = ImageTk.PhotoImage(resized)

        self._canvas.delete("all")
        self._image_id = self._canvas.create_image(
            0, 0, anchor="nw", image=self._photo_image,
        )
        self._canvas.configure(scrollregion=(0, 0, w, h))

    def _on_mousewheel(self, event: tk.Event) -> None:
        """Зум колесом мыши (Windows/macOS)."""
        if event.delta > 0:
            self._apply_zoom(self.ZOOM_STEP)
        else:
            self._apply_zoom(1.0 / self.ZOOM_STEP)

    def _on_mousewheel_linux_up(self, event: tk.Event) -> None:
        """Зум колесом мыши вверх (Linux)."""
        self._apply_zoom(self.ZOOM_STEP)

    def _on_mousewheel_linux_down(self, event: tk.Event) -> None:
        """Зум колесом мыши вниз (Linux)."""
        self._apply_zoom(1.0 / self.ZOOM_STEP)

    def _apply_zoom(self, factor: float) -> None:
        """Применить зум.

        Args:
            factor: Множитель зума (>1 = увеличение, <1 = уменьшение).
        """
        new_zoom = self._zoom * factor
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))

        if new_zoom != self._zoom:
            self._zoom = new_zoom
            self._render()

    def _on_pan_start(self, event: tk.Event) -> None:
        """Начало панорамирования."""
        self._canvas.scan_mark(event.x, event.y)

    def _on_pan_move(self, event: tk.Event) -> None:
        """Панорамирование (drag)."""
        self._canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_reset_zoom(self, event: tk.Event) -> None:
        """Сброс зума на 100% (двойной клик)."""
        self._zoom = 1.0
        self._render()


class ToolTip:
    """Всплывающая подсказка для tkinter виджетов.

    Пример использования::

        button = ttk.Button(root, text="Кнопка")
        ToolTip(button, "Описание кнопки")
    """

    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        delay: int = 500,
    ) -> None:
        """Создание всплывающей подсказки.

        Args:
            widget: Виджет, к которому привязана подсказка.
            text: Текст подсказки.
            delay: Задержка показа в миллисекундах.
        """
        self._widget = widget
        self._text = text
        self._delay = delay
        self._tooltip_window: tk.Toplevel | None = None
        self._after_id: str | None = None

        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        # Скрывать при любом событии, когда виджет теряет видимость
        widget.bind("<Unmap>", self._on_leave)
        widget.bind("<Destroy>", self._on_leave)

    def _on_enter(self, event: tk.Event) -> None:
        """Курсор наведён — запланировать показ подсказки."""
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _on_leave(self, event: tk.Event) -> None:
        """Курсор ушёл — скрыть подсказку."""
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        """Отменить запланированный показ."""
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        """Показать подсказку."""
        if self._tooltip_window is not None:
            return

        # Не показывать если виджет не виден (например, на другой вкладке)
        try:
            if not self._widget.winfo_ismapped() or not self._widget.winfo_viewable():
                return
        except tk.TclError:
            return

        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 5

        self._tooltip_window = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        label = ttk.Label(
            tw,
            text=self._text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padding=(5, 2),
        )
        label.pack()

    def _hide(self) -> None:
        """Скрыть подсказку."""
        if self._tooltip_window is not None:
            self._tooltip_window.destroy()
            self._tooltip_window = None

    def update_text(self, text: str) -> None:
        """Обновить текст подсказки.

        Args:
            text: Новый текст.
        """
        self._text = text


class ScrollableFrame(ttk.Frame):
    """Фрейм с вертикальной прокруткой.

    Все дочерние виджеты добавляются в ``self.interior``.

    Пример использования::

        sf = ScrollableFrame(root)
        sf.pack(fill="both", expand=True)
        ttk.Label(sf.interior, text="Длинный контент").pack()
    """

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        """Инициализация прокручиваемого фрейма.

        Args:
            parent: Родительский виджет.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)

        self._canvas = tk.Canvas(self, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview,
        )
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self.interior = ttk.Frame(self._canvas)
        self._interior_id = self._canvas.create_window(
            (0, 0), window=self.interior, anchor="nw",
        )

        # Layout
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # Привязки
        self.interior.bind("<Configure>", self._on_interior_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Прокрутка колесом мыши
        self.interior.bind("<Enter>", self._bind_mousewheel)
        self.interior.bind("<Leave>", self._unbind_mousewheel)

    def _on_interior_configure(self, event: tk.Event) -> None:
        """Обновление scrollregion при изменении размера содержимого."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        """Растягивание содержимого по ширине канваса."""
        self._canvas.itemconfig(self._interior_id, width=event.width)

    def _bind_mousewheel(self, event: tk.Event) -> None:
        """Привязка прокрутки колесом мыши."""
        self._canvas.bind_all("<Button-4>", self._on_mousewheel_up)
        self._canvas.bind_all("<Button-5>", self._on_mousewheel_down)
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event: tk.Event) -> None:
        """Отвязка прокрутки колесом мыши."""
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")
        self._canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        """Прокрутка колесом (Windows/macOS)."""
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_up(self, event: tk.Event) -> None:
        """Прокрутка колесом вверх (Linux)."""
        self._canvas.yview_scroll(-1, "units")

    def _on_mousewheel_down(self, event: tk.Event) -> None:
        """Прокрутка колесом вниз (Linux)."""
        self._canvas.yview_scroll(1, "units")


class ImageFileDialog:
    """Диалог выбора файла изображения.

    Обёртка над стандартным filedialog.askopenfilename с запоминанием
    последней директории.

    Пример использования::

        dialog = ImageFileDialog(parent, title="Открыть изображение")
        path = dialog.show()
        if path is not None:
            print(f"Выбран файл: {path}")
    """

    _last_directory: Path | None = None

    def __init__(
        self,
        parent: tk.Widget,
        title: str = "Выбор изображения",
        initial_dir: Path | str | None = None,
    ) -> None:
        """Инициализация диалога.

        Args:
            parent: Родительский виджет.
            title: Заголовок окна.
            initial_dir: Начальная директория.
        """
        self._parent = parent
        self._title = title

        if initial_dir is not None and Path(initial_dir).is_dir():
            self._initial_dir = str(initial_dir)
        elif (
            ImageFileDialog._last_directory is not None
            and ImageFileDialog._last_directory.is_dir()
        ):
            self._initial_dir = str(ImageFileDialog._last_directory)
        else:
            self._initial_dir = str(Path.home())

    def show(self) -> Path | None:
        """Показать диалог и дождаться выбора.

        Returns:
            Path к выбранному файлу или None при отмене.
        """
        from tkinter import filedialog

        path_str = filedialog.askopenfilename(
            parent=self._parent,
            title=self._title,
            initialdir=self._initial_dir,
            filetypes=[
                ("Изображения", "*.png *.jpg *.jpeg *.bmp *.tiff *.tif"),
                ("Все файлы", "*.*"),
            ],
        )

        if path_str:
            result = Path(path_str)
            ImageFileDialog._last_directory = result.parent
            return result

        return None

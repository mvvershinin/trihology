"""
Главное окно приложения Phototrichogram.

Содержит класс App(tk.Tk) — корневое окно с вкладками (ttk.Notebook),
меню, статусбаром и глобальными хоткеями.

Принимает Config и передаёт его во вкладки.
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

import numpy as np

from phototrichogram import __version__
from phototrichogram.data.database import BackupError, Database, RestoreError
from phototrichogram.data.models import Patient
from phototrichogram.gui.widgets import StatusBar

if TYPE_CHECKING:
    from phototrichogram.gui.series import ZoneSeriesResult
    from phototrichogram.utils.config import Config

logger = logging.getLogger(__name__)


class App(tk.Tk):
    """Главное окно приложения Фототрихограмма.

    Содержит:
    - ttk.Notebook с вкладками (Захват, Анализ, Калибровка)
    - Строку меню (Файл, Настройки, Помощь)
    - StatusBar внизу окна
    - Глобальные хоткеи

    Пример использования::

        from phototrichogram.utils.config import load_config
        config = load_config()
        app = App(config=config)
        app.run()
    """

    def __init__(self, config: Config | None = None) -> None:
        """Инициализация главного окна.

        Args:
            config: Конфигурация приложения. Если None — используются значения
                    по умолчанию.
        """
        super().__init__()

        # Загрузка конфига по умолчанию при необходимости
        if config is None:
            from phototrichogram.utils.config import Config as ConfigClass
            self._config = ConfigClass()
        else:
            self._config = config

        # База данных — один экземпляр на всё приложение
        self._db = Database(Path(self._config.app.db_path))

        # Активный пациент (выбранный на вкладке «Пациенты»)
        self._active_patient: Patient | None = None

        self._setup_window()
        self._setup_style()
        self._create_menu()
        self._create_statusbar()
        self._create_notebook()
        self._setup_hotkeys()
        self._setup_close_handler()
        self._restore_active_patient()

        logger.info("Главное окно инициализировано")

    # Свойство config намеренно перекрывает метод tk.Misc.config: приложение
    # никогда не использует виджетный self.config(...), а отдаёт объект Config.
    @property
    def config(self) -> Config:  # type: ignore[override]
        """Текущая конфигурация приложения."""
        return self._config

    @property
    def status_bar(self) -> StatusBar:
        """Статусбар приложения — для обновления статуса из вкладок."""
        return self._status_bar

    @property
    def db(self) -> Database:
        """Экземпляр базы данных приложения."""
        return self._db

    @property
    def active_patient(self) -> Patient | None:
        """Текущий активный пациент или None."""
        return self._active_patient

    @active_patient.setter
    def active_patient(self, patient: Patient | None) -> None:
        """Установить активного пациента.

        Обновляет статусбар и запоминает id пациента в конфиге
        (app.active_patient_id) — для восстановления при следующем запуске.

        Args:
            patient: Объект Patient или None для сброса.
        """
        self._active_patient = patient
        if patient is not None:
            self._config.app.active_patient_id = patient.id
            self._status_bar.set_ready(f"Пациент: {patient.name}")
            logger.info("Активный пациент: id=%d %s", patient.id, patient.name)
        else:
            self._config.app.active_patient_id = None
            self._status_bar.set_text("Пациент не выбран")
            logger.info("Активный пациент сброшен")

    def _restore_active_patient(self) -> None:
        """Восстановить активного пациента из конфига при запуске.

        Читает app.active_patient_id и пытается загрузить пациента из БД.
        Найден → делает активным (обновляет статусбар и вкладки). Удалён
        (в БД не найден) → сбрасывает поле в конфиге и логирует. Ошибки БД
        не прерывают запуск приложения.
        """
        patient_id = self._config.app.active_patient_id
        if patient_id is None:
            return

        try:
            patient = self._db.get_patient(patient_id)
        except Exception as e:
            logger.warning(
                "Не удалось восстановить активного пациента id=%d: %s",
                patient_id, e,
            )
            return

        if patient is not None:
            self.active_patient = patient
            logger.info(
                "Восстановлен активный пациент из конфига: id=%d", patient_id,
            )
        else:
            self._config.app.active_patient_id = None
            logger.info(
                "Сохранённый пациент id=%d не найден (удалён), выбор сброшен",
                patient_id,
            )

    def run(self) -> None:
        """Запуск главного цикла событий (mainloop)."""
        logger.info("Запуск главного цикла GUI")
        self.mainloop()

    # ──────────────────────────── Настройка окна ─────────────────────────────

    def _setup_window(self) -> None:
        """Настройка параметров окна."""
        self.title(f"Фототрихограмма v{__version__}")

        width = self._config.app.window_width
        height = self._config.app.window_height

        # Центрирование окна на экране
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(800, 600)

        # Растягиваемость
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _setup_style(self) -> None:
        """Настройка темы и стилей ttk."""
        style = ttk.Style(self)

        # Тема из конфига (по умолчанию 'clam')
        theme = self._config.app.theme
        available_themes = style.theme_names()
        if theme in available_themes:
            style.theme_use(theme)
            logger.debug("Тема ttk: %s", theme)
        else:
            logger.warning(
                "Тема '%s' недоступна, доступные: %s",
                theme,
                ", ".join(available_themes),
            )

        # Пользовательские стили
        style.configure("Header.TLabel", font=("", 12, "bold"))
        style.configure("Result.TLabel", font=("", 10))
        style.configure("Value.TLabel", font=("", 11, "bold"))

    # ──────────────────────────── Меню ────────────────────────────────────────

    def _create_menu(self) -> None:
        """Создание строки меню."""
        self._menubar = tk.Menu(self)
        self.configure(menu=self._menubar)

        # Файл
        file_menu = tk.Menu(self._menubar, tearoff=0)
        file_menu.add_command(
            label="Открыть изображение...",
            accelerator="Ctrl+O",
            command=self._on_open_image,
        )
        file_menu.add_command(
            label="Сохранить результат...",
            accelerator="Ctrl+S",
            command=self._on_save_result,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Резервная копия базы...",
            command=self._on_backup_db,
        )
        file_menu.add_command(
            label="Восстановить из копии...",
            command=self._on_restore_db,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Выход",
            accelerator="Ctrl+Q",
            command=self._on_close,
        )
        self._menubar.add_cascade(label="Файл", menu=file_menu)

        # Настройки
        settings_menu = tk.Menu(self._menubar, tearoff=0)
        settings_menu.add_command(
            label="Калибровка",
            command=self._on_goto_calibration,
        )
        settings_menu.add_command(
            label="Параметры анализа...",
            command=self._on_open_settings,
        )
        self._menubar.add_cascade(label="Настройки", menu=settings_menu)

        # Помощь
        help_menu = tk.Menu(self._menubar, tearoff=0)
        help_menu.add_command(
            label="О программе",
            command=self._on_about,
        )
        self._menubar.add_cascade(label="Помощь", menu=help_menu)

    # ──────────────────────────── Вкладки ─────────────────────────────────────

    def _create_notebook(self) -> None:
        """Создание ttk.Notebook с вкладками."""
        self._notebook = ttk.Notebook(self)
        self._notebook.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 0))

        # Скрывать тултипы при переключении вкладок
        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Импорт вкладок (ленивый — чтобы не падало если модуль ещё не создан)
        self._tabs: dict[str, ttk.Frame] = {}

        self._create_tab(
            "patients",
            "Пациенты",
            "phototrichogram.gui.patients_tab",
            "PatientsTab",
        )
        self._create_tab(
            "capture",
            "Захват",
            "phototrichogram.gui.capture_tab",
            "CaptureTab",
        )
        self._create_tab(
            "analysis",
            "Анализ",
            "phototrichogram.gui.analysis_tab",
            "AnalysisTab",
        )
        self._create_tab(
            "results",
            "Результаты",
            "phototrichogram.gui.multizone_tab",
            "MultizoneTab",
        )
        self._create_tab(
            "history",
            "История",
            "phototrichogram.gui.history_tab",
            "HistoryTab",
        )
        self._create_tab(
            "calibration",
            "Калибровка",
            "phototrichogram.gui.calibration_tab",
            "CalibrationTab",
        )

    def _create_tab(
        self,
        key: str,
        title: str,
        module_name: str,
        class_name: str,
    ) -> None:
        """Создание одной вкладки через динамический импорт.

        Args:
            key: Ключ вкладки для внутреннего словаря.
            title: Заголовок вкладки (на русском).
            module_name: Полное имя модуля.
            class_name: Имя класса вкладки.
        """
        try:
            import importlib
            module = importlib.import_module(module_name)
            tab_class = getattr(module, class_name)
            tab = tab_class(self._notebook, app=self)
            self._notebook.add(tab, text=title)
            self._tabs[key] = tab
            logger.debug("Вкладка '%s' создана", title)
        except (ImportError, AttributeError) as e:
            # Вкладка ещё не реализована — добавляем заглушку
            placeholder = ttk.Frame(self._notebook)
            ttk.Label(
                placeholder,
                text=f"Вкладка «{title}» в разработке",
                style="Header.TLabel",
            ).pack(expand=True)
            self._notebook.add(placeholder, text=title)
            logger.warning("Не удалось загрузить вкладку '%s': %s", title, e)

    # ──────────────────────────── Статусбар ────────────────────────────────────

    def _create_statusbar(self) -> None:
        """Создание статусбара внизу окна."""
        self._status_bar = StatusBar(self)
        self._status_bar.grid(row=1, column=0, sticky="ew")
        self._status_bar.set_ready()

    def _on_tab_changed(self, event: object) -> None:
        """Обработка переключения вкладок — уничтожить тултипы, обновить данные."""
        # Уничтожить все Toplevel-тултипы (overrideredirect окна без заголовка)
        for widget in self.winfo_children():
            if isinstance(widget, tk.Toplevel):
                try:
                    if widget.wm_overrideredirect():
                        widget.destroy()
                except tk.TclError:
                    pass

        # Автообновление вкладки истории при переключении на неё
        current_tab = self._get_current_tab()
        if current_tab is not None and hasattr(current_tab, "on_tab_selected"):
            current_tab.on_tab_selected()

    # ──────────────────────────── Хоткеи ──────────────────────────────────────

    def _setup_hotkeys(self) -> None:
        """Настройка глобальных горячих клавиш."""
        self.bind("<Control-o>", lambda e: self._on_open_image())
        self.bind("<Control-O>", lambda e: self._on_open_image())
        self.bind("<Control-s>", lambda e: self._on_save_result())
        self.bind("<Control-S>", lambda e: self._on_save_result())
        self.bind("<Control-q>", lambda e: self._on_close())
        self.bind("<Control-Q>", lambda e: self._on_close())

    def _setup_close_handler(self) -> None:
        """Настройка подтверждения при закрытии окна."""
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ──────────────────────────── Обработчики ─────────────────────────────────

    def _on_open_image(self) -> None:
        """Открытие изображения через диалог файлов."""
        filetypes = [
            ("Изображения", "*.png *.jpg *.jpeg *.bmp *.tiff *.tif"),
            ("Все файлы", "*.*"),
        ]
        path = filedialog.askopenfilename(
            title="Открыть изображение",
            filetypes=filetypes,
        )
        if path:
            # Передать изображение во вкладку анализа если она доступна
            analysis_tab = self._tabs.get("analysis")
            if analysis_tab is not None and hasattr(analysis_tab, "load_image_from_path"):
                analysis_tab.load_image_from_path(path)
                # ttk.Notebook.select не типизирован в stubs typeshed
                self._notebook.select(analysis_tab)  # type: ignore[no-untyped-call]
                self._status_bar.set_ready(f"Загружено: {path}")
            else:
                self._status_bar.set_text(f"Файл выбран: {path}")

    def _on_save_result(self) -> None:
        """Сохранение результата анализа."""
        # Делегировать в текущую активную вкладку
        current_tab = self._get_current_tab()
        if current_tab is not None and hasattr(current_tab, "save_result"):
            current_tab.save_result()
        else:
            self._status_bar.set_text("Нет данных для сохранения")

    def _on_backup_db(self) -> None:
        """Создать резервную копию БД в выбранный пользователем файл.

        Диалог сохранения предлагает имя с текущей датой/временем и
        открывается в папке отчётов (или домашней, если она не задана либо
        не существует). Ошибку резервного копирования (BackupError) показывает
        в диалоге и статусбаре, не прерывая работу приложения.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        default_name = f"phototrichogram_backup_{timestamp}.db"

        reports_dir = self._config.app.reports_dir
        initialdir = (
            reports_dir if reports_dir and Path(reports_dir).is_dir() else str(Path.home())
        )

        path = filedialog.asksaveasfilename(
            title="Резервная копия базы",
            defaultextension=".db",
            initialfile=default_name,
            initialdir=initialdir,
            filetypes=[("База данных", "*.db"), ("Все файлы", "*.*")],
        )
        if not path:
            return

        try:
            self._db.backup_to(Path(path))
        except BackupError as e:
            logger.error("Ошибка резервного копирования БД в %s: %s", path, e)
            self._status_bar.set_error("Не удалось создать резервную копию")
            messagebox.showerror(
                "Резервная копия",
                f"Не удалось создать резервную копию:\n{e}",
            )
            return

        self._status_bar.set_ready(f"Резервная копия сохранена: {path}")
        logger.info("Резервная копия БД сохранена: %s", path)
        messagebox.showinfo("Резервная копия", f"База сохранена:\n{path}")

    def _on_restore_db(self) -> None:
        """Восстановить БД из резервной копии (деструктивная операция).

        Перед выбором файла запрашивает подтверждение — восстановление
        безвозвратно заменяет текущие данные. Выбранный файл валидируется
        data-слоем (Database.restore_from) ДО замены: при некорректном файле
        текущая БД не повреждается, показывается ошибка, приложение не падает.
        После успешного восстановления активный пациент сбрасывается (его может
        не быть в новой БД), а вкладки, читающие БД, перечитываются
        (см. _reload_db_tabs), чтобы в UI не осталось устаревших данных.
        """
        confirm = messagebox.askyesno(
            "Восстановление базы",
            "Текущие данные будут ЗАМЕНЕНЫ данными из резервной копии.\n\n"
            "Это действие необратимо. Продолжить?",
        )
        if not confirm:
            return

        reports_dir = self._config.app.reports_dir
        initialdir = (
            reports_dir if reports_dir and Path(reports_dir).is_dir() else str(Path.home())
        )

        path = filedialog.askopenfilename(
            title="Восстановить из копии",
            initialdir=initialdir,
            filetypes=[("База данных", "*.db"), ("Все файлы", "*.*")],
        )
        if not path:
            return

        try:
            self._db.restore_from(Path(path))
        except RestoreError as e:
            # Файл БД уже заменён, но восстановление не завершилось — состояние
            # неопределённо, «данные сохранены» было бы ложью.
            logger.error("Восстановление прервано после замены БД из %s: %s", path, e)
            self._status_bar.set_error("Восстановление прервано")
            messagebox.showerror(
                "Восстановление прервано",
                f"База могла быть частично заменена:\n{e}\n\n"
                "Перезапустите приложение.",
            )
            return
        except BackupError as e:
            # Замена не начиналась — текущие данные целы (по контракту API).
            logger.error("Ошибка восстановления БД из %s: %s", path, e)
            self._status_bar.set_error("Восстановление не удалось")
            messagebox.showerror(
                "Восстановление не удалось",
                f"Файл не является корректной резервной копией:\n{e}\n\n"
                "Текущие данные сохранены.",
            )
            return

        # Соединение уже переоткрыто на данные бэкапа. Активный пациент старой БД
        # мог не сохраниться в восстановленной (или его id указывать на другого
        # пациента) → сброс: setter обновит статусбар и конфиг. Затем перечитываем
        # вкладки, чтобы в UI не осталось записей со старыми id.
        self.active_patient = None
        self._reload_db_tabs()

        self._status_bar.set_ready(f"База восстановлена из копии: {path}")
        logger.info("БД восстановлена из копии: %s", path)
        messagebox.showinfo("Восстановление", f"База восстановлена из копии:\n{path}")

    def _reload_db_tabs(self) -> None:
        """Перечитать вкладки, читающие БД, после восстановления из копии.

        Вызывается из _on_restore_db, когда соединение уже указывает на данные
        бэкапа, но вкладки ещё показывают прежние записи и держат ссылки на
        объекты (карточка пациента, список, история), которых в новой БД может
        не быть. Карточка пациента очищается (сбрасывается устаревший выбранный
        пациент — иначе «Удалить»/«Сохранить» сработали бы по чужому id
        восстановленной БД), список пациентов и история перечитываются из БД.
        Методы вызываются через hasattr: если вкладка не загрузилась (заглушка),
        шаг безопасно пропускается.
        """
        patients_tab = self._tabs.get("patients")
        if patients_tab is not None and hasattr(patients_tab, "_on_clear_form"):
            patients_tab._on_clear_form()
        if patients_tab is not None and hasattr(patients_tab, "_refresh_list"):
            patients_tab._refresh_list()

        history_tab = self._tabs.get("history")
        if history_tab is not None and hasattr(history_tab, "refresh"):
            history_tab.refresh()

        # Вкладка «Результаты» держит в памяти результаты и id сессии СТАРОЙ БД.
        # После восстановления id могут указывать на чужие сессии новой БД
        # (SQLite перезапускает автоинкремент для каждого файла) — сбрасываем,
        # иначе экспорт PDF выгрузил бы данные, не соответствующие пациенту.
        results_tab = self._tabs.get("results")
        if results_tab is not None and hasattr(results_tab, "load_results"):
            results_tab.load_results({}, None)

    def _on_goto_calibration(self) -> None:
        """Переключение на вкладку калибровки."""
        calibration_tab = self._tabs.get("calibration")
        if calibration_tab is not None:
            self._notebook.select(calibration_tab)  # type: ignore[no-untyped-call]

    def _on_open_settings(self) -> None:
        """Открытие окна настроек параметров анализа."""
        _SettingsDialog(self, self._config)

    def _on_about(self) -> None:
        """Показать окно 'О программе'."""
        messagebox.showinfo(
            "О программе",
            f"Фототрихограмма v{__version__}\n\n"
            "Десктопное приложение для\n"
            "трихоскопического анализа волос\n"
            "с USB-микроскопа.\n\n"
            "Python 3.11+ | tkinter | OpenCV\n\n"
            "Лицензия: MIT\n\n"
            "Результаты анализа являются вспомогательными\n"
            "и не заменяют консультацию специалиста.",
        )

    def _on_close(self) -> None:
        """Подтверждение закрытия приложения."""
        confirm = messagebox.askyesno(
            "Выход",
            "Вы действительно хотите выйти?",
        )
        if confirm:
            # Освобождение ресурсов камеры после подтверждения
            capture_tab = self._tabs.get("capture")
            if capture_tab is not None and hasattr(capture_tab, "release_camera"):
                capture_tab.release_camera()

            # Сохранение конфигурации (в т.ч. активного пациента) при выходе
            try:
                from phototrichogram.utils.config import save_config
                save_config(self._config)
            except Exception as e:
                logger.warning("Ошибка сохранения конфигурации при выходе: %s", e)

            # Закрытие базы данных
            try:
                self._db.close()
                logger.info("База данных закрыта")
            except Exception as e:
                logger.warning("Ошибка закрытия БД: %s", e)

            logger.info("Пользователь подтвердил выход")
            self.destroy()

    def get_tab(self, key: str) -> ttk.Frame | None:
        """Получить вкладку по ключу.

        Args:
            key: Ключ вкладки ('capture', 'analysis', 'calibration').

        Returns:
            Виджет вкладки или None.
        """
        return self._tabs.get(key)

    def select_tab(self, key: str) -> None:
        """Переключить на вкладку по ключу.

        Args:
            key: Ключ вкладки.
        """
        tab = self._tabs.get(key)
        if tab is not None:
            self._notebook.select(tab)  # type: ignore[no-untyped-call]

    def navigate_to_results(
        self,
        zone_results: dict[str, ZoneSeriesResult],
        session_id: int | None = None,
    ) -> None:
        """Переключиться на вкладку результатов и загрузить данные.

        Вызывается из capture_tab после завершения мультизонального анализа.

        Args:
            zone_results: Словарь {код_зоны: ZoneSeriesResult} — усреднённые по
                серии метрики зоны + число снимков и разброс плотности.
            session_id: id сессии, в которую сохранены эти результаты, или None
                (не сохранены). Экспорт PDF использует именно эту сессию.
        """
        results_tab = self._tabs.get("results")
        if results_tab is not None and hasattr(results_tab, "load_results"):
            results_tab.load_results(zone_results, session_id)
            self._notebook.select(results_tab)  # type: ignore[no-untyped-call]
            self._status_bar.set_ready(
                f"Результаты мультизонального анализа: {len(zone_results)} зон",
            )
        else:
            self._status_bar.set_error("Вкладка результатов недоступна")
            logger.warning("Вкладка результатов не найдена или не поддерживает load_results")

    def get_camera_frame(self) -> np.ndarray[Any, Any] | None:
        """Вернуть свежий кадр с камеры вкладки «Захват».

        Делегирует получение кадра вкладке захвата. Если вкладка не загрузилась
        (заглушка) или не поддерживает захват — безопасно возвращает None.

        Returns:
            Копия последнего кадра камеры (BGR numpy array) или None, если
            камера не подключена либо вкладка захвата недоступна.
        """
        capture_tab = self._tabs.get("capture")
        getter = getattr(capture_tab, "get_camera_frame", None)
        if callable(getter):
            frame: np.ndarray[Any, Any] | None = getter()
            return frame
        return None

    def _get_current_tab(self) -> ttk.Frame | None:
        """Получить текущую активную вкладку.

        Returns:
            Текущая вкладка или None.
        """
        try:
            current = self._notebook.select()  # type: ignore[no-untyped-call]
            for tab in self._tabs.values():
                if str(tab) == str(current):
                    return tab
        except tk.TclError:
            pass
        return None


class _SettingsDialog(tk.Toplevel):
    """Диалог настроек параметров анализа.

    Позволяет изменить основные параметры AnalysisConfig
    и сохранить их в файл конфигурации.
    """

    def __init__(self, parent: App, config: Config) -> None:
        """Инициализация диалога настроек.

        Args:
            parent: Родительское окно.
            config: Текущая конфигурация.
        """
        super().__init__(parent)
        self.title("Параметры анализа")
        # Высота вмещает все секции (контент ~820px) — иначе полоса кнопок
        # («Сохранить») уезжала под нижний край и настройки нельзя было
        # сохранить. Вертикальный ресайз разрешён как страховка: кнопки прибиты
        # к низу окна (см. _create_widgets) и остаются видимы при любой высоте;
        # ширина фиксирована (от неё зависит перенос строк подсказок).
        # Высоту ограничиваем экраном: на низких ноутбуках (768px) окно 830px
        # не влезло бы, и нижние кнопки снова оказались бы за краем. Ресайз
        # вертикальный разрешён — при нехватке места окно можно растянуть/скроллом
        # экрана добраться. Ширина фиксирована (перенос подсказок зависит от неё).
        height = min(830, self.winfo_screenheight() - 80)
        self.geometry(f"470x{height}")
        self.resizable(False, True)
        self.transient(parent)
        self.grab_set()

        self._parent = parent
        self._config = config
        self._vars: dict[str, tk.Variable] = {}

        self._create_widgets()
        self._load_values()

    def _create_widgets(self) -> None:
        """Создание виджетов диалога.

        Полоса кнопок («Сохранить»/«Отмена») пакуется к нижнему краю Toplevel
        ПЕРВОЙ (``side="bottom"``): так она резервирует себе место и остаётся
        видимой, даже если суммарная высота секций превышает высоту окна.
        Основной контент пакуется после неё сверху и занимает остаток.
        """
        # Полоса кнопок — к низу окна, ДО основного контента (резервирует место).
        btn_frame = ttk.Frame(self, padding=(10, 8))
        btn_frame.pack(side="bottom", fill="x")

        ttk.Button(
            btn_frame, text="Сохранить", command=self._on_save,
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            btn_frame, text="Отмена", command=self.destroy,
        ).pack(side="right")

        # Основной контент — оставшееся пространство сверху.
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(side="top", fill="both", expand=True)

        # Калибровка
        cal_frame = ttk.LabelFrame(main_frame, text="Калибровка", padding=5)
        cal_frame.pack(fill="x", pady=(0, 10))

        self._add_field(cal_frame, "um_per_px", "Масштаб (мкм/px):", 0, "double")
        self._add_field(cal_frame, "fov_width_px", "Ширина кадра (px):", 1, "int")
        self._add_field(cal_frame, "fov_height_px", "Высота кадра (px):", 2, "int")

        # Сегментация
        seg_frame = ttk.LabelFrame(main_frame, text="Сегментация", padding=5)
        seg_frame.pack(fill="x", pady=(0, 10))

        self._add_field(seg_frame, "clahe_clip_limit", "CLAHE clip limit:", 0, "double")
        self._add_field(seg_frame, "median_blur_kernel", "Медианный фильтр:", 1, "int")
        self._add_field(seg_frame, "morph_kernel_size", "Ядро морфологии:", 2, "int")

        # Детекция
        det_frame = ttk.LabelFrame(main_frame, text="Детекция", padding=5)
        det_frame.pack(fill="x", pady=(0, 10))

        self._add_field(det_frame, "min_hair_area", "Мин. площадь (px^2):", 0, "int")
        self._add_field(det_frame, "max_circularity", "Макс. циркулярность:", 1, "double")

        # Классификация
        cls_frame = ttk.LabelFrame(main_frame, text="Классификация", padding=5)
        cls_frame.pack(fill="x", pady=(0, 10))

        self._add_field(cls_frame, "vellus_threshold_um", "Порог Vellus (мкм):", 0, "double")
        self._add_field(
            cls_frame, "intermediate_threshold_um", "Порог Terminal (мкм):", 1, "double",
        )

        # Подсчёт волос — режим count_mode
        self._add_count_mode(main_frame)

        # Хранение — папка автосохранения снимков
        self._add_storage(main_frame)

    def _add_field(
        self,
        parent: ttk.Widget,
        key: str,
        label: str,
        row: int,
        var_type: str,
    ) -> None:
        """Добавить поле настройки.

        Args:
            parent: Родительский фрейм.
            key: Ключ параметра в AnalysisConfig.
            label: Подпись поля (на русском).
            row: Номер строки в grid.
            var_type: Тип переменной ('int', 'double', 'string').
        """
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=2)

        var: tk.Variable
        if var_type == "int":
            var = tk.IntVar()
        elif var_type == "double":
            var = tk.DoubleVar()
        else:
            var = tk.StringVar()

        entry = ttk.Entry(parent, textvariable=var, width=15)
        entry.grid(row=row, column=1, sticky="e", padx=5, pady=2)
        self._vars[key] = var

    def _add_count_mode(self, parent: ttk.Widget) -> None:
        """Секция выбора режима подсчёта волос (count_mode).

        Радиокнопки «Только растущие» = ``rooted`` (дефолт: не считает волосы,
        проходящие через кадр насквозь) и «Все волосы» = ``all`` (поведение до
        v1.5). StringVar регистрируется в ``self._vars`` под ключом
        ``count_mode`` — значит, generic ``_load_values`` / ``_on_save``
        подхватят его как остальные параметры (загрузка из конфига и сохранение
        в него без спец-обработки).

        Args:
            parent: Родительский фрейм диалога.
        """
        count_frame = ttk.LabelFrame(parent, text="Подсчёт волос", padding=5)
        count_frame.pack(fill="x", pady=(0, 10))

        count_var = tk.StringVar()
        self._vars["count_mode"] = count_var

        ttk.Radiobutton(
            count_frame,
            text="Только растущие, от корней (по умолчанию)",
            variable=count_var,
            value="rooted",
        ).grid(row=0, column=0, sticky="w", padx=5, pady=1)
        ttk.Radiobutton(
            count_frame,
            text="Все волосы (как есть)",
            variable=count_var,
            value="all",
        ).grid(row=1, column=0, sticky="w", padx=5, pady=1)

        ttk.Label(
            count_frame,
            text=(
                "«Только растущие» не считает волосы, проходящие через кадр "
                "насквозь (растущие вне участка). Плотность выходит ниже, чем "
                "в режиме «Все волосы»: снимки, посчитанные в разных режимах, "
                "между собой не сравнивают."
            ),
            font=("", 8),
            foreground="#757575",
            wraplength=420,
            justify="left",
        ).grid(row=2, column=0, sticky="w", padx=5, pady=(3, 0))

    def _add_storage(self, parent: ttk.Widget) -> None:
        """Секция «Хранение»: папки снимков и PDF-отчётов.

        Два пути только для чтения (Entry ``state="readonly"``) с кнопками
        «Обзор…» (выбор каталога через ``filedialog.askdirectory``):
        ``config.app.images_dir`` (автосохранение снимков) и
        ``config.app.reports_dir`` (стартовая папка диалога «Сохранить отчёт
        PDF», см. multizone_tab). Значения хранятся в отдельных
        ``self._images_dir_var`` / ``self._reports_dir_var`` (НЕ в
        ``self._vars``): generic-цикл ``_load_values`` / ``_on_save`` перебирает
        секцию ``analysis``, а эти пути лежат в секции ``app`` — их загрузка и
        сохранение выполняются явно (см. ``_load_values`` и ``_on_save``).

        Args:
            parent: Родительский фрейм диалога.
        """
        storage_frame = ttk.LabelFrame(parent, text="Хранение", padding=5)
        storage_frame.pack(fill="x", pady=(0, 10))
        storage_frame.columnconfigure(0, weight=1)

        # Папка снимков.
        ttk.Label(storage_frame, text="Папка снимков:").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 2),
        )
        self._images_dir_var = tk.StringVar()
        self._images_dir_entry = ttk.Entry(
            storage_frame,
            textvariable=self._images_dir_var,
            state="readonly",
        )
        self._images_dir_entry.grid(row=1, column=0, sticky="we", padx=5, pady=2)
        ttk.Button(
            storage_frame, text="Обзор…", command=self._on_browse_images_dir,
        ).grid(row=1, column=1, sticky="e", padx=(0, 5), pady=2)
        ttk.Label(
            storage_frame,
            text="Сюда сохраняются снимки при включённом автосохранении.",
            font=("", 8),
            foreground="#757575",
            wraplength=420,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(3, 8))

        # Папка отчётов.
        ttk.Label(storage_frame, text="Папка отчётов:").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 2),
        )
        self._reports_dir_var = tk.StringVar()
        self._reports_dir_entry = ttk.Entry(
            storage_frame,
            textvariable=self._reports_dir_var,
            state="readonly",
        )
        self._reports_dir_entry.grid(row=4, column=0, sticky="we", padx=5, pady=2)
        ttk.Button(
            storage_frame, text="Обзор…", command=self._on_browse_reports_dir,
        ).grid(row=4, column=1, sticky="e", padx=(0, 5), pady=2)
        ttk.Label(
            storage_frame,
            text="Стартовая папка при сохранении PDF-отчёта.",
            font=("", 8),
            foreground="#757575",
            wraplength=420,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=5, pady=(3, 0))

    def _on_browse_images_dir(self) -> None:
        """Выбор папки автосохранения снимков через системный диалог.

        Открывает ``askdirectory`` от текущей папки (или домашней, если текущей
        нет). Выбранный путь пишется в ``self._images_dir_var``; фактическая
        запись в конфиг и создание каталога происходят при нажатии «Сохранить»
        (см. ``_on_save``).
        """
        current = self._images_dir_var.get().strip()
        initialdir = current if current and Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(
            parent=self,
            title="Выберите папку для снимков",
            initialdir=initialdir,
        )
        if chosen:
            self._images_dir_var.set(chosen)
            # Прокрутить поле к концу пути — виден последний (значимый) сегмент.
            self._images_dir_entry.xview_moveto(1.0)

    def _on_browse_reports_dir(self) -> None:
        """Выбор папки для PDF-отчётов через системный диалог.

        Открывает ``askdirectory`` от текущей папки (или домашней, если текущей
        нет). Выбранный путь пишется в ``self._reports_dir_var``; запись в
        конфиг и создание каталога — при нажатии «Сохранить» (см. ``_on_save``).
        """
        current = self._reports_dir_var.get().strip()
        initialdir = current if current and Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(
            parent=self,
            title="Выберите папку для отчётов",
            initialdir=initialdir,
        )
        if chosen:
            self._reports_dir_var.set(chosen)
            self._reports_dir_entry.xview_moveto(1.0)

    def _load_values(self) -> None:
        """Загрузка текущих значений из конфигурации."""
        analysis = self._config.analysis
        for key, var in self._vars.items():
            value = getattr(analysis, key, None)
            if value is not None:
                var.set(value)

        # Папки снимков и отчётов лежат в секции app (не analysis) — отдельно.
        self._images_dir_var.set(self._config.app.images_dir)
        self._reports_dir_var.set(self._config.app.reports_dir)

    def _on_save(self) -> None:
        """Сохранение настроек."""
        try:
            analysis = self._config.analysis
            for key, var in self._vars.items():
                # tk.Variable.get не типизирован в stubs typeshed (базовый класс)
                value = var.get()  # type: ignore[no-untyped-call]
                setattr(analysis, key, value)

            # Валидация критических параметров
            if analysis.um_per_px <= 0:
                self._parent.status_bar.set_error("Масштаб (мкм/px) должен быть > 0")
                return

            # Папка снимков (секция «Хранение» → app.images_dir). Хранится вне
            # generic-цикла analysis, поэтому пишем явно. Каталог создаётся сразу,
            # чтобы автосохранение снимков не падало. mkdir может бросить OSError —
            # он перехватывается общим except ниже.
            images_dir = self._images_dir_var.get().strip()
            if images_dir:
                Path(images_dir).mkdir(parents=True, exist_ok=True)
                self._config.app.images_dir = images_dir

            # Папка отчётов (секция app). Создаётся сразу, чтобы диалог
            # сохранения отчёта открывался в существующем каталоге.
            reports_dir = self._reports_dir_var.get().strip()
            if reports_dir:
                Path(reports_dir).mkdir(parents=True, exist_ok=True)
                self._config.app.reports_dir = reports_dir

            from phototrichogram.utils.config import save_config
            save_config(self._config)

            self._parent.status_bar.set_ready("Настройки сохранены")
            logger.info("Настройки анализа сохранены")
            self.destroy()
        except (tk.TclError, ValueError, OSError) as e:
            logger.error("Ошибка сохранения настроек: %s", e)
            self._parent.status_bar.set_error(f"Ошибка сохранения: {e}")

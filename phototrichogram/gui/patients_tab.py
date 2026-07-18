"""
Вкладка управления пациентами — CRUD.

Содержит PatientsTab: список пациентов с поиском, карточка пациента
для создания/редактирования, выбор активного пациента.

Layout:
- Слева: поиск + Treeview со списком пациентов + кнопка «Новый»
- Справа: форма редактирования (имя, дата рождения, пол, этнос, заметки)

НЕ импортирует cv2 — чисто GUI-слой.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any, TypedDict

from phototrichogram.core.norms import Ethnicity
from phototrichogram.data.models import Patient

if TYPE_CHECKING:
    from phototrichogram.gui.app import App

logger = logging.getLogger(__name__)

# Отображение пола
_GENDER_OPTIONS = ("--", "М", "Ж")
_GENDER_TO_DB = {"--": None, "М": "M", "Ж": "F"}
_GENDER_FROM_DB = {None: "--", "M": "М", "F": "Ж"}

# Отображение этнической принадлежности.
# Русские подписи ↔ коды phototrichogram.core.norms.Ethnicity: подписи строятся
# из перечисления, поэтому DB-коды (Ethnicity.*.value) совпадают с core.norms
# «точь-в-точь». «Не указан» соответствует отсутствию данных — в data-слое это
# None; core.norms трактует и None, и строку 'unspecified' одинаково — как
# Ethnicity.UNSPECIFIED (см. Ethnicity.from_code).
_ETHNICITY_UNSPECIFIED_LABEL = "Не указан"

# Явные (кроме UNSPECIFIED) варианты в порядке их показа в выпадающем списке.
_ETHNICITY_LABELS: dict[Ethnicity, str] = {
    Ethnicity.CAUCASIAN: "Европеоид",
    Ethnicity.HISPANIC: "Латиноамериканец",
    Ethnicity.AFRICAN: "Африканец",
    Ethnicity.EAST_ASIAN: "Восточноазиат",
    Ethnicity.SOUTH_ASIAN: "Южноазиат",
}
_ETHNICITY_OPTIONS: tuple[str, ...] = (
    _ETHNICITY_UNSPECIFIED_LABEL,
    *_ETHNICITY_LABELS.values(),
)
_ETHNICITY_TO_DB: dict[str, str | None] = {
    _ETHNICITY_UNSPECIFIED_LABEL: None,
    **{label: eth.value for eth, label in _ETHNICITY_LABELS.items()},
}
_ETHNICITY_FROM_DB: dict[str | None, str] = {
    None: _ETHNICITY_UNSPECIFIED_LABEL,
    Ethnicity.UNSPECIFIED.value: _ETHNICITY_UNSPECIFIED_LABEL,
    **{eth.value: label for eth, label in _ETHNICITY_LABELS.items()},
}


class _PatientFormData(TypedDict):
    """Данные формы пациента (имя обязательно, остальное опционально)."""

    name: str
    birth_date: str | None
    gender: str | None
    ethnicity: str | None
    notes: str | None


class PatientsTab(ttk.Frame):
    """Вкладка управления пациентами.

    Предоставляет:
    - Список пациентов с поиском по имени
    - Карточку создания/редактирования пациента
    - Выбор активного пациента (двойной клик)
    """

    def __init__(self, parent: tk.Widget, app: App, **kwargs: Any) -> None:
        """Инициализация вкладки пациентов.

        Args:
            parent: Родительский виджет (Notebook).
            app: Ссылка на главное окно приложения.
            **kwargs: Аргументы ttk.Frame.
        """
        super().__init__(parent, **kwargs)
        self._app = app
        self._selected_patient: Patient | None = None

        self._create_widgets()
        self._refresh_list()

        logger.info("Вкладка пациентов инициализирована")

    # ──────────────────────────── Создание виджетов ───────────────────────────

    def _create_widgets(self) -> None:
        """Создание всех виджетов вкладки."""
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self._create_list_panel()
        self._create_card_panel()

    def _create_list_panel(self) -> None:
        """Создание левой панели — поиск + список пациентов."""
        list_frame = ttk.LabelFrame(self, text="Список пациентов", padding=5)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(5, 2), pady=5)
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        # Панель поиска + кнопка «Новый»
        search_frame = ttk.Frame(list_frame)
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        search_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(search_frame, text="Поиск:").grid(row=0, column=0, padx=(0, 5))

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        search_entry = ttk.Entry(search_frame, textvariable=self._search_var)
        search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 5))

        self._new_btn = ttk.Button(
            search_frame, text="Новый пациент", command=self._on_new_patient, width=16,
        )
        self._new_btn.grid(row=0, column=2)

        # Treeview
        tree_frame = ttk.Frame(list_frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        columns = ("name", "gender", "created")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self._tree.heading("name", text="Имя")
        self._tree.heading("gender", text="Пол")
        self._tree.heading("created", text="Дата создания")

        self._tree.column("name", width=200, minwidth=120)
        self._tree.column("gender", width=50, minwidth=40, anchor="center")
        self._tree.column("created", width=120, minwidth=80, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-Button-1>", self._on_tree_double_click)

        # Подсказка внизу
        self._list_hint_var = tk.StringVar(value="Двойной клик — выбрать активного пациента")
        ttk.Label(
            list_frame,
            textvariable=self._list_hint_var,
            foreground="#888888",
            font=("", 9),
        ).grid(row=2, column=0, sticky="w", pady=(3, 0))

    def _create_card_panel(self) -> None:
        """Создание правой панели — карточка пациента."""
        card_frame = ttk.LabelFrame(self, text="Карточка пациента", padding=10)
        card_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 5), pady=5)
        card_frame.grid_columnconfigure(1, weight=1)

        row = 0

        # Имя
        ttk.Label(card_frame, text="Имя:").grid(row=row, column=0, sticky="w", pady=5)
        self._name_var = tk.StringVar()
        self._name_entry = ttk.Entry(card_frame, textvariable=self._name_var)
        self._name_entry.grid(row=row, column=1, sticky="ew", padx=(5, 0), pady=5)
        row += 1

        # Дата рождения
        ttk.Label(card_frame, text="Дата рождения:").grid(row=row, column=0, sticky="w", pady=5)
        self._birth_var = tk.StringVar()
        birth_entry = ttk.Entry(card_frame, textvariable=self._birth_var)
        birth_entry.grid(row=row, column=1, sticky="ew", padx=(5, 0), pady=5)
        ttk.Label(
            card_frame, text="(ГГГГ-ММ-ДД)", foreground="#888888", font=("", 9),
        ).grid(row=row, column=2, padx=(5, 0))
        row += 1

        # Пол
        ttk.Label(card_frame, text="Пол:").grid(row=row, column=0, sticky="w", pady=5)
        self._gender_var = tk.StringVar(value="--")
        gender_combo = ttk.Combobox(
            card_frame,
            textvariable=self._gender_var,
            values=_GENDER_OPTIONS,
            state="readonly",
            width=8,
        )
        gender_combo.grid(row=row, column=1, sticky="w", padx=(5, 0), pady=5)
        row += 1

        # Этническая принадлежность
        ttk.Label(card_frame, text="Этническая принадлежность:").grid(
            row=row, column=0, sticky="w", pady=5,
        )
        self._ethnicity_var = tk.StringVar(value=_ETHNICITY_UNSPECIFIED_LABEL)
        ethnicity_combo = ttk.Combobox(
            card_frame,
            textvariable=self._ethnicity_var,
            values=_ETHNICITY_OPTIONS,
            state="readonly",
            width=18,
        )
        ethnicity_combo.grid(row=row, column=1, sticky="w", padx=(5, 0), pady=5)
        row += 1

        # Заметки
        ttk.Label(card_frame, text="Заметки:").grid(row=row, column=0, sticky="nw", pady=5)
        self._notes_text = tk.Text(card_frame, height=5, width=30, wrap="word")
        self._notes_text.grid(
            row=row, column=1, columnspan=2, sticky="nsew", padx=(5, 0), pady=5,
        )
        card_frame.grid_rowconfigure(row, weight=1)
        row += 1

        # Разделитель
        ttk.Separator(card_frame, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=10,
        )
        row += 1

        # Информационная строка
        self._info_var = tk.StringVar(value="")
        ttk.Label(
            card_frame,
            textvariable=self._info_var,
            foreground="#666666",
            font=("", 9),
        ).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        # Кнопки
        btn_frame = ttk.Frame(card_frame)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        self._save_btn = ttk.Button(
            btn_frame, text="Сохранить", command=self._on_save, width=14,
        )
        self._save_btn.pack(side="left", padx=(0, 5))

        self._delete_btn = ttk.Button(
            btn_frame, text="Удалить", command=self._on_delete, width=14,
            state="disabled",
        )
        self._delete_btn.pack(side="left", padx=(0, 5))

        self._clear_btn = ttk.Button(
            btn_frame, text="Очистить", command=self._on_clear_form, width=14,
        )
        self._clear_btn.pack(side="right")

    # ──────────────────────────── Загрузка данных ─────────────────────────────

    def _refresh_list(self, select_id: int | None = None) -> None:
        """Обновить список пациентов из БД.

        Args:
            select_id: ID пациента, который нужно выделить после обновления.
        """
        query = self._search_var.get().strip()

        try:
            db = self._app.db
            patients = db.search_patients(query) if query else db.get_all_patients()
        except Exception as e:
            logger.error("Ошибка загрузки пациентов: %s", e)
            self._app.status_bar.set_error(f"Ошибка загрузки: {e}")
            return

        # Сохранить маппинг iid -> patient
        self._patients_map: dict[str, Patient] = {}

        self._tree.delete(*self._tree.get_children())

        for patient in patients:
            gender_display = _GENDER_FROM_DB.get(patient.gender, "--")
            created = patient.created_at[:10] if patient.created_at else "--"
            iid = str(patient.id)
            self._tree.insert(
                "",
                "end",
                iid=iid,
                values=(patient.name, gender_display, created),
            )
            self._patients_map[iid] = patient

        # Выделить нужного пациента
        if select_id is not None:
            target_iid = str(select_id)
            if self._tree.exists(target_iid):
                self._tree.selection_set(target_iid)
                self._tree.see(target_iid)

        self._list_hint_var.set(
            f"Всего: {len(patients)} | Двойной клик — выбрать активного",
        )

    def _on_search_changed(self, *_args: object) -> None:
        """Обработка изменения текста поиска."""
        self._refresh_list()

    # ──────────────────────────── Выбор в списке ──────────────────────────────

    def _on_tree_select(self, event: object) -> None:
        """Обработка выбора пациента в Treeview."""
        selection = self._tree.selection()
        if not selection:
            return

        iid = selection[0]
        patient = self._patients_map.get(iid)
        if patient is None:
            return

        self._selected_patient = patient
        self._fill_form(patient)
        self._delete_btn.config(state="normal")

    def _on_tree_double_click(self, event: object) -> None:
        """Двойной клик — установить активного пациента."""
        selection = self._tree.selection()
        if not selection:
            return

        iid = selection[0]
        patient = self._patients_map.get(iid)
        if patient is None:
            return

        self._app.active_patient = patient
        self._app.status_bar.set_ready(f"Активный пациент: {patient.name}")
        logger.info("Активный пациент: id=%d %s", patient.id, patient.name)

    # ──────────────────────────── Форма ────────────────────────────────────────

    def _fill_form(self, patient: Patient) -> None:
        """Заполнить карточку данными пациента.

        Args:
            patient: Объект Patient.
        """
        self._name_var.set(patient.name)
        self._birth_var.set(patient.birth_date or "")
        self._gender_var.set(_GENDER_FROM_DB.get(patient.gender, "--"))
        self._ethnicity_var.set(
            _ETHNICITY_FROM_DB.get(patient.ethnicity, _ETHNICITY_UNSPECIFIED_LABEL),
        )

        self._notes_text.delete("1.0", "end")
        if patient.notes:
            self._notes_text.insert("1.0", patient.notes)

        # Информация
        info_parts: list[str] = []
        if patient.created_at:
            info_parts.append(f"Создан: {patient.created_at[:10]}")
        if patient.updated_at:
            info_parts.append(f"Обновлён: {patient.updated_at[:10]}")

        # Проверим, является ли этот пациент активным
        active = self._app.active_patient
        if active is not None and active.id == patient.id:
            info_parts.append("(активный)")

        self._info_var.set("  |  ".join(info_parts))

    def _clear_form(self) -> None:
        """Очистить карточку пациента."""
        self._selected_patient = None
        self._name_var.set("")
        self._birth_var.set("")
        self._gender_var.set("--")
        self._ethnicity_var.set(_ETHNICITY_UNSPECIFIED_LABEL)
        self._notes_text.delete("1.0", "end")
        self._info_var.set("")
        self._delete_btn.config(state="disabled")

    def _on_clear_form(self) -> None:
        """Обработка нажатия кнопки «Очистить»."""
        self._clear_form()
        self._tree.selection_remove(*self._tree.selection())

    def _get_form_data(self) -> _PatientFormData:
        """Получить данные из формы.

        Returns:
            Словарь с полями пациента.
        """
        name = self._name_var.get().strip()
        birth_date = self._birth_var.get().strip() or None
        gender = _GENDER_TO_DB.get(self._gender_var.get())
        ethnicity = _ETHNICITY_TO_DB.get(self._ethnicity_var.get())
        notes = self._notes_text.get("1.0", "end").strip() or None

        return {
            "name": name,
            "birth_date": birth_date,
            "gender": gender,
            "ethnicity": ethnicity,
            "notes": notes,
        }

    # ──────────────────────────── CRUD ────────────────────────────────────────

    def _on_new_patient(self) -> None:
        """Обработка кнопки «Новый пациент» — очистить форму для ввода."""
        self._clear_form()
        self._tree.selection_remove(*self._tree.selection())
        self._name_entry.focus_set()

    def _on_save(self) -> None:
        """Сохранение (создание или обновление) пациента."""
        data = self._get_form_data()

        if not data["name"]:
            self._app.status_bar.set_error("Укажите имя пациента")
            self._name_entry.focus_set()
            return

        try:
            db = self._app.db
            if self._selected_patient is not None:
                # Обновление
                pid = self._selected_patient.id
                # Выбранный пациент загружен из БД — id гарантированно заполнен
                assert pid is not None
                db.update_patient(pid, **data)
                self._app.status_bar.set_ready(f"Пациент обновлён: {data['name']}")

                # Обновить активного пациента если это он
                active = self._app.active_patient
                if active is not None and active.id == pid:
                    updated = db.get_patient(pid)
                    if updated:
                        self._app.active_patient = updated

                self._refresh_list(select_id=pid)
                logger.info("Обновлён пациент id=%d", pid)
            else:
                # Создание
                patient = db.add_patient(**data)
                self._app.status_bar.set_ready(f"Пациент создан: {data['name']}")
                self._refresh_list(select_id=patient.id)
                self._selected_patient = patient
                self._delete_btn.config(state="normal")
                logger.info("Создан пациент id=%d", patient.id)
        except Exception as e:
            logger.error("Ошибка сохранения пациента: %s", e)
            self._app.status_bar.set_error(f"Ошибка: {e}")

    def _on_delete(self) -> None:
        """Удаление пациента с подтверждением."""
        if self._selected_patient is None:
            return

        name = self._selected_patient.name
        confirm = messagebox.askyesno(
            "Подтверждение удаления",
            f"Удалить пациента «{name}» и все связанные данные?\n\n"
            "Это действие необратимо.",
        )
        if not confirm:
            return

        try:
            db = self._app.db
            pid = self._selected_patient.id
            # Выбранный пациент загружен из БД — id гарантированно заполнен
            assert pid is not None

            # Если удаляем активного пациента — сбросить
            active = self._app.active_patient
            if active is not None and active.id == pid:
                self._app.active_patient = None

            db.delete_patient(pid)
            self._app.status_bar.set_ready(f"Пациент удалён: {name}")
            self._clear_form()
            self._refresh_list()
            logger.info("Удалён пациент id=%d: %s", pid, name)
        except Exception as e:
            logger.error("Ошибка удаления пациента: %s", e)
            self._app.status_bar.set_error(f"Ошибка удаления: {e}")

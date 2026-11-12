"""Ventana principal de Conversor Pro."""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

from .. import __version__
from ..config import (
    ACCENT_LABELS,
    ACCENTS,
    APP_NAME,
    GITHUB_URL,
    LANGUAGE_LABEL,
    OCR_LANGUAGES,
    OUTPUT_BY_ID,
    OUTPUT_FORMATS,
    SUPPORTED_INPUTS,
    AppConfig,
    accent_colors,
    config_dir,
    config_path,
    load_config,
    save_config,
)
from ..ocr import ToolStatus, detect_tools
from ..pipeline import Converter, JobResult
from . import theme
from .widgets import (
    DND_AVAILABLE,
    BaseWindow,
    Card,
    FileRow,
    FormatChip,
    Toast,
    ToolTip,
    open_path,
    short_path,
)

LABEL_TO_CODE = {label: code for label, code in OCR_LANGUAGES}
MODE_TO_LABEL = {"System": "Sistema", "Light": "Claro", "Dark": "Oscuro"}
LABEL_TO_MODE = {label: mode for mode, label in MODE_TO_LABEL.items()}

PAGE_META = {
    "convert": (
        "Conversión de documentos",
        "Añade archivos, elige formatos y conviértelos en un clic.",
    ),
    "settings": (
        "Ajustes",
        "Apariencia, herramientas OCR y configuración avanzada.",
    ),
    "about": (
        "Acerca de",
        "Formatos admitidos, atajos y créditos.",
    ),
}


class ConversorApp(BaseWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        theme.apply_appearance(self.config.theme)
        self.carpeta_entrada = Path(self.config.input_dir)
        self.carpeta_salida = Path(self.config.output_dir)
        try:
            self.carpeta_entrada.mkdir(parents=True, exist_ok=True)
            self.carpeta_salida.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        self.tools = ToolStatus()
        self._tools_ready = False
        self._tools_detecting = False
        self._ui_ready = False
        self._syncing_theme = False
        self._current_page = "convert"
        self._run_lock_widgets: list = []
        self._accent_buttons: dict[str, ctk.CTkButton] = {}
        self._tool_rows: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}

        self.queue: list[Path] = []
        self.rows: dict[Path, FileRow] = {}
        self._queue_keys: set[str] = set()
        self.running = False
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.progress_total = 0
        self.progress_done = 0
        self.run_started = 0.0
        self.stats = {"ok": 0, "error": 0, "cancelled": 0, "words": 0, "outputs": 0}

        self._configure_window()
        self._build_layout()
        self._apply_config_to_ui()
        self._apply_accent()
        self._apply_log_tags()
        self._bind_shortcuts()
        self._register_dnd()
        self._set_page("convert")
        self._ui_ready = True

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._scroll_left_top)
        self.after(250, self._detect_tools_bg)

    # ------------------------------------------------------------------
    # Construcción general
    # ------------------------------------------------------------------

    def _configure_window(self) -> None:
        self.title(f"{APP_NAME} · OCR y conversión de documentos")
        self.minsize(1120, 720)
        geometry = self.config.geometry
        use_saved = False
        if geometry:
            try:
                size = geometry.split("+")[0]
                width, height = (int(part) for part in size.split("x"))
                use_saved = width >= 1120 and height >= 700
            except (ValueError, TypeError):
                use_saved = False
        if use_saved:
            try:
                self.geometry(geometry)
            except Exception:
                self._center_window(1280, 840)
        else:
            self._center_window(1280, 840)
        self.configure(fg_color=theme.BG)

    def _center_window(self, width: int, height: int) -> None:
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self._build_topbar()

        self.page_container = ctk.CTkFrame(self.main, fg_color="transparent")
        self.page_container.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 18))
        self.page_container.grid_rowconfigure(0, weight=1)
        self.page_container.grid_columnconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}
        self.pages["convert"] = self._build_convert_page()
        self.pages["settings"] = self._build_settings_page()
        self.pages["about"] = self._build_about_page()
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()

        self.toast = Toast(self)

    def _build_sidebar(self) -> None:
        self.sidebar = ctk.CTkFrame(
            self, width=238, corner_radius=0, fg_color=theme.SIDEBAR_BG
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(3, weight=1)

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 24))
        brand.grid_columnconfigure(1, weight=1)

        self.logo_badge = ctk.CTkLabel(
            brand,
            text="⚡",
            width=44,
            height=44,
            corner_radius=13,
            fg_color=accent_colors(self.config.accent)[0],
            text_color="#ffffff",
            font=theme.font(21, "bold"),
        )
        self.logo_badge.grid(row=0, column=0, rowspan=2, padx=(0, 11))

        ctk.CTkLabel(
            brand,
            text="Conversor",
            font=theme.font(18, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            brand,
            text="PRO · OCR & DOCS",
            font=theme.font(9, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="nw")

        nav = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=12)
        nav.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            nav,
            text="PRINCIPAL",
            font=theme.font(9, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(0, 6))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for index, (key, icon, label) in enumerate(
            (
                ("convert", "🔄", "Convertir"),
                ("settings", "⚙️", "Ajustes"),
                ("about", "ℹ️", "Acerca de"),
            ),
            start=1,
        ):
            button = ctk.CTkButton(
                nav,
                text=f"  {icon}   {label}",
                anchor="w",
                height=42,
                corner_radius=10,
                fg_color="transparent",
                hover_color=theme.SUBTLE,
                text_color=theme.MUTED,
                font=theme.font(13, "bold"),
                command=lambda k=key: self._set_page(k),
            )
            button.grid(row=index, column=0, sticky="ew", pady=2)
            self.nav_buttons[key] = button

        ctk.CTkFrame(self.sidebar, fg_color="transparent", height=8).grid(row=2, column=0)

        status_card = ctk.CTkFrame(
            self.sidebar,
            corner_radius=13,
            fg_color=theme.CARD_BG,
            border_width=1,
            border_color=theme.CARD_BORDER,
        )
        status_card.grid(row=3, column=0, sticky="sew", padx=14, pady=(8, 12))
        status_card.grid_columnconfigure(1, weight=1)

        self.tool_dot = ctk.CTkLabel(
            status_card, text="●", font=theme.font(16), text_color=theme.AMBER
        )
        self.tool_dot.grid(row=0, column=0, sticky="nw", padx=(12, 2), pady=(10, 0))
        self.tool_status_label = ctk.CTkLabel(
            status_card,
            text="Detectando OCR…",
            font=theme.font(11, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        )
        self.tool_status_label.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(10, 0))
        self.tool_detail_label = ctk.CTkLabel(
            status_card,
            text="Comprobando herramientas",
            font=theme.font(9),
            text_color=theme.MUTED,
            anchor="w",
            justify="left",
            wraplength=150,
        )
        self.tool_detail_label.grid(row=1, column=1, sticky="ew", padx=(0, 8))

        self.btn_refresh_tools = ctk.CTkButton(
            status_card,
            text="Actualizar",
            height=24,
            width=80,
            corner_radius=7,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.MUTED,
            font=theme.font(9, "bold"),
            command=self._detect_tools_bg,
        )
        self.btn_refresh_tools.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 10)
        )

        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            text=f"v{__version__} · Windows / macOS / Linux",
            font=theme.font(8),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        github = ctk.CTkButton(
            footer,
            text="★  GitHub",
            height=24,
            width=80,
            corner_radius=7,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            text_color=theme.MUTED,
            font=theme.font(9, "bold"),
            command=lambda: webbrowser.open(GITHUB_URL),
        )
        github.grid(row=0, column=1, sticky="e")
        ToolTip(github, GITHUB_URL)

    def _build_topbar(self) -> None:
        topbar = ctk.CTkFrame(self.main, fg_color="transparent", height=72)
        topbar.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 12))
        topbar.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(topbar, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        self.topbar_title = ctk.CTkLabel(
            titles,
            text="Conversión de documentos",
            font=theme.font(22, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        )
        self.topbar_title.grid(row=0, column=0, sticky="w")
        self.topbar_subtitle = ctk.CTkLabel(
            titles,
            text="Añade archivos, elige formatos y conviértelos en un clic.",
            font=theme.font(11),
            text_color=theme.MUTED,
            anchor="w",
        )
        self.topbar_subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))

        actions = ctk.CTkFrame(topbar, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")

        self.theme_topbar = ctk.CTkSegmentedButton(
            actions,
            values=["Sistema", "Claro", "Oscuro"],
            width=190,
            height=32,
            corner_radius=8,
            font=theme.font(10, "bold"),
            command=self._on_theme_segment,
        )
        self.theme_topbar.grid(row=0, column=0, padx=(0, 8))
        self.theme_topbar.set(MODE_TO_LABEL.get(self.config.theme, "Sistema"))

        self.btn_open_output = ctk.CTkButton(
            actions,
            text="📂  Abrir salida",
            height=32,
            width=128,
            corner_radius=8,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.TEXT,
            font=theme.font(11, "bold"),
            command=self._open_output,
        )
        self.btn_open_output.grid(row=0, column=1)
        ToolTip(self.btn_open_output, "Abrir la carpeta de salida")

    # ------------------------------------------------------------------
    # Página: convertir
    # ------------------------------------------------------------------

    def _build_convert_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.page_container, fg_color="transparent")
        page.grid_columnconfigure(0, weight=0, minsize=402)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)

        left_container = ctk.CTkFrame(page, fg_color="transparent")
        left_container.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left_container.grid_rowconfigure(0, weight=1)
        left_container.grid_columnconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            left_container,
            width=396,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
            scrollbar_button_hover_color=theme.MUTED,
        )
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        self.left_scroll = left

        self._build_paths_card(left)
        self._build_formats_card(left)
        self._build_ocr_card(left)
        self._build_actions(left_container)

        right = ctk.CTkFrame(page, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(2, weight=2)

        self._build_queue_card(right)
        self._build_progress_card(right)
        self._build_log_card(right)
        return page

    def _build_paths_card(self, parent) -> None:
        card = Card(
            parent,
            "Carpetas de trabajo",
            "El origen y el destino de tus conversiones.",
            icon="📁",
        )
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        body = card.body

        for index, (label, attr, icon) in enumerate(
            (
                ("Entrada", "input", "📥"),
                ("Salida", "output", "📤"),
            )
        ):
            row = index * 3
            ctk.CTkLabel(
                body,
                text=f"{icon}  {label}",
                font=theme.font(10, "bold"),
                text_color=theme.MUTED,
                anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0 if index == 0 else 8, 1))

            path_label = ctk.CTkLabel(
                body,
                text="",
                font=theme.font(10),
                text_color=theme.TEXT,
                anchor="w",
                justify="left",
            )
            path_label.grid(row=row + 1, column=0, sticky="ew", padx=(0, 8))
            if attr == "input":
                self.lbl_input = path_label
            else:
                self.lbl_output = path_label

            button = ctk.CTkButton(
                body,
                text="Cambiar",
                width=70,
                height=26,
                corner_radius=7,
                fg_color="transparent",
                hover_color=theme.SUBTLE,
                border_width=1,
                border_color=theme.CARD_BORDER,
                text_color=theme.TEXT,
                font=theme.font(9, "bold"),
                command=self.seleccionar_entrada if attr == "input" else self.seleccionar_salida,
            )
            button.grid(row=row + 1, column=1, sticky="e")
            self._run_lock_widgets.append(button)

        body.grid_columnconfigure(0, weight=1)

    def _build_formats_card(self, parent) -> None:
        card = Card(
            parent,
            "Formatos de salida",
            "Puedes generar varios formatos a la vez.",
            icon="🧾",
        )
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        all_button = ctk.CTkButton(
            card.header_actions,
            text="Todos",
            width=52,
            height=24,
            corner_radius=6,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            text_color=theme.MUTED,
            font=theme.font(9, "bold"),
            command=self._select_all_formats,
        )
        all_button.grid(row=0, column=0, padx=(0, 4))
        md_button = ctk.CTkButton(
            card.header_actions,
            text="Solo MD",
            width=62,
            height=24,
            corner_radius=6,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            text_color=theme.MUTED,
            font=theme.font(9, "bold"),
            command=self._select_md_only,
        )
        md_button.grid(row=0, column=1)
        self._run_lock_widgets.extend([all_button, md_button])

        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        primary, hover = accent_colors(self.config.accent)
        self.format_chips: dict[str, FormatChip] = {}
        for index, (format_id, name, extension, description) in enumerate(OUTPUT_FORMATS):
            chip = FormatChip(
                body,
                format_id,
                name,
                extension,
                description,
                selected=format_id in self.config.formats,
                primary=primary,
                hover=hover,
                command=self._on_format_toggle,
            )
            chip.grid(
                row=index // 2,
                column=index % 2,
                sticky="ew",
                padx=3,
                pady=3,
            )
            self.format_chips[format_id] = chip
            self._run_lock_widgets.append(chip)

    def _build_ocr_card(self, parent) -> None:
        card = Card(
            parent,
            "OCR y opciones",
            "Reconoce texto en escaneos, fotos y PDF sin capa de texto.",
            icon="🔍",
        )
        card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        body = card.body

        ctk.CTkLabel(
            body,
            text="Idioma del documento",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.opt_lang = ctk.CTkOptionMenu(
            body,
            values=[label for label, _ in OCR_LANGUAGES],
            width=190,
            height=28,
            corner_radius=7,
            font=theme.font(10),
            dropdown_font=theme.font(10),
            command=self._on_lang_change,
        )
        self.opt_lang.grid(row=0, column=1, sticky="e", pady=(0, 4))
        self._run_lock_widgets.append(self.opt_lang)

        ctk.CTkLabel(
            body,
            text="Páginas (ej. 1-5, 8)",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.var_range = tk.StringVar(value=self.config.page_range)
        self.entry_range = ctk.CTkEntry(
            body,
            textvariable=self.var_range,
            width=190,
            height=28,
            corner_radius=7,
            placeholder_text="Vacío = todas",
            font=theme.font(10),
        )
        self.entry_range.grid(row=1, column=1, sticky="e", pady=(4, 0))
        self.var_range.trace_add("write", lambda *_: self._save_settings_from_ui())
        self._run_lock_widgets.append(self.entry_range)

        ctk.CTkFrame(body, height=1, fg_color=theme.CARD_BORDER).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=10
        )

        self.var_ocr = tk.BooleanVar(value=self.config.ocr_enabled)
        self.var_force = tk.BooleanVar(value=self.config.force_ocr)
        self.var_deskew = tk.BooleanVar(value=self.config.deskew)
        self.var_rotate = tk.BooleanVar(value=self.config.rotate_pages)
        self.var_recursive = tk.BooleanVar(value=self.config.recursive)
        self.var_open_output = tk.BooleanVar(value=self.config.open_output)
        self.var_overwrite = tk.BooleanVar(value=self.config.overwrite)
        self.var_include_title = tk.BooleanVar(value=self.config.include_title)
        self.var_keep_pages = tk.BooleanVar(value=self.config.keep_pages)

        basic_switches = [
            ("OCR activo", self.var_ocr, self._on_ocr_toggle),
            ("Forzar OCR", self.var_force, self._on_option_change),
            ("Enderezar", self.var_deskew, self._on_option_change),
            ("Rotar páginas", self.var_rotate, self._on_option_change),
        ]
        self.switch_widgets: list[ctk.CTkSwitch] = []
        for index, (text, variable, command) in enumerate(basic_switches):
            switch = ctk.CTkSwitch(
                body,
                text=text,
                variable=variable,
                onvalue=True,
                offvalue=False,
                font=theme.font(10),
                progress_color=accent_colors(self.config.accent)[0],
                command=command,
            )
            switch.grid(
                row=3 + index // 2,
                column=index % 2,
                sticky="w",
                padx=(0 if index % 2 == 0 else 8, 0),
                pady=3,
            )
            self.switch_widgets.append(switch)
            self._run_lock_widgets.append(switch)

        self.btn_advanced = ctk.CTkButton(
            body,
            text="▸  Opciones avanzadas",
            height=26,
            corner_radius=7,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            text_color=theme.MUTED,
            font=theme.font(10, "bold"),
            anchor="w",
            command=self._toggle_advanced,
        )
        self.btn_advanced.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._run_lock_widgets.append(self.btn_advanced)

        self.advanced_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.advanced_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.advanced_frame.grid_columnconfigure(0, weight=1)
        self.advanced_frame.grid_columnconfigure(1, weight=1)
        self.advanced_frame.grid_remove()

        advanced_switches = [
            ("Incluir subcarpetas", self.var_recursive),
            ("Abrir al terminar", self.var_open_output),
            ("Sobrescribir", self.var_overwrite),
            ("Incluir título", self.var_include_title),
            ("Separar páginas", self.var_keep_pages),
        ]
        for index, (text, variable) in enumerate(advanced_switches):
            switch = ctk.CTkSwitch(
                self.advanced_frame,
                text=text,
                variable=variable,
                onvalue=True,
                offvalue=False,
                font=theme.font(10),
                progress_color=accent_colors(self.config.accent)[0],
                command=self._on_option_change,
            )
            switch.grid(
                row=index // 2,
                column=index % 2,
                sticky="w",
                padx=(0 if index % 2 == 0 else 8, 0),
                pady=3,
            )
            self.switch_widgets.append(switch)
            self._run_lock_widgets.append(switch)

        self._refresh_ocr_controls()

    def _build_actions(self, parent) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        frame.grid_columnconfigure(0, weight=1)

        self.btn_start = ctk.CTkButton(
            frame,
            text="▶   Iniciar conversión",
            height=52,
            corner_radius=12,
            font=theme.font(15, "bold"),
            command=self.iniciar_proceso,
        )
        self.btn_start.grid(row=0, column=0, sticky="ew")

        secondary = ctk.CTkFrame(frame, fg_color="transparent")
        secondary.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        secondary.grid_columnconfigure(0, weight=1)
        secondary.grid_columnconfigure(1, weight=1)

        self.btn_stop = ctk.CTkButton(
            secondary,
            text="⏹  Detener",
            height=36,
            corner_radius=9,
            fg_color="transparent",
            hover_color=("#fee2e2", "#3f1d24"),
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.RED,
            font=theme.font(11, "bold"),
            state="disabled",
            command=self.detener_proceso,
        )
        self.btn_stop.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.btn_clear = ctk.CTkButton(
            secondary,
            text="🗑  Limpiar cola",
            height=36,
            corner_radius=9,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.MUTED,
            font=theme.font(11, "bold"),
            command=self._clear_queue,
        )
        self.btn_clear.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._run_lock_widgets.append(self.btn_clear)

        self.lbl_action_hint = ctk.CTkLabel(
            frame,
            text="Consejo: arrastra archivos a la cola o usa Ctrl+O.",
            font=theme.font(9),
            text_color=theme.MUTED,
            anchor="w",
        )
        self.lbl_action_hint.grid(row=2, column=0, sticky="w", pady=(7, 0))

    def _build_queue_card(self, parent) -> None:
        card = Card(
            parent,
            "Cola de archivos",
            "Añade archivos o suelta carpetas aquí.",
            icon="📂",
        )
        card.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        card.body.grid_rowconfigure(0, weight=1)
        card.body.grid_columnconfigure(0, weight=1)

        self.lbl_queue_count = ctk.CTkLabel(
            card.header_actions,
            text="0",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            fg_color=theme.SUBTLE,
            corner_radius=10,
            width=28,
            height=22,
        )
        self.lbl_queue_count.grid(row=0, column=0, padx=(0, 6))

        self.btn_add_files = ctk.CTkButton(
            card.header_actions,
            text="＋",
            width=28,
            height=26,
            corner_radius=7,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.TEXT,
            font=theme.font(13, "bold"),
            command=self._add_files_dialog,
        )
        self.btn_add_files.grid(row=0, column=1, padx=2)
        ToolTip(self.btn_add_files, "Añadir archivos (Ctrl+O)")

        self.btn_add_folder = ctk.CTkButton(
            card.header_actions,
            text="📁",
            width=28,
            height=26,
            corner_radius=7,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.TEXT,
            font=theme.font(11, "bold"),
            command=self._add_folder_dialog,
        )
        self.btn_add_folder.grid(row=0, column=2, padx=2)
        ToolTip(self.btn_add_folder, "Añadir una carpeta (Ctrl+Mayús+O)")

        self.btn_clear_queue = ctk.CTkButton(
            card.header_actions,
            text="🗑",
            width=28,
            height=26,
            corner_radius=7,
            fg_color="transparent",
            hover_color=("#fee2e2", "#3f1d24"),
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.MUTED,
            font=theme.font(11, "bold"),
            command=self._clear_queue,
        )
        self.btn_clear_queue.grid(row=0, column=3, padx=(2, 0))
        ToolTip(self.btn_clear_queue, "Vaciar la cola")
        self._run_lock_widgets.extend([self.btn_add_files, self.btn_add_folder])

        self.queue_list = ctk.CTkScrollableFrame(
            card.body,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
            scrollbar_button_hover_color=theme.MUTED,
        )
        self.queue_list.grid(row=0, column=0, sticky="nsew")
        self.queue_list.grid_columnconfigure(0, weight=1)

        self.empty_state = ctk.CTkFrame(self.queue_list, fg_color="transparent")
        self.empty_state.grid(row=0, column=0, sticky="nsew", pady=26)
        self.empty_state.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.empty_state,
            text="📥",
            font=theme.font(34),
        ).grid(row=0, column=0)
        ctk.CTkLabel(
            self.empty_state,
            text="Arrastra tus archivos aquí" if DND_AVAILABLE else "Tu cola está vacía",
            font=theme.font(13, "bold"),
            text_color=theme.TEXT,
        ).grid(row=1, column=0, pady=(4, 0))
        ctk.CTkLabel(
            self.empty_state,
            text=(
                "o usa los botones ＋ y 📁 para añadir archivos y carpetas."
                if DND_AVAILABLE
                else "Usa los botones ＋ y 📁 para añadir archivos y carpetas."
            ),
            font=theme.font(10),
            text_color=theme.MUTED,
        ).grid(row=2, column=0, pady=(2, 0))

    def _build_progress_card(self, parent) -> None:
        card = Card(parent, "Progreso", icon="📈")
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(
            card.body,
            height=10,
            corner_radius=5,
            progress_color=accent_colors(self.config.accent)[0],
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0)

        info = ctk.CTkFrame(card.body, fg_color="transparent")
        info.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        info.grid_columnconfigure(0, weight=1)
        self.lbl_progress_main = ctk.CTkLabel(
            info,
            text="0 / 0 archivos",
            font=theme.font(10, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        )
        self.lbl_progress_main.grid(row=0, column=0, sticky="w")
        self.lbl_eta = ctk.CTkLabel(
            info,
            text="Listo para convertir",
            font=theme.font(9),
            text_color=theme.MUTED,
            anchor="e",
        )
        self.lbl_eta.grid(row=0, column=1, sticky="e")

        stats = ctk.CTkFrame(card.body, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.lbl_stat_ok = ctk.CTkLabel(
            stats, text="✅ 0", font=theme.font(9, "bold"), text_color=theme.GREEN
        )
        self.lbl_stat_ok.grid(row=0, column=0, sticky="w")
        self.lbl_stat_err = ctk.CTkLabel(
            stats, text="❌ 0", font=theme.font(9, "bold"), text_color=theme.RED
        )
        self.lbl_stat_err.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.lbl_stat_words = ctk.CTkLabel(
            stats, text="📝 0 palabras", font=theme.font(9), text_color=theme.MUTED
        )
        self.lbl_stat_words.grid(row=0, column=2, sticky="e")
        stats.grid_columnconfigure(2, weight=1)

    def _build_log_card(self, parent) -> None:
        card = Card(
            parent,
            "Registro",
            "Actividad del proceso en tiempo real.",
            icon="🖥️",
        )
        card.grid(row=2, column=0, sticky="nsew")
        card.body.grid_rowconfigure(0, weight=1)
        card.body.grid_columnconfigure(0, weight=1)

        save_button = ctk.CTkButton(
            card.header_actions,
            text="Guardar",
            width=58,
            height=24,
            corner_radius=6,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            text_color=theme.MUTED,
            font=theme.font(9, "bold"),
            command=self._save_log,
        )
        save_button.grid(row=0, column=0, padx=(0, 4))
        clear_button = ctk.CTkButton(
            card.header_actions,
            text="Limpiar",
            width=58,
            height=24,
            corner_radius=6,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            text_color=theme.MUTED,
            font=theme.font(9, "bold"),
            command=self._clear_log,
        )
        clear_button.grid(row=0, column=1)

        self.log_box = ctk.CTkTextbox(
            card.body,
            height=150,
            corner_radius=9,
            fg_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            font=theme.mono_font(10),
            wrap="word",
            text_color=theme.TEXT,
        )
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")
        self._append_log("Sistema inicializado. Esperando archivos…", "info")

    # ------------------------------------------------------------------
    # Página: ajustes
    # ------------------------------------------------------------------

    def _build_settings_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.page_container, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            page,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
            scrollbar_button_hover_color=theme.MUTED,
        )
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        self._build_appearance_card(scroll)
        self._build_tools_card(scroll)
        self._build_performance_card(scroll)
        self._build_config_card(scroll)
        self._build_shortcuts_card(scroll)
        return page

    def _build_appearance_card(self, parent) -> None:
        card = Card(
            parent,
            "Apariencia",
            "Elige el tema y el color de acento de la aplicación.",
            icon="🎨",
        )
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(
            card.body,
            text="Tema",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.theme_settings = ctk.CTkSegmentedButton(
            card.body,
            values=["Sistema", "Claro", "Oscuro"],
            width=230,
            height=30,
            corner_radius=8,
            font=theme.font(10, "bold"),
            command=self._on_theme_segment,
        )
        self.theme_settings.grid(row=0, column=1, sticky="e")
        self.theme_settings.set(MODE_TO_LABEL.get(self.config.theme, "Sistema"))

        ctk.CTkLabel(
            card.body,
            text="Color de acento",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(14, 0))
        swatches = ctk.CTkFrame(card.body, fg_color="transparent")
        swatches.grid(row=1, column=1, sticky="e", pady=(14, 0))
        for index, (name, (primary, hover)) in enumerate(ACCENTS.items()):
            button = ctk.CTkButton(
                swatches,
                text="",
                width=30,
                height=30,
                corner_radius=15,
                fg_color=primary,
                hover_color=hover,
                border_width=2,
                border_color=theme.CARD_BG,
                font=theme.font(11, "bold"),
                command=lambda n=name: self._set_accent(n),
            )
            button.grid(row=0, column=index, padx=3)
            ToolTip(button, ACCENT_LABELS.get(name, name))
            self._accent_buttons[name] = button

        card.body.grid_columnconfigure(0, weight=1)

    def _build_tools_card(self, parent) -> None:
        card = Card(
            parent,
            "Herramientas OCR",
            "Estado de los motores que convierten escaneos en texto.",
            icon="🔧",
        )
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        body = card.body

        rows = (
            ("ocrmypdf", "OCRmyPDF", "Motor de OCR para PDF"),
            ("tesseract", "Tesseract", "Reconocimiento de texto"),
            ("ghostscript", "Ghostscript", "Requerido por OCRmyPDF en Windows"),
            ("pytesseract", "pytesseract", "Puente de OCR para imágenes"),
            ("languages", "Idiomas", "Paquetes de idioma instalados"),
        )
        for index, (key, name, description) in enumerate(rows):
            row = index * 2
            ctk.CTkLabel(
                body,
                text=name,
                font=theme.font(10, "bold"),
                text_color=theme.TEXT,
                anchor="w",
            ).grid(row=row, column=0, sticky="w", pady=(0 if index == 0 else 8, 0))
            value = ctk.CTkLabel(
                body,
                text="Comprobando…",
                font=theme.font(10, "bold"),
                text_color=theme.MUTED,
                anchor="e",
            )
            value.grid(row=row, column=1, sticky="e", pady=(0 if index == 0 else 8, 0))
            detail = ctk.CTkLabel(
                body,
                text=description,
                font=theme.font(9),
                text_color=theme.MUTED,
                anchor="w",
                justify="left",
                wraplength=520,
            )
            detail.grid(row=row + 1, column=0, columnspan=2, sticky="w")
            self._tool_rows[key] = (value, detail)

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=len(rows) * 2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.grid_columnconfigure(0, weight=1)
        self.btn_detect = ctk.CTkButton(
            actions,
            text="🔄  Volver a detectar",
            height=30,
            corner_radius=8,
            font=theme.font(10, "bold"),
            command=self._detect_tools_bg,
        )
        self.btn_detect.grid(row=0, column=0, sticky="w")
        copy_button = ctk.CTkButton(
            actions,
            text="Copiar diagnóstico",
            height=30,
            corner_radius=8,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.TEXT,
            font=theme.font(10, "bold"),
            command=self._copy_diagnostics,
        )
        copy_button.grid(row=0, column=1, sticky="e")
        body.grid_columnconfigure(0, weight=1)

        note = ctk.CTkLabel(
            body,
            text=(
                "En Windows, OCRmyPDF necesita Tesseract y Ghostscript instalados y "
                "en el PATH. Sin ellos, la app sigue convirtiendo PDF con texto y "
                "documentos de Office."
            ),
            font=theme.font(9),
            text_color=theme.MUTED,
            anchor="w",
            justify="left",
            wraplength=760,
        )
        note.grid(row=len(rows) * 2 + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_performance_card(self, parent) -> None:
        card = Card(
            parent,
            "Rendimiento",
            "Ajusta la velocidad y la optimización del OCR.",
            icon="⚡",
        )
        card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        body = card.body

        ctk.CTkLabel(
            body,
            text="Procesos simultáneos",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        jobs_frame = ctk.CTkFrame(body, fg_color="transparent")
        jobs_frame.grid(row=0, column=1, sticky="e")
        self.jobs_slider = ctk.CTkSlider(
            jobs_frame,
            from_=1,
            to=8,
            number_of_steps=7,
            width=190,
            command=self._on_jobs_change,
        )
        self.jobs_slider.grid(row=0, column=0)
        self.jobs_slider.set(self.config.jobs)
        self.lbl_jobs_value = ctk.CTkLabel(
            jobs_frame,
            text=str(self.config.jobs),
            font=theme.font(10, "bold"),
            text_color=theme.TEXT,
            width=22,
        )
        self.lbl_jobs_value.grid(row=0, column=1, padx=(6, 0))

        ctk.CTkLabel(
            body,
            text="Optimización del PDF",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.optimize_seg = ctk.CTkSegmentedButton(
            body,
            values=["0", "1", "2", "3"],
            width=190,
            height=28,
            corner_radius=7,
            font=theme.font(10, "bold"),
            command=self._on_optimize_change,
        )
        self.optimize_seg.grid(row=1, column=1, sticky="e", pady=(12, 0))
        self.optimize_seg.set(str(self.config.optimize))
        ctk.CTkLabel(
            body,
            text="0 = más rápido · 3 = archivo más pequeño",
            font=theme.font(9),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        body.grid_columnconfigure(0, weight=1)

    def _build_config_card(self, parent) -> None:
        card = Card(
            parent,
            "Configuración",
            "La configuración se guarda automáticamente en tu equipo.",
            icon="💾",
        )
        card.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        body = card.body

        ctk.CTkLabel(
            body,
            text="Archivo de configuración",
            font=theme.font(10, "bold"),
            text_color=theme.MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.lbl_config_path = ctk.CTkLabel(
            body,
            text=short_path(config_path(), 72),
            font=theme.font(9),
            text_color=theme.TEXT,
            anchor="w",
        )
        self.lbl_config_path.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        ToolTip(self.lbl_config_path, str(config_path()))

        open_config = ctk.CTkButton(
            body,
            text="📂  Abrir carpeta",
            height=30,
            corner_radius=8,
            fg_color="transparent",
            hover_color=theme.SUBTLE,
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.TEXT,
            font=theme.font(10, "bold"),
            command=lambda: open_path(config_dir()),
        )
        open_config.grid(row=2, column=0, sticky="w")
        reset = ctk.CTkButton(
            body,
            text="Restablecer valores",
            height=30,
            corner_radius=8,
            fg_color="transparent",
            hover_color=("#fee2e2", "#3f1d24"),
            border_width=1,
            border_color=theme.CARD_BORDER,
            text_color=theme.RED,
            font=theme.font(10, "bold"),
            command=self._reset_config,
        )
        reset.grid(row=2, column=1, sticky="e")
        body.grid_columnconfigure(0, weight=1)

    def _build_shortcuts_card(self, parent) -> None:
        card = Card(
            parent,
            "Atajos de teclado",
            "Para trabajar más rápido.",
            icon="⌨️",
        )
        card.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        shortcuts = (
            ("Ctrl + O", "Añadir archivos"),
            ("Ctrl + Mayús + O", "Añadir carpeta"),
            ("F5", "Iniciar conversión"),
            ("Esc", "Detener el proceso"),
            ("Ctrl + L", "Limpiar el registro"),
            ("Ctrl + Q", "Cerrar la aplicación"),
        )
        for index, (keys, description) in enumerate(shortcuts):
            ctk.CTkLabel(
                card.body,
                text=keys,
                font=theme.font(10, "bold"),
                text_color=theme.TEXT,
                anchor="w",
                width=140,
            ).grid(row=index, column=0, sticky="w", pady=2)
            ctk.CTkLabel(
                card.body,
                text=description,
                font=theme.font(10),
                text_color=theme.MUTED,
                anchor="w",
            ).grid(row=index, column=1, sticky="w", pady=2)
        card.body.grid_columnconfigure(1, weight=1)

    # ------------------------------------------------------------------
    # Página: acerca de
    # ------------------------------------------------------------------

    def _build_about_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.page_container, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            page,
            fg_color="transparent",
            scrollbar_button_color=theme.CARD_BORDER,
            scrollbar_button_hover_color=theme.MUTED,
        )
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        hero = Card(scroll, "Conversor Pro", "OCR, documentos y exportación profesional.", icon="⚡")
        hero.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        hero_body = hero.body
        ctk.CTkLabel(
            hero_body,
            text=(
                "Convierte PDF, imágenes, Word, HTML, Markdown, CSV, Excel y EPUB "
                "a los formatos que necesites. Aplica OCR, conserva la estructura y "
                "genera Markdown, HTML, Word, EPUB, JSON, CSV o PDF listo para buscar."
            ),
            font=theme.font(11),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=820,
        ).grid(row=0, column=0, sticky="w")

        features = ctk.CTkFrame(hero_body, fg_color="transparent")
        features.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        for index, text in enumerate(
            (
                "✅  OCR para PDF e imágenes",
                "✅  Cola por lotes y carpetas",
                "✅  Ocho formatos de salida",
                "✅  Ajustes persistentes",
                "✅  Interfaz clara y modo oscuro",
                "✅  Sin ventanas emergentes del sistema",
            )
        ):
            ctk.CTkLabel(
                features,
                text=text,
                font=theme.font(10),
                text_color=theme.MUTED,
                anchor="w",
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 30), pady=2)
        features.grid_columnconfigure(0, weight=1)
        features.grid_columnconfigure(1, weight=1)

        formats_card = Card(
            scroll,
            "Formatos admitidos",
            "Entrada y salida disponibles en esta versión.",
            icon="🧩",
        )
        formats_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(
            formats_card.body,
            text="Entrada: " + ", ".join(sorted(set(SUPPORTED_INPUTS.values()))),
            font=theme.font(10),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=820,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            formats_card.body,
            text="Salida: " + ", ".join(item[1] for item in OUTPUT_FORMATS),
            font=theme.font(10),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=820,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        links = Card(
            scroll,
            "Enlaces y créditos",
            "Proyecto abierto y herramientas que lo hacen posible.",
            icon="🔗",
        )
        links.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkButton(
            links.body,
            text="★  Ver repositorio en GitHub",
            height=34,
            corner_radius=8,
            font=theme.font(10, "bold"),
            command=lambda: webbrowser.open(GITHUB_URL),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            links.body,
            text=(
                "Hecho con CustomTkinter, OCRmyPDF, Tesseract, pypdf, python-docx, "
                "BeautifulSoup, reportlab y Pillow. Licencia MIT."
            ),
            font=theme.font(9),
            text_color=theme.MUTED,
            anchor="w",
            justify="left",
            wraplength=820,
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))
        return page

    # ------------------------------------------------------------------
    # Navegación y tema
    # ------------------------------------------------------------------

    def _set_page(self, key: str) -> None:
        if key not in self.pages:
            return
        self._current_page = key
        for page_key, page in self.pages.items():
            if page_key == key:
                page.grid()
            else:
                page.grid_remove()

        title, subtitle = PAGE_META[key]
        self.topbar_title.configure(text=title)
        self.topbar_subtitle.configure(text=subtitle)
        primary, hover = accent_colors(self.config.accent)
        for nav_key, button in self.nav_buttons.items():
            if nav_key == key:
                button.configure(fg_color=primary, hover_color=hover, text_color="#ffffff")
            else:
                button.configure(
                    fg_color="transparent",
                    hover_color=theme.SUBTLE,
                    text_color=theme.MUTED,
                )

    def _on_theme_segment(self, label: str) -> None:
        if self._syncing_theme:
            return
        mode = LABEL_TO_MODE.get(label, "System")
        if mode == self.config.theme:
            return
        self.config.theme = mode
        theme.apply_appearance(mode)
        self._syncing_theme = True
        try:
            self.theme_topbar.set(MODE_TO_LABEL.get(mode, "Sistema"))
            self.theme_settings.set(MODE_TO_LABEL.get(mode, "Sistema"))
        finally:
            self._syncing_theme = False
        self._apply_log_tags()
        self._save_settings_from_ui()

    def _set_accent(self, name: str) -> None:
        if name not in ACCENT_LABELS:
            return
        self.config.accent = name
        self._apply_accent()
        self._save_settings_from_ui()

    def _apply_accent(self) -> None:
        primary, hover = accent_colors(self.config.accent)
        self.logo_badge.configure(fg_color=primary)
        self.btn_start.configure(fg_color=primary, hover_color=hover)
        self.progress_bar.configure(progress_color=primary)
        for segmented in (self.theme_topbar, self.theme_settings):
            try:
                segmented.configure(selected_color=primary, selected_hover_color=hover)
            except Exception:
                pass
        for chip in getattr(self, "format_chips", {}).values():
            chip.set_accent(primary, hover)
        for switch in getattr(self, "switch_widgets", []):
            try:
                switch.configure(progress_color=primary)
            except Exception:
                pass
        for name, button in self._accent_buttons.items():
            selected = name == self.config.accent
            button.configure(
                text="✓" if selected else "",
                border_width=3 if selected else 2,
                border_color=("#0f172a", "#ffffff") if selected else theme.CARD_BG,
            )
        self._set_page(self._current_page)

    def _apply_log_tags(self) -> None:
        if not hasattr(self, "log_box"):
            return
        dark = ctk.get_appearance_mode() == "Dark"
        colors = {
            "info": "#94a3b8" if dark else "#64748b",
            "success": "#34d399" if dark else "#059669",
            "warn": "#fbbf24" if dark else "#d97706",
            "error": "#f87171" if dark else "#dc2626",
            "accent": "#60a5fa" if dark else "#2563eb",
        }
        text_widget = getattr(self.log_box, "_textbox", self.log_box)
        for tag, color in colors.items():
            try:
                text_widget.tag_config(tag, foreground=color)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    def _apply_config_to_ui(self) -> None:
        self.carpeta_entrada = Path(self.config.input_dir)
        self.carpeta_salida = Path(self.config.output_dir)
        self.lbl_input.configure(text=short_path(self.carpeta_entrada, 48))
        self.lbl_output.configure(text=short_path(self.carpeta_salida, 48))
        ToolTip(self.lbl_input, str(self.carpeta_entrada))
        ToolTip(self.lbl_output, str(self.carpeta_salida))

        for format_id, chip in self.format_chips.items():
            chip.set_selected(format_id in self.config.formats)

        self.opt_lang.set(LANGUAGE_LABEL.get(self.config.ocr_lang, "Español"))
        self.var_range.set(self.config.page_range)
        self.var_ocr.set(self.config.ocr_enabled)
        self.var_force.set(self.config.force_ocr)
        self.var_deskew.set(self.config.deskew)
        self.var_rotate.set(self.config.rotate_pages)
        self.var_recursive.set(self.config.recursive)
        self.var_open_output.set(self.config.open_output)
        self.var_overwrite.set(self.config.overwrite)
        self.var_include_title.set(self.config.include_title)
        self.var_keep_pages.set(self.config.keep_pages)
        self._refresh_ocr_controls()

        self.theme_topbar.set(MODE_TO_LABEL.get(self.config.theme, "Sistema"))
        self.theme_settings.set(MODE_TO_LABEL.get(self.config.theme, "Sistema"))
        self.jobs_slider.set(self.config.jobs)
        self.lbl_jobs_value.configure(text=str(self.config.jobs))
        self.optimize_seg.set(str(self.config.optimize))
        self._update_queue_count()
        self._update_start_hint()

    def _save_settings_from_ui(self) -> None:
        if not self._ui_ready:
            return
        self.config.input_dir = str(self.carpeta_entrada)
        self.config.output_dir = str(self.carpeta_salida)
        self.config.formats = [
            format_id for format_id, chip in self.format_chips.items() if chip.selected
        ] or ["md"]
        self.config.ocr_lang = LABEL_TO_CODE.get(self.opt_lang.get(), "spa")
        self.config.page_range = self.var_range.get().strip()
        self.config.ocr_enabled = bool(self.var_ocr.get())
        self.config.force_ocr = bool(self.var_force.get())
        self.config.deskew = bool(self.var_deskew.get())
        self.config.rotate_pages = bool(self.var_rotate.get())
        self.config.recursive = bool(self.var_recursive.get())
        self.config.open_output = bool(self.var_open_output.get())
        self.config.overwrite = bool(self.var_overwrite.get())
        self.config.include_title = bool(self.var_include_title.get())
        self.config.keep_pages = bool(self.var_keep_pages.get())
        self.config.jobs = int(round(self.jobs_slider.get()))
        try:
            self.config.optimize = int(self.optimize_seg.get())
        except (TypeError, ValueError):
            self.config.optimize = 1
        try:
            self.config.geometry = self.geometry()
        except Exception:
            pass
        save_config(self.config)

    def _reset_config(self) -> None:
        if not messagebox.askyesno(
            "Restablecer configuración",
            "¿Quieres restaurar todas las opciones a sus valores por defecto?\n"
            "La cola de archivos no se modificará.",
        ):
            return
        self.config = AppConfig()
        try:
            self.config.ensure_dirs()
        except OSError:
            pass
        save_config(self.config)
        self._apply_config_to_ui()
        self._set_page(self._current_page)
        self._apply_accent()
        self.log("⚙️ Configuración restablecida.", "warn")
        self.toast.show("Configuración restablecida", "success")

    def _on_option_change(self) -> None:
        self._save_settings_from_ui()
        self._update_start_hint()

    def _on_ocr_toggle(self) -> None:
        self._refresh_ocr_controls()
        self._on_option_change()

    def _refresh_ocr_controls(self) -> None:
        enabled = bool(self.var_ocr.get())
        state = "normal" if enabled else "disabled"
        for switch in getattr(self, "switch_widgets", [])[1:4]:
            try:
                switch.configure(state=state)
            except Exception:
                pass
        try:
            self.opt_lang.configure(state=state)
        except Exception:
            pass

    def _on_lang_change(self, _value: str) -> None:
        self._save_settings_from_ui()

    def _on_jobs_change(self, value: float) -> None:
        jobs = int(round(value))
        self.lbl_jobs_value.configure(text=str(jobs))
        self._save_settings_from_ui()

    def _on_optimize_change(self, _value: str) -> None:
        self._save_settings_from_ui()

    def _on_format_toggle(self, format_id: str, selected: bool) -> None:
        if not selected and sum(1 for chip in self.format_chips.values() if chip.selected) <= 1:
            self.toast.show("Elige al menos un formato de salida", "warn")
            return
        self.format_chips[format_id].set_selected(selected)
        self._save_settings_from_ui()
        self._update_start_hint()

    def _select_all_formats(self) -> None:
        for chip in self.format_chips.values():
            chip.set_selected(True)
        self._save_settings_from_ui()
        self._update_start_hint()

    def _select_md_only(self) -> None:
        for format_id, chip in self.format_chips.items():
            chip.set_selected(format_id == "md")
        self._save_settings_from_ui()
        self._update_start_hint()

    def _update_start_hint(self) -> None:
        selected = [
            OUTPUT_BY_ID[format_id][1]
            for format_id, chip in self.format_chips.items()
            if chip.selected and format_id in OUTPUT_BY_ID
        ]
        if selected:
            self.lbl_action_hint.configure(
                text=f"Salida: {', '.join(selected)} · Ctrl+O para añadir archivos."
            )

    def _scroll_left_top(self) -> None:
        try:
            self.left_scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def _toggle_advanced(self) -> None:
        if self.advanced_frame.winfo_ismapped():
            self.advanced_frame.grid_remove()
            self.btn_advanced.configure(text="▸  Opciones avanzadas")
        else:
            self.advanced_frame.grid()
            self.btn_advanced.configure(text="▾  Opciones avanzadas")

    # ------------------------------------------------------------------
    # Carpetas
    # ------------------------------------------------------------------

    def seleccionar_entrada(self) -> None:
        folder = filedialog.askdirectory(
            initialdir=str(self.carpeta_entrada), title="Selecciona la carpeta de entrada"
        )
        if folder:
            self.carpeta_entrada = Path(folder)
            self.lbl_input.configure(text=short_path(self.carpeta_entrada, 48))
            ToolTip(self.lbl_input, str(self.carpeta_entrada))
            self._save_settings_from_ui()
            self.log(f"📥 Carpeta de entrada: {self.carpeta_entrada}", "info")

    def seleccionar_salida(self) -> None:
        folder = filedialog.askdirectory(
            initialdir=str(self.carpeta_salida), title="Selecciona la carpeta de salida"
        )
        if folder:
            self.carpeta_salida = Path(folder)
            self.lbl_output.configure(text=short_path(self.carpeta_salida, 48))
            ToolTip(self.lbl_output, str(self.carpeta_salida))
            self._save_settings_from_ui()
            self.log(f"📤 Carpeta de salida: {self.carpeta_salida}", "info")

    def _open_output(self) -> None:
        try:
            self.carpeta_salida.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        open_path(self.carpeta_salida)

    # ------------------------------------------------------------------
    # Cola
    # ------------------------------------------------------------------

    def _path_key(self, path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path).casefold()

    def _collect_files(self, paths: list[Path], recursive: bool) -> list[Path]:
        collected: list[Path] = []
        for path in paths:
            path = Path(path)
            if path.is_dir():
                pattern = "**/*" if recursive else "*"
                try:
                    candidates = list(path.glob(pattern))
                except OSError:
                    candidates = []
                collected.extend(
                    item for item in candidates if item.is_file() and item.suffix.lower() in SUPPORTED_INPUTS
                )
            elif path.is_file() and path.suffix.lower() in SUPPORTED_INPUTS:
                collected.append(path)
        return collected

    def _add_paths(self, paths: list[Path], *, recursive: bool | None = None, notify: bool = True) -> int:
        if self.running:
            self.toast.show("Espera a que termine la conversión", "warn")
            return 0
        if recursive is None:
            recursive = bool(self.var_recursive.get())
        files = self._collect_files(paths, recursive)
        added = 0
        ignored = 0
        for path in files:
            key = self._path_key(path)
            if key in self._queue_keys:
                ignored += 1
                continue
            self._queue_keys.add(key)
            self.queue.append(path)
            row = FileRow(
                self.queue_list,
                path,
                on_remove=self._remove_path,
                on_open=lambda p: open_path(p),
            )
            row.grid(row=len(self.queue) - 1, column=0, sticky="ew", pady=3)
            self.rows[path] = row
            added += 1

        if added:
            self.empty_state.grid_remove()
            self._update_queue_count()
            self.log(f"➕ {added} archivo(s) añadido(s) a la cola.", "success")
            if notify:
                self.toast.show(f"{added} archivo(s) añadido(s)", "success")
        elif files and ignored:
            self.log(f"ℹ️ {ignored} archivo(s) ya estaban en la cola.", "info")
        elif notify:
            self.log("⚠️ No se encontraron archivos compatibles.", "warn")
            self.toast.show("No se encontraron archivos compatibles", "warn")
        return added

    def _add_files_dialog(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_INPUTS))
        files = filedialog.askopenfilenames(
            title="Selecciona archivos para convertir",
            initialdir=str(self.carpeta_entrada),
            filetypes=[
                ("Documentos admitidos", patterns),
                ("PDF", "*.pdf"),
                ("Imágenes", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
                ("Word", "*.docx"),
                ("Texto y web", "*.txt *.md *.markdown *.html *.htm"),
                ("Hojas de cálculo", "*.csv *.xlsx"),
                ("EPUB", "*.epub"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if files:
            self._add_paths([Path(item) for item in files])

    def _add_folder_dialog(self) -> None:
        folder = filedialog.askdirectory(
            initialdir=str(self.carpeta_entrada), title="Selecciona una carpeta para añadir"
        )
        if not folder:
            return
        path = Path(folder)
        self.carpeta_entrada = path
        self.lbl_input.configure(text=short_path(path, 48))
        ToolTip(self.lbl_input, str(path))
        self._save_settings_from_ui()
        self._add_paths([path], recursive=bool(self.var_recursive.get()))

    def _remove_path(self, path: Path) -> None:
        if self.running:
            return
        row = self.rows.pop(path, None)
        if row is not None:
            row.destroy()
        try:
            self.queue.remove(path)
        except ValueError:
            pass
        self._queue_keys.discard(self._path_key(path))
        self._relayout_queue()
        self._update_queue_count()

    def _relayout_queue(self) -> None:
        for index, path in enumerate(self.queue):
            row = self.rows.get(path)
            if row is not None:
                row.grid_configure(row=index)
        if not self.queue:
            self.empty_state.grid(row=0, column=0, sticky="nsew", pady=26)

    def _clear_queue(self) -> None:
        if self.running:
            self.toast.show("No puedes vaciar la cola mientras se procesa", "warn")
            return
        for row in self.rows.values():
            row.destroy()
        self.rows.clear()
        self.queue.clear()
        self._queue_keys.clear()
        self.empty_state.grid(row=0, column=0, sticky="nsew", pady=26)
        self._update_queue_count()
        self.log("🗑 Cola vaciada.", "info")

    def _update_queue_count(self) -> None:
        count = len(self.queue)
        self.lbl_queue_count.configure(text=str(count))
        if count:
            self.empty_state.grid_remove()
        else:
            self.empty_state.grid(row=0, column=0, sticky="nsew", pady=26)

    def _reset_rows(self) -> None:
        for row in self.rows.values():
            row.set_status("pending")

    def _set_row_status(self, path: Path, status: str, detail: str | None = None) -> None:
        row = self.rows.get(path)
        if row:
            row.set_status(status, detail)

    # ------------------------------------------------------------------
    # Conversión
    # ------------------------------------------------------------------

    def iniciar_proceso(self) -> None:
        if self.running:
            return
        self._save_settings_from_ui()

        if not self.config.formats:
            messagebox.showwarning("Sin formatos", "Selecciona al menos un formato de salida.")
            return
        try:
            self.carpeta_salida.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Carpeta de salida", f"No se pudo crear la carpeta:\n{exc}")
            return

        if not self.queue:
            self.log(f"📂 Buscando archivos en {self.carpeta_entrada}…", "info")
            self._add_paths([self.carpeta_entrada], recursive=bool(self.var_recursive.get()), notify=False)
        if not self.queue:
            messagebox.showwarning(
                "Sin archivos",
                "La cola está vacía y no se encontraron archivos compatibles en la carpeta de entrada.\n\n"
                "Añade archivos con el botón ＋ o arrástralos a la ventana.",
            )
            return

        run_config = AppConfig.from_dict(self.config.to_dict())
        files = list(self.queue)
        self.stats = {"ok": 0, "error": 0, "cancelled": 0, "words": 0, "outputs": 0}
        self.progress_total = len(files)
        self.progress_done = 0
        self.run_started = time.time()
        self.stop_event.clear()
        self._reset_rows()
        self._update_progress(0, "Preparando conversión…")
        self._set_stats()
        self.log("-" * 56, "info")
        self.log(
            f"🚀 Iniciando {len(files)} archivo(s) · {len(run_config.formats)} formato(s)",
            "accent",
        )
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._worker,
            args=(files, run_config),
            daemon=True,
        )
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        self.running = running
        if running:
            self.btn_start.configure(state="disabled", text="⏳  Procesando…")
            self.btn_stop.configure(state="normal")
            self.btn_clear_queue.configure(state="disabled")
            for widget in self._run_lock_widgets:
                try:
                    widget.configure(state="disabled")
                except Exception:
                    pass
            for row in self.rows.values():
                row.set_enabled_remove(False)
            self._tick_timer()
        else:
            self.btn_start.configure(state="normal", text="▶   Iniciar conversión")
            self.btn_stop.configure(state="disabled")
            self.btn_clear_queue.configure(state="normal")
            for widget in self._run_lock_widgets:
                try:
                    widget.configure(state="normal")
                except Exception:
                    pass
            for row in self.rows.values():
                row.set_enabled_remove(True)
            self._refresh_ocr_controls()

    def _worker(self, files: list[Path], run_config: AppConfig) -> None:
        if not self._tools_ready:
            try:
                self.tools = detect_tools()
                self._tools_ready = True
                self.after(0, lambda: self._on_tools_detected(self.tools, quiet=True))
            except Exception:
                pass

        converter = Converter(
            run_config,
            tools=self.tools,
            log=self.log,
            stop_event=self.stop_event,
        )
        started = time.time()
        for index, path in enumerate(files, start=1):
            if self.stop_event.is_set():
                break
            self.after(0, self._set_row_status, path, "processing")
            self.after(0, self._update_progress, index - 1, f"Procesando {path.name}…")
            result: JobResult = converter.convert(path)
            if result.status == "done":
                self.stats["ok"] += 1
                self.stats["outputs"] += len(result.outputs)
                if result.document:
                    self.stats["words"] += result.document.word_count
                detail = f"{len(result.outputs)} archivo(s) · {result.duration:.1f}s"
                self.after(0, self._set_row_status, path, "done", detail)
            elif result.status == "cancelled":
                self.stats["cancelled"] += 1
                self.after(0, self._set_row_status, path, "cancelled")
            else:
                self.stats["error"] += 1
                detail = (result.error or "Error desconocido")[:46]
                self.after(0, self._set_row_status, path, "error", detail)
            self.after(0, self._update_progress, index, f"{index} de {len(files)} archivo(s)")
            self.after(0, self._set_stats)

        elapsed = time.time() - started
        self.after(0, lambda: self._finish(elapsed, len(files)))

    def _finish(self, elapsed: float, total: int) -> None:
        if self.stop_event.is_set():
            for path in self.queue:
                row = self.rows.get(path)
                if row and row.status.cget("text").strip().startswith(("•", "⏳")):
                    row.set_status("cancelled")
            self.lbl_eta.configure(text="Detenido por el usuario")
            self.log("⏹ Proceso detenido por el usuario.", "warn")
        else:
            self.lbl_eta.configure(text=f"Completado en {self._format_duration(elapsed)}")
            self.log(
                f"🎉 Proceso completado: {self.stats['ok']} correcto(s), "
                f"{self.stats['error']} error(es) · {self._format_duration(elapsed)}",
                "success" if self.stats["error"] == 0 else "warn",
            )
            if self.stats["outputs"]:
                self.log(f"📦 {self.stats['outputs']} archivo(s) generado(s).", "info")

        self.progress_done = min(total, self.stats["ok"] + self.stats["error"] + self.stats["cancelled"])
        if total and not self.stop_event.is_set():
            self.progress_bar.set(1.0)
        self.lbl_progress_main.configure(
            text=f"{self.stats['ok'] + self.stats['error'] + self.stats['cancelled']} / {total} archivos"
        )
        self._set_running(False)

        if self.stats["ok"] and self.stats["error"]:
            self.toast.show(
                f"Completado con {self.stats['error']} error(es)", "warn", 4200
            )
        elif self.stats["ok"]:
            self.toast.show(
                f"¡Listo! {self.stats['ok']} archivo(s) convertido(s)", "success", 3800
            )
        elif self.stats["error"]:
            self.toast.show("No se pudo convertir ningún archivo", "error", 4200)

        if self.var_open_output.get() and self.stats["ok"]:
            self.after(400, self._open_output)

    def detener_proceso(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.btn_stop.configure(state="disabled")
        self.lbl_eta.configure(text="Deteniendo…")
        self.log("⏹ Deteniendo… se terminará el archivo actual.", "warn")
        self.toast.show("Deteniendo el proceso…", "warn")

    def _update_progress(self, done: int, detail: str) -> None:
        self.progress_done = done
        total = max(1, self.progress_total)
        self.progress_bar.set(min(1.0, done / total))
        self.lbl_progress_main.configure(text=f"{done} / {self.progress_total} archivos")
        self.lbl_eta.configure(text=detail)

    def _tick_timer(self) -> None:
        if not self.running:
            return
        elapsed = time.time() - self.run_started
        done = max(1, self.progress_done)
        remaining = max(0.0, (elapsed / done) * (self.progress_total - self.progress_done))
        text = f"⏱ {self._format_duration(elapsed)}"
        if self.progress_done:
            text += f" · ~{self._format_duration(remaining)} restante"
        self.lbl_eta.configure(text=text)
        self.after(1000, self._tick_timer)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m"

    def _set_stats(self) -> None:
        self.lbl_stat_ok.configure(text=f"✅ {self.stats['ok']}")
        self.lbl_stat_err.configure(text=f"❌ {self.stats['error']}")
        self.lbl_stat_words.configure(text=f"📝 {self.stats['words']:,} palabras")

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    def log(self, message: str, level: str = "info") -> None:
        if threading.current_thread() is threading.main_thread():
            self._append_log(message, level)
        else:
            self.after(0, self._append_log, message, level)

    def _append_log(self, message: str, level: str = "info") -> None:
        if not hasattr(self, "log_box"):
            return
        timestamp = time.strftime("%H:%M:%S")
        try:
            widget = getattr(self.log_box, "_textbox", self.log_box)
            widget.configure(state="normal")
            widget.insert("end", f"[{timestamp}] {message}\n", level)
            try:
                line_count = int(widget.index("end-1c").split(".")[0])
                if line_count > 2500:
                    widget.delete("1.0", "500.0")
            except Exception:
                pass
            widget.see("end")
            widget.configure(state="disabled")
        except Exception:
            pass

    def _clear_log(self) -> None:
        try:
            widget = getattr(self.log_box, "_textbox", self.log_box)
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")
            self._append_log("Registro limpio.", "info")
        except Exception:
            pass

    def _save_log(self) -> None:
        try:
            widget = getattr(self.log_box, "_textbox", self.log_box)
            content = widget.get("1.0", "end-1c")
        except Exception:
            content = ""
        path = filedialog.asksaveasfilename(
            title="Guardar registro",
            defaultextension=".txt",
            initialfile="conversor_registro.txt",
            filetypes=[("Texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(content, encoding="utf-8")
            self.toast.show("Registro guardado", "success")
        except OSError as exc:
            messagebox.showerror("Guardar registro", f"No se pudo guardar:\n{exc}")

    # ------------------------------------------------------------------
    # Herramientas OCR
    # ------------------------------------------------------------------

    def _detect_tools_bg(self) -> None:
        if self._tools_detecting:
            return
        self._tools_detecting = True
        try:
            self.btn_detect.configure(state="disabled", text="⏳  Detectando…")
            self.btn_refresh_tools.configure(state="disabled")
        except Exception:
            pass
        self.tool_status_label.configure(text="Detectando OCR…")
        self.tool_detail_label.configure(text="Comprobando herramientas")
        self.tool_dot.configure(text_color=theme.AMBER)
        threading.Thread(target=self._detect_tools_worker, daemon=True).start()

    def _detect_tools_worker(self) -> None:
        try:
            status = detect_tools()
        except Exception:
            status = ToolStatus()
        self.after(0, lambda: self._on_tools_detected(status))

    def _on_tools_detected(self, status: ToolStatus, quiet: bool = False) -> None:
        self.tools = status
        self._tools_ready = True
        self._tools_detecting = False
        try:
            self.btn_detect.configure(state="normal", text="🔄  Volver a detectar")
            self.btn_refresh_tools.configure(state="normal")
        except Exception:
            pass

        if status.can_ocr_pdf and status.can_ocr_images:
            dot, title, detail = theme.GREEN, "OCR listo", "PDF + imágenes"
        elif status.can_ocr_images:
            dot, title, detail = theme.AMBER, "OCR parcial", "Solo imágenes"
        elif status.tesseract:
            dot, title, detail = theme.AMBER, "OCR parcial", "Falta Ghostscript u OCRmyPDF"
        else:
            dot, title, detail = theme.RED, "OCR no disponible", "Instala Tesseract + Ghostscript"

        self.tool_dot.configure(text_color=dot)
        self.tool_status_label.configure(text=title)
        self.tool_detail_label.configure(text=detail)

        values = {
            "ocrmypdf": (
                f"v{status.ocrmypdf}" if status.ocrmypdf else "No instalado",
                "OCRmyPDF es el motor principal para PDF.",
            ),
            "tesseract": (
                status.tesseract_version or ("Detectado" if status.tesseract else "No encontrado"),
                status.tesseract or "Instala Tesseract-OCR y añádelo al PATH.",
            ),
            "ghostscript": (
                "Detectado" if status.ghostscript else "No encontrado",
                status.ghostscript or "Ghostscript es necesario para OCRmyPDF en Windows.",
            ),
            "pytesseract": (
                "Instalado" if status.pytesseract else "No instalado",
                "Permite leer imágenes directamente con Tesseract.",
            ),
            "languages": (
                f"{len(status.languages)} idioma(s)" if status.languages else "No detectados",
                ", ".join(status.languages) if status.languages else "Sin paquetes de idioma.",
            ),
        }
        for key, (value_text, detail_text) in values.items():
            if key not in self._tool_rows:
                continue
            value_label, detail_label = self._tool_rows[key]
            value_label.configure(
                text=value_text,
                text_color=theme.GREEN if value_text not in {"No instalado", "No encontrado", "No detectados"} else theme.RED,
            )
            detail_label.configure(text=detail_text)

        if not quiet:
            self.log(f"🔧 {status.summary}", "success" if status.can_ocr_pdf else "warn")
            if not status.can_ocr_pdf:
                missing = []
                if not status.ocrmypdf:
                    missing.append("OCRmyPDF (pip install ocrmypdf)")
                if not status.tesseract:
                    missing.append("Tesseract-OCR")
                if not status.ghostscript:
                    missing.append("Ghostscript")
                if missing:
                    self.log("   Instala: " + " · ".join(missing), "warn")

    def _copy_diagnostics(self) -> None:
        status = self.tools
        text = (
            f"{APP_NAME} {__version__}\n"
            f"Python: {__import__('sys').version}\n"
            f"OCRmyPDF: {status.ocrmypdf or 'no'}\n"
            f"Tesseract: {status.tesseract or 'no'}\n"
            f"Tesseract versión: {status.tesseract_version or 'no'}\n"
            f"Ghostscript: {status.ghostscript or 'no'}\n"
            f"pytesseract: {status.pytesseract}\n"
            f"Idiomas: {', '.join(status.languages or []) or 'no'}\n"
            f"Config: {config_path()}\n"
        )
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.toast.show("Diagnóstico copiado al portapapeles", "success")
        except Exception:
            messagebox.showerror("Copiar diagnóstico", "No se pudo copiar al portapapeles.")

    # ------------------------------------------------------------------
    # Arrastrar y soltar / atajos / cierre
    # ------------------------------------------------------------------

    def _register_dnd(self) -> None:
        if not getattr(self, "dnd_ready", False):
            return
        try:
            self.register_drop(self._on_drop_paths)
            self.lbl_action_hint.configure(
                text="Consejo: arrastra archivos a la ventana o usa Ctrl+O."
            )
        except Exception:
            pass

    def _on_drop_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        self._set_page("convert")
        self._add_paths(paths, recursive=bool(self.var_recursive.get()))

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self._add_files_dialog())
        self.bind_all("<Control-O>", lambda _e: self._add_folder_dialog())
        self.bind_all("<Control-Shift-O>", lambda _e: self._add_folder_dialog())
        self.bind_all("<F5>", lambda _e: self.iniciar_proceso())
        self.bind_all("<Escape>", lambda _e: self.detener_proceso())
        self.bind_all("<Control-l>", lambda _e: self._clear_log())
        self.bind_all("<Control-q>", lambda _e: self._on_close())

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(
                "Conversión en curso",
                "Hay una conversión en curso. ¿Quieres detenerla y salir?",
            ):
                return
            self.stop_event.set()
        self._save_settings_from_ui()
        try:
            self.destroy()
        except Exception:
            pass


def run() -> None:
    app = ConversorApp()
    app.mainloop()

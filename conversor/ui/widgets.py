"""Widgets reutilizables de la interfaz."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from . import theme

try:  # pragma: no cover - depende de tkdnd
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DnDWrapper = TkinterDnD.DnDWrapper
    DND_AVAILABLE = True
except Exception:  # pragma: no cover
    DND_FILES = "DND_Files"
    DnDWrapper = object  # type: ignore
    TkinterDnD = None  # type: ignore
    DND_AVAILABLE = False


def open_path(path: Path) -> None:
    """Abre una carpeta o archivo con la aplicación del sistema."""
    path = Path(path)
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def short_path(path: Path, limit: int = 46) -> str:
    text = str(path)
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1):]


class ToolTip:
    """Tooltip sencillo para widgets CustomTkinter."""

    def __init__(self, widget, text: str, delay: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._window: ctk.CTkToplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._window or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        window = ctk.CTkToplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        window.attributes("-topmost", True)
        label = ctk.CTkLabel(
            window,
            text=self.text,
            font=theme.font(11),
            fg_color=("#0f172a", "#e2e8f0"),
            text_color=("#ffffff", "#0f172a"),
            corner_radius=6,
            padx=8,
            pady=4,
            justify="left",
            wraplength=420,
        )
        label.pack()
        self._window = window

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None


class Card(ctk.CTkFrame):
    """Tarjeta con título, subtítulo y cuerpo."""

    def __init__(
        self,
        master,
        title: str,
        subtitle: str | None = None,
        *,
        icon: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color=theme.CARD_BG,
            border_width=1,
            border_color=theme.CARD_BORDER,
            **kwargs,
        )
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent", height=1, width=1)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        header.grid_columnconfigure(1, weight=1)

        if icon:
            ctk.CTkLabel(
                header, text=icon, font=theme.font(17), width=24
            ).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 8))

        self.title_label = ctk.CTkLabel(
            header, text=title, font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w"
        )
        self.title_label.grid(row=0, column=1, sticky="w")
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = ctk.CTkLabel(
                header,
                text=subtitle,
                font=theme.font(11),
                text_color=theme.MUTED,
                anchor="w",
                justify="left",
                wraplength=330,
            )
            self.subtitle_label.grid(row=1, column=1, sticky="w", pady=(1, 0))

        self.header_actions = ctk.CTkFrame(header, fg_color="transparent", height=1, width=1)
        self.header_actions.grid(row=0, column=2, rowspan=2, sticky="e")

        self.body = ctk.CTkFrame(self, fg_color="transparent", height=1, width=1)
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(10, 14))
        self.body.grid_columnconfigure(0, weight=1)


class FormatChip(ctk.CTkButton):
    """Botón seleccionable para un formato de salida."""

    ICONS = {
        "md": "📝",
        "txt": "📄",
        "html": "🌐",
        "docx": "📘",
        "json": "🧩",
        "csv": "📊",
        "epub": "📚",
        "pdf": "📕",
    }

    def __init__(
        self,
        master,
        format_id: str,
        name: str,
        extension: str,
        description: str,
        *,
        selected: bool,
        primary: str,
        hover: str,
        command: Callable[[str, bool], None],
    ) -> None:
        self.format_id = format_id
        self.name = name
        self.extension = extension
        self.description = description
        self.selected = selected
        self.primary = primary
        self.hover = hover
        self._callback = command
        super().__init__(
            master,
            text=f"{name}\n{extension}",
            font=theme.font(11, "bold"),
            height=54,
            corner_radius=10,
            border_width=1,
            border_spacing=8,
            anchor="w",
            command=self._toggle,
        )
        self.refresh()
        ToolTip(self, description)

    def _toggle(self) -> None:
        self._callback(self.format_id, not self.selected)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.refresh()

    def set_accent(self, primary: str, hover: str) -> None:
        self.primary = primary
        self.hover = hover
        self.refresh()

    def refresh(self) -> None:
        if self.selected:
            self.configure(
                fg_color=self.primary,
                hover_color=self.hover,
                border_color=self.primary,
                text_color="#ffffff",
            )
        else:
            self.configure(
                fg_color=theme.ROW_BG,
                hover_color=theme.SUBTLE,
                border_color=theme.ROW_BORDER,
                text_color=theme.TEXT,
            )


class FileRow(ctk.CTkFrame):
    """Fila de la cola con nombre, tamaño y estado."""

    ICONS = {
        ".pdf": "📕",
        ".png": "🖼️",
        ".jpg": "🖼️",
        ".jpeg": "🖼️",
        ".tif": "🖼️",
        ".tiff": "🖼️",
        ".bmp": "🖼️",
        ".webp": "🖼️",
        ".docx": "📘",
        ".txt": "📄",
        ".md": "📝",
        ".markdown": "📝",
        ".html": "🌐",
        ".htm": "🌐",
        ".csv": "📊",
        ".xlsx": "📊",
        ".epub": "📚",
    }

    STATUS = {
        "pending": ("•  Pendiente", theme.MUTED),
        "processing": ("⏳  Procesando", theme.BLUE),
        "done": ("✅  Listo", theme.GREEN),
        "error": ("❌  Error", theme.RED),
        "cancelled": ("⏹  Cancelado", theme.AMBER),
    }

    def __init__(
        self,
        master,
        path: Path,
        *,
        on_remove: Callable[[Path], None],
        on_open: Callable[[Path], None],
    ) -> None:
        super().__init__(
            master,
            corner_radius=9,
            fg_color=theme.ROW_BG,
            border_width=1,
            border_color=theme.ROW_BORDER,
        )
        self.path = path
        self._on_remove = on_remove
        self.grid_columnconfigure(1, weight=1)

        icon = ctk.CTkLabel(
            self, text=self.ICONS.get(path.suffix.lower(), "📄"), font=theme.font(20), width=32
        )
        icon.grid(row=0, column=0, rowspan=2, padx=(10, 6), pady=8)

        name = ctk.CTkLabel(
            self,
            text=path.name,
            font=theme.font(12, "bold"),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
        )
        name.grid(row=0, column=1, sticky="ew", pady=(8, 0))
        try:
            size_text = human_size(path.stat().st_size)
        except OSError:
            size_text = "—"
        self.info = ctk.CTkLabel(
            self,
            text=f"{path.suffix.upper().lstrip('.')} · {size_text}",
            font=theme.font(10),
            text_color=theme.MUTED,
            anchor="w",
        )
        self.info.grid(row=1, column=1, sticky="ew", pady=(0, 8))

        self.status = ctk.CTkLabel(
            self,
            text=self.STATUS["pending"][0],
            font=theme.font(10, "bold"),
            text_color=self.STATUS["pending"][1],
            width=95,
            anchor="e",
        )
        self.status.grid(row=0, column=2, rowspan=2, padx=(4, 2))

        remove = ctk.CTkButton(
            self,
            text="✕",
            width=26,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=("#fee2e2", "#3f1d24"),
            text_color=theme.MUTED,
            font=theme.font(11),
            command=lambda: self._on_remove(self.path),
        )
        remove.grid(row=0, column=3, rowspan=2, padx=(0, 6))
        ToolTip(remove, "Quitar de la cola")

        for widget in (self, name, icon, self.info):
            widget.bind("<Double-Button-1>", lambda _e: self._on_open(self.path), add="+")

        self._name = name
        self._icon = icon
        self._remove = remove

    def set_status(self, status: str, detail: str | None = None) -> None:
        text, color = self.STATUS.get(status, self.STATUS["pending"])
        self.status.configure(text=text, text_color=color)
        if detail:
            self.info.configure(text=detail)
        elif status == "done":
            self.info.configure(text=f"{self.path.suffix.upper().lstrip('.')} · convertido")
        elif status == "pending":
            try:
                self.info.configure(
                    text=f"{self.path.suffix.upper().lstrip('.')} · {human_size(self.path.stat().st_size)}"
                )
            except OSError:
                pass

    def set_enabled_remove(self, enabled: bool) -> None:
        self._remove.configure(state="normal" if enabled else "disabled")


class Toast(ctk.CTkLabel):
    """Mensaje flotante no intrusivo."""

    def __init__(self, master) -> None:
        super().__init__(
            master,
            text="",
            font=theme.font(12, "bold"),
            fg_color=("#0f172a", "#e2e8f0"),
            text_color=("#ffffff", "#0f172a"),
            corner_radius=10,
            padx=16,
            pady=10,
        )
        self._after_id = None

    def show(self, message: str, kind: str = "info", duration: int = 3200) -> None:
        colors = {
            "info": ("#2563eb", "#60a5fa"),
            "success": ("#059669", "#34d399"),
            "error": ("#dc2626", "#f87171"),
            "warn": ("#d97706", "#fbbf24"),
        }
        fg = colors.get(kind, colors["info"])
        self.configure(text=message, fg_color=fg, text_color="#ffffff")
        self.place(relx=0.985, rely=0.965, anchor="se")
        self.lift()
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = self.after(duration, self._hide)

    def _hide(self) -> None:
        self.place_forget()
        self._after_id = None


class BaseWindow(ctk.CTk, DnDWrapper):  # type: ignore[misc]
    """CTk con soporte opcional de arrastrar y soltar."""

    def __init__(self, *args, **kwargs) -> None:
        ctk.CTk.__init__(self, *args, **kwargs)
        self.dnd_ready = False
        if DND_AVAILABLE and TkinterDnD is not None:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd_ready = True
            except Exception:
                self.dnd_ready = False

    def register_drop(self, callback: Callable[[list[Path]], None]) -> None:
        if not self.dnd_ready:
            return
        try:
            self.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.dnd_bind(  # type: ignore[attr-defined]
                "<<Drop>>",
                lambda event: callback(
                    [Path(item) for item in self.tk.splitlist(event.data)]
                ),
            )
        except Exception:
            self.dnd_ready = False

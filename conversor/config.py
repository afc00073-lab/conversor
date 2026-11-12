"""Configuración persistente y constantes de la aplicación."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "Conversor Pro"
APP_VERSION = "2.0.0"
GITHUB_URL = "https://github.com/afc00073-lab/conversor"

# ---------------------------------------------------------------------------
# Formatos
# ---------------------------------------------------------------------------

#: extensiones de entrada admitidas -> nombre visible
SUPPORTED_INPUTS: dict[str, str] = {
    ".pdf": "PDF",
    ".png": "Imagen PNG",
    ".jpg": "Imagen JPEG",
    ".jpeg": "Imagen JPEG",
    ".tif": "Imagen TIFF",
    ".tiff": "Imagen TIFF",
    ".bmp": "Imagen BMP",
    ".webp": "Imagen WebP",
    ".docx": "Word",
    ".txt": "Texto",
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".html": "HTML",
    ".htm": "HTML",
    ".csv": "CSV",
    ".xlsx": "Excel",
    ".epub": "EPUB",
}

IMAGE_INPUTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

#: id, nombre, extensión, descripción
OUTPUT_FORMATS: list[tuple[str, str, str, str]] = [
    ("md", "Markdown", ".md", "Texto con títulos, listas y tablas. Ideal para notas y LLM."),
    ("txt", "Texto plano", ".txt", "Texto limpio sin marcas de formato."),
    ("html", "HTML", ".html", "Página web autocontenida, con estilos y tabla de contenido."),
    ("docx", "Word", ".docx", "Documento de Word con estilos, listas y tablas."),
    ("json", "JSON", ".json", "Estructura completa: metadatos, bloques y páginas."),
    ("csv", "CSV", ".csv", "Texto por página, compatible con Excel."),
    ("epub", "EPUB", ".epub", "Libro electrónico para lectores como Kindle o Calibre."),
    ("pdf", "PDF", ".pdf", "PDF con OCR (o PDF generado desde el documento)."),
]

OUTPUT_BY_ID = {item[0]: item for item in OUTPUT_FORMATS}

#: idioma visible -> código tesseract
OCR_LANGUAGES: list[tuple[str, str]] = [
    ("Español", "spa"),
    ("Inglés", "eng"),
    ("Español + Inglés", "spa+eng"),
    ("Portugués", "por"),
    ("Francés", "fra"),
    ("Alemán", "deu"),
    ("Italiano", "ita"),
    ("Neerlandés", "nld"),
    ("Ruso", "rus"),
    ("Árabe", "ara"),
    ("Chino simplificado", "chi_sim"),
    ("Japonés", "jpn"),
    ("Coreano", "kor"),
    ("Turco", "tur"),
    ("Polaco", "pol"),
]

LANGUAGE_LABEL = {code: label for label, code in OCR_LANGUAGES}

#: nombre -> (primario, hover, texto sobre el color)
ACCENTS: dict[str, tuple[str, str]] = {
    "blue": ("#3b82f6", "#2563eb"),
    "emerald": ("#10b981", "#059669"),
    "violet": ("#8b5cf6", "#7c3aed"),
    "rose": ("#f43f5e", "#e11d48"),
    "amber": ("#f59e0b", "#d97706"),
    "cyan": ("#06b6d4", "#0891b2"),
}

ACCENT_LABELS = {
    "blue": "Azul",
    "emerald": "Esmeralda",
    "violet": "Violeta",
    "rose": "Rosa",
    "amber": "Ámbar",
    "cyan": "Cian",
}


def _desktop_dir() -> Path:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Escritorio",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "Escritorio",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path.cwd()


def default_input_dir() -> Path:
    return _desktop_dir() / "PDFAMD"


def default_output_dir() -> Path:
    return _desktop_dir() / "MD_EXPORTADOS"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        path = Path(base) / "ConversorPro"
    else:
        path = Path.home() / ".config" / "conversor-pro"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    input_dir: str = field(default_factory=lambda: str(default_input_dir()))
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    formats: list[str] = field(default_factory=lambda: ["md"])
    ocr_enabled: bool = True
    ocr_lang: str = "spa"
    force_ocr: bool = True
    deskew: bool = True
    rotate_pages: bool = False
    optimize: int = 1
    jobs: int = 2
    page_range: str = ""
    keep_pages: bool = False
    include_title: bool = True
    recursive: bool = False
    overwrite: bool = True
    open_output: bool = False
    theme: str = "System"
    accent: str = "blue"
    geometry: str = ""
    last_files: list[str] = field(default_factory=list)

    # -- validación / serialización -----------------------------------------

    def __post_init__(self) -> None:
        valid_formats = [f for f in self.formats if f in OUTPUT_BY_ID]
        self.formats = valid_formats or ["md"]
        if self.ocr_lang not in LANGUAGE_LABEL:
            self.ocr_lang = "spa"
        if self.theme not in {"System", "Light", "Dark"}:
            self.theme = "System"
        if self.accent not in ACCENTS:
            self.accent = "blue"
        self.optimize = max(0, min(3, int(self.optimize)))
        self.jobs = max(1, min(8, int(self.jobs)))
        self.keep_pages = bool(self.keep_pages)
        self.include_title = bool(self.include_title)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        try:
            return cls(**clean)
        except (TypeError, ValueError):
            return cls()

    def ensure_dirs(self) -> None:
        Path(self.input_dir).mkdir(parents=True, exist_ok=True)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg = AppConfig.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError):
            cfg = AppConfig()
    else:
        cfg = AppConfig()

    # Si las carpetas guardadas ya no existen, volvemos a las del escritorio.
    if not Path(cfg.input_dir).parent.exists():
        cfg.input_dir = str(default_input_dir())
    if not Path(cfg.output_dir).parent.exists():
        cfg.output_dir = str(default_output_dir())

    try:
        cfg.ensure_dirs()
    except OSError:
        pass
    return cfg


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        # La configuración no es crítica: nunca debe cerrar la aplicación.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def accent_colors(name: str) -> tuple[str, str]:
    return ACCENTS.get(name, ACCENTS["blue"])

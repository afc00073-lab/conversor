"""Detección y ejecución de OCR (OCRmyPDF, Tesseract y Ghostscript)."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LogFn = Callable[[str, str], None]


class OcrError(RuntimeError):
    """Error controlado del motor OCR."""


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run_quiet(args: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def _first_existing(candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        str(Path(program_files) / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(program_files_x86) / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(local) / "Tesseract-OCR" / "tesseract.exe"),
        str(Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tesseract.exe"),
    ]
    if sys.platform == "darwin":
        candidates.extend(["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"])
    elif sys.platform.startswith("linux"):
        candidates.extend(["/usr/bin/tesseract", "/usr/local/bin/tesseract"])
    return _first_existing(candidates)


def find_ghostscript() -> str | None:
    found = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")
    if found:
        return found
    candidates: list[str] = []
    if os.name == "nt":
        for root in (r"C:\Program Files", r"C:\Program Files (x86)"):
            candidates.extend(glob.glob(str(Path(root) / "gs" / "*" / "bin" / "gswin64c.exe")))
            candidates.extend(glob.glob(str(Path(root) / "gs" / "*" / "bin" / "gswin32c.exe")))
            candidates.append(str(Path(root) / "Ghostscript" / "bin" / "gswin64c.exe"))
    else:
        candidates.extend(["/usr/bin/gs", "/usr/local/bin/gs", "/opt/homebrew/bin/gs"])
    candidates.sort(reverse=True)
    return _first_existing(candidates)


def configure_tesseract() -> str | None:
    """Configura pytesseract si está instalado."""
    path = find_tesseract()
    if path:
        os.environ.setdefault("TESSERACT_CMD", path)
        try:
            import pytesseract  # type: ignore

            pytesseract.pytesseract.tesseract_cmd = path
        except Exception:
            pass
    return path


@dataclass
class ToolStatus:
    ocrmypdf: str | None = None
    tesseract: str | None = None
    tesseract_version: str | None = None
    ghostscript: str | None = None
    pytesseract: bool = False
    languages: list[str] | None = None

    @property
    def can_ocr_pdf(self) -> bool:
        return bool(self.ocrmypdf and self.tesseract and self.ghostscript)

    @property
    def can_ocr_images(self) -> bool:
        return bool(self.pytesseract and self.tesseract)

    @property
    def summary(self) -> str:
        if self.can_ocr_pdf and self.can_ocr_images:
            return "OCR completo disponible"
        if self.can_ocr_images:
            return "OCR de imágenes disponible"
        if self.tesseract:
            return "Tesseract detectado (falta OCRmyPDF/Ghostscript)"
        return "OCR no disponible"


def detect_tools(refresh: bool = True) -> ToolStatus:
    status = ToolStatus()
    configure_tesseract()

    try:
        import ocrmypdf  # type: ignore

        status.ocrmypdf = getattr(ocrmypdf, "__version__", "instalado")
    except Exception:
        status.ocrmypdf = None

    status.tesseract = find_tesseract()
    if status.tesseract:
        ok, output = _run_quiet([status.tesseract, "--version"])
        if ok and output:
            status.tesseract_version = output.splitlines()[0].strip()

    status.ghostscript = find_ghostscript()

    try:
        import pytesseract  # type: ignore  # noqa: F401

        status.pytesseract = True
    except Exception:
        status.pytesseract = False

    if status.tesseract:
        ok, output = _run_quiet([status.tesseract, "--list-langs"])
        if ok:
            langs = [
                line.strip()
                for line in output.splitlines()
                if line.strip() and not line.lower().startswith("list of available")
            ]
            status.languages = langs
    return status


def _tesseract_language(lang: str, available: list[str] | None) -> str:
    if not available:
        return lang
    requested = [part for part in lang.split("+") if part]
    usable = [part for part in requested if part in available]
    return "+".join(usable) if usable else ("eng" if "eng" in available else lang)


def ocr_pdf(
    source: Path,
    destination: Path,
    *,
    language: str = "spa",
    force: bool = True,
    deskew: bool = True,
    rotate: bool = False,
    optimize: int = 1,
    jobs: int = 2,
    log: LogFn | None = None,
) -> Path:
    """Aplica OCR a un PDF y devuelve el PDF resultante."""
    try:
        import ocrmypdf  # type: ignore
    except Exception as exc:  # pragma: no cover - depende del entorno
        raise OcrError(
            "OCRmyPDF no está instalado. Ejecuta: pip install ocrmypdf"
        ) from exc

    if find_tesseract() is None:
        raise OcrError(
            "No se encontró Tesseract. Instálalo y añádelo al PATH para aplicar OCR."
        )
    if find_ghostscript() is None:
        raise OcrError(
            "No se encontró Ghostscript, necesario para OCRmyPDF en Windows. "
            "Instálalo desde ghostscript.com."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {
        "language": language,
        "deskew": deskew,
        "rotate_pages": rotate,
        "optimize": max(0, min(3, optimize)),
        "jobs": max(1, min(8, jobs)),
        "progress_bar": False,
        "output_file": str(destination),
    }
    if force:
        kwargs["force_ocr"] = True
    else:
        kwargs["skip_text"] = True

    if log:
        log(f"   🔍 OCRmyPDF · idioma={language} · forzar={force}", "info")
    try:
        result = ocrmypdf.ocr(str(source), **kwargs)
    except Exception as exc:  # OCRmyPDF lanza varias excepciones propias
        message = str(exc).strip() or exc.__class__.__name__
        raise OcrError(f"OCRmyPDF falló: {message}") from exc

    if result not in (0, None) and int(result) != 0:
        raise OcrError(f"OCRmyPDF terminó con código {result}")
    if not destination.exists():
        raise OcrError("OCRmyPDF no generó el PDF de salida.")
    return destination


def ocr_image(
    source: Path,
    *,
    language: str = "spa",
    log: LogFn | None = None,
) -> str:
    """Extrae texto de una imagen con Tesseract."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover
        raise OcrError(
            "Faltan dependencias de OCR de imágenes (pytesseract y Pillow)."
        ) from exc

    tesseract = configure_tesseract()
    if not tesseract:
        raise OcrError(
            "No se encontró Tesseract. Instálalo para poder leer imágenes."
        )

    status_langs = None
    try:
        status_langs = pytesseract.get_languages(config="")
    except Exception:
        pass
    lang = _tesseract_language(language, status_langs)

    if log:
        log(f"   🔍 Tesseract · idioma={lang}", "info")

    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("L", "RGB"):
                image = image.convert("RGB")
            # Mejora suave para documentos escaneados.
            image = ImageOps.autocontrast(image.convert("L"))
            width, height = image.size
            if width < 1000:
                scale = max(1.0, 1400 / max(width, 1))
                image = image.resize((int(width * scale), int(height * scale)))
            text = pytesseract.image_to_string(image, lang=lang)
    except Exception as exc:
        # Reintento con inglés si el paquete de idioma no está instalado.
        try:
            with Image.open(source) as image:
                text = pytesseract.image_to_string(image.convert("RGB"), lang="eng")
        except Exception:
            raise OcrError(f"Tesseract no pudo leer la imagen: {exc}") from exc

    return text.strip()


def image_to_pdf(source: Path, destination: Path) -> Path:
    """Convierte una imagen (o TIFF multipágina) en PDF."""
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        raise OcrError("Pillow no está instalado; no se puede crear el PDF.") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        frames = []
        try:
            while True:
                frame = image.copy()
                if frame.mode in ("RGBA", "LA", "P"):
                    frame = frame.convert("RGB")
                frames.append(frame)
                image.seek(image.tell() + 1)
        except EOFError:
            pass
        if not frames:
            frames = [image.convert("RGB")]
        first, rest = frames[0], frames[1:]
        if rest:
            first.save(destination, "PDF", resolution=300.0, save_all=True, append_images=rest)
        else:
            first.save(destination, "PDF", resolution=300.0)
    return destination

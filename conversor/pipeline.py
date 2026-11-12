"""Orquestación de la conversión: extraer, normalizar y renderizar."""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import IMAGE_INPUTS, OUTPUT_BY_ID, AppConfig
from .extract import ConversionError, ExtractOptions, extract_document
from .model import Document
from .ocr import OcrError, ToolStatus, image_to_pdf
from .render import (
    RenderError,
    RenderOptions,
    render_csv,
    render_docx,
    render_epub,
    render_html,
    render_json,
    render_markdown,
    render_pdf,
    render_text,
)

LogFn = Callable[[str, str], None]


class ConversionCancelled(RuntimeError):
    """El usuario detuvo el proceso."""


@dataclass
class JobResult:
    source: Path
    status: str
    outputs: list[Path] = field(default_factory=list)
    error: str | None = None
    document: Document | None = None
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "done"


def _safe_stem(stem: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in " ._-()[]" else "_" for char in stem
    ).strip(" ._")
    return cleaned or "documento"


def _effective_language(lang: str, tools: ToolStatus) -> str:
    available = tools.languages
    if not available:
        return lang
    requested = [part for part in lang.split("+") if part]
    usable = [part for part in requested if part in available]
    if usable:
        return "+".join(usable)
    return "eng" if "eng" in available else (available[0] if available else lang)


def _write_text(path: Path, content: str) -> Path:
    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise RenderError(f"No se pudo escribir {path.name}: {exc}") from exc
    return path


class Converter:
    """Convierte archivos individuales usando la configuración de la app."""

    def __init__(
        self,
        config: AppConfig,
        *,
        tools: ToolStatus | None = None,
        log: LogFn | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.tools = tools or ToolStatus()
        self.log = log or (lambda message, level="info": None)
        self.stop_event = stop_event or threading.Event()
        self.output_dir = Path(config.output_dir)
        self.render_options = RenderOptions(
            include_title=config.include_title,
            keep_pages=config.keep_pages,
        )

    # -- helpers ------------------------------------------------------------

    def _check_cancel(self) -> None:
        if self.stop_event.is_set():
            raise ConversionCancelled("Proceso detenido por el usuario.")

    def _destination(self, source: Path, extension: str) -> Path:
        base = _safe_stem(source.stem)
        if extension == ".pdf" and source.suffix.lower() == ".pdf":
            candidate = self.output_dir / f"{base}.pdf"
            try:
                if candidate.resolve() == source.resolve():
                    base = f"{base}_ocr"
            except OSError:
                pass
        candidate = self.output_dir / f"{base}{extension}"
        if self.config.overwrite or not candidate.exists():
            return candidate
        for index in range(2, 1000):
            alternative = self.output_dir / f"{base}_{index}{extension}"
            if not alternative.exists():
                return alternative
        return self.output_dir / f"{base}_{int(time.time())}{extension}"

    def _render_pdf_output(
        self,
        source: Path,
        document: Document,
        ocr_pdf: Path | None,
        destination: Path,
    ) -> Path:
        if ocr_pdf and ocr_pdf.exists():
            shutil.copy2(ocr_pdf, destination)
            self.log("      · PDF con OCR guardado.", "success")
            return destination
        if source.suffix.lower() in IMAGE_INPUTS:
            image_to_pdf(source, destination)
            self.log("      · PDF de imagen guardado.", "success")
            return destination
        if source.suffix.lower() == ".pdf":
            shutil.copy2(source, destination)
            self.log("      · PDF original copiado (sin OCR nuevo).", "info")
            return destination
        render_pdf(document, self.render_options, destination)
        self.log("      · PDF generado desde el documento.", "success")
        return destination

    # -- conversión ---------------------------------------------------------

    def convert(self, source: Path) -> JobResult:
        started = time.perf_counter()
        source = Path(source)
        result = JobResult(source=source, status="error")
        if not source.exists():
            result.error = "El archivo no existe."
            return result

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"📄 Procesando: {source.name}", "accent")
        self.log(
            f"   Formatos: {', '.join(OUTPUT_BY_ID[f][1] for f in self.config.formats if f in OUTPUT_BY_ID)}",
            "info",
        )

        try:
            self._check_cancel()
            language = _effective_language(self.config.ocr_lang, self.tools)
            if language != self.config.ocr_lang:
                self.log(
                    f"   ⚠️ Idioma {self.config.ocr_lang} no instalado; usando {language}.",
                    "warn",
                )

            extract_options = ExtractOptions(
                ocr_enabled=self.config.ocr_enabled,
                ocr_lang=language,
                force_ocr=self.config.force_ocr,
                deskew=self.config.deskew,
                rotate_pages=self.config.rotate_pages,
                optimize=self.config.optimize,
                jobs=self.config.jobs,
                page_range=self.config.page_range,
                keep_pages=self.config.keep_pages,
                log=self.log,
                tools=self.tools,
            )

            with tempfile.TemporaryDirectory(prefix="conversor_pro_") as temp:
                workdir = Path(temp)
                extraction = extract_document(source, extract_options, workdir)
                document = extraction.document
                result.document = document
                result.warnings.extend(extraction.warnings)
                for warning in extraction.warnings:
                    self.log(f"   ⚠️ {warning}", "warn")

                if not document.blocks and "pdf" not in self.config.formats:
                    raise ConversionError(
                        "No se pudo extraer texto del archivo. Activa el OCR o revisa el documento."
                    )

                self.log(
                    f"   📊 {document.page_count} página(s) · {document.word_count:,} palabras",
                    "info",
                )

                outputs: list[Path] = []
                for format_id in self.config.formats:
                    self._check_cancel()
                    if format_id not in OUTPUT_BY_ID:
                        continue
                    _, name, extension, _ = OUTPUT_BY_ID[format_id]
                    destination = self._destination(source, extension)
                    self.log(f"   🔄 Generando {name}...", "info")

                    if format_id == "md":
                        outputs.append(
                            _write_text(destination, render_markdown(document, self.render_options))
                        )
                    elif format_id == "txt":
                        outputs.append(
                            _write_text(destination, render_text(document, self.render_options))
                        )
                    elif format_id == "html":
                        outputs.append(
                            _write_text(destination, render_html(document, self.render_options))
                        )
                    elif format_id == "json":
                        outputs.append(
                            _write_text(destination, render_json(document, self.render_options))
                        )
                    elif format_id == "docx":
                        outputs.append(render_docx(document, self.render_options, destination))
                    elif format_id == "csv":
                        outputs.append(render_csv(document, self.render_options, destination))
                    elif format_id == "epub":
                        outputs.append(render_epub(document, self.render_options, destination))
                    elif format_id == "pdf":
                        outputs.append(
                            self._render_pdf_output(
                                source, document, extraction.ocr_pdf, destination
                            )
                        )

                result.outputs = outputs
                result.status = "done"
                result.duration = time.perf_counter() - started
                self.log(
                    f"   ✅ Listo en {result.duration:.1f}s · {len(outputs)} archivo(s).",
                    "success",
                )
                return result

        except ConversionCancelled as exc:
            result.status = "cancelled"
            result.error = str(exc)
            result.duration = time.perf_counter() - started
            self.log(f"   ⏹ {exc}", "warn")
            return result
        except (ConversionError, OcrError, RenderError) as exc:
            result.status = "error"
            result.error = str(exc)
            result.duration = time.perf_counter() - started
            self.log(f"   ❌ Error: {exc}", "error")
            return result
        except Exception as exc:  # red de seguridad para no matar el hilo
            result.status = "error"
            result.error = f"{exc.__class__.__name__}: {exc}"
            result.duration = time.perf_counter() - started
            self.log(f"   ❌ Error inesperado: {result.error}", "error")
            return result

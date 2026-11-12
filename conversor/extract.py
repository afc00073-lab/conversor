"""Extracción de contenido desde todos los formatos de entrada."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree

from .config import IMAGE_INPUTS, SUPPORTED_INPUTS
from .model import Block, Document, blocks_from_plain_text, normalise_paragraph
from .ocr import (
    OcrError,
    ToolStatus,
    detect_tools,
    image_to_pdf,
    ocr_image,
    ocr_pdf,
)

LogFn = Callable[[str, str], None]


class ConversionError(RuntimeError):
    """Error de conversión con un mensaje apto para el usuario."""


@dataclass
class ExtractOptions:
    ocr_enabled: bool = True
    ocr_lang: str = "spa"
    force_ocr: bool = True
    deskew: bool = True
    rotate_pages: bool = False
    optimize: int = 1
    jobs: int = 2
    page_range: str = ""
    keep_pages: bool = False
    log: LogFn | None = None
    tools: ToolStatus | None = None


@dataclass
class ExtractionResult:
    document: Document
    ocr_pdf: Path | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilidades comunes
# ---------------------------------------------------------------------------


def _log(options: ExtractOptions, message: str, level: str = "info") -> None:
    if options.log:
        options.log(message, level)


def parse_page_range(spec: str, total: int) -> list[int]:
    """Convierte '1-3,7' en índices 0-based. Si no es válido, todas las páginas."""
    if total <= 0:
        return []
    if not spec or not spec.strip():
        return list(range(total))

    selected: set[int] = set()
    for chunk in spec.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, _, end_s = chunk.partition("-")
            try:
                start = int(start_s) if start_s.strip() else 1
                end = int(end_s) if end_s.strip() else total
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            for page in range(max(1, start), min(total, end) + 1):
                selected.add(page - 1)
        else:
            try:
                page = int(chunk)
            except ValueError:
                continue
            if 1 <= page <= total:
                selected.add(page - 1)
    return sorted(selected) if selected else list(range(total))


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean_title(value: str | None, fallback: Path) -> str:
    if value:
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", errors="ignore")
            except Exception:
                value = None
    if value and str(value).strip():
        return str(value).strip()
    return fallback.stem.replace("_", " ").replace("-", " ").strip() or fallback.name


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def extract_pdf(path: Path, options: ExtractOptions, workdir: Path) -> ExtractionResult:
    try:
        import pypdf  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ConversionError(
            "Falta pypdf. Instálalo con: pip install pypdf"
        ) from exc

    tools = options.tools or detect_tools()
    ocr_result: Path | None = None
    warnings: list[str] = []

    source_pdf = path
    if options.ocr_enabled:
        if tools.can_ocr_pdf:
            ocr_result = workdir / f"{path.stem}_ocr.pdf"
            try:
                _log(options, "   ⏳ Aplicando OCR al PDF (puede tardar)...", "info")
                ocr_pdf(
                    path,
                    ocr_result,
                    language=options.ocr_lang,
                    force=options.force_ocr,
                    deskew=options.deskew,
                    rotate=options.rotate_pages,
                    optimize=options.optimize,
                    jobs=options.jobs,
                    log=options.log,
                )
                source_pdf = ocr_result
                _log(options, "   ✅ OCR aplicado al PDF.", "success")
            except OcrError as exc:
                warnings.append(str(exc))
                _log(options, f"   ⚠️ OCR no aplicado: {exc}", "warn")
                ocr_result = None
        else:
            message = (
                "OCR no disponible (faltan OCRmyPDF, Tesseract o Ghostscript). "
                "Se extraerá el texto que ya tenga el PDF."
            )
            warnings.append(message)
            _log(options, f"   ⚠️ {message}", "warn")
    else:
        _log(options, "   ℹ️ OCR desactivado: extrayendo texto existente.", "info")

    try:
        reader = pypdf.PdfReader(str(source_pdf))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ConversionError("El PDF está protegido con contraseña.")
            except Exception as exc:
                raise ConversionError("El PDF está protegido con contraseña.") from exc

        total = len(reader.pages)
        indices = parse_page_range(options.page_range, total)
        if not indices:
            raise ConversionError("El PDF no contiene páginas legibles.")

        page_texts: list[str] = []
        blocks: list[Block] = []
        for page_index in indices:
            try:
                text = reader.pages[page_index].extract_text() or ""
            except Exception as exc:
                warnings.append(f"No se pudo leer la página {page_index + 1}: {exc}")
                text = ""
            page_texts.append(text)
            if options.keep_pages:
                blocks.append(Block(type="heading", text=f"Página {page_index + 1}", level=2))
            blocks.extend(blocks_from_plain_text(text))

        metadata: dict = {}
        try:
            info = reader.metadata or {}
            for key in ("/Title", "/Author", "/Subject", "/Creator", "/Producer"):
                value = info.get(key) if hasattr(info, "get") else None
                if value:
                    metadata[key.lstrip("/").lower()] = str(value)
            if info and hasattr(info, "get"):
                created = info.get("/CreationDate")
                if created:
                    metadata["creation_date"] = str(created)
        except Exception:
            pass

        metadata.update(
            {
                "paginas_totales": total,
                "paginas_procesadas": len(indices),
                "formato_origen": "PDF",
                "rango_paginas": options.page_range or "todas",
            }
        )
        title = _clean_title(metadata.get("title"), path)
        document = Document(
            source=path,
            title=title,
            blocks=blocks,
            page_texts=page_texts,
            metadata=metadata,
        )
        if not document.plain_text.strip():
            warnings.append(
                "No se encontró texto en el PDF. Activa el OCR o revisa que el documento "
                "tenga texto seleccionable."
            )
        return ExtractionResult(document, ocr_result, warnings)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"No se pudo leer el PDF: {exc}") from exc


# ---------------------------------------------------------------------------
# Imágenes
# ---------------------------------------------------------------------------


def extract_image(path: Path, options: ExtractOptions, workdir: Path) -> ExtractionResult:
    tools = options.tools or detect_tools()
    warnings: list[str] = []
    ocr_result: Path | None = None
    text = ""

    if options.ocr_enabled:
        if tools.can_ocr_images:
            try:
                _log(options, "   ⏳ Leyendo imagen con Tesseract...", "info")
                text = ocr_image(path, language=options.ocr_lang, log=options.log)
                _log(options, "   ✅ Texto reconocido en la imagen.", "success")
            except OcrError as exc:
                warnings.append(str(exc))
                _log(options, f"   ⚠️ OCR de imagen falló: {exc}", "warn")
        elif tools.can_ocr_pdf:
            try:
                temp_pdf = workdir / f"{path.stem}_imagen.pdf"
                image_to_pdf(path, temp_pdf)
                ocr_result = workdir / f"{path.stem}_ocr.pdf"
                ocr_pdf(
                    temp_pdf,
                    ocr_result,
                    language=options.ocr_lang,
                    force=True,
                    deskew=options.deskew,
                    rotate=options.rotate_pages,
                    optimize=options.optimize,
                    jobs=options.jobs,
                    log=options.log,
                )
                import pypdf  # type: ignore

                reader = pypdf.PdfReader(str(ocr_result))
                text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
                _log(options, "   ✅ OCR aplicado a la imagen.", "success")
            except Exception as exc:
                warnings.append(f"No se pudo aplicar OCR a la imagen: {exc}")
                _log(options, f"   ⚠️ OCR de imagen falló: {exc}", "warn")
                ocr_result = None
        else:
            message = (
                "OCR no disponible. Instala Tesseract y pytesseract para leer imágenes."
            )
            warnings.append(message)
            _log(options, f"   ⚠️ {message}", "warn")
    else:
        _log(options, "   ℹ️ OCR desactivado para la imagen.", "info")

    metadata: dict = {"formato_origen": SUPPORTED_INPUTS.get(path.suffix.lower(), "Imagen")}
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            metadata.update(
                {
                    "ancho_px": image.width,
                    "alto_px": image.height,
                    "modo_color": image.mode,
                }
            )
    except Exception:
        pass

    blocks: list[Block] = []
    if text.strip():
        if options.keep_pages:
            blocks.append(Block(type="heading", text="Página 1", level=2))
        blocks.extend(blocks_from_plain_text(text))
    else:
        warnings.append("La imagen no produjo texto. Se generará el PDF de imagen si lo solicitas.")

    document = Document(
        source=path,
        title=_clean_title(None, path),
        blocks=blocks,
        page_texts=[text] if text.strip() else [],
        metadata=metadata,
    )
    return ExtractionResult(document, ocr_result, warnings)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def extract_docx(path: Path, options: ExtractOptions) -> ExtractionResult:
    try:
        from docx import Document as DocxDocument  # type: ignore
        from docx.oxml.ns import qn  # type: ignore
        from docx.table import Table  # type: ignore
        from docx.text.paragraph import Paragraph  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ConversionError(
            "Falta python-docx. Instálalo con: pip install python-docx"
        ) from exc

    try:
        docx = DocxDocument(str(path))
    except Exception as exc:
        raise ConversionError(f"No se pudo abrir el documento Word: {exc}") from exc

    blocks: list[Block] = []
    pending_list: list[str] = []

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list:
            blocks.append(Block(type="list", items=pending_list))
            pending_list = []

    heading_re = re.compile(r"^(?:heading|t[ií]tulo)\s*(\d)$", re.IGNORECASE)

    for child in docx.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, docx)
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = (paragraph.style.name if paragraph.style else "") or ""
            heading_match = heading_re.match(style_name.strip())
            is_list = bool(paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None)
            style_lower = style_name.lower()
            if "list" in style_lower or "lista" in style_lower:
                is_list = True

            if heading_match:
                flush_list()
                level = max(1, min(6, int(heading_match.group(1))))
                blocks.append(Block(type="heading", text=text, level=level))
            elif is_list:
                pending_list.append(text)
            else:
                flush_list()
                blocks.append(Block(type="paragraph", text=normalise_paragraph(text)))
        elif child.tag == qn("w:tbl"):
            flush_list()
            table = Table(child, docx)
            rows: list[list[str]] = []
            for row in table.rows:
                values = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                if any(values):
                    rows.append(values)
            if rows:
                blocks.append(Block(type="table", rows=rows))
    flush_list()

    metadata: dict = {"formato_origen": "DOCX"}
    try:
        props = docx.core_properties
        for key in ("title", "author", "subject", "keywords", "comments"):
            value = getattr(props, key, None)
            if value:
                metadata[key] = str(value)
    except Exception:
        pass

    title = _clean_title(metadata.get("title"), path)
    document = Document(
        source=path,
        title=title,
        blocks=blocks,
        metadata=metadata,
    )
    if blocks:
        document.page_texts = [document.plain_text]
    return ExtractionResult(document)


# ---------------------------------------------------------------------------
# HTML / Markdown / texto
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "form", "iframe"}
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _table_rows(table_tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table_tag.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if any(values):
            rows.append(values)
    return rows


def _html_to_blocks(soup) -> tuple[list[Block], str]:
    blocks: list[Block] = []

    def walk(node) -> None:
        for child in getattr(node, "children", []):
            if not getattr(child, "name", None):
                continue
            name = child.name.lower()
            if name in _SKIP_TAGS:
                continue
            if name in _HEADING_TAGS:
                text = child.get_text(" ", strip=True)
                if text:
                    blocks.append(Block(type="heading", text=text, level=_HEADING_TAGS[name]))
                continue
            if name == "p":
                text = child.get_text(" ", strip=True)
                if text:
                    blocks.append(Block(type="paragraph", text=normalise_paragraph(text)))
                continue
            if name in ("ul", "ol"):
                items = []
                for li in child.find_all("li", recursive=False):
                    text = li.get_text(" ", strip=True)
                    if text:
                        items.append(text)
                if items:
                    blocks.append(Block(type="list", items=items))
                continue
            if name == "table":
                rows = _table_rows(child)
                if rows:
                    blocks.append(Block(type="table", rows=rows))
                continue
            if name == "blockquote":
                text = child.get_text(" ", strip=True)
                if text:
                    blocks.append(Block(type="quote", text=normalise_paragraph(text)))
                continue
            if name in ("pre", "code"):
                text = child.get_text("\n", strip=False)
                if text.strip():
                    blocks.append(Block(type="code", text=text.strip("\n")))
                continue
            walk(child)

    walk(soup)
    text = "\n\n".join(
        block.text
        if block.type in {"paragraph", "quote", "code", "heading"}
        else "\n".join(block.items)
        if block.type == "list"
        else "\n".join(" | ".join(row) for row in block.rows)
        for block in blocks
    )
    return blocks, text


def extract_html(path: Path, options: ExtractOptions) -> ExtractionResult:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ConversionError(
            "Falta beautifulsoup4. Instálalo con: pip install beautifulsoup4"
        ) from exc

    try:
        soup = BeautifulSoup(read_text_file(path), "html.parser")
    except Exception as exc:
        raise ConversionError(f"No se pudo leer el HTML: {exc}") from exc

    for tag in soup(list(_SKIP_TAGS)):
        tag.decompose()
    title_tag = soup.find("title")
    title = _clean_title(title_tag.get_text(" ", strip=True) if title_tag else None, path)
    body = soup.body or soup
    blocks, text = _html_to_blocks(body)
    if not blocks:
        fallback = soup.get_text("\n", strip=True)
        blocks = blocks_from_plain_text(fallback)
        text = fallback

    metadata = {
        "formato_origen": "HTML",
        "imagenes": len(soup.find_all("img")),
        "enlaces": len(soup.find_all("a")),
    }
    document = Document(
        source=path,
        title=title,
        blocks=blocks,
        page_texts=[text] if text else [],
        metadata=metadata,
    )
    return ExtractionResult(document)


def parse_markdown(text: str) -> list[Block]:
    """Parser Markdown suficiente para conservar la estructura habitual."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    paragraph: list[str] = []
    bullets: list[str] = []
    quote: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            joined = normalise_paragraph("\n".join(paragraph))
            if joined:
                blocks.append(Block(type="paragraph", text=joined))
            paragraph = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            blocks.append(Block(type="list", items=bullets))
            bullets = []

    def flush_quote() -> None:
        nonlocal quote
        if quote:
            blocks.append(Block(type="quote", text=normalise_paragraph("\n".join(quote))))
            quote = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        fence = re.match(r"^```\s*([\w+-]*)", stripped)
        if fence:
            flush_paragraph()
            flush_bullets()
            flush_quote()
            lang = fence.group(1)
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append(Block(type="code", text="\n".join(code_lines), lang=lang))
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            flush_paragraph()
            flush_bullets()
            flush_quote()
            blocks.append(
                Block(type="heading", text=heading.group(2).strip(), level=len(heading.group(1)))
            )
            i += 1
            continue

        # Encabezados setext
        if i + 1 < len(lines) and stripped and re.match(r"^(=+|-+)\s*$", lines[i + 1].strip()):
            level = 1 if lines[i + 1].strip().startswith("=") else 2
            flush_paragraph()
            flush_bullets()
            flush_quote()
            blocks.append(Block(type="heading", text=stripped, level=level))
            i += 2
            continue

        if re.match(r"^([-*_])\1{2,}\s*$", stripped):
            flush_paragraph()
            flush_bullets()
            flush_quote()
            i += 1
            continue

        # Tablas pipe
        if "|" in stripped and i + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[i + 1]
        ):
            flush_paragraph()
            flush_bullets()
            flush_quote()
            rows: list[list[str]] = []
            header_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            rows.append(header_cells)
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if any(cells):
                    rows.append(cells)
                i += 1
            blocks.append(Block(type="table", rows=rows))
            continue

        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        numbered = re.match(r"^\s*\d{1,3}[.)]\s+(.*)$", line)
        if bullet or numbered:
            flush_paragraph()
            flush_quote()
            bullets.append((bullet or numbered).group(1).strip())
            i += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_bullets()
            quote.append(stripped.lstrip(">").strip())
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            flush_bullets()
            flush_quote()
            i += 1
            continue

        flush_bullets()
        flush_quote()
        paragraph.append(line)
        i += 1

    flush_paragraph()
    flush_bullets()
    flush_quote()
    return blocks


def extract_text(path: Path, options: ExtractOptions) -> ExtractionResult:
    try:
        text = read_text_file(path)
    except Exception as exc:
        raise ConversionError(f"No se pudo leer el archivo de texto: {exc}") from exc

    if path.suffix.lower() in {".md", ".markdown"}:
        blocks = parse_markdown(text)
        formato = "Markdown"
    else:
        blocks = blocks_from_plain_text(text)
        formato = "Texto"

    document = Document(
        source=path,
        title=_clean_title(None, path),
        blocks=blocks,
        page_texts=[text] if text.strip() else [],
        metadata={"formato_origen": formato, "lineas": len(text.splitlines())},
    )
    return ExtractionResult(document)


# ---------------------------------------------------------------------------
# CSV / Excel
# ---------------------------------------------------------------------------


def extract_csv(path: Path, options: ExtractOptions) -> ExtractionResult:
    try:
        text = read_text_file(path)
    except Exception as exc:
        raise ConversionError(f"No se pudo leer el CSV: {exc}") from exc

    sample = text[:8192]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        counts = {candidate: sample.count(candidate) for candidate in ",;\t|"}
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ","

    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise ConversionError("El CSV está vacío.")

    blocks = [Block(type="table", rows=rows)]
    document = Document(
        source=path,
        title=_clean_title(None, path),
        blocks=blocks,
        page_texts=["\n".join(" | ".join(row) for row in rows)],
        metadata={
            "formato_origen": "CSV",
            "filas": len(rows),
            "columnas": max(len(row) for row in rows),
            "delimitador": delimiter,
            "tipo": "tabla",
        },
    )
    return ExtractionResult(document)


def extract_xlsx(path: Path, options: ExtractOptions) -> ExtractionResult:
    try:
        import openpyxl  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ConversionError(
            "Falta openpyxl. Instálalo con: pip install openpyxl"
        ) from exc

    try:
        workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:
        raise ConversionError(f"No se pudo abrir el Excel: {exc}") from exc

    blocks: list[Block] = []
    page_texts: list[str] = []
    total_rows = 0
    sheet_count = len(workbook.sheetnames)
    try:
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                values = ["" if cell is None else str(cell) for cell in row]
                if any(value.strip() for value in values):
                    rows.append(values)
                if len(rows) > 20000:
                    break
            if not rows:
                continue
            total_rows += len(rows)
            blocks.append(Block(type="heading", text=sheet.title, level=2))
            blocks.append(Block(type="table", rows=rows))
            page_texts.append("\n".join("\t".join(row) for row in rows))
    finally:
        try:
            workbook.close()
        except Exception:
            pass

    if not blocks:
        raise ConversionError("El libro de Excel no contiene datos.")

    document = Document(
        source=path,
        title=_clean_title(None, path),
        blocks=blocks,
        page_texts=page_texts,
        metadata={
            "formato_origen": "Excel",
            "hojas": sheet_count,
            "filas": total_rows,
            "tipo": "tabla",
        },
    )
    return ExtractionResult(document)


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------


def extract_epub(path: Path, options: ExtractOptions) -> ExtractionResult:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ConversionError("Falta beautifulsoup4 para leer EPUB.") from exc

    blocks: list[Block] = []
    page_texts: list[str] = []
    title = path.stem
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            opf_name = next((name for name in names if name.lower().endswith(".opf")), None)
            if opf_name:
                try:
                    root = ElementTree.fromstring(archive.read(opf_name))
                    for element in root.iter():
                        if element.tag.endswith("title") and element.text:
                            title = element.text.strip()
                            break
                except Exception:
                    pass

            html_names = sorted(
                name
                for name in names
                if name.lower().endswith((".xhtml", ".html", ".htm"))
            )
            for index, name in enumerate(html_names, start=1):
                try:
                    soup = BeautifulSoup(archive.read(name), "html.parser")
                except Exception:
                    continue
                for tag in soup(list(_SKIP_TAGS)):
                    tag.decompose()
                body = soup.body or soup
                section_blocks, text = _html_to_blocks(body)
                if not section_blocks:
                    continue
                if options.keep_pages:
                    blocks.append(Block(type="heading", text=f"Sección {index}", level=2))
                blocks.extend(section_blocks)
                page_texts.append(text)
    except zipfile.BadZipFile as exc:
        raise ConversionError("El EPUB está dañado o no es válido.") from exc
    except Exception as exc:
        raise ConversionError(f"No se pudo leer el EPUB: {exc}") from exc

    if not blocks:
        raise ConversionError("El EPUB no contiene texto legible.")

    document = Document(
        source=path,
        title=_clean_title(title, path),
        blocks=blocks,
        page_texts=page_texts,
        metadata={"formato_origen": "EPUB", "secciones": len(page_texts)},
    )
    return ExtractionResult(document)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def extract_document(path: Path, options: ExtractOptions, workdir: Path) -> ExtractionResult:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_INPUTS:
        raise ConversionError(
            f"Formato no admitido: {suffix or 'sin extensión'}. "
            f"Admitidos: {', '.join(sorted(SUPPORTED_INPUTS))}"
        )
    if suffix == ".pdf":
        return extract_pdf(path, options, workdir)
    if suffix in IMAGE_INPUTS:
        return extract_image(path, options, workdir)
    if suffix == ".docx":
        return extract_docx(path, options)
    if suffix in {".html", ".htm"}:
        return extract_html(path, options)
    if suffix in {".txt", ".md", ".markdown"}:
        return extract_text(path, options)
    if suffix == ".csv":
        return extract_csv(path, options)
    if suffix == ".xlsx":
        return extract_xlsx(path, options)
    if suffix == ".epub":
        return extract_epub(path, options)
    raise ConversionError(f"Formato no soportado: {suffix}")

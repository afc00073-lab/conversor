"""Renderizadores de documentos a Markdown, TXT, HTML, DOCX, JSON, CSV, EPUB y PDF."""

from __future__ import annotations

import csv
import html
import json
import re
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import __version__
from .model import Block, Document

APP_NAME = "Conversor Pro"


class RenderError(RuntimeError):
    """Error al generar un formato de salida."""


@dataclass
class RenderOptions:
    include_title: bool = True
    keep_pages: bool = False


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _pad_rows(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return []
    columns = max(len(row) for row in rows)
    return [row + [""] * (columns - len(row)) for row in rows]


def _strip_inline_markdown(text: str) -> str:
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text)
    return text


def _inline_md_to_html(text: str, *, xhtml: bool = False) -> str:
    escaped = html.escape(text, quote=False)
    stash: dict[str, str] = {}

    def keep_code(match: re.Match[str]) -> str:
        token = f"\x00CODE{len(stash)}\x00"
        stash[token] = f"<code>{match.group(1)}</code>"
        return token

    escaped = re.sub(r"`([^`\n]+)`", keep_code, escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', escaped)
    for token, value in stash.items():
        escaped = escaped.replace(token, value)
    br = "<br/>" if xhtml else "<br>"
    return escaped.replace("\n", br)


def _safe_filename(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "-", text).strip(" .-")
    return text or "documento"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def render_markdown(document: Document, options: RenderOptions) -> str:
    parts: list[str] = []
    if options.include_title:
        parts.append(f"# {document.title}\n")
        source_line = f"*Fuente: `{document.source.name}`*"
        parts.append(source_line)

    for block in document.blocks:
        if block.type == "heading":
            level = max(1, min(6, block.level or 2))
            parts.append(f"{'#' * level} {block.text.strip()}")
        elif block.type == "paragraph":
            parts.append(block.text.strip())
        elif block.type == "quote":
            lines = [f"> {line}" for line in block.text.strip().splitlines() or [""]]
            parts.append("\n".join(lines))
        elif block.type == "code":
            language = block.lang or ""
            parts.append(f"```{language}\n{block.text.rstrip()}\n```")
        elif block.type == "list":
            parts.append("\n".join(f"- {item.strip()}" for item in block.items if item.strip()))
        elif block.type == "table":
            rows = _pad_rows(block.rows)
            if not rows:
                continue
            header = "| " + " | ".join(cell.replace("|", "\\|") for cell in rows[0]) + " |"
            separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
            body = [
                "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"
                for row in rows[1:]
            ]
            parts.append("\n".join([header, separator, *body]))

    return "\n\n".join(part for part in parts if part).strip() + "\n"


def render_text(document: Document, options: RenderOptions) -> str:
    parts: list[str] = []
    if options.include_title:
        parts.append(document.title)
        parts.append("=" * min(len(document.title), 72))
        parts.append(f"Fuente: {document.source.name}")

    for block in document.blocks:
        if block.type == "heading":
            text = block.text.strip()
            parts.append(text)
            if block.level <= 2:
                parts.append("-" * min(len(text), 72))
        elif block.type in {"paragraph", "quote"}:
            parts.append(_strip_inline_markdown(block.text.strip()))
        elif block.type == "code":
            parts.append(block.text.rstrip())
        elif block.type == "list":
            parts.append("\n".join(f"- {_strip_inline_markdown(item.strip())}" for item in block.items))
        elif block.type == "table":
            rows = _pad_rows(block.rows)
            parts.append("\n".join("\t".join(row) for row in rows))

    return "\n\n".join(part for part in parts if part).strip() + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML_CSS = """
:root {
  --bg: #f6f7fb; --panel: #ffffff; --text: #1f2937; --muted: #6b7280;
  --accent: #3b82f6; --border: #e5e7eb; --code: #f3f4f6; --quote: #f8fafc;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f172a; --panel: #111c33; --text: #e5e7eb; --muted: #94a3b8;
    --accent: #60a5fa; --border: #243047; --code: #1a2740; --quote: #16223a;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  line-height: 1.75; font-size: 16px;
}
.layout { display: flex; gap: 40px; max-width: 1180px; margin: 0 auto; padding: 48px 28px 80px; }
main { flex: 1; min-width: 0; background: var(--panel); border: 1px solid var(--border);
  border-radius: 18px; padding: 44px 52px; box-shadow: 0 18px 60px rgba(15,23,42,.08); }
.toc { width: 240px; flex: 0 0 240px; position: sticky; top: 40px; align-self: flex-start;
  font-size: 13px; color: var(--muted); }
.toc h2 { font-size: 11px; letter-spacing: .12em; text-transform: uppercase; margin: 0 0 12px; }
.toc ol { list-style: none; padding: 0; margin: 0; border-left: 2px solid var(--border); }
.toc li { margin: 0; }
.toc a { display: block; padding: 5px 0 5px 14px; color: var(--muted); text-decoration: none;
  border-left: 2px solid transparent; margin-left: -2px; }
.toc a:hover { color: var(--accent); border-left-color: var(--accent); }
.toc .lvl3 { padding-left: 28px; }
.toc .lvl4 { padding-left: 42px; }
.doc-header { border-bottom: 1px solid var(--border); padding-bottom: 26px; margin-bottom: 34px; }
.doc-header h1 { font-size: 2.15rem; line-height: 1.2; margin: 0 0 12px; letter-spacing: -.02em; }
.doc-meta { color: var(--muted); font-size: 13px; display: flex; flex-wrap: wrap; gap: 8px 18px; }
.doc-meta span { white-space: nowrap; }
h1, h2, h3, h4, h5, h6 { line-height: 1.3; margin: 1.8em 0 .6em; letter-spacing: -.01em; }
h2 { font-size: 1.55rem; border-bottom: 1px solid var(--border); padding-bottom: .25em; }
h3 { font-size: 1.25rem; }
h4 { font-size: 1.08rem; }
p { margin: 0 0 1.05em; }
a { color: var(--accent); text-underline-offset: 3px; }
ul, ol { padding-left: 1.5em; margin: 0 0 1.1em; }
li { margin: .25em 0; }
blockquote { margin: 1.2em 0; padding: 14px 20px; border-left: 4px solid var(--accent);
  background: var(--quote); border-radius: 0 10px 10px 0; color: var(--muted); }
code { background: var(--code); padding: .16em .38em; border-radius: 5px;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: .9em; }
pre { background: var(--code); padding: 18px 20px; border-radius: 12px; overflow-x: auto;
  border: 1px solid var(--border); }
pre code { background: none; padding: 0; font-size: .88em; }
table { width: 100%; border-collapse: collapse; margin: 1.4em 0; font-size: .94em; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--border); padding: 9px 12px; text-align: left; vertical-align: top; }
th { background: var(--code); font-weight: 650; }
tbody tr:nth-child(even) { background: color-mix(in srgb, var(--code) 45%, transparent); }
.doc-footer { margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--border);
  color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
@media (max-width: 900px) {
  .layout { display: block; padding: 20px 14px 50px; }
  .toc { display: none; }
  main { padding: 28px 22px; border-radius: 14px; }
  .doc-header h1 { font-size: 1.7rem; }
}
@media print {
  body { background: #fff; }
  .layout { display: block; padding: 0; max-width: none; }
  main { border: none; box-shadow: none; padding: 0; }
  .toc { display: none; }
  a { color: inherit; }
}
"""


def _html_blocks(document: Document, *, xhtml: bool = False) -> tuple[str, list[tuple[int, str, str]]]:
    parts: list[str] = []
    headings: list[tuple[int, str, str]] = []
    heading_index = 0

    for block in document.blocks:
        if block.type == "heading":
            heading_index += 1
            level = max(1, min(6, block.level or 2))
            anchor = f"seccion-{heading_index}"
            headings.append((level, block.text.strip(), anchor))
            parts.append(
                f'<h{level} id="{anchor}">{_inline_md_to_html(block.text, xhtml=xhtml)}</h{level}>'
            )
        elif block.type == "paragraph":
            parts.append(f"<p>{_inline_md_to_html(block.text, xhtml=xhtml)}</p>")
        elif block.type == "quote":
            parts.append(f"<blockquote>{_inline_md_to_html(block.text, xhtml=xhtml)}</blockquote>")
        elif block.type == "code":
            language = f' class="language-{html.escape(block.lang)}"' if block.lang else ""
            parts.append(f"<pre><code{language}>{html.escape(block.text)}</code></pre>")
        elif block.type == "list":
            items = "".join(
                f"<li>{_inline_md_to_html(item, xhtml=xhtml)}</li>"
                for item in block.items
                if item.strip()
            )
            if items:
                parts.append(f"<ul>{items}</ul>")
        elif block.type == "table":
            rows = _pad_rows(block.rows)
            if not rows:
                continue
            header = "".join(
                f"<th>{_inline_md_to_html(cell, xhtml=xhtml)}</th>" for cell in rows[0]
            )
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline_md_to_html(cell, xhtml=xhtml)}</td>" for cell in row) + "</tr>"
                for row in rows[1:]
            )
            parts.append(
                f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
            )

    return "\n".join(parts), headings


def render_html(document: Document, options: RenderOptions) -> str:
    body, headings = _html_blocks(document)
    toc_items = ""
    if len(headings) >= 2:
        links = "".join(
            f'<li class="lvl{min(level, 4)}"><a href="#{anchor}">{html.escape(text)}</a></li>'
            for level, text, anchor in headings
        )
        toc_items = (
            '<aside class="toc"><h2>Contenido</h2><ol>' + links + "</ol></aside>"
        )

    title = html.escape(document.title)
    header = ""
    if options.include_title:
        meta_items = [
            f"<span>📄 {html.escape(document.source.name)}</span>",
            f"<span>📝 {document.word_count:,} palabras</span>",
            f"<span>📄 {document.page_count} página(s)</span>",
        ]
        header = (
            '<header class="doc-header">'
            f"<h1>{title}</h1>"
            f'<div class="doc-meta">{"".join(meta_items)}</div>'
            "</header>"
        )

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    footer = (
        '<footer class="doc-footer">'
        f"<span>Generado con {APP_NAME} {__version__}</span>"
        f"<span>{now}</span>"
        "</footer>"
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="{APP_NAME} {__version__}">
<title>{title}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<div class="layout">
{toc_items}
<main>
{header}
{body}
{footer}
</main>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _shade_cell(cell, color: str) -> None:
    from docx.oxml import OxmlElement  # type: ignore
    from docx.oxml.ns import qn  # type: ignore

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tc_pr.append(shd)


def _add_page_number(paragraph) -> None:
    from docx.oxml import OxmlElement  # type: ignore
    from docx.oxml.ns import qn  # type: ignore

    paragraph.alignment = 1
    run = paragraph.add_run()
    fld_char1 = OxmlElement("w:fldChar")
    fld_char1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = "PAGE"
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char1)
    run._r.append(instr_text)
    run._r.append(fld_char2)


def render_docx(document: Document, options: RenderOptions, destination: Path) -> Path:
    try:
        from docx import Document as DocxDocument  # type: ignore
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore
        from docx.shared import Cm, Pt, RGBColor  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RenderError("Falta python-docx. Instálalo con: pip install python-docx") from exc

    docx = DocxDocument()
    section = docx.sections[0]
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.4)
    section.right_margin = Cm(2.4)

    styles = docx.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    for name, size, color in (
        ("Heading 1", 20, RGBColor(0x1D, 0x4E, 0xD8)),
        ("Heading 2", 16, RGBColor(0x1D, 0x4E, 0xD8)),
        ("Heading 3", 13, RGBColor(0x33, 0x41, 0x55)),
        ("Heading 4", 12, RGBColor(0x33, 0x41, 0x55)),
    ):
        try:
            style = styles[name]
            style.font.size = Pt(size)
            style.font.color.rgb = color
            style.font.name = "Calibri"
        except KeyError:
            pass

    if options.include_title:
        title_paragraph = docx.add_paragraph()
        title_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        title_run = title_paragraph.add_run(document.title)
        title_run.bold = True
        title_run.font.size = Pt(26)
        title_run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

        meta = docx.add_paragraph()
        meta_run = meta.add_run(
            f"Fuente: {document.source.name}  ·  {document.word_count:,} palabras  ·  "
            f"{document.page_count} página(s)"
        )
        meta_run.italic = True
        meta_run.font.size = Pt(9)
        meta_run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
        meta.paragraph_format.space_after = Pt(18)

    for block in document.blocks:
        if block.type == "heading":
            level = max(1, min(4, block.level or 2))
            docx.add_heading(block.text.strip(), level=level)
        elif block.type == "paragraph":
            docx.add_paragraph(_strip_inline_markdown(block.text.strip()))
        elif block.type == "quote":
            paragraph = docx.add_paragraph(_strip_inline_markdown(block.text.strip()))
            paragraph.style = styles["Quote"] if "Quote" in [s.name for s in styles] else normal
            paragraph.paragraph_format.left_indent = Cm(0.8)
            paragraph.runs[0].italic = True
        elif block.type == "code":
            paragraph = docx.add_paragraph()
            run = paragraph.add_run(block.text.rstrip())
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            paragraph.paragraph_format.left_indent = Cm(0.5)
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.space_after = Pt(10)
        elif block.type == "list":
            for item in block.items:
                if item.strip():
                    try:
                        docx.add_paragraph(
                            _strip_inline_markdown(item.strip()), style="List Bullet"
                        )
                    except KeyError:
                        docx.add_paragraph("• " + _strip_inline_markdown(item.strip()))
        elif block.type == "table":
            rows = _pad_rows(block.rows)
            if not rows:
                continue
            table = docx.add_table(rows=len(rows), cols=len(rows[0]))
            try:
                table.style = "Light Grid Accent 1"
            except KeyError:
                table.style = "Table Grid"
            for row_index, row in enumerate(rows):
                for col_index, value in enumerate(row):
                    cell = table.cell(row_index, col_index)
                    cell.text = _strip_inline_markdown(value)
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(9)
                            if row_index == 0:
                                run.bold = True
                    if row_index == 0:
                        _shade_cell(cell, "E8EEF9")
            docx.add_paragraph()

    try:
        footer = section.footer.paragraphs[0]
        footer.text = ""
        _add_page_number(footer)
    except Exception:
        pass

    try:
        props = docx.core_properties
        props.title = document.title
        props.author = APP_NAME
        props.comments = f"Generado con {APP_NAME} {__version__}"
        props.subject = f"Convertido desde {document.source.name}"
    except Exception:
        pass

    try:
        docx.save(str(destination))
    except Exception as exc:
        raise RenderError(f"No se pudo guardar el DOCX: {exc}") from exc
    return destination


# ---------------------------------------------------------------------------
# JSON / CSV
# ---------------------------------------------------------------------------


def render_json(document: Document, options: RenderOptions) -> str:
    payload = {
        "aplicacion": APP_NAME,
        "version": __version__,
        "generado": datetime.now(timezone.utc).isoformat(),
        "fuente": {
            "nombre": document.source.name,
            "ruta": str(document.source),
            "titulo": document.title,
        },
        "estadisticas": {
            "paginas": document.page_count,
            "palabras": document.word_count,
            "caracteres": document.char_count,
            "bloques": len(document.blocks),
        },
        "metadatos": document.metadata,
        "bloques": [asdict(block) for block in document.blocks],
        "paginas": [
            {"numero": index + 1, "texto": text}
            for index, text in enumerate(document.page_texts)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_csv(document: Document, options: RenderOptions, destination: Path) -> Path:
    try:
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            if document.metadata.get("tipo") == "tabla":
                tables = [block for block in document.blocks if block.type == "table"]
                for index, table in enumerate(tables):
                    if index:
                        writer.writerow([])
                    if len(tables) > 1:
                        headings = [
                            block.text
                            for block in document.blocks
                            if block.type == "heading"
                        ]
                        if index < len(headings):
                            writer.writerow([headings[index]])
                    writer.writerows(_pad_rows(table.rows))
            else:
                writer.writerow(["pagina", "texto"])
                if document.page_texts:
                    for index, text in enumerate(document.page_texts, start=1):
                        writer.writerow([index, text.strip()])
                else:
                    writer.writerow([1, document.plain_text.strip()])
    except OSError as exc:
        raise RenderError(f"No se pudo guardar el CSV: {exc}") from exc
    return destination


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

_EPUB_CSS = """
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.65; margin: 5%;
  color: #1f2937; }
h1, h2, h3, h4 { font-family: system-ui, sans-serif; line-height: 1.25; color: #111827; }
h1 { font-size: 1.9em; margin: 1.2em 0 .8em; }
h2 { font-size: 1.45em; margin: 1.4em 0 .6em; border-bottom: 1px solid #e5e7eb; }
h3 { font-size: 1.2em; }
p { margin: 0 0 .9em; text-align: justify; }
blockquote { margin: 1em 0; padding: .6em 1em; border-left: 3px solid #3b82f6;
  background: #f8fafc; color: #4b5563; }
code { font-family: monospace; background: #f3f4f6; padding: .1em .3em; }
pre { background: #f3f4f6; padding: 1em; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .9em; }
th, td { border: 1px solid #d1d5db; padding: .4em .55em; text-align: left; }
th { background: #f3f4f6; }
"""


def render_epub(document: Document, options: RenderOptions, destination: Path) -> Path:
    body, headings = _html_blocks(document, xhtml=True)
    title = html.escape(document.title)
    header = f"<h1>{title}</h1>" if options.include_title else ""
    nav_links = "".join(
        f'<li><a href="content.xhtml#{anchor}">{html.escape(text)}</a></li>'
        for _, text, anchor in headings
    )
    if not nav_links:
        nav_links = '<li><a href="content.xhtml">Inicio</a></li>'

    content_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="es" lang="es">
<head><meta charset="utf-8"/><title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
{header}
{body}
</body>
</html>
"""

    nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="es" lang="es">
<head><meta charset="utf-8"/><title>Contenido</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>Contenido</h1><ol>{nav_links}</ol></nav>
</body>
</html>
"""

    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="es">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{book_id}</dc:identifier>
<dc:title>{title}</dc:title>
<dc:language>es</dc:language>
<dc:creator>{APP_NAME}</dc:creator>
<dc:description>Generado desde {html.escape(document.source.name)}</dc:description>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
</manifest>
<spine><itemref idref="content"/></spine>
</package>
"""

    container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

    try:
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/content.opf", content_opf, zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/nav.xhtml", nav_xhtml, zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/content.xhtml", content_xhtml, zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/style.css", _EPUB_CSS, zipfile.ZIP_DEFLATED)
    except OSError as exc:
        raise RenderError(f"No se pudo guardar el EPUB: {exc}") from exc
    return destination


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _register_pdf_fonts() -> tuple[str, str | None]:
    """Devuelve (fuente normal, fuente negrita) intentando una fuente Unicode."""
    try:
        from reportlab.pdfbase import pdfmetrics  # type: ignore
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore
    except Exception:
        return "Helvetica", "Helvetica-Bold"

    candidates = [
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        (r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for regular, bold in candidates:
        if not Path(regular).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("ConversorFont", regular))
            if Path(bold).exists():
                pdfmetrics.registerFont(TTFont("ConversorFont-Bold", bold))
                return "ConversorFont", "ConversorFont-Bold"
            return "ConversorFont", "ConversorFont"
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"


def render_pdf(document: Document, options: RenderOptions, destination: Path) -> Path:
    try:
        from reportlab.lib import colors  # type: ignore
        from reportlab.lib.enums import TA_JUSTIFY  # type: ignore
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
        from reportlab.lib.units import mm  # type: ignore
        from reportlab.platypus import (  # type: ignore
            HRFlowable,
            ListFlowable,
            ListItem,
            PageBreak,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as exc:  # pragma: no cover
        raise RenderError(
            "Falta reportlab para generar PDF desde documentos. "
            "Instálalo con: pip install reportlab"
        ) from exc

    normal_font, bold_font = _register_pdf_fonts()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ConversorTitle", parent=styles["Title"], fontName=bold_font or normal_font,
        fontSize=24, leading=29, spaceAfter=10, textColor=colors.HexColor("#0f172a"),
    )
    meta_style = ParagraphStyle(
        "ConversorMeta", parent=styles["Normal"], fontName=normal_font, fontSize=8.5,
        leading=12, textColor=colors.HexColor("#6b7280"), spaceAfter=14,
    )
    body_style = ParagraphStyle(
        "ConversorBody", parent=styles["BodyText"], fontName=normal_font, fontSize=10.5,
        leading=15.5, spaceAfter=8, alignment=TA_JUSTIFY, splitLongWords=1,
    )
    heading_styles = {}
    for level, size in ((1, 18), (2, 15), (3, 13), (4, 11.5), (5, 11), (6, 11)):
        heading_styles[level] = ParagraphStyle(
            f"ConversorH{level}", parent=styles["Heading1"], fontName=bold_font or normal_font,
            fontSize=size, leading=size * 1.3, spaceBefore=12, spaceAfter=6,
            textColor=colors.HexColor("#1e3a8a"),
        )
    quote_style = ParagraphStyle(
        "ConversorQuote", parent=body_style, leftIndent=12, rightIndent=8,
        textColor=colors.HexColor("#4b5563"), borderPadding=8, backColor=colors.HexColor("#f8fafc"),
    )
    code_style = ParagraphStyle(
        "ConversorCode", parent=styles["Code"], fontName="Courier", fontSize=8.2,
        leading=10.5, leftIndent=8, backColor=colors.HexColor("#f3f4f6"), borderPadding=6,
        spaceBefore=4, spaceAfter=10,
    )

    def safe(text: str) -> str:
        text = _strip_inline_markdown(text)
        if normal_font == "Helvetica":
            text = text.encode("latin-1", "replace").decode("latin-1")
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )

    story: list = []
    if options.include_title:
        story.append(Paragraph(safe(document.title), title_style))
        story.append(
            Paragraph(
                safe(
                    f"Fuente: {document.source.name} · {document.word_count:,} palabras · "
                    f"{document.page_count} página(s)"
                ),
                meta_style,
            )
        )
        story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#e5e7eb")))
        story.append(Spacer(1, 8))

    for block in document.blocks:
        if block.type == "heading":
            level = max(1, min(6, block.level or 2))
            story.append(Paragraph(safe(block.text), heading_styles[level]))
        elif block.type == "paragraph":
            story.append(Paragraph(safe(block.text), body_style))
        elif block.type == "quote":
            story.append(Paragraph(safe(block.text), quote_style))
        elif block.type == "code":
            story.append(Preformatted(block.text.rstrip(), code_style))
        elif block.type == "list":
            items = [
                ListItem(Paragraph(safe(item), body_style), leftIndent=12)
                for item in block.items
                if item.strip()
            ]
            if items:
                story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=14))
                story.append(Spacer(1, 4))
        elif block.type == "table":
            rows = _pad_rows(block.rows)
            if not rows:
                continue
            data = [[Paragraph(safe(cell), body_style) for cell in row] for row in rows]
            columns = len(rows[0])
            available = A4[0] - 44 * mm
            col_width = available / max(1, columns)
            table = Table(data, colWidths=[col_width] * columns, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef9")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 10))

    if not story:
        story.append(Paragraph(safe(document.title), title_style))

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(normal_font, 8)
        canvas.setFillColor(colors.HexColor("#9ca3af"))
        canvas.drawString(22 * mm, 12 * mm, f"{APP_NAME} · {document.source.name}")
        canvas.drawRightString(A4[0] - 22 * mm, 12 * mm, f"Página {doc.page}")
        canvas.restoreState()

    try:
        pdf = SimpleDocTemplate(
            str(destination),
            pagesize=A4,
            leftMargin=22 * mm,
            rightMargin=22 * mm,
            topMargin=20 * mm,
            bottomMargin=18 * mm,
            title=document.title,
            author=APP_NAME,
            subject=f"Convertido desde {document.source.name}",
        )
        pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    except Exception as exc:
        raise RenderError(f"No se pudo generar el PDF: {exc}") from exc
    return destination

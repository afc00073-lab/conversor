"""Pruebas del núcleo de Conversor Pro (sin interfaz gráfica)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from conversor.config import AppConfig, OUTPUT_BY_ID
from conversor.extract import (
    ExtractOptions,
    extract_csv,
    extract_docx,
    extract_html,
    extract_text,
    parse_page_range,
)
from conversor.model import Block, Document
from conversor.render import (
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


def sample_document() -> Document:
    return Document(
        source=Path("informe.pdf"),
        title="Informe de prueba",
        blocks=[
            Block(type="heading", text="Introducción", level=2),
            Block(type="paragraph", text="Este es un párrafo con **negrita** y `código`."),
            Block(type="list", items=["Primero", "Segundo", "Tercero"]),
            Block(type="quote", text="Una cita interesante."),
            Block(
                type="table",
                rows=[["Nombre", "Valor"], ["Alfa", "1"], ["Beta", "2"]],
            ),
            Block(type="code", text="print('hola')", lang="python"),
        ],
        page_texts=["Página uno", "Página dos"],
        metadata={"formato_origen": "PDF", "autor": "Pruebas"},
    )


def test_parse_page_range() -> None:
    assert parse_page_range("", 5) == [0, 1, 2, 3, 4]
    assert parse_page_range("1-2,4", 5) == [0, 1, 3]
    assert parse_page_range("3", 5) == [2]
    assert parse_page_range("8", 5) == [0, 1, 2, 3, 4]
    assert parse_page_range("4-2", 5) == [1, 2, 3]


def test_render_markdown() -> None:
    text = render_markdown(sample_document(), RenderOptions())
    assert "# Informe de prueba" in text
    assert "## Introducción" in text
    assert "- Primero" in text
    assert "| Nombre | Valor |" in text
    assert "```python" in text


def test_render_text() -> None:
    text = render_text(sample_document(), RenderOptions())
    assert "Informe de prueba" in text
    assert "Introducción" in text
    assert "**negrita**" not in text
    assert "Nombre\tValor" in text


def test_render_html() -> None:
    html = render_html(sample_document(), RenderOptions())
    assert "<!DOCTYPE html>" in html
    assert "Informe de prueba" in html
    assert "<table>" in html
    assert "seccion-1" in html


def test_render_json() -> None:
    payload = json.loads(render_json(sample_document(), RenderOptions()))
    assert payload["fuente"]["titulo"] == "Informe de prueba"
    assert payload["estadisticas"]["paginas"] == 2
    assert payload["bloques"][0]["type"] == "heading"


def test_render_docx(tmp_path: Path) -> None:
    target = tmp_path / "salida.docx"
    render_docx(sample_document(), RenderOptions(), target)
    assert target.exists() and target.stat().st_size > 1000

    from docx import Document as DocxDocument

    docx = DocxDocument(str(target))
    assert any("Informe de prueba" in p.text for p in docx.paragraphs)
    assert len(docx.tables) == 1


def test_render_csv(tmp_path: Path) -> None:
    target = tmp_path / "salida.csv"
    render_csv(sample_document(), RenderOptions(), target)
    content = target.read_text(encoding="utf-8-sig")
    assert "pagina,texto" in content
    assert "Página uno" in content


def test_render_epub(tmp_path: Path) -> None:
    target = tmp_path / "salida.epub"
    render_epub(sample_document(), RenderOptions(), target)
    assert target.exists()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        assert "OEBPS/content.xhtml" in names
        assert "OEBPS/nav.xhtml" in names
        assert b"Informe de prueba" in archive.read("OEBPS/content.xhtml")


def test_render_pdf(tmp_path: Path) -> None:
    target = tmp_path / "salida.pdf"
    render_pdf(sample_document(), RenderOptions(), target)
    assert target.exists()
    assert target.read_bytes().startswith(b"%PDF")
    assert target.stat().st_size > 1000


def test_extract_markdown(tmp_path: Path) -> None:
    source = tmp_path / "documento.md"
    source.write_text(
        "# Título\n\nPárrafo con **negrita**.\n\n- Uno\n- Dos\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
        encoding="utf-8",
    )
    result = extract_text(source, ExtractOptions())
    types = [block.type for block in result.document.blocks]
    assert "heading" in types
    assert "list" in types
    assert "table" in types
    assert result.document.title == "documento"


def test_extract_html(tmp_path: Path) -> None:
    source = tmp_path / "pagina.html"
    source.write_text(
        "<html><head><title>Mi página</title></head><body>"
        "<h1>Hola</h1><p>Texto <b>importante</b>.</p>"
        "<ul><li>A</li><li>B</li></ul>"
        "<table><tr><th>X</th></tr><tr><td>1</td></tr></table>"
        "</body></html>",
        encoding="utf-8",
    )
    result = extract_html(source, ExtractOptions())
    assert result.document.title == "Mi página"
    assert any(block.type == "heading" for block in result.document.blocks)
    assert any(block.type == "table" for block in result.document.blocks)


def test_extract_csv(tmp_path: Path) -> None:
    source = tmp_path / "datos.csv"
    source.write_text("nombre;edad\nAna;30\nLuis;25\n", encoding="utf-8")
    result = extract_csv(source, ExtractOptions())
    table = next(block for block in result.document.blocks if block.type == "table")
    assert table.rows[0] == ["nombre", "edad"]
    assert table.rows[1] == ["Ana", "30"]
    assert result.document.metadata["tipo"] == "tabla"


def test_extract_docx(tmp_path: Path) -> None:
    from docx import Document as DocxDocument

    source = tmp_path / "documento.docx"
    docx = DocxDocument()
    docx.add_heading("Capítulo 1", level=1)
    docx.add_paragraph("Contenido del capítulo.")
    table = docx.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "2"
    docx.save(str(source))

    result = extract_docx(source, ExtractOptions())
    assert any(block.type == "heading" for block in result.document.blocks)
    assert any(block.type == "table" for block in result.document.blocks)


def test_config_roundtrip() -> None:
    config = AppConfig(
        formats=["md", "html", "inventado"],
        ocr_lang="eng",
        jobs=99,
        optimize=9,
        theme="Dark",
        accent="violet",
    )
    restored = AppConfig.from_dict(config.to_dict())
    assert restored.formats == ["md", "html"]
    assert restored.jobs == 8
    assert restored.optimize == 3
    assert restored.theme == "Dark"
    assert restored.accent == "violet"


def test_output_formats_are_unique() -> None:
    ids = [item[0] for item in OUTPUT_BY_ID.values()]
    assert len(ids) == len(set(ids))
    assert {"md", "pdf", "docx", "epub"} <= set(ids)

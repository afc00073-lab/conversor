"""Modelo intermedio de documento, independiente del formato de origen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Block:
    """Un bloque semántico de un documento."""

    type: str = "paragraph"
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    lang: str = ""

    def is_empty(self) -> bool:
        if self.type == "table":
            return not any(any(cell.strip() for cell in row) for row in self.rows)
        if self.type == "list":
            return not any(item.strip() for item in self.items)
        return not self.text.strip()


@dataclass
class Document:
    """Resultado normalizado de extraer un archivo."""

    source: Path
    title: str
    blocks: list[Block] = field(default_factory=list)
    page_texts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- utilidades ---------------------------------------------------------

    def clean_blocks(self) -> list[Block]:
        result: list[Block] = []
        for block in self.blocks:
            if block.type == "paragraph":
                block.text = normalise_paragraph(block.text)
            if block.is_empty():
                continue
            result.append(block)
        return result

    @property
    def plain_text(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if block.type in {"paragraph", "quote", "code"}:
                parts.append(block.text.strip())
            elif block.type == "heading":
                parts.append(block.text.strip())
            elif block.type == "list":
                parts.extend(item.strip() for item in block.items)
            elif block.type == "table":
                for row in block.rows:
                    parts.append("\t".join(cell.strip() for cell in row))
        return "\n\n".join(part for part in parts if part)

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\S+", self.plain_text))

    @property
    def char_count(self) -> int:
        return len(self.plain_text)

    @property
    def page_count(self) -> int:
        if self.page_texts:
            return len(self.page_texts)
        return 1

    def all_text(self) -> str:
        return self.plain_text


def normalise_paragraph(text: str) -> str:
    """Une saltos de línea suaves conservando los párrafos."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Los PDF suelen cortar cada línea; unimos líneas que no terminan frase.
    lines = [line.strip() for line in text.split("\n")]
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()


def blocks_from_plain_text(text: str, *, keep_pages: bool = False, page_number: int = 0) -> list[Block]:
    """Convierte texto plano en párrafos, listas y encabezados suaves."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[Block] = []
    if keep_pages and page_number:
        blocks.append(Block(type="heading", text=f"Página {page_number}", level=2))

    lines = text.split("\n")
    paragraph: list[str] = []
    bullets: list[str] = []

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

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            continue
        bullet_match = re.match(r"^[\u2022\u25cf\u25aa\u25e6\-*]\s+(.*)$", line)
        numbered_match = re.match(r"^\d{1,3}[.)]\s+(.*)$", line)
        if bullet_match or numbered_match:
            flush_paragraph()
            bullets.append((bullet_match or numbered_match).group(1).strip())
            continue
        flush_bullets()
        paragraph.append(line)

    flush_paragraph()
    flush_bullets()
    return blocks

"""Chunking estructural.

- Documentos (PDF/web/texto): respeta encabezados Markdown y párrafos; nunca
  corta a mitad de párrafo salvo que el párrafo solo exceda el tamaño objetivo.
- Transcripciones: agrupa fragmentos consecutivos conservando el rango de tiempo.

El tamaño se estima con ~4 caracteres por token, suficiente para controlar el
contexto; no hace falta el tokenizador exacto del modelo de embeddings.
"""

import re
from dataclasses import dataclass, field

from aula_sama.models import Chunk, ParsedDocument, Segment

CHARS_PER_TOKEN = 4
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass
class _Block:
    text: str
    page: int | None
    headings: list[str] = field(default_factory=list)
    start: float | None = None
    end: float | None = None


def chunk_document(
    doc: ParsedDocument, source_id: str, version: int, target_tokens: int = 650, overlap_tokens: int = 80
) -> list[Chunk]:
    target = target_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    if doc.is_timed:
        blocks = [_Block(s.text, None, start=s.start, end=s.end) for s in doc.segments]
        groups = _pack(blocks, target, overlap, sep=" ")
    elif doc.source_type == "image":
        # Cada imagen es una unidad autocontenida: nunca se mezclan dos en un chunk
        groups = [g for seg in doc.segments for g in _pack(_document_blocks([seg], target), target, overlap, "\n\n")]
    else:
        groups = _pack(_document_blocks(doc.segments, target), target, overlap, sep="\n\n")

    chunks = []
    for i, group in enumerate(groups):
        pages = [b.page for b in group if b.page is not None]
        times = [b for b in group if b.start is not None]
        chunks.append(
            Chunk(
                source_id=source_id,
                version=version,
                index=i,
                text=(" " if doc.is_timed else "\n\n").join(b.text for b in group).strip(),
                heading_path=group[-1].headings,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                start=times[0].start if times else None,
                end=times[-1].end if times else None,
            )
        )
    return chunks


def _document_blocks(segments: list[Segment], max_chars: int) -> list[_Block]:
    """Párrafos con la ruta de encabezados vigente. Los encabezados se adjuntan al párrafo siguiente."""
    blocks: list[_Block] = []
    headings: list[tuple[int, str]] = []
    pending_heading = ""
    for seg in segments:
        for para in re.split(r"\n\s*\n", seg.text):
            para = para.strip()
            if not para:
                continue
            m = _HEADING.match(para) if "\n" not in para else None
            if m:
                level, title = len(m.group(1)), _clean_heading(m.group(2))
                headings = [h for h in headings if h[0] < level] + [(level, title)]
                pending_heading = f"{pending_heading}\n\n{para}".strip()
                continue
            text = f"{pending_heading}\n\n{para}".strip() if pending_heading else para
            pending_heading = ""
            path = [h[1] for h in headings]
            for piece in _split_long(text, max_chars):
                blocks.append(_Block(piece, seg.page, path))
    if pending_heading:
        blocks.append(_Block(pending_heading, segments[-1].page, [h[1] for h in headings]))
    return blocks


def _clean_heading(text: str) -> str:
    return re.sub(r"[*_`]", "", text).strip()


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > max_chars:  # oración patológica (tablas, listas sin puntos)
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _pack(blocks: list[_Block], target: int, overlap: int, sep: str) -> list[list[_Block]]:
    """Agrupa bloques hasta `target` caracteres; el siguiente grupo repite la cola (≤ `overlap`)."""
    groups: list[list[_Block]] = []
    current: list[_Block] = []
    size = 0
    fresh = 0  # bloques nuevos (no solapados) en el grupo actual
    for block in blocks:
        new_section = bool(current) and block.headings[:1] != current[-1].headings[:1] and size > target // 3
        if current and fresh and (size + len(sep) + len(block.text) > target or new_section):
            groups.append(current)
            current = [] if new_section else _tail(current, overlap)
            size = sum(len(b.text) + len(sep) for b in current)
            fresh = 0
        current.append(block)
        size += len(block.text) + len(sep)
        fresh += 1
    if current and fresh:
        groups.append(current)
    return groups


def _tail(blocks: list[_Block], overlap: int) -> list[_Block]:
    tail: list[_Block] = []
    size = 0
    for block in reversed(blocks):
        if size + len(block.text) > overlap:
            break
        tail.insert(0, block)
        size += len(block.text)
    return tail

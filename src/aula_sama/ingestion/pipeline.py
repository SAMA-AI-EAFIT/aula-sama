"""Orquestación de la ingesta: registro → snapshot → parse → chunks → Qdrant.

Idempotente: si el contenido (hash) no cambió respecto a la versión indexada, no
se hace nada. Si cambió, se crea una versión nueva y se reemplazan sus chunks.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from aula_sama import registry
from aula_sama.config import get_settings
from aula_sama.ingestion import parsers
from aula_sama.ingestion.chunking import chunk_document
from aula_sama.ingestion.indexer import Indexer
from aula_sama.ingestion.storage import get_storage
from aula_sama.models import ParsedDocument, SourceMetadata


@dataclass
class IngestResult:
    source_id: str
    version: int
    status: str  # indexed | unchanged | failed
    n_chunks: int = 0
    error: str = ""


def slugify(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "fuente"


def ingest_upload(data: bytes, filename: str, meta: SourceMetadata, source_id: str | None = None) -> IngestResult:
    suffix = Path(filename).suffix.lower() or ".bin"
    if suffix in parsers.AUDIO_VIDEO_SUFFIXES:
        # Se archiva la transcripción, no el medio: los archivos de video pesan demasiado para el storage.
        # El hash se calcula sobre el medio para que resubir el mismo archivo no lo vuelva a transcribir.
        content_hash = hashlib.sha256(data).hexdigest()
        if (cached := _unchanged(source_id or meta.title or Path(filename).stem, content_hash)) is not None:
            return cached
        doc, transcript = parsers.parse_audio_video(data, filename, meta.municipalities)
        return _ingest(doc, transcript, ".transcript.json", meta, source_id, content_hash)
    return _ingest(parsers.parse_upload(data, filename), data, suffix, meta, source_id)


def ingest_images(
    images: list[tuple[str, bytes]], meta: SourceMetadata, source_id: str | None = None
) -> IngestResult:
    """Un conjunto de imágenes como una sola fuente (cada imagen es una página citable)."""
    if not meta.title and not source_id:
        raise ValueError("Un conjunto de imágenes necesita título o ID de fuente.")
    content_hash = parsers.images_digest(images)
    if (cached := _unchanged(source_id or meta.title, content_hash)) is not None:
        return cached  # evita volver a pagar la conversión con Claude
    doc = parsers.parse_images(images, context=meta.title)
    return _ingest(doc, parsers.images_archive(images), ".images.zip", meta, source_id, content_hash)


def ingest_zip(data: bytes, meta: SourceMetadata) -> tuple[list[IngestResult], list[str]]:
    """Cada carpeta con imágenes dentro del zip se ingiere como una fuente aparte.

    El título es la ruta completa de la carpeta: carpetas homónimas en zips distintos
    (p. ej. "mov en masa" en banners y en el material base) no deben pisarse.
    """
    groups, skipped = parsers.read_zip_images(data)
    titles = {f: " / ".join(p.strip().capitalize() for p in Path(f).parts if p != ".") or "Imágenes" for f in groups}
    ids = [slugify(t) for t in titles.values()]
    if len(set(ids)) != len(ids):
        raise parsers.ParseError("Dos carpetas del zip producen el mismo ID de fuente; renombra una.")
    results = []
    for folder, images in groups.items():
        m = SourceMetadata(**{**meta.__dict__, "title": titles[folder]})
        try:
            results.append(ingest_images(images, m))
        except parsers.ParseError as exc:
            results.append(IngestResult(slugify(m.title), 0, "failed", error=str(exc)))
    return results, skipped


def ingest_youtube(url: str, meta: SourceMetadata, source_id: str | None = None) -> IngestResult:
    source_id = source_id or f"yt-{parsers.youtube_video_id(url)}"
    if (cached := _unchanged(source_id)) is not None:
        return cached  # evita volver a pagar la transcripción; para forzarla, eliminar la fuente
    doc, raw = parsers.fetch_youtube(url, meta.municipalities)
    meta.title = meta.title or doc.title_hint
    return _ingest(doc, raw, ".transcript.json", meta, source_id)


def ingest_web(url: str, meta: SourceMetadata, source_id: str | None = None) -> IngestResult:
    doc, raw = parsers.fetch_web(url)
    meta.title = meta.title or doc.title_hint
    suffix = ".pdf" if doc.source_type == "pdf" else ".html"
    return _ingest(doc, raw, suffix, meta, source_id)


def _unchanged(source_id: str, content_hash: str | None = None) -> IngestResult | None:
    """Resultado `unchanged` si la fuente ya está indexada (con ese hash, si se da)."""
    with registry.session() as db:
        source = registry.get_source(db, slugify(source_id))
        latest = source.versions[-1] if source and source.versions else None
        if latest and latest.status == "indexed" and content_hash in (None, latest.content_hash):
            return IngestResult(source.source_id, latest.version, "unchanged", latest.n_chunks)
    return None


def _ingest(
    doc: ParsedDocument,
    raw: bytes,
    suffix: str,
    meta: SourceMetadata,
    source_id: str | None,
    content_hash: str | None = None,
) -> IngestResult:
    settings = get_settings()
    meta.title = meta.title or doc.title_hint or "Sin título"
    source_id = slugify(source_id or meta.title)
    content_hash = content_hash or hashlib.sha256(raw).hexdigest()
    storage = get_storage(settings)
    indexer = Indexer(settings)

    with registry.session() as db:
        source = registry.get_source(db, source_id)
        if source is None:
            source = registry.Source(source_id=source_id, source_type=doc.source_type)
            db.add(source)
        _apply_meta(source, meta, doc)

        latest = source.versions[-1] if source.versions else None
        if latest and latest.content_hash == content_hash and latest.status == "indexed":
            db.commit()  # puede haber cambiado solo la metadata editorial
            return IngestResult(source_id, latest.version, "unchanged", latest.n_chunks)

        db.flush()
        version_no = (db.scalar(select(func.max(registry.SourceVersion.version)).where(
            registry.SourceVersion.source_pk == source.id)) or 0) + 1
        prefix = f"sources/{source_id}/v{version_no}"
        version = registry.SourceVersion(
            source=source,
            version=version_no,
            content_hash=content_hash,
            original_key=storage.put(f"{prefix}/original{suffix}", raw),
            markdown_key=storage.put(f"{prefix}/content.md", doc.markdown.encode()),
            status="processing",
            embedding_model=settings.embedding_model,
        )
        db.add(version)
        db.commit()

        try:
            chunks = chunk_document(
                doc, source_id, version_no, settings.chunk_target_tokens, settings.chunk_overlap_tokens
            )
            n = indexer.upsert(chunks, meta, doc.source_type, source.uri)
            indexer.delete_source(source_id, keep_version=version_no)
        except Exception as exc:
            version.status, version.error = "failed", f"{exc.__class__.__name__}: {exc}"
            db.commit()
            return IngestResult(source_id, version_no, "failed", error=version.error)

        version.status, version.n_chunks = "indexed", n
        source.current_version = version_no
        db.commit()
        return IngestResult(source_id, version_no, "indexed", n)


def _apply_meta(source: registry.Source, meta: SourceMetadata, doc: ParsedDocument) -> None:
    source.title = meta.title
    source.uri = doc.uri or source.uri or ""
    source.publisher = meta.publisher
    source.authority = meta.authority
    source.hazards = list(meta.hazards)
    source.municipalities = list(meta.municipalities)
    source.published_on = meta.published_on
    source.language = meta.language
    source.notes = meta.notes


def delete_source(source_id: str) -> None:
    """Elimina la fuente del índice, del registro y del almacenamiento."""
    Indexer().delete_source(source_id)
    get_storage().delete_prefix(f"sources/{source_id}/")
    with registry.session() as db:
        if source := registry.get_source(db, source_id):
            db.delete(source)
            db.commit()

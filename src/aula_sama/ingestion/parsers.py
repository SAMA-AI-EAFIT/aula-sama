"""Conversión de originales a segmentos con ubicación (página o timestamps)."""

import hashlib
import io
import json
import re
import tempfile
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from aula_sama.config import get_settings
from aula_sama.ingestion import transcription, vision
from aula_sama.models import ParsedDocument, Segment

TRANSCRIPT_LANGUAGES = ("es", "es-419", "es-CO", "es-ES", "en")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_ZIP_BYTES = 1_000_000_000  # tamaño descomprimido; protege contra zips malformados o "bombas"
AUDIO_VIDEO_SUFFIXES = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".mp4", ".webm", ".mov", ".mkv")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class ParseError(Exception):
    pass


def parse_pdf(data: bytes) -> ParsedDocument:
    import pymupdf
    import pymupdf4llm

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        # El OCR solo se aplica a las páginas (o regiones) sin texto extraíble
        pages = pymupdf4llm.to_markdown(
            doc, page_chunks=True, ocr_function=_ocr_function(), ocr_dpi=get_settings().ocr_dpi
        )
        title = (doc.metadata or {}).get("title", "")
    segments = [
        Segment(text=p["text"].strip(), page=p["metadata"]["page_number"]) for p in pages if p["text"].strip()
    ]
    if not segments:
        raise ParseError("No se pudo extraer texto del PDF, ni siquiera con OCR.")
    return ParsedDocument(source_type="pdf", segments=segments, title_hint=title)


@cache
def _ocr_function():
    """OCR con RapidOCR (instalado con pip, sin dependencias del sistema) y su modelo latino.

    Por defecto pymupdf4llm usa el modelo chino/inglés con imágenes reducidas a 2000 px,
    que pierde tildes y líneas largas. Reemplazamos su motor antes del primer uso; depende
    de internos de pymupdf4llm, por eso las versiones quedan fijadas en uv.lock.
    """
    from pymupdf4llm.ocr import rapidocr_391_backend, rapidocr_api
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

    rapidocr_391_backend.ENGINE = RapidOCR(
        params={
            "Global.max_side_len": 4000,
            "Rec.lang_type": LangRec.LATIN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
        }
    )
    return rapidocr_api.exec_ocr


def parse_text(data: bytes, filename: str = "") -> ParsedDocument:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        raise ParseError("El archivo está vacío.")
    return ParsedDocument(source_type="text", segments=[Segment(text=text)], title_hint=Path(filename).stem)


def youtube_video_id(url: str) -> str:
    """Extrae el ID de video de las formas usuales de URL de YouTube."""
    url = url.strip()
    if _YT_ID.match(url):
        return url
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]
    if not _YT_ID.match(candidate):
        raise ParseError(f"No se reconoce como URL de YouTube: {url}")
    return candidate


def fetch_youtube(url: str, keywords: list[str] = ()) -> tuple[ParsedDocument, bytes]:
    """Transcripción con timestamps: subtítulos de YouTube si existen, si no se transcribe el audio.

    Devuelve el documento y el JSON crudo para archivar.
    """
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import CouldNotRetrieveTranscript

    video_id = youtube_video_id(url)
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=TRANSCRIPT_LANGUAGES)
        snippets = [s for s in fetched.snippets if s.text.strip()]
        segments = [
            Segment(text=" ".join(s.text.split()), start=s.start, end=s.start + s.duration) for s in snippets
        ]
        method = f"youtube-captions:{fetched.language_code}" + (":auto" if fetched.is_generated else "")
    except CouldNotRetrieveTranscript as exc:
        if not get_settings().openai_api_key:
            raise ParseError(
                f"{video_id} no tiene subtítulos ({exc.__class__.__name__}) y falta OPENAI_API_KEY para transcribirlo."
            ) from exc
        with tempfile.TemporaryDirectory() as tmp:
            try:
                audio = transcription.download_audio(canonical, Path(tmp))
                segments = transcription.transcribe_file(audio, keywords)
            except transcription.TranscriptionError as err:
                raise ParseError(str(err)) from err
        method = f"openai:{get_settings().transcription_model}"

    doc = ParsedDocument(source_type="youtube", segments=segments, title_hint=_youtube_title(canonical), uri=canonical)
    return doc, _transcript_json(doc, method, video_id=video_id)


def parse_audio_video(data: bytes, filename: str, keywords: list[str] = ()) -> tuple[ParsedDocument, bytes]:
    """Transcribe un archivo de audio o video subido. El JSON de la transcripción también se archiva."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"media{Path(filename).suffix.lower()}"
        path.write_bytes(data)
        try:
            segments = transcription.transcribe_file(path, keywords)
        except transcription.TranscriptionError as exc:
            raise ParseError(str(exc)) from exc
    doc = ParsedDocument(source_type="audio", segments=segments, title_hint=Path(filename).stem)
    return doc, _transcript_json(doc, f"openai:{get_settings().transcription_model}", filename=filename)


def _transcript_json(doc: ParsedDocument, method: str, **extra) -> bytes:
    raw = {
        **extra,
        "method": method,
        "segments": [{"text": s.text, "start": s.start, "end": s.end} for s in doc.segments],
    }
    return json.dumps(raw, ensure_ascii=False).encode()


def _youtube_title(url: str) -> str:
    try:
        r = httpx.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=10)
        r.raise_for_status()
        return r.json().get("title", "")
    except httpx.HTTPError:
        return ""


def fetch_web(url: str) -> tuple[ParsedDocument, bytes]:
    """Descarga una página web y extrae el contenido principal como Markdown."""
    import trafilatura

    try:
        r = httpx.get(url, follow_redirects=True, timeout=30, headers={"User-Agent": "Aula-SAMA ingest"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseError(f"No se pudo descargar {url}: {exc}") from exc
    if "pdf" in r.headers.get("content-type", ""):
        doc = parse_pdf(r.content)
        doc.uri = str(r.url)
        return doc, r.content
    text = trafilatura.extract(r.text, output_format="markdown", include_tables=True, url=str(r.url))
    if not text:
        raise ParseError(f"No se encontró contenido principal en {url}")
    meta = trafilatura.extract_metadata(r.text)
    doc = ParsedDocument(
        source_type="web", segments=[Segment(text=text)], title_hint=(meta.title if meta else "") or "", uri=str(r.url)
    )
    return doc, r.content


def parse_images(images: list[tuple[str, bytes]], context: str = "") -> ParsedDocument:
    """Un conjunto de imágenes (p. ej. una carpeta) → un documento; cada imagen es una "página"."""
    try:
        texts = vision.describe_images(images, context)
    except vision.VisionError as exc:
        raise ParseError(str(exc)) from exc
    segments = [
        Segment(text=f"{text}\n\n_Imagen: {Path(name).name}_", page=i)
        for i, ((name, _), text) in enumerate(zip(images, texts, strict=True), start=1)
    ]
    return ParsedDocument(source_type="image", segments=segments)


def images_digest(images: list[tuple[str, bytes]]) -> str:
    """Hash estable del conjunto (nombres + contenido) para no reprocesar lo ya indexado."""
    h = hashlib.sha256()
    for name, data in images:
        h.update(Path(name).name.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def images_archive(images: list[tuple[str, bytes]]) -> bytes:
    """Empaqueta las imágenes originales en un zip para archivarlas como un solo objeto."""
    out = tempfile.SpooledTemporaryFile()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for name, data in images:
            zf.writestr(zipfile.ZipInfo(Path(name).name, date_time=(2000, 1, 1, 0, 0, 0)), data)
    out.seek(0)
    return out.read()


def read_zip_images(data: bytes) -> tuple[dict[str, list[tuple[str, bytes]]], list[str]]:
    """Imágenes de un zip agrupadas por carpeta, sin duplicados exactos.

    Devuelve `{carpeta: [(ruta, bytes), ...]}` y la lista de archivos omitidos. Se lee en
    memoria, sin extraer a disco, así que rutas como `../` no tienen efecto.
    """
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ParseError("El archivo no es un zip válido.") from exc
    entries = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
    if sum(i.file_size for i in entries) > MAX_ZIP_BYTES:
        raise ParseError("El zip descomprimido supera 1 GB.")

    groups: dict[str, list[tuple[str, bytes]]] = defaultdict(list)
    skipped, seen = [], set()
    for info in sorted(entries, key=lambda i: i.filename):
        name = _zip_name(info)
        if Path(name).suffix.lower() not in IMAGE_SUFFIXES or Path(name).name.startswith("."):
            skipped.append(name)
            continue
        content = zf.read(info)
        digest = hashlib.sha256(content).digest()
        if digest in seen:
            skipped.append(f"{name} (duplicado)")
            continue
        seen.add(digest)
        groups[str(Path(name).parent)].append((name, content))
    return dict(groups), skipped


def _zip_name(info: zipfile.ZipInfo) -> str:
    # Zips creados en Windows/OneDrive sin la marca UTF-8 traen los nombres en cp437 mal decodificados
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


MIN_FIGURE_BYTES = 15_000  # imágenes menores suelen ser íconos o logos: no vale la pena describirlas


def parse_docx(data: bytes, filename: str = "") -> ParsedDocument:
    """Word → Markdown en el orden del documento: párrafos, listas, tablas y figuras.

    Las figuras se convierten a texto con Claude (visión) en su posición, usando el pie de
    figura cercano como contexto; muchos documentos técnicos tienen el contenido en imágenes.
    """
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"No se pudo abrir el documento Word: {exc}") from exc

    blocks: list[str | tuple[str, bytes]] = []  # texto, o (nombre, bytes) de una figura pendiente
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            blocks.append(_docx_table(item))
            continue
        assert isinstance(item, Paragraph)
        blocks.extend(_docx_figures(item, document))
        if text := _docx_paragraph(item):
            blocks.append(text)

    figures = [b for b in blocks if isinstance(b, tuple)]
    if figures:
        texts = _docx_figure_texts(blocks, filename)
        it = iter(texts)
        blocks = [f"{next(it)}\n\n_Figura del documento_" if isinstance(b, tuple) else b for b in blocks]
    text = "\n\n".join(b for b in blocks if b.strip())
    if not text.strip():
        raise ParseError("El documento Word no tiene texto ni figuras.")
    # El título interno de Word suele ser basura de plantillas; el nombre del archivo es más fiable
    return ParsedDocument(source_type="docx", segments=[Segment(text=text)], title_hint=Path(filename).stem)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(p) -> str:
    """Texto de un párrafo de Word, incluidos los cambios con control aceptados.

    `Paragraph.text` de python-docx omite el texto insertado con control de cambios (`w:ins`)
    y el de algunos contenedores; aquí se lee todo `w:t` (lo borrado va en `w:delText`).
    """
    parts = []
    for el in p._p.iter(f"{_W}t", f"{_W}tab", f"{_W}br", f"{_W}cr"):
        parts.append(el.text or "" if el.tag == f"{_W}t" else "\t" if el.tag == f"{_W}tab" else "\n")
    return "".join(parts)


def docx_is_list(p) -> bool:
    """Ítem de lista de Word: la numeración puede estar en el párrafo o heredarse del estilo."""
    style = p.style
    while style is not None:
        if style.element.pPr is not None and style.element.pPr.numPr is not None:
            return True
        style = style.base_style
    return p._p.pPr is not None and p._p.pPr.numPr is not None


def _docx_paragraph(p) -> str:
    text = re.sub(r"[ \t]+", " ", docx_text(p)).strip()
    if not text:
        return ""
    style = (p.style.name if p.style is not None else "").lower()
    if style == "title":
        return f"# {text}"
    if m := re.match(r"heading (\d)", style):
        return f"{'#' * (int(m.group(1)) + 1)} {text}"
    runs = [r for r in p.runs if r.text.strip()]
    # Estos documentos no usan estilos de título: una línea corta toda en negrita hace de subtítulo
    if runs and all(r.bold for r in runs) and len(text) <= 120 and "\n" not in text:
        return f"### {text}"
    if docx_is_list(p):
        return f"- {text}"
    return text


_OLD_VERSION_HEADER = re.compile(r"\b(actual|anterior|vigente|versi[oó]n anterior)\b", re.I)
_EDITORIAL_NOTE = re.compile(r"^\s*nota\s*:", re.I)


def _docx_version_table(table) -> str | None:
    """Tabla que compara la versión vigente de un texto con la nueva («Aula SAMA – Actual» |
    «Aula SAMA 2026»): solo se indexa la última columna, para que el chat no cite textos
    reemplazados. Las notas editoriales («Nota: Figura 2 (editar)») tampoco se indexan.
    """
    rows = table.rows
    if len(rows) < 2 or len(rows[0].cells) < 2 or not _OLD_VERSION_HEADER.search(docx_text(rows[0].cells[0].paragraphs[0])):
        return None
    parts = []
    for row in rows[1:]:
        for p in row.cells[-1].paragraphs:
            if text := _docx_paragraph(p):
                # Una nota puede ir al final de un párrafo, tras un salto de línea
                text = "\n".join(l for l in text.split("\n") if not _EDITORIAL_NOTE.match(l)).strip()
                if text and not _EDITORIAL_NOTE.match(text.lstrip("#- ")):
                    parts.append(text)
    return "\n\n".join(parts)


def _docx_table(table) -> str:
    if (latest := _docx_version_table(table)) is not None:
        return latest

    def cell(c) -> str:
        return re.sub(r"\s+", " ", " ".join(docx_text(p) for p in c.paragraphs)).strip().replace("|", "\\|")

    rows = [[cell(c) for c in row.cells] for row in table.rows]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if max(len(c) for r in rows for c in r) > 300:
        # Celdas con párrafos enteros: como tabla Markdown quedan ilegibles y no se pueden partir en chunks
        header = rows[0]
        return "\n\n".join(
            f"**{h or f'Columna {j + 1}'}:** {c}" for r in rows[1:] for j, (h, c) in enumerate(zip(header, r)) if c
        )
    lines =["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _docx_figures(p, document) -> list[tuple[str, bytes]]:
    out = []
    for blip in p._p.xpath(".//a:blip"):
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        part = document.part.related_parts.get(rid) if rid else None
        blob = getattr(part, "blob", b"")
        if len(blob) >= MIN_FIGURE_BYTES:
            out.append((Path(str(part.partname)).name, blob))
    return out


def _docx_figure_texts(blocks: list, filename: str) -> list[str]:
    """Describe las figuras en paralelo; el contexto es el título y el texto que las rodea."""
    figures, contexts = [], []
    for i, b in enumerate(blocks):
        if isinstance(b, tuple):
            near = [x for x in blocks[max(0, i - 2) : i + 3] if isinstance(x, str)]
            contexts.append(f"Figura del documento «{Path(filename).stem}». Texto cercano: {' / '.join(near)[:500]}")
            figures.append(b)
    settings = get_settings()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(lambda fc: vision.describe_image(fc[0][1], fc[1], settings), zip(figures, contexts)))
    except vision.VisionError as exc:
        raise ParseError(f"No se pudieron procesar las figuras del documento: {exc}") from exc


def parse_upload(data: bytes, filename: str) -> ParsedDocument:
    """Documentos de texto (PDF/Word/MD/TXT). Audio y video van por `parse_audio_video`."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(data)
    if suffix == ".docx":
        return parse_docx(data, filename)
    if suffix in (".md", ".txt"):
        return parse_text(data, filename)
    raise ParseError(f"Tipo de archivo no soportado: {suffix or filename}")

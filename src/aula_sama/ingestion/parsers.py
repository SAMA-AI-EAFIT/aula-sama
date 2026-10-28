"""Conversión de originales a segmentos con ubicación (página o timestamps)."""

import hashlib
import json
import re
import tempfile
import zipfile
from collections import defaultdict
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


def parse_upload(data: bytes, filename: str) -> ParsedDocument:
    """Documentos de texto (PDF/MD/TXT). Audio y video van por `parse_audio_video`."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(data)
    if suffix in (".md", ".txt"):
        return parse_text(data, filename)
    raise ParseError(f"Tipo de archivo no soportado: {suffix or filename}")

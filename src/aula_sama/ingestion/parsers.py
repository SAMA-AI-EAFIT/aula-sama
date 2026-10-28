"""Conversión de originales a segmentos con ubicación (página o timestamps)."""

import json
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from aula_sama.config import get_settings
from aula_sama.ingestion import transcription
from aula_sama.models import ParsedDocument, Segment

TRANSCRIPT_LANGUAGES = ("es", "es-419", "es-CO", "es-ES", "en")
AUDIO_VIDEO_SUFFIXES = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".mp4", ".webm", ".mov", ".mkv")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class ParseError(Exception):
    pass


def parse_pdf(data: bytes) -> ParsedDocument:
    import pymupdf
    import pymupdf4llm

    settings = get_settings()
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        # El OCR solo se aplica a las páginas (o regiones) sin texto extraíble
        pages = pymupdf4llm.to_markdown(
            doc, page_chunks=True, ocr_language=settings.ocr_language, ocr_dpi=settings.ocr_dpi
        )
        title = (doc.metadata or {}).get("title", "")
    segments = [
        Segment(text=p["text"].strip(), page=p["metadata"]["page_number"]) for p in pages if p["text"].strip()
    ]
    if not segments:
        raise ParseError("No se pudo extraer texto del PDF, ni siquiera con OCR.")
    return ParsedDocument(source_type="pdf", segments=segments, title_hint=title)


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


def parse_upload(data: bytes, filename: str) -> ParsedDocument:
    """Documentos de texto (PDF/MD/TXT). Audio y video van por `parse_audio_video`."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(data)
    if suffix in (".md", ".txt"):
        return parse_text(data, filename)
    raise ParseError(f"Tipo de archivo no soportado: {suffix or filename}")

import pytest

from aula_sama.ingestion.chunking import chunk_document
from aula_sama.ingestion.parsers import ParseError, youtube_video_id
from aula_sama.ingestion.pipeline import slugify
from aula_sama.models import ParsedDocument, Segment


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "youtube.com/embed/dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ],
)
def test_youtube_video_id(url):
    assert youtube_video_id(url) == "dQw4w9WgXcQ"


def test_youtube_video_id_rejects_other_urls():
    with pytest.raises(ParseError):
        youtube_video_id("https://www.dagran.gov.co/video")


def test_slugify():
    assert slugify("Plan Municipal de Gestión del Riesgo — Medellín 2026") == "plan-municipal-de-gestion-del-riesgo-medellin-2026"


def _pdf_like(pages: int, paras_per_page: int) -> ParsedDocument:
    segments = []
    for p in range(1, pages + 1):
        body = "\n\n".join(f"Párrafo {p}.{i}. " + "Las crecientes súbitas ocurren en cuencas pequeñas. " * 6 for i in range(paras_per_page))
        segments.append(Segment(text=f"## Capítulo {p}\n\n{body}", page=p))
    return ParsedDocument(source_type="pdf", segments=segments)


def test_chunks_respect_size_and_track_pages():
    chunks = chunk_document(_pdf_like(4, 8), "doc", 1, target_tokens=300, overlap_tokens=40)
    assert len(chunks) > 4
    assert all(len(c.text) <= 300 * 4 * 1.1 for c in chunks)
    assert all(c.page_start is not None and c.page_start <= c.page_end for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].heading_path == ["Capítulo 1"]
    assert chunks[0].chunk_id == "doc#c000"


def test_chunks_cover_all_paragraphs():
    doc = _pdf_like(3, 5)
    text = " ".join(c.text for c in chunk_document(doc, "doc", 1, target_tokens=200, overlap_tokens=0))
    for p in range(1, 4):
        for i in range(5):
            assert f"Párrafo {p}.{i}." in text


def test_long_paragraph_is_split():
    doc = ParsedDocument(source_type="text", segments=[Segment(text="Frase de prueba larga. " * 500)])
    chunks = chunk_document(doc, "t", 1, target_tokens=100, overlap_tokens=0)
    assert len(chunks) > 5
    assert all(len(c.text) <= 400 for c in chunks)


def test_transcript_chunks_keep_timestamps():
    segs = [Segment(text=f"frase número {i} sobre alertas tempranas", start=i * 3.0, end=i * 3.0 + 3) for i in range(200)]
    chunks = chunk_document(ParsedDocument(source_type="youtube", segments=segs), "yt-x", 1, 150, 20)
    assert len(chunks) > 3
    assert chunks[0].start == 0.0
    assert all(c.start < c.end for c in chunks)
    assert chunks[-1].end == 200 * 3.0
    assert chunks[1].start < chunks[0].end  # solapamiento
    assert chunks[0].locator.startswith("0:00-")

"""Contratos documentales compartidos entre ingesta y retrieval."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

SourceType = Literal["pdf", "youtube", "audio", "web", "text"]
Authority = Literal["oficial", "academica", "otra"]
Hazard = Literal["inundacion", "movimiento_en_masa", "avenida_torrencial", "general"]


@dataclass
class SourceMetadata:
    """Metadata editorial de una fuente, capturada en la pantalla de administración."""

    title: str
    publisher: str = ""
    authority: Authority = "oficial"
    hazards: list[Hazard] = field(default_factory=list)
    municipalities: list[str] = field(default_factory=list)
    published_on: date | None = None
    language: str = "es"
    notes: str = ""


@dataclass
class Segment:
    """Unidad mínima extraída de un original, con su ubicación en la fuente.

    Para PDF `page` está definido; para video `start`/`end` (segundos).
    """

    text: str
    page: int | None = None
    start: float | None = None
    end: float | None = None


@dataclass
class ParsedDocument:
    source_type: SourceType
    segments: list[Segment]
    title_hint: str = ""
    uri: str = ""

    @property
    def is_timed(self) -> bool:
        """Transcripciones (video/audio): los segmentos tienen timestamps en lugar de páginas."""
        return bool(self.segments) and self.segments[0].start is not None

    @property
    def markdown(self) -> str:
        if self.is_timed:
            return "\n\n".join(f"[{_fmt_time(s.start)}] {s.text}" for s in self.segments)
        return "\n\n".join(s.text for s in self.segments)


@dataclass
class Chunk:
    source_id: str
    version: int
    index: int
    text: str
    heading_path: list[str] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    start: float | None = None
    end: float | None = None

    @property
    def chunk_id(self) -> str:
        return f"{self.source_id}#c{self.index:03d}"

    @property
    def locator(self) -> str:
        """Ubicación legible para citación: páginas o rango de tiempo."""
        if self.page_start is not None:
            if self.page_end and self.page_end != self.page_start:
                return f"pp. {self.page_start}-{self.page_end}"
            return f"p. {self.page_start}"
        if self.start is not None:
            return f"{_fmt_time(self.start)}-{_fmt_time(self.end or self.start)}"
        return ""


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

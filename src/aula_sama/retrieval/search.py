"""Retrieval: búsqueda híbrida (dense + BM25, fusión RRF) seguida de reranking con Voyage."""

from dataclasses import dataclass
from functools import cached_property

from qdrant_client import models

from aula_sama.config import Settings, get_settings
from aula_sama.ingestion.indexer import Indexer


@dataclass
class Evidence:
    """Un chunk recuperado, con todo lo necesario para citarlo."""

    chunk_id: str
    text: str
    title: str
    locator: str
    uri: str
    source_type: str
    publisher: str
    authority: str
    heading_path: list[str]
    start: float | None = None
    fusion_score: float = 0.0
    rerank_score: float | None = None

    @classmethod
    def from_point(cls, point: models.ScoredPoint) -> "Evidence":
        p = point.payload or {}
        return cls(
            chunk_id=p["chunk_id"],
            text=p["text"],
            title=p.get("title", ""),
            locator=p.get("locator", ""),
            uri=p.get("uri", ""),
            source_type=p.get("source_type", ""),
            publisher=p.get("publisher", ""),
            authority=p.get("authority", ""),
            heading_path=p.get("heading_path") or [],
            start=p.get("start"),
            fusion_score=point.score,
        )

    @property
    def link(self) -> str:
        """Enlace a la fuente; para YouTube, al segundo exacto."""
        if self.source_type == "youtube" and self.uri and self.start is not None:
            return f"{self.uri}&t={int(self.start)}s"
        return self.uri

    @property
    def label(self) -> str:
        return f"{self.title} ({self.locator})" if self.locator else self.title


class Retriever:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.indexer = Indexer(self.settings)

    @cached_property
    def _voyage(self):
        return self.indexer.voyage

    def retrieve(self, query: str, filters: models.Filter | None = None) -> list[Evidence]:
        """Candidatos de la búsqueda híbrida, ordenados por RRF."""
        s = self.settings
        dense, sparse = self.indexer.embed_query(query)
        points = self.indexer.qdrant.query_points(
            self.indexer.collection,
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=s.retrieval_prefetch, filter=filters),
                models.Prefetch(query=sparse, using="bm25", limit=s.retrieval_prefetch, filter=filters),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=s.retrieval_candidates,
            with_payload=True,
        ).points
        return [Evidence.from_point(p) for p in points]

    def rerank(self, query: str, candidates: list[Evidence]) -> list[Evidence]:
        """Reordena con el reranker y devuelve los `rerank_top_k` mejores, con su score (0-1)."""
        if not candidates:
            return []
        result = self._voyage.rerank(
            query,
            [f"{c.label}\n\n{c.text}" for c in candidates],
            model=self.settings.rerank_model,
            top_k=self.settings.rerank_top_k,
        )
        ranked = []
        for r in result.results:
            evidence = candidates[r.index]
            evidence.rerank_score = r.relevance_score
            ranked.append(evidence)
        return ranked

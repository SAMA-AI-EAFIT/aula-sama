"""Embeddings (Voyage dense + BM25 sparse) y escritura en Qdrant."""

import uuid
from functools import cached_property

from qdrant_client import QdrantClient, models

from aula_sama.config import Settings, get_settings
from aula_sama.models import Chunk, SourceMetadata

DENSE = "dense"
SPARSE = "bm25"
_POINT_NS = uuid.UUID("6f1d7a52-3c1e-4b8e-9d0a-5a8f2c1b7e44")
_VOYAGE_BATCH = 64


def point_id(source_id: str, version: int, index: int) -> str:
    """ID determinista: reingestar la misma versión sobrescribe en vez de duplicar."""
    return str(uuid.uuid5(_POINT_NS, f"{source_id}:v{version}:c{index}"))


def embedding_text(chunk: Chunk, meta: SourceMetadata) -> str:
    """Texto que se vectoriza: el chunk con su contexto documental (título y sección)."""
    header = [f"Fuente: {meta.title}"]
    if chunk.heading_path:
        header.append("Sección: " + " > ".join(chunk.heading_path))
    return "\n".join(header) + "\n\n" + chunk.text


class Indexer:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.collection = self.settings.qdrant_collection

    @cached_property
    def qdrant(self) -> QdrantClient:
        return QdrantClient(url=self.settings.qdrant_url, api_key=self.settings.qdrant_api_key or None, timeout=60)

    @cached_property
    def voyage(self):
        import voyageai

        if not self.settings.voyage_api_key:
            raise RuntimeError("Falta VOYAGE_API_KEY en .env")
        return voyageai.Client(api_key=self.settings.voyage_api_key)

    @cached_property
    def sparse(self):
        from fastembed import SparseTextEmbedding

        return SparseTextEmbedding(self.settings.sparse_model, language="spanish")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _VOYAGE_BATCH):
            batch = texts[i : i + _VOYAGE_BATCH]
            vectors.extend(self.voyage.embed(batch, model=self.settings.embedding_model, input_type="document").embeddings)
        return vectors

    def embed_query(self, text: str) -> tuple[list[float], models.SparseVector]:
        dense = self.voyage.embed([text], model=self.settings.embedding_model, input_type="query").embeddings[0]
        sparse = next(iter(self.sparse.query_embed(text)))
        return dense, models.SparseVector(indices=sparse.indices.tolist(), values=sparse.values.tolist())

    def ensure_collection(self, dim: int) -> None:
        if self.qdrant.collection_exists(self.collection):
            existing = self.qdrant.get_collection(self.collection).config.params.vectors[DENSE].size
            if existing != dim:
                raise RuntimeError(
                    f"La colección '{self.collection}' tiene vectores de {existing} dims y el modelo "
                    f"{self.settings.embedding_model} produce {dim}. Usa otra QDRANT_COLLECTION o reindexa todo."
                )
            return
        self.qdrant.create_collection(
            self.collection,
            vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        for field, schema in [
            ("source_id", models.PayloadSchemaType.KEYWORD),
            ("version", models.PayloadSchemaType.INTEGER),
            ("source_type", models.PayloadSchemaType.KEYWORD),
            ("authority", models.PayloadSchemaType.KEYWORD),
            ("hazards", models.PayloadSchemaType.KEYWORD),
            ("municipalities", models.PayloadSchemaType.KEYWORD),
        ]:
            self.qdrant.create_payload_index(self.collection, field, schema)

    def upsert(self, chunks: list[Chunk], meta: SourceMetadata, source_type: str, uri: str) -> int:
        if not chunks:
            return 0
        texts = [embedding_text(c, meta) for c in chunks]
        dense = self.embed_documents(texts)
        sparse = list(self.sparse.embed(texts))
        self.ensure_collection(len(dense[0]))

        points = [
            models.PointStruct(
                id=point_id(c.source_id, c.version, c.index),
                vector={
                    DENSE: d,
                    SPARSE: models.SparseVector(indices=s.indices.tolist(), values=s.values.tolist()),
                },
                payload={
                    "chunk_id": c.chunk_id,
                    "source_id": c.source_id,
                    "version": c.version,
                    "chunk_index": c.index,
                    "text": c.text,
                    "heading_path": c.heading_path,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "start": c.start,
                    "end": c.end,
                    "locator": c.locator,
                    "title": meta.title,
                    "publisher": meta.publisher,
                    "authority": meta.authority,
                    "hazards": meta.hazards,
                    "municipalities": meta.municipalities,
                    "published_on": meta.published_on.isoformat() if meta.published_on else None,
                    "language": meta.language,
                    "source_type": source_type,
                    "uri": uri,
                    "embedding_model": self.settings.embedding_model,
                },
            )
            for c, d, s in zip(chunks, dense, sparse, strict=True)
        ]
        self.qdrant.upsert(self.collection, points=points, wait=True)
        return len(points)

    def delete_source(self, source_id: str, keep_version: int | None = None) -> None:
        """Borra los chunks de una fuente; con `keep_version`, solo los de otras versiones."""
        if not self.qdrant.collection_exists(self.collection):
            return
        must = [models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id))]
        must_not = []
        if keep_version is not None:
            must_not.append(models.FieldCondition(key="version", match=models.MatchValue(value=keep_version)))
        self.qdrant.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=models.Filter(must=must, must_not=must_not)),
            wait=True,
        )

    def search(self, query: str, limit: int = 8, prefetch: int = 30) -> list[models.ScoredPoint]:
        """Búsqueda híbrida dense + sparse con fusión RRF (sin rerank; para verificar la ingesta)."""
        dense, sparse = self.embed_query(query)
        return self.qdrant.query_points(
            self.collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=prefetch),
                models.Prefetch(query=sparse, using=SPARSE, limit=prefetch),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        ).points

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "aula_sama_chunks"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aula_sama"

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "data/storage"
    s3_bucket: str = ""
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str = "us-east-1"

    embedding_model: str = "voyage-4"
    sparse_model: str = "Qdrant/bm25"

    openai_api_key: str = ""
    transcription_model: str = "gpt-transcribe"
    # Términos que el modelo de transcripción debe reconocer literalmente
    transcription_keywords: list[str] = [
        "Antioquia", "Valle de Aburrá", "DAGRAN", "SIATA", "UNGRD", "IDEAM", "SAMA", "Corantioquia",
        "Área Metropolitana", "avenida torrencial", "movimiento en masa", "río Medellín",
    ]

    # Voz de la guardiana en el chat (texto a voz de OpenAI). tts_voice acepta una voz integrada
    # ("marin", "coral"...) o el ID "voice_..." de una voz personalizada
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "marin"
    tts_instructions: str = (
        "Habla en español colombiano con un acento paisa (antioqueño) natural y suave. Voz cálida, "
        "cercana y serena de una mujer joven; ritmo pausado y claro, sin exagerar el acento. Lee "
        "SAMA y DAGRAN como palabras. Si el texto habla de una emergencia, usa un tono firme y calmado."
    )

    # Imágenes (infografías) → Markdown con Claude
    vision_model: str = "claude-opus-5-5"
    vision_effort: str = "low"

    # Resolución a la que se renderizan las páginas escaneadas para el OCR
    ocr_dpi: int = 300

    # Retrieval: candidatos de la búsqueda híbrida → reranker → evidencia para el LLM
    retrieval_prefetch: int = 30
    retrieval_candidates: int = 25
    rerank_model: str = "rerank-3"
    rerank_top_k: int = 6

    # Evidence gate (scores del reranker, 0-1). Provisionales: calibrar con el conjunto de evaluación
    evidence_min_top_score: float = 0.75
    evidence_min_chunk_score: float = 0.5

    # Chat (Claude)
    chat_model: str = "claude-opus-5-5"
    classify_effort: str = "low"
    answer_effort: str = "medium"

    # Límites de uso del chat (protegen costo y reducen intentos de manipulación)
    max_input_chars: int = 1000
    max_turns_per_conversation: int = 30
    max_messages_per_minute: int = 8

    chunk_target_tokens: int = 650
    chunk_overlap_tokens: int = 80

    admin_password: str = ""

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        # Los proveedores gestionados entregan "postgresql://..."; SQLAlchemy lo asociaría a psycopg2.
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()

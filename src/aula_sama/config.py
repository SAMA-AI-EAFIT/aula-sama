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

    # Resolución a la que se renderizan las páginas escaneadas para el OCR
    ocr_dpi: int = 300

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

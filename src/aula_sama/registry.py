"""Registro de fuentes y versiones (PostgreSQL; SQLite sirve para pruebas locales).

Qdrant solo guarda chunks para recuperación; la fuente de verdad sobre qué se
ingirió, cuándo y desde qué original vive aquí.
"""

from datetime import date, datetime, timezone
from functools import lru_cache

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from aula_sama.config import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    uri: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    authority: Mapped[str] = mapped_column(String(20), default="oficial")
    hazards: Mapped[list] = mapped_column(JSON, default=list)
    municipalities: Mapped[list] = mapped_column(JSON, default=list)
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="es")
    notes: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    versions: Mapped[list["SourceVersion"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="SourceVersion.version"
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (UniqueConstraint("source_pk", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_pk: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    original_key: Mapped[str] = mapped_column(Text)
    markdown_key: Mapped[str] = mapped_column(Text, default="")
    # pending | processing | indexed | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    source: Mapped[Source] = relationship(back_populates="versions")


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def session() -> Session:
    return _sessionmaker()()


def list_sources(db: Session) -> list[Source]:
    return list(db.scalars(select(Source).order_by(Source.updated_at.desc())))


def get_source(db: Session, source_id: str) -> Source | None:
    return db.scalar(select(Source).where(Source.source_id == source_id))

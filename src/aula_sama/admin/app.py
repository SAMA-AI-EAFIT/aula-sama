"""Pantalla de administración del corpus.

    uv run streamlit run src/aula_sama/admin/app.py
"""

import hmac
from datetime import date

import streamlit as st

from aula_sama import registry
from aula_sama.config import get_settings
from aula_sama.ingestion import pipeline
from aula_sama.ingestion.indexer import Indexer
from aula_sama.ingestion.parsers import AUDIO_VIDEO_SUFFIXES, ParseError
from aula_sama.ingestion.storage import get_storage
from aula_sama.models import SourceMetadata

HAZARDS = {
    "inundacion": "Inundación",
    "movimiento_en_masa": "Movimiento en masa",
    "avenida_torrencial": "Avenida torrencial",
    "general": "Gestión del riesgo (general)",
}
AUTHORITIES = {"oficial": "Oficial", "academica": "Académica", "otra": "Otra"}

st.set_page_config(page_title="Aula-SAMA · Admin", page_icon="📚", layout="wide")


def require_login() -> None:
    password = get_settings().admin_password
    if not password or st.session_state.get("authed"):
        if not password:
            st.sidebar.warning("ADMIN_PASSWORD no está definido: la pantalla no tiene protección.")
        return
    with st.form("login"):
        attempt = st.text_input("Contraseña de administrador", type="password")
        if st.form_submit_button("Entrar"):
            if hmac.compare_digest(attempt, password):
                st.session_state.authed = True
                st.rerun()
            st.error("Contraseña incorrecta")
    st.stop()


def metadata_form(key: str, title_required: bool) -> tuple[SourceMetadata, str | None]:
    c1, c2 = st.columns(2)
    title = c1.text_input(
        "Título" + ("" if title_required else " (opcional: se toma de la fuente)"), key=f"{key}_title"
    )
    source_id = c2.text_input(
        "ID de fuente (opcional)",
        key=f"{key}_sid",
        help="Identificador estable usado en las citas. Si ya existe, se crea una nueva versión de esa fuente.",
    )
    c1, c2, c3 = st.columns(3)
    publisher = c1.text_input("Entidad que publica", placeholder="DAGRAN, SIATA, UNGRD…", key=f"{key}_pub")
    authority = c2.selectbox("Autoridad", AUTHORITIES, format_func=AUTHORITIES.get, key=f"{key}_auth")
    published_on = c3.date_input("Fecha de publicación", value=None, max_value=date.today(), key=f"{key}_date")
    c1, c2 = st.columns(2)
    hazards = c1.multiselect("Amenazas", HAZARDS, format_func=HAZARDS.get, key=f"{key}_haz")
    municipalities = c2.text_input("Municipios (separados por coma)", key=f"{key}_mun")
    notes = st.text_area("Notas internas", key=f"{key}_notes", height=68)
    meta = SourceMetadata(
        title=title.strip(),
        publisher=publisher.strip(),
        authority=authority,
        hazards=hazards,
        municipalities=[m.strip() for m in municipalities.split(",") if m.strip()],
        published_on=published_on,
        notes=notes.strip(),
    )
    return meta, source_id.strip() or None


def show_result(label: str, run) -> None:
    with st.status(f"Procesando {label}…", expanded=True) as status:
        try:
            result = run()
        except ParseError as exc:
            status.update(label=f"{label}: {exc}", state="error")
            return
        except Exception as exc:  # errores de red/BD: mostrarlos en vez de romper la página
            status.update(label=f"{label}: error inesperado", state="error")
            st.exception(exc)
            return
        if result.status == "failed":
            status.update(label=f"{label}: falló la indexación", state="error")
            st.code(result.error)
        elif result.status == "unchanged":
            status.update(label=f"{label}: sin cambios (v{result.version}, {result.n_chunks} chunks)", state="complete")
        else:
            status.update(
                label=f"{label} → `{result.source_id}` v{result.version}: {result.n_chunks} chunks indexados",
                state="complete",
            )


def tab_upload() -> None:
    files = st.file_uploader(
        "PDF, Markdown, texto, audio o video",
        type=["pdf", "md", "txt", *(s.lstrip(".") for s in AUDIO_VIDEO_SUFFIXES)],
        accept_multiple_files=True,
        help="Los PDF escaneados pasan por OCR. El audio y el video se transcriben con OpenAI (~USD 0,27/hora).",
    )
    meta, source_id = metadata_form("up", title_required=False)
    if len(files or []) > 1 and (meta.title or source_id):
        st.info("Con varios archivos, el título y el ID se derivan de cada nombre de archivo.")
    if st.button("Ingestar documentos", type="primary", disabled=not files):
        for f in files:
            m = SourceMetadata(**{**meta.__dict__})
            single = len(files) == 1
            m.title = (meta.title if single else "") or f.name.rsplit(".", 1)[0]
            show_result(f.name, lambda f=f, m=m: pipeline.ingest_upload(f.getvalue(), f.name, m, source_id if single else None))


def tab_links() -> None:
    urls = st.text_area(
        "URLs (una por línea)",
        placeholder="https://www.youtube.com/watch?v=…\nhttps://www.dagran.gov.co/…",
        help="YouTube: se usan los subtítulos (español primero); si no tiene, se transcribe el audio con OpenAI. Otras URLs: se extrae el contenido principal de la página o el PDF enlazado.",
    )
    meta, source_id = metadata_form("url", title_required=False)
    lines = [u.strip() for u in urls.splitlines() if u.strip()]
    if st.button("Ingestar enlaces", type="primary", disabled=not lines):
        for url in lines:
            m = SourceMetadata(**{**meta.__dict__})
            single = len(lines) == 1
            if not single:
                m.title = ""
            sid = source_id if single else None
            is_yt = "youtube.com" in url or "youtu.be" in url
            fn = pipeline.ingest_youtube if is_yt else pipeline.ingest_web
            show_result(url, lambda url=url, m=m, sid=sid, fn=fn: fn(url, m, sid))


def tab_sources() -> None:
    with registry.session() as db:
        sources = registry.list_sources(db)
        rows = []
        for s in sources:
            latest = s.versions[-1] if s.versions else None
            rows.append(
                {
                    "ID": s.source_id,
                    "Título": s.title,
                    "Tipo": s.source_type,
                    "Entidad": s.publisher,
                    "Autoridad": s.authority,
                    "Amenazas": ", ".join(HAZARDS.get(h, h) for h in s.hazards),
                    "Versión": s.current_version,
                    "Estado": latest.status if latest else "—",
                    "Chunks": latest.n_chunks if latest else 0,
                    "Actualizado": s.updated_at.strftime("%Y-%m-%d %H:%M"),
                }
            )
        markdown_keys = {s.source_id: (s.versions[-1].markdown_key if s.versions else "") for s in sources}
        errors = {s.source_id: s.versions[-1].error for s in sources if s.versions and s.versions[-1].error}
    if not rows:
        st.info("Aún no hay fuentes. Sube documentos o enlaces en las otras pestañas.")
        return
    st.dataframe(rows, hide_index=True, width="stretch")

    selected = st.selectbox("Fuente", [r["ID"] for r in rows])
    if selected in errors:
        st.error(errors[selected])
    c1, c2 = st.columns(2)
    if c1.toggle("Ver texto normalizado") and markdown_keys.get(selected):
        try:
            st.markdown(get_storage().get(markdown_keys[selected]).decode()[:20000])
        except (OSError, KeyError) as exc:
            st.warning(f"No se encontró el Markdown en el almacenamiento: {exc}")
    confirm = c2.checkbox(f"Confirmo eliminar `{selected}` del índice y del almacenamiento")
    if c2.button("Eliminar fuente", disabled=not confirm):
        pipeline.delete_source(selected)
        st.success(f"`{selected}` eliminada")
        st.rerun()


def tab_search() -> None:
    st.caption("Búsqueda híbrida (Voyage + BM25, fusión RRF) sin reranker, para verificar lo indexado.")
    query = st.text_input("Pregunta", placeholder="¿Qué hacer si el río crece rápidamente?")
    if not query:
        return
    try:
        points = Indexer().search(query)
    except Exception as exc:
        st.exception(exc)
        return
    for p in points:
        pl = p.payload or {}
        link = pl.get("uri", "")
        if pl.get("source_type") == "youtube" and pl.get("start") is not None:  # enlace al minuto exacto
            link = f"{link}&t={int(pl['start'])}s"
        with st.expander(f"{p.score:.3f} · {pl.get('title')} · {pl.get('locator', '')} · `{pl.get('chunk_id')}`"):
            if pl.get("heading_path"):
                st.caption(" > ".join(pl["heading_path"]))
            st.markdown(pl.get("text", ""))
            if link:
                st.markdown(f"[Abrir fuente]({link})")


require_login()
st.title("Aula-SAMA · Administración del corpus")
s = get_settings()
st.sidebar.markdown(
    f"**Qdrant:** `{s.qdrant_url}`  \n**Colección:** `{s.qdrant_collection}`  \n"
    f"**Embeddings:** `{s.embedding_model}`  \n**Almacenamiento:** `{s.storage_backend}`"
)
upload, links, sources, search = st.tabs(["📄 Documentos", "🔗 Videos y enlaces", "🗂️ Fuentes", "🔎 Probar búsqueda"])
with upload:
    tab_upload()
with links:
    tab_links()
with sources:
    tab_sources()
with search:
    tab_search()

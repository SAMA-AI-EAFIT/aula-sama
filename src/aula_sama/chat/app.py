"""Chat de Aula-SAMA (demo).

    uv run streamlit run src/aula_sama/chat/app.py --server.port 8502
"""

import time
import uuid

import streamlit as st

from aula_sama import prompts
from aula_sama.chat import persona
from aula_sama.config import get_settings
from aula_sama.graph import build_graph

st.set_page_config(page_title="Aula SAMA", page_icon="🌧️")

EXAMPLES = [
    "¿Qué significa la alerta naranja?",
    "¿Sembrar cualquier árbol evita los deslizamientos?",
    "¿Qué es un volcán de lodo?",
    "¿Qué debe llevar el morral de vida?",
]


@st.cache_resource
def graph():
    # Un solo grafo (y su memoria conversacional) por proceso; cada sesión usa su propio thread_id
    return build_graph()


def render_sources(citations: list[dict]) -> None:
    if not citations:
        return
    with st.expander(f"Fuentes ({len(citations)})"):
        for c in citations:
            label = f"[{c['n']}] {c['label']}"
            st.markdown(f"{label} — [abrir]({c['link']})" if c["link"] else label)


def render_trace(trace: dict) -> None:
    with st.expander("Detalles técnicos"):
        st.json(trace, expanded=False)


def render_voice(i: int, msg: dict) -> None:
    """Botón para escuchar la respuesta; el audio se genera una vez y queda en la sesión."""
    if st.button("🔊 Escuchar", key=f"tts-{i}", type="tertiary"):
        try:
            with st.spinner("Generando audio…"):
                msg["audio"] = persona.synthesize(msg["content"])
            msg["autoplay"] = True
        except Exception:
            st.caption("No pude generar el audio en este momento.")
    if msg.get("audio"):
        st.audio(msg["audio"], format="audio/mp3", autoplay=msg.pop("autoplay", False))


def add_answer(content: str, expression: str, **extra) -> None:
    st.session_state.history.append({"role": "assistant", "content": content, "expression": expression, **extra})


def new_conversation() -> None:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []


def usage_limit_message() -> str | None:
    """Límites por sesión del navegador: turnos por conversación y mensajes por minuto."""
    s = get_settings()
    if sum(m["role"] == "user" for m in st.session_state.history) >= s.max_turns_per_conversation:
        return prompts.TURN_LIMIT_MESSAGE
    now = time.monotonic()
    recent = [t for t in st.session_state.get("sent_at", []) if now - t < 60]
    if len(recent) >= s.max_messages_per_minute:
        st.session_state.sent_at = recent
        return prompts.RATE_LIMIT_MESSAGE
    st.session_state.sent_at = [*recent, now]
    return None


if "thread_id" not in st.session_state:
    new_conversation()

with st.sidebar:
    st.image(persona.avatar("neutral"), width=140)
    st.markdown("### Guardiana virtual de Aula SAMA")
    st.caption(
        "Un asistente de IA que le ayuda a aprender sobre gestión del riesgo en Antioquia. Responde "
        "solo con el material de Aula SAMA y cita sus fuentes. **No emite alertas ni tiene datos en "
        "tiempo real.** En una emergencia, llame al **123**."
    )
    st.button("Nueva conversación", on_click=new_conversation, width="stretch")
    voice = st.toggle("Escuchar respuestas", value=bool(get_settings().openai_api_key))
    show_trace = st.toggle("Mostrar detalles técnicos", value=False)

st.title("Aula SAMA")

with st.chat_message("assistant", avatar=persona.avatar("neutral")):
    st.markdown(prompts.WELCOME_MESSAGE)

for i, msg in enumerate(st.session_state.history):
    is_bot = msg["role"] == "assistant"
    with st.chat_message(msg["role"], avatar=persona.avatar(msg.get("expression", "neutral")) if is_bot else None):
        st.markdown(msg["content"])
        render_sources(msg.get("citations", []))
        if is_bot and voice:
            render_voice(i, msg)
        if show_trace and msg.get("trace"):
            render_trace(msg["trace"])

prompt = st.chat_input("Escriba su pregunta sobre gestión del riesgo…", max_chars=get_settings().max_input_chars)
if not st.session_state.history and not prompt:
    st.caption("Pruebe con:")
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLES):
        if cols[i % 2].button(example, width="stretch"):
            prompt = example

if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    if limit := usage_limit_message():
        add_answer(limit, "pensando")
        st.rerun()

    with st.chat_message("assistant", avatar=persona.avatar("pensando")):
        placeholder = st.empty()
        placeholder.markdown("_Buscando en el material de Aula SAMA…_")
        streamed, state = "", {}
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        try:
            for mode, chunk in graph().stream(
                {"messages": [{"role": "user", "content": prompt}]}, config, stream_mode=["custom", "values"]
            ):
                if mode == "custom" and chunk.get("type") == "token":
                    streamed += chunk["text"]
                    placeholder.markdown(streamed + "▌")
                elif mode == "values":
                    state = chunk
        except Exception as exc:  # error de red o de API: nunca dejar a la persona sin la ruta de emergencia
            add_answer(prompts.SERVICE_UNAVAILABLE_MESSAGE, "seria", trace={"error": repr(exc)})
            st.rerun()

    citations = state.get("citations", [])
    # Se vuelve a dibujar desde el historial: así la respuesta queda con su expresión y el botón de voz
    add_answer(
        state.get("answer") or "No pude generar una respuesta.",
        persona.expression_for(state.get("request_class"), state.get("evidence_status"), citations),
        citations=citations,
        trace={
            "clase": state.get("request_class"),
            "consulta": state.get("retrieval_query"),
            "evidencia": state.get("evidence_status"),
            "razón": state.get("route_reason"),
            "guardrails": state.get("guardrail_flags", []),
            "rerank": [
                {"chunk": e["chunk_id"], "score": round(e["rerank_score"] or 0, 3)} for e in state.get("evidence", [])
            ],
        },
    )
    st.rerun()

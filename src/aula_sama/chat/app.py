"""Chat de Aula-SAMA (demo).

    uv run streamlit run src/aula_sama/chat/app.py --server.port 8502
"""

import uuid

import streamlit as st

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


def new_conversation() -> None:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []


if "thread_id" not in st.session_state:
    new_conversation()

with st.sidebar:
    st.markdown("### Aula SAMA")
    st.caption(
        "Asistente educativo sobre gestión del riesgo en Antioquia. Responde solo con el material "
        "de Aula SAMA y cita sus fuentes. **No emite alertas ni tiene datos en tiempo real.** "
        "En una emergencia, llama al **123**."
    )
    st.button("Nueva conversación", on_click=new_conversation, width="stretch")
    show_trace = st.toggle("Mostrar detalles técnicos", value=False)

st.title("Aula SAMA")

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        render_sources(msg.get("citations", []))
        if show_trace and msg.get("trace"):
            render_trace(msg["trace"])

prompt = st.chat_input("Escribe tu pregunta sobre gestión del riesgo…")
if not st.session_state.history and not prompt:
    st.caption("Prueba con:")
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLES):
        if cols[i % 2].button(example, width="stretch"):
            prompt = example

if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
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
        except Exception as exc:  # la demo no debe romperse por un error de red o de API
            placeholder.error("Ocurrió un error al generar la respuesta. Intenta de nuevo.")
            if show_trace:
                st.exception(exc)
            st.stop()

        answer = state.get("answer") or "No pude generar una respuesta."
        placeholder.markdown(answer)
        citations = state.get("citations", [])
        render_sources(citations)
        trace = {
            "clase": state.get("request_class"),
            "consulta": state.get("retrieval_query"),
            "evidencia": state.get("evidence_status"),
            "razón": state.get("route_reason"),
            "rerank": [
                {"chunk": e["chunk_id"], "score": round(e["rerank_score"] or 0, 3)} for e in state.get("evidence", [])
            ],
        }
        if show_trace:
            render_trace(trace)
    st.session_state.history.append(
        {"role": "assistant", "content": answer, "citations": citations, "trace": trace}
    )

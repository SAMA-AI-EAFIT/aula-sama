"""Grafo de serving de Aula-SAMA.

classify_request ─┬─ educativa ──► retrieve_hybrid ► rerank ► assess_evidence ─┬─ suficiente ► generate_answer ► attach_citations
                  ├─ conversacional ► respond_conversational                    └─ insuficiente ► escalate
                  └─ emergencia / fuera de alcance ► escalate
"""

import operator
from dataclasses import asdict
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from aula_sama import prompts
from aula_sama.config import get_settings
from aula_sama.graph import llm
from aula_sama.retrieval.search import Evidence, Retriever

HISTORY_MESSAGES = 8  # mensajes previos que se envían como contexto conversacional


class AulaSamaState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]  # {"role", "content"} en texto plano
    request_class: str | None
    retrieval_query: str | None
    conversational_reply: str | None
    candidates: list[dict]
    evidence: list[dict]
    evidence_status: str | None  # sufficient | insufficient
    route_reason: str | None
    draft: dict | None  # {"text", "segments": [[texto, [índices de evidencia]], ...]}
    answer: str | None
    citations: list[dict]  # [{"n", "chunk_id", "label", "link", "score"}]


def _question(state: AulaSamaState) -> str:
    return state["messages"][-1]["content"]


def _history(state: AulaSamaState) -> list[dict]:
    return state["messages"][:-1][-HISTORY_MESSAGES:]


def _reply(text: str, reason: str, **extra) -> dict:
    return {"answer": text, "route_reason": reason, "messages": [{"role": "assistant", "content": text}], **extra}


# --- Nodos -------------------------------------------------------------------------------

def classify_request(state: AulaSamaState) -> dict:
    c = llm.classify(_question(state), _history(state))
    # Se reinician los campos del turno: el estado persiste entre turnos de la conversación
    return {
        "request_class": c.request_class,
        "retrieval_query": c.retrieval_query or _question(state),
        "conversational_reply": c.conversational_reply,
        "candidates": [],
        "evidence": [],
        "evidence_status": None,
        "route_reason": None,
        "draft": None,
        "answer": None,
        "citations": [],
    }


def retrieve_hybrid(state: AulaSamaState) -> dict:
    return {"candidates": [asdict(e) for e in _retriever().retrieve(state["retrieval_query"])]}


def rerank(state: AulaSamaState) -> dict:
    candidates = [Evidence(**c) for c in state["candidates"]]
    return {"evidence": [asdict(e) for e in _retriever().rerank(state["retrieval_query"], candidates)]}


def assess_evidence(state: AulaSamaState) -> dict:
    """Gate determinista sobre los scores del reranker (umbrales provisionales, a calibrar con evals)."""
    s = get_settings()
    useful = [e for e in state["evidence"] if (e["rerank_score"] or 0) >= s.evidence_min_chunk_score]
    top = useful[0]["rerank_score"] if useful else 0.0
    if top >= s.evidence_min_top_score:
        return {"evidence": useful, "evidence_status": "sufficient", "route_reason": f"top_score={top:.2f}"}
    return {"evidence_status": "insufficient", "route_reason": f"low_relevance top_score={top:.2f}"}


def generate_answer(state: AulaSamaState) -> dict:
    write = get_stream_writer()
    result = llm.answer(
        _question(state), _history(state), state["evidence"], on_text=lambda t: write({"type": "token", "text": t})
    )
    if result.refused:
        return _reply(prompts.REFUSAL_MESSAGE, "refusal")
    return {"draft": {"text": result.text, "segments": [[t, c] for t, c in result.segments]}}


def attach_citations(state: AulaSamaState) -> dict:
    """Numera las fuentes citadas en orden de aparición y pone los marcadores [n] al final de cada línea."""
    draft = state["draft"]
    evidence = state["evidence"]
    numbers: dict[int, int] = {}
    lines: list[str] = [""]
    line_refs: list[list[int]] = [[]]
    for text, cited in draft["segments"]:
        refs = [numbers.setdefault(i, len(numbers) + 1) for i in cited]
        pieces = text.split("\n")
        for k, piece in enumerate(pieces):
            if k:
                lines.append("")
                line_refs.append([])
            lines[-1] += piece
        # La cita respalda el texto del segmento: va a la última línea con contenido que lo contiene
        target = next((j for j in range(len(lines) - 1, -1, -1) if lines[j].strip()), len(lines) - 1)
        line_refs[target] += [n for n in refs if n not in line_refs[target]]
    answer = "\n".join(
        line.rstrip() + "".join(f"[{n}]" for n in sorted(refs)) if refs else line for line, refs in zip(lines, line_refs)
    )
    citations = [
        {
            "n": n,
            "chunk_id": evidence[i]["chunk_id"],
            "label": Evidence(**evidence[i]).label,
            "link": Evidence(**evidence[i]).link,
            "score": evidence[i]["rerank_score"],
        }
        for i, n in numbers.items()
    ]
    reason = state["route_reason"] + ("" if citations else " no_citations")
    # El historial guarda el texto sin marcadores: las citas no se reenvían al modelo como contenido
    return {
        "answer": answer,
        "citations": citations,
        "route_reason": reason,
        "messages": [{"role": "assistant", "content": draft["text"]}],
    }


def respond_conversational(state: AulaSamaState) -> dict:
    return _reply(state["conversational_reply"] or prompts.OUT_OF_SCOPE_MESSAGE, "conversational")


def escalate(state: AulaSamaState) -> dict:
    if state["request_class"] == "operational_or_emergency":
        return _reply(prompts.EMERGENCY_MESSAGE, "operational_or_emergency")
    if state["request_class"] == "out_of_scope":
        return _reply(prompts.OUT_OF_SCOPE_MESSAGE, "out_of_scope")
    return _reply(prompts.INSUFFICIENT_EVIDENCE_MESSAGE, state["route_reason"] or "insufficient_evidence")


# --- Rutas -------------------------------------------------------------------------------

def _route_class(state: AulaSamaState) -> str:
    return {"educational_in_scope": "retrieve_hybrid", "conversational": "respond_conversational"}.get(
        state["request_class"], "escalate"
    )


def _route_evidence(state: AulaSamaState) -> str:
    return "generate_answer" if state["evidence_status"] == "sufficient" else "escalate"


def _route_generated(state: AulaSamaState) -> str:
    return END if state["route_reason"] == "refusal" else "attach_citations"


_RETRIEVER: Retriever | None = None


def _retriever() -> Retriever:
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = Retriever()
    return _RETRIEVER


def build_graph(checkpointer=None):
    g = StateGraph(AulaSamaState)
    for node in (classify_request, retrieve_hybrid, rerank, assess_evidence, generate_answer, attach_citations,
                 respond_conversational, escalate):
        g.add_node(node.__name__, node)
    g.add_edge(START, "classify_request")
    g.add_conditional_edges("classify_request", _route_class, ["retrieve_hybrid", "respond_conversational", "escalate"])
    g.add_edge("retrieve_hybrid", "rerank")
    g.add_edge("rerank", "assess_evidence")
    g.add_conditional_edges("assess_evidence", _route_evidence, ["generate_answer", "escalate"])
    g.add_conditional_edges("generate_answer", _route_generated, ["attach_citations", END])
    g.add_edge("attach_citations", END)
    g.add_edge("respond_conversational", END)
    g.add_edge("escalate", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())

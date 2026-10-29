"""Rutas del grafo con Claude y el retriever simulados (sin llamadas a APIs)."""

import pytest

import aula_sama.graph as G
from aula_sama.graph import llm
from aula_sama.retrieval.search import Evidence


def _ev(i: int, score: float) -> Evidence:
    return Evidence(
        chunk_id=f"fuente#c00{i}", text=f"Texto {i}", title="Guía", locator=f"img. {i}", uri="",
        source_type="image", publisher="SAMA", authority="oficial", heading_path=[], rerank_score=score,
    )


class FakeRetriever:
    def __init__(self, scores):
        self.scores = scores

    def retrieve(self, query):
        return [_ev(i, 0) for i in range(len(self.scores))]

    def rerank(self, query, candidates):
        return [_ev(i, s) for i, s in enumerate(self.scores)]


@pytest.fixture
def run(monkeypatch):
    def _run(request_class, scores=(0.9, 0.6, 0.3), turns=("¿qué es una avenida torrencial?",), segments=None):
        monkeypatch.setattr(G, "_RETRIEVER", FakeRetriever(scores))
        monkeypatch.setattr(llm, "classify", lambda q, h: llm.Classification(
            request_class=request_class, retrieval_query=f"consulta: {q}", reply="¡Hola!"))
        seen = {}

        def fake_answer(question, history, evidence, on_text=None):
            seen.update(history=history, evidence=evidence)
            return llm.CitedAnswer(segments=segments or [
                ("**Es un flujo rápido**", [1, 0]), (" de agua y lodo.", [0]), ("\n\n- Aléjate del cauce.", [0]),
            ])

        monkeypatch.setattr(llm, "answer", fake_answer)
        graph = G.build_graph()
        cfg = {"configurable": {"thread_id": "t"}}
        for q in turns:
            state = graph.invoke({"messages": [{"role": "user", "content": q}]}, cfg)
        return state, seen

    return _run


def test_sufficient_evidence_generates_cited_answer(run):
    state, seen = run("educational_in_scope")
    assert state["evidence_status"] == "sufficient"
    assert [e["rerank_score"] for e in seen["evidence"]] == [0.9, 0.6]  # 0.3 queda fuera del paquete
    assert state["answer"] == "**Es un flujo rápido** de agua y lodo.[1][2]\n\n- Aléjate del cauce.[2]"
    assert [c["chunk_id"] for c in state["citations"]] == ["fuente#c001", "fuente#c000"]
    assert state["messages"][-1]["content"] == "**Es un flujo rápido** de agua y lodo.\n\n- Aléjate del cauce."


def test_low_scores_escalate_without_calling_llm(run):
    state, seen = run("educational_in_scope", scores=(0.6, 0.4))
    assert state["evidence_status"] == "insufficient"
    assert "No tengo una fuente" in state["answer"]
    assert seen == {}


@pytest.mark.parametrize(
    "cls,text", [("emergency", "123"), ("current_status", "fuente vigente"), ("out_of_scope", "fuera de lo que")]
)
def test_emergency_and_out_of_scope_skip_retrieval(run, cls, text):
    state, seen = run(cls)
    assert text in state["answer"] and state["candidates"] == [] and seen == {}


@pytest.mark.parametrize("cls", ["conversational", "needs_clarification"])
def test_conversational_and_clarification(run, cls):
    state, seen = run(cls)
    assert state["answer"] == "¡Hola!" and state["route_reason"] == cls and seen == {}


def test_multi_turn_history_and_reset(run):
    state, seen = run("educational_in_scope", turns=("hola", "¿y qué hago?"))
    assert [m["role"] for m in seen["history"]] == ["user", "assistant"]
    assert len(state["messages"]) == 4


def test_emergency_keywords_skip_classifier(run, monkeypatch):
    def boom(q, h):
        raise AssertionError("no debe llamar al clasificador")

    monkeypatch.setattr(llm, "classify", boom)
    graph = G.build_graph()
    state = graph.invoke({"messages": [{"role": "user", "content": "¡Está sonando la alarma en mi sector!"}]},
                        {"configurable": {"thread_id": "e"}})
    assert state["route_reason"] == "emergency_keywords" and "123" in state["answer"]


def test_too_long_input_is_rejected(run):
    state, seen = run("educational_in_scope", turns=("x" * 5000,))
    assert state["request_class"] == "too_long" and "muy largo" in state["answer"] and seen == {}


def test_check_answer_drops_ungrounded_and_forbidden_sentences(run):
    state, _ = run("educational_in_scope", segments=[
        ("Es un flujo rápido de agua y lodo.", [0]),
        (" Llame al 604 555 1234 para más información.", [0]),
        ("\n\n- No se preocupe, eso casi nunca pasa.", []),
        ("\n- Aléjese del cauce.", [1]),
    ])
    assert state["answer"] == "Es un flujo rápido de agua y lodo.[1]\n\n- Aléjese del cauce.[2]"
    assert state["guardrail_flags"] == ["sin_fuente:6045551234", "prohibida:minimiza"]
    assert state["route_reason"].endswith("guardrail_edited")


def test_check_answer_falls_back_when_nothing_survives(run):
    state, _ = run("educational_in_scope", segments=[("Ya registré su solicitud de inspección.", [0])])
    assert state["route_reason"] == "guardrail_empty" and "No tengo una fuente" in state["answer"]

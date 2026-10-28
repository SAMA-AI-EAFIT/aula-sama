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
    def _run(request_class, scores=(0.9, 0.6, 0.3), turns=("¿qué es una avenida torrencial?",)):
        monkeypatch.setattr(G, "_RETRIEVER", FakeRetriever(scores))
        monkeypatch.setattr(llm, "classify", lambda q, h: llm.Classification(
            request_class=request_class, retrieval_query=f"consulta: {q}", conversational_reply="¡Hola!"))
        seen = {}

        def fake_answer(question, history, evidence, on_text=None):
            seen.update(history=history, evidence=evidence)
            return llm.CitedAnswer(segments=[
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
    assert "No encontré información suficiente" in state["answer"]
    assert seen == {}


@pytest.mark.parametrize("cls,text", [("operational_or_emergency", "123"), ("out_of_scope", "Puedo ayudarte")])
def test_emergency_and_out_of_scope_skip_retrieval(run, cls, text):
    state, seen = run(cls)
    assert text in state["answer"] and state["candidates"] == [] and seen == {}


def test_conversational(run):
    state, _ = run("conversational")
    assert state["answer"] == "¡Hola!"


def test_multi_turn_history_and_reset(run):
    state, seen = run("educational_in_scope", turns=("hola", "¿y qué hago?"))
    assert [m["role"] for m in seen["history"]] == ["user", "assistant"]
    assert len(state["messages"]) == 4

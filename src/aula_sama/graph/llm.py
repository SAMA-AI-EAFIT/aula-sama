"""Llamadas a Claude usadas por el grafo: clasificación (salida estructurada) y respuesta con citas."""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

import anthropic
from pydantic import BaseModel

from aula_sama.config import get_settings
from aula_sama.prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
RequestClass = Literal[
    "educational_in_scope", "emergency", "current_status", "needs_clarification", "out_of_scope", "conversational"
]


class Classification(BaseModel):
    request_class: RequestClass
    retrieval_query: str
    reply: str  # saludo (conversational) o pregunta aclaratoria (needs_clarification)


@dataclass
class CitedAnswer:
    """Respuesta de Claude en segmentos de texto, cada uno con los índices de evidencia que lo respaldan."""

    segments: list[tuple[str, list[int]]] = field(default_factory=list)
    refused: bool = False

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.segments)


@cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, max_retries=3)


def _history_text(history: list[dict]) -> str:
    lines = [f"{'Usuario' if m['role'] == 'user' else 'Asistente'}: {m['content']}" for m in history]
    return "\n".join(lines) or "(sin mensajes previos)"


def classify(question: str, history: list[dict]) -> Classification:
    s = get_settings()
    response = _client().beta.messages.parse(
        model=s.chat_model,
        max_tokens=4000,
        system=CLASSIFY_SYSTEM,
        output_config={"effort": s.classify_effort},
        output_format=Classification,
        betas=[_FALLBACK_BETA],
        fallbacks="default",
        messages=[
            {
                "role": "user",
                "content": f"Conversación previa:\n{_history_text(history)}\n\nÚltimo mensaje del usuario:\n{question}",
            }
        ],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        # Ante la duda, tratar como potencialmente sensible: el grafo responde con el mensaje seguro
        return Classification(request_class="emergency", retrieval_query="", reply="")
    return response.parsed_output


def answer(
    question: str, history: list[dict], evidence: list[dict], on_text: Callable[[str], None] | None = None
) -> CitedAnswer:
    """Genera la respuesta citando la evidencia (bloques `search_result` con citas nativas)."""
    s = get_settings()
    results = [
        {
            "type": "search_result",
            "source": e["chunk_id"],
            "title": e["title"] + (f" ({e['locator']})" if e["locator"] else ""),
            # Párrafos como bloques separados: las citas apuntan a fragmentos más precisos
            "content": [{"type": "text", "text": p} for p in _paragraphs(e["text"])],
            "citations": {"enabled": True},
        }
        for e in evidence
    ]
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": [*results, {"type": "text", "text": question}]})

    with _client().beta.messages.stream(
        model=s.chat_model,
        max_tokens=16000,
        system=ANSWER_SYSTEM,
        output_config={"effort": s.answer_effort},
        betas=[_FALLBACK_BETA],
        fallbacks="default",
        messages=messages,
    ) as stream:
        if on_text:
            for text in stream.text_stream:
                on_text(text)
        final = stream.get_final_message()

    if final.stop_reason == "refusal":
        return CitedAnswer(refused=True)
    index = {e["chunk_id"]: i for i, e in enumerate(evidence)}
    out = CitedAnswer()
    for block in final.content:
        if block.type != "text":
            continue
        cited = []
        for c in block.citations or []:
            # Se valida contra la evidencia entregada: una cita a algo fuera de ella se descarta
            i = index.get(getattr(c, "source", None))
            if i is not None and i not in cited:
                cited.append(i)
        out.segments.append((block.text, cited))
    return out


def _paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    return parts or [text]

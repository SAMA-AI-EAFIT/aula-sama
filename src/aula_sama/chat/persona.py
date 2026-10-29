"""Cara y voz de la guardiana virtual en el chat.

Las ilustraciones de assets/avatar/ son provisionales. Para usar las definitivas basta con
dejar en esa carpeta un archivo por expresión (neutral, explicando, pensando, seria) en PNG,
WEBP, JPG o SVG: el PNG/WEBP/JPG tiene prioridad sobre el SVG del mismo nombre.
"""

import re
from functools import cache
from pathlib import Path

from openai import OpenAI

from aula_sama.config import get_settings

AVATAR_DIR = Path(__file__).parent / "assets" / "avatar"
EXPRESSIONS = ("neutral", "explicando", "pensando", "seria")
TTS_MAX_CHARS = 4096  # límite de la API de voz por solicitud

# Expresión según la ruta del grafo: seria en emergencias y límites, pensando cuando no hay respuesta
_EXPRESSION_BY_CLASS = {
    "emergency": "seria",
    "current_status": "seria",
    "too_long": "pensando",
    "needs_clarification": "pensando",
    "out_of_scope": "pensando",
    "conversational": "neutral",
}


def avatar(expression: str = "neutral") -> str:
    for suffix in (".png", ".webp", ".jpg", ".svg"):
        path = AVATAR_DIR / f"{expression}{suffix}"
        if path.is_file():
            return str(path)
    return "🌿"  # sin ilustraciones: un emoji para que el chat no se rompa


def expression_for(request_class: str | None, evidence_status: str | None, citations: list) -> str:
    if request_class in _EXPRESSION_BY_CLASS:
        return _EXPRESSION_BY_CLASS[request_class]
    return "explicando" if evidence_status == "sufficient" and citations else "pensando"


def speakable(markdown: str) -> str:
    """Texto para leer en voz alta: sin marcadores de cita, formato Markdown ni enlaces."""
    text = re.sub(r"\[\d+\]", "", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:TTS_MAX_CHARS]


@cache
def _client() -> OpenAI:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("Falta OPENAI_API_KEY para generar la voz")
    return OpenAI(api_key=s.openai_api_key, max_retries=2)


def synthesize(text: str) -> bytes:
    """MP3 con la voz de la guardiana."""
    s = get_settings()
    # Una voz personalizada (p. ej. de una locutora con autorización) se configura con su ID "voice_..."
    voice = {"id": s.tts_voice} if s.tts_voice.startswith("voice_") else s.tts_voice
    response = _client().audio.speech.create(
        model=s.tts_model,
        voice=voice,
        input=speakable(text),
        instructions=s.tts_instructions,
        response_format="mp3",
    )
    return response.content

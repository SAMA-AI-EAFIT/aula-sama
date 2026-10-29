"""Guardrails deterministas de entrada y salida (sin llamadas a modelos).

- Entrada: detección de emergencias por palabras clave, antes del clasificador.
- Salida: datos verificables (números, teléfonos, enlaces, correos) que no aparecen en la
  evidencia ni en la pregunta, y frases prohibidas por el diseño del bot. Las oraciones que
  los contienen se eliminan de la respuesta.
"""

import re
import unicodedata
from collections.abc import Callable

# Números que pueden aparecer aunque no estén en la evidencia (línea de emergencias)
ALLOWED_NUMBERS = {"123"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# --- Entrada -------------------------------------------------------------------------------

# Sobre texto normalizado (minúsculas, sin tildes). Favorecen la sensibilidad: un falso positivo
# solo muestra el mensaje de emergencia, un falso negativo deja la ruta en manos del clasificador.
_EMERGENCY_PATTERNS = [
    r"\b(auxilio|socorro|ayuda urgente|necesito ayuda ya)\b",
    r"\b(atrapad|sepultad|enterrad|arrastrad)[oa]s?\b",
    r"\b(hay|tengo|hubo) (un |una |varios |varias )?(herid|muert|desaparecid|lesionad)",
    r"\b(esta|estan) sonando\b.*\b(alarma|sirena)", r"\b(alarma|sirena)s? (esta |estan )?sonando\b",
    r"\bsono (la|una) (alarma|sirena)\b",
    r"\bse (esta|estan) (creciendo|desbordando|inundando|cayendo|viniendo|derrumbando|deslizando|quemando)\b",
    r"\bse (crecio|desbordo|vino|derrumbo|cayo|desplomo|hundio)\b.*\b(rio|quebrada|montana|loma|ladera|"
    r"barranco|talud|tierra|lodo|casa|vivienda|puente|muro|pared)\b",
    r"\b(el agua|el lodo|la creciente) (se esta metiendo|esta entrando|entro|se metio)\b",
    r"\b(la|mi) (casa|vivienda|pared|piso) (se esta|esta) (agrietando|rajando|hundiendo|cayendo)\b",
    r"\bhay (un )?(incendio|fuego|humo) (cerca|en mi|en el barrio|en la vereda)\b",
    r"\b(tenemos|tengo) que (salir|evacuar) (ya|ahora)\b",
]
_EMERGENCY_RE = re.compile("|".join(_EMERGENCY_PATTERNS))


def is_emergency(text: str) -> bool:
    return bool(_EMERGENCY_RE.search(normalize(text)))


# --- Salida --------------------------------------------------------------------------------

_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
# Cifras con separadores internos ("1.523", "1,5", "604 385 50 00"); ", " separa cifras distintas
_NUMBER = re.compile(r"\d+(?:[.,:-]\d+|[ \t]\d+)*")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:gov\.co|edu\.co|com|org|net|co)\b(?:/\S*)?")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Afirmaciones que el bot no puede hacer (reporte de diseño: límites y voz)
FORBIDDEN = {
    "solicitud_enviada": r"\b(envie|registre|tramite|radique|reporte|programe) (su|tu|la|el) "
                         r"(solicitud|reporte|peticion|caso|inspeccion|capacitacion)\b",
    "estado_actual": r"\b(le confirmo|te confirmo|actualmente|en este momento|ahora mismo|hoy) "
                     r"(hay|esta|rige|se encuentra|sigue)\b.*\balerta\b",
    "garantia": r"\b(le garantizo|te garantizo|no le va a pasar nada|no te va a pasar nada|esta (usted )?a salvo)\b",
    "minimiza": r"\b(no se preocupe|no te preocupes|no pasa nada|no es para tanto|tranquil[oa])\b",
    "persona_real": r"\b(soy una persona( real)?|yo (vivi|sobrevivi)|lo vivi en carne propia|"
                    r"(lo|la|los|las) conozco personalmente)\b",
}
_FORBIDDEN_RE = {name: re.compile(p) for name, p in FORBIDDEN.items()}

_SENTENCE_SPLIT = re.compile(r"((?<=[.!?])[ \t]+|\n+)")


def _numbers(text: str) -> set[str]:
    text = _LIST_MARKER.sub("", text)
    return {d for m in _NUMBER.findall(text) if len(d := re.sub(r"\D", "", m)) >= 2}


def _links(text: str) -> set[str]:
    found = {m.rstrip(".,;:)]*") for m in _URL.findall(text)} | set(_EMAIL.findall(text))
    return {re.sub(r"^(https?://)?(www\.)?", "", f.lower()).rstrip("/") for f in found}


def violations(sentence: str, reference: str) -> list[str]:
    """Problemas de una oración frente al texto de referencia (evidencia + pregunta)."""
    ref_numbers = _numbers(reference) | ALLOWED_NUMBERS
    ref_text = reference.lower()
    found = [f"sin_fuente:{n}" for n in sorted(_numbers(sentence) - ref_numbers)]
    found += [f"sin_fuente:{link}" for link in sorted(_links(sentence)) if link not in ref_text]
    norm = normalize(sentence)
    found += [f"prohibida:{name}" for name, rx in _FORBIDDEN_RE.items() if rx.search(norm)]
    return found


def drop_sentences(text: str, check: Callable[[str], list[str]]) -> tuple[str, list[str]]:
    """Elimina las oraciones con problemas y devuelve el texto limpio y los problemas hallados."""
    parts = _SENTENCE_SPLIT.split(text)
    flags: list[str] = []
    for i in range(0, len(parts), 2):  # posiciones pares: oraciones; impares: separadores
        if found := check(parts[i]):
            flags += found
            parts[i] = ""
    cleaned = re.sub(r"(?<=\S)[ \t]{2,}", " ", "".join(parts))
    return cleaned, flags

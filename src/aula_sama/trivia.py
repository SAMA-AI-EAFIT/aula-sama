"""Retos SAMA: trivias por niveles leídas de los documentos Word de `docs/trivias/`.

Los documentos siguen la estructura de las cartillas «Reto SAMA»:

    Misión: <título>
    Nivel 1: Explorador (<tema>)
    <introducción del nivel>
    1. <pregunta>            A. <opción> B. <opción> C. <opción>
    ¡Respuesta! La B. <explicación>
    ...
    💡 APRENDIZAJE SAMA - NIVEL 1: <cierre del nivel>

Las opciones pueden venir en la misma línea («A. … B. …»), en líneas separadas o como lista
numerada de Word (sin letra visible). La respuesta se reconoce por la letra («La B», «Las
respuestas B y C») o, en los «Mito o Realidad», por la palabra «¡Mito!»/«¡Realidad!».
Para agregar un reto basta con dejar otro .docx con esta estructura en la carpeta.
"""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

LETTERS = "ABCDEFGH"

_MISSION = re.compile(r"^Misi[oó]n\s*:\s*(.+)$", re.I)
_LEVEL = re.compile(r"^Nivel\s+(\d+)\s*:\s*(.+)$", re.I)
_LEARNING = re.compile(r"^💡?\s*APRENDIZAJE SAMA\s*-?\s*NIVEL\s*\d+\s*:?\s*(.*)$", re.I)
_QUESTION = re.compile(r"^(\d{1,2})\s*[.)]\s*(.+)$")
_OPTION = re.compile(r"^([A-Ha-h])\s*[.)]\s*(.+)$")
_INLINE_OPTION = re.compile(r"(?:^|(?<=[\s?.!:”\"]))([A-H])\.\s")
_ANSWER = re.compile(r"^¡\s*Respuesta\s*!\s*(.*)$", re.I)
_ANSWER_LETTERS = re.compile(r"\b(?:La|Las respuestas?|Respuestas?)\s+([A-H](?:\s*(?:,|y|e)\s*[A-H])*)\b")


class TriviaError(Exception):
    pass


@dataclass
class Question:
    number: int
    text: str
    options: list[str]
    correct: list[int]  # índices de `options`; algunas preguntas aceptan más de una respuesta
    explanation: str

    @property
    def is_myth(self) -> bool:
        return self.text.lower().startswith("mito o realidad")


@dataclass
class Level:
    number: int
    name: str
    intro: str = ""
    learning: str = ""
    questions: list[Question] = field(default_factory=list)


@dataclass
class Reto:
    slug: str
    mission: str
    levels: list[Level]

    @property
    def n_questions(self) -> int:
        return sum(len(lv.questions) for lv in self.levels)


def _lines(data: bytes) -> list[str]:
    import docx

    from aula_sama.ingestion.parsers import docx_is_list, docx_text

    document = docx.Document(io.BytesIO(data))
    lines = []
    for p in document.paragraphs:
        is_list = docx_is_list(p)
        for line in docx_text(p).split("\n"):
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                # Las listas de Word no guardan el número/letra en el texto: se marca para reconocerlas
                lines.append(f"\x00{line}" if is_list else line)
    return lines


def _split_inline_options(text: str) -> tuple[str, list[str]]:
    """«¿Pregunta?A. uno.B. dos.C. tres.» → («¿Pregunta?», [uno, dos, tres])."""
    marks = list(_INLINE_OPTION.finditer(text))
    # Solo cuenta si las letras aparecen en orden desde la A (evita confundir «la B.» del enunciado)
    seq = []
    for m in marks:
        if m.group(1) == LETTERS[len(seq)]:
            seq.append(m)
    if len(seq) < 2:
        return text.strip(), []
    stem = text[: seq[0].start()].strip()
    bounds = [m.start() for m in seq] + [len(text)]
    options = [text[seq[i].end() : bounds[i + 1]].strip() for i in range(len(seq))]
    return stem, options


def _correct(answer: str, options: list[str]) -> list[int]:
    if m := _ANSWER_LETTERS.search(answer):
        idx = [LETTERS.index(c) for c in re.findall(r"[A-H]", m.group(1))]
        return [i for i in idx if i < len(options)]
    # «¡Mito! Muchos diapiros…» sin letra: se busca la opción que empieza por esa palabra
    if m := re.match(r"^¡\s*(Mito|Realidad)\s*!", answer, re.I):
        word = m.group(1).lower()
        return [i for i, o in enumerate(options) if o.lower().startswith(word)]
    return []


def parse_reto(data: bytes, slug: str = "") -> Reto:
    mission, levels = "", []
    question: Question | None = None  # pregunta abierta: las líneas siguientes son sus opciones
    in_learning = False  # el texto del aprendizaje puede venir en la línea siguiente al rótulo

    def level() -> Level:
        if not levels:
            raise TriviaError("Pregunta antes del primer «Nivel N:».")
        return levels[-1]

    def start_question(text: str, number: int | None = None) -> Question:
        stem, options = _split_inline_options(text)
        q = Question(number or sum(len(x.questions) for x in levels) + 1, stem, options, [], "")
        level().questions.append(q)
        return q

    for raw in _lines(data):
        is_list, line = raw.startswith("\x00"), raw.lstrip("\x00")
        if line.lower().startswith("reto sama"):
            continue
        if m := _MISSION.match(line):
            mission = m.group(1).strip()
        elif m := _LEVEL.match(line):
            levels.append(Level(int(m.group(1)), m.group(2).strip()))
            question, in_learning = None, False
        elif m := _LEARNING.match(line):
            level().learning = m.group(1).strip()
            question, in_learning = None, True
        elif m := _ANSWER.match(line):
            if question is None:
                raise TriviaError(f"Respuesta sin pregunta: {line[:60]}")
            question.explanation = m.group(1).strip()
            question.correct = _correct(question.explanation, question.options)
            question = None
        elif question is not None:
            # Dentro de una pregunta cada línea es una opción, con letra o sin ella (lista de Word)
            if m := _OPTION.match(line):
                question.options.append(m.group(2).strip())
            else:
                question.options.extend(_split_inline_options(line)[1] or [line])
        elif in_learning:
            level().learning = f"{level().learning} {line}".strip()
        elif m := _QUESTION.match(line):
            question = start_question(m.group(2), int(m.group(1)))
        elif is_list and levels:
            question = start_question(line)  # pregunta escrita como ítem de lista numerada de Word
        elif levels and not level().questions:
            level().intro = f"{level().intro} {line}".strip()

    reto = Reto(slug=slug, mission=mission, levels=[lv for lv in levels if lv.questions])
    _validate(reto)
    return reto


def _validate(reto: Reto) -> None:
    if not reto.mission:
        raise TriviaError("Falta la línea «Misión: …».")
    if not reto.levels:
        raise TriviaError("No se encontraron niveles con preguntas.")
    for lv in reto.levels:
        for q in lv.questions:
            if len(q.options) < 2:
                raise TriviaError(f"Nivel {lv.number}, pregunta {q.number}: tiene menos de dos opciones.")
            if not q.correct:
                raise TriviaError(f"Nivel {lv.number}, pregunta {q.number}: no se reconoce la respuesta correcta.")


def load_retos(folder: str | Path) -> tuple[list[Reto], dict[str, str]]:
    """Todos los retos de la carpeta; los que no se pueden leer se devuelven como errores."""
    retos, errors = [], {}
    for path in sorted(Path(folder).glob("*.docx")):
        if path.name.startswith("~$"):  # archivos de bloqueo de Word abierto
            continue
        try:
            retos.append(parse_reto(path.read_bytes(), slug=path.stem))
        except Exception as exc:
            errors[path.name] = str(exc)
    return retos, errors

import io
from pathlib import Path

import docx
import pytest

from aula_sama import trivia


def _docx(lines: list[str | tuple[str, str]]) -> bytes:
    """Cada línea es texto normal o (estilo, texto) para simular listas de Word."""
    d = docx.Document()
    for line in lines:
        if isinstance(line, tuple):
            d.add_paragraph(line[1], style=line[0])
        else:
            d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


HEADER = ["Reto SAMA: un territorio, múltiples desafíos", "Misión: El poder del agua", "Nivel 1: Explorador (Básico)",
          "¡A calentar motores!"]


def test_inline_options_letter_answer_and_learning_on_next_line():
    data = _docx([
        *HEADER,
        "1. ¿Una creciente es lo mismo que una inundación?A. Son lo mismo.B. La creciente sube en el cauce.C. Otra.",
        "¡Respuesta! La B. El agua puede subir sin desbordarse.",
        "2. Mito o Realidad: “Si el río sube, siempre se inunda”.\nA. Realidad. Siempre.\nB. Mito. No siempre.",
        "¡Respuesta! ¡Mito! (La B). Depende del cauce.",
        "💡 APRENDIZAJE SAMA - NIVEL 1:\nEl riesgo depende del territorio.",
    ])
    reto = trivia.parse_reto(data, "agua")
    lv = reto.levels[0]
    assert reto.mission == "El poder del agua" and lv.name == "Explorador (Básico)" and lv.intro == "¡A calentar motores!"
    q1, q2 = lv.questions
    assert q1.text == "¿Una creciente es lo mismo que una inundación?"
    assert q1.options == ["Son lo mismo.", "La creciente sube en el cauce.", "Otra."] and q1.correct == [1]
    assert q2.is_myth and q2.options[1].startswith("Mito") and q2.correct == [1]
    assert lv.learning == "El riesgo depende del territorio."


def test_word_list_options_multiple_answers_and_myth_without_letter():
    data = _docx([
        *HEADER,
        ("List Number", "¿Qué es un diapiro?"),
        ("List Bullet", "Lo mismo que un volcán."),
        ("List Bullet", "El ascenso bajo tierra."),
        ("List Bullet", "El lodo que sube."),
        "¡Respuesta! Las respuestas B y C son correctas.",
        "2. Mito o Realidad: \"Todo el lodo sale\".",
        ("List Bullet", "Realidad."),
        ("List Bullet", "Mito."),
        "¡Respuesta! ¡Mito! Muchos quedan atrapados.",
    ])
    q1, q2 = trivia.parse_reto(data).levels[0].questions
    assert q1.number == 1 and len(q1.options) == 3 and q1.correct == [1, 2]
    assert q2.number == 2 and q2.correct == [1]


def test_unrecognized_answer_is_an_error():
    data = _docx([*HEADER, "1. ¿Pregunta?A. uno.B. dos.", "¡Respuesta! Depende."])
    with pytest.raises(trivia.TriviaError, match="respuesta correcta"):
        trivia.parse_reto(data)


def test_load_retos_reports_bad_files_without_failing(tmp_path):
    (tmp_path / "malo.docx").write_bytes(b"no es word")
    (tmp_path / "bueno.docx").write_bytes(_docx([*HEADER, "1. ¿P?A. x.B. y.", "¡Respuesta! La A. Porque sí."]))
    retos, errors = trivia.load_retos(tmp_path)
    assert [r.slug for r in retos] == ["bueno"] and list(errors) == ["malo.docx"]


def test_project_trivias_have_three_levels_of_five_questions():
    retos, errors = trivia.load_retos(Path("docs/trivias"))
    assert not errors and len(retos) == 3
    for reto in retos:
        assert [len(lv.questions) for lv in reto.levels] == [5, 5, 5], reto.slug
        assert all(lv.intro and lv.learning for lv in reto.levels), reto.slug
        for q in (q for lv in reto.levels for q in lv.questions):
            assert q.explanation and q.correct and all(i < len(q.options) for i in q.correct)

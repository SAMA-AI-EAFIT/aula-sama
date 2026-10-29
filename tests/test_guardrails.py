"""Reglas deterministas de aula_sama.guardrails."""

import pytest

from aula_sama import guardrails as g


@pytest.mark.parametrize("text", [
    "Está sonando la alarma en mi sector",
    "las sirenas están sonando",
    "la quebrada se está creciendo muy rápido",
    "se vino la montaña encima de la casa",
    "hay personas atrapadas en el barro",
    "AUXILIO",
    "el agua está entrando a la casa",
    "mi casa se está agrietando",
])
def test_detects_emergencies(text):
    assert g.is_emergency(text)


@pytest.mark.parametrize("text", [
    "¿Qué hacer si suena la alarma?",
    "¿Qué significa la alerta naranja?",
    "¿Por qué se crecen los ríos cuando llueve?",
    "vos sos la guardiana?",
    "¿Cómo se forma una avenida torrencial?",
])
def test_ignores_educational_questions(text):
    assert not g.is_emergency(text)


REF = "La línea de atención es 604 385 5000. Más información en www.dagran.gov.co. En 2019 hubo 1.523 eventos."


@pytest.mark.parametrize("sentence", [
    "Llame al 604 385 5000.",
    "Llame al 6043855000.",
    "Consulte https://www.dagran.gov.co/.",
    "En 2019 hubo 1.523 eventos.",
    "En emergencias llame al 123.",
    "1. Prepare el morral de vida.",
    "Hay 3 niveles de alerta.",
])
def test_grounded_sentences_pass(sentence):
    assert g.violations(sentence, REF) == []


@pytest.mark.parametrize("sentence,flag", [
    ("Llame al 604 444 1234.", "sin_fuente:6044441234"),
    ("Escriba a ayuda@sama.gov.co.", "sin_fuente:ayuda@sama.gov.co"),
    ("Consulte www.siata.gov.co.", "sin_fuente:siata.gov.co"),
    ("Aléjese al menos 50 metros del río.", "sin_fuente:50"),
    ("Ya envié su solicitud al DAGRAN.", "prohibida:solicitud_enviada"),
    ("Hoy hay alerta roja en Bello.", "prohibida:estado_actual"),
    ("Le garantizo que su barrio es seguro.", "prohibida:garantia"),
    ("Tranquilo, eso no es nada.", "prohibida:minimiza"),
    ("Yo viví la avenida torrencial de Mocoa.", "prohibida:persona_real"),
])
def test_violations(sentence, flag):
    assert flag in g.violations(sentence, REF)


def test_drop_sentences_keeps_layout():
    text = "Primera frase. Llame al 555 1234. Tercera frase.\n- Viñeta buena."
    cleaned, flags = g.drop_sentences(text, lambda s: g.violations(s, REF))
    assert cleaned == "Primera frase. Tercera frase.\n- Viñeta buena."
    assert flags == ["sin_fuente:5551234"]

"""Avatar y voz de la guardiana (sin llamadas a la API de voz)."""

from aula_sama.chat import persona


def test_every_expression_has_an_illustration():
    for expression in persona.EXPRESSIONS:
        assert persona.avatar(expression).endswith(".svg")


def test_expression_by_route():
    assert persona.expression_for("emergency", None, []) == "seria"
    assert persona.expression_for("current_status", None, []) == "seria"
    assert persona.expression_for("conversational", None, []) == "neutral"
    assert persona.expression_for("educational_in_scope", "sufficient", [{"n": 1}]) == "explicando"
    assert persona.expression_for("educational_in_scope", "insufficient", []) == "pensando"


def test_speakable_strips_markdown_and_citations():
    text = "**La alerta naranja** significa *prepararse*.[1][2]\n\n- Aléjese del cauce.[2]\n1. Vea el [Geoportal](https://x.co)."
    assert persona.speakable(text) == "La alerta naranja significa prepararse.\nAléjese del cauce.\nVea el Geoportal."

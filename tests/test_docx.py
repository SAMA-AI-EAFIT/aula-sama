import io
from pathlib import Path

import docx
import pytest
from docx.oxml.ns import qn
from docx.oxml.parser import OxmlElement
from PIL import Image

from aula_sama.ingestion import parsers
from aula_sama.ingestion.parsers import ParseError


def _docx(build) -> bytes:
    d = docx.Document()
    build(d)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _png(size=(400, 400)) -> io.BytesIO:
    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, format="PNG")  # ruido: no se comprime, supera el mínimo
    buf.seek(0)
    return buf


def _tracked_insert(paragraph, text: str) -> None:
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), "1")
    ins.set(qn("w:author"), "revisor")
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    ins.append(run)
    paragraph._p.append(ins)


def test_docx_to_markdown_keeps_order_headings_lists_tables_and_tracked_inserts():
    def build(d):
        d.add_heading("Monitoreo", level=1)
        d.add_paragraph().add_run("¿Cómo monitoreamos las lluvias?").bold = True
        p = d.add_paragraph("La red de pluviómetros registra")
        _tracked_insert(p, " continuamente la lluvia.")
        d.add_paragraph("Pluviómetros", style="List Bullet")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "Estado", "Qué hace la comunidad"
        t.cell(1, 0).text, t.cell(1, 1).text = "Alerta roja", "Evacuar | seguir instrucciones"

    doc = parsers.parse_upload(_docx(build), "monitoreo.docx")
    md = doc.markdown
    assert doc.source_type == "docx" and doc.title_hint == "monitoreo"
    assert md.index("## Monitoreo") < md.index("### ¿Cómo monitoreamos") < md.index("registra continuamente")
    assert "- Pluviómetros" in md
    assert "| Estado | Qué hace la comunidad |" in md and "Evacuar \\| seguir" in md


def test_docx_table_with_long_cells_becomes_labeled_paragraphs():
    def build(d):
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "Tema", "Detalle"
        t.cell(1, 0).text, t.cell(1, 1).text = "a " * 200, "b " * 200

    md = parsers.parse_upload(_docx(build), "x.docx").markdown
    assert md.startswith("**Tema:** a a") and "**Detalle:** b b" in md and "| ---" not in md


def test_docx_figures_are_described_in_place_with_caption_context(monkeypatch):
    calls = []

    def fake(data, context, settings=None):
        calls.append(context)
        return "# Fenómenos asociados"

    monkeypatch.setattr(parsers.vision, "describe_image", fake)

    def build(d):
        d.add_paragraph("Antes de la figura")
        d.add_picture(_png())
        d.add_paragraph("Figura 1. Fenómenos asociados a los movimientos en masa")

    md = parsers.parse_upload(_docx(build), "movimientos.docx").markdown
    assert md.index("Antes") < md.index("# Fenómenos asociados") < md.index("Figura 1.")
    assert len(calls) == 1 and "Figura 1." in calls[0] and "movimientos" in calls[0]


def test_docx_skips_tiny_images_and_reports_vision_errors(monkeypatch):
    def broken(*a, **k):
        raise parsers.vision.VisionError("sin clave")

    monkeypatch.setattr(parsers.vision, "describe_image", broken)
    small = _docx(lambda d: (d.add_paragraph("Texto"), d.add_picture(_png((10, 10)))))
    assert parsers.parse_upload(small, "a.docx").markdown == "Texto"
    with pytest.raises(ParseError, match="figuras"):
        parsers.parse_upload(_docx(lambda d: d.add_picture(_png())), "b.docx")


def test_invalid_docx_raises_parse_error():
    with pytest.raises(ParseError):
        parsers.parse_upload(b"no es un zip", "roto.docx")


@pytest.mark.parametrize("path", sorted(Path("docs/documentos_tecnicos").glob("*.docx")), ids=lambda p: p.name[:30])
def test_technical_documents_parse(path, monkeypatch):
    monkeypatch.setattr(parsers.vision, "describe_image", lambda *a, **k: "figura")
    assert len(parsers.parse_upload(path.read_bytes(), path.name).markdown) > 1000


def test_version_comparison_table_keeps_only_newest_column_without_editorial_notes():
    def build(d):
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "Aula SAMA – Actual", "Aula SAMA 2026"
        t.cell(1, 0).text = "Texto viejo que ya no se usa."
        new = t.cell(1, 1)
        new.text = "Texto nuevo de 2026."
        new.add_paragraph("Nota: Figura 2 (editar y referenciar)")
        new.add_paragraph("Otro párrafo.\nNota: revisar redacción")

    md = parsers.parse_upload(_docx(build), "x.docx").markdown
    assert md == "Texto nuevo de 2026.\n\nOtro párrafo."

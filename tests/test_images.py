import io
import zipfile

import pytest

from aula_sama.ingestion import parsers
from aula_sama.ingestion.chunking import chunk_document
from aula_sama.ingestion.parsers import ParseError, images_digest, read_zip_images


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_read_zip_groups_by_folder_and_skips_duplicates_and_non_images():
    data = _zip({
        "BASE/MOV EN MASA/a.jpg": b"img-a",
        "BASE/MOV EN MASA/copia.jpg": b"img-a",
        "BASE/LLUVIA/Señales.png": b"img-b",
        "BASE/notas.docx": b"doc",
        "__MACOSX/BASE/._a.jpg": b"junk",
    })
    groups, skipped = read_zip_images(data)
    assert set(groups) == {"BASE/MOV EN MASA", "BASE/LLUVIA"}
    assert [n for n, _ in groups["BASE/LLUVIA"]] == ["BASE/LLUVIA/Señales.png"]
    assert len(groups["BASE/MOV EN MASA"]) == 1
    assert "BASE/notas.docx" in skipped and any("duplicado" in s for s in skipped)


def test_read_zip_rejects_invalid_file():
    with pytest.raises(ParseError):
        read_zip_images(b"not a zip")


def test_images_digest_depends_on_names_and_content():
    a = [("x/1.jpg", b"a"), ("x/2.jpg", b"b")]
    assert images_digest(a) == images_digest([("otra/1.jpg", b"a"), ("otra/2.jpg", b"b")])
    assert images_digest(a) != images_digest([("x/1.jpg", b"a"), ("x/2.jpg", b"c")])


def test_parse_images_makes_one_chunk_per_image(monkeypatch):
    texts = [f"# Pieza {i}\n\nContenido corto de la infografía {i}." for i in range(3)]
    monkeypatch.setattr(parsers.vision, "describe_images", lambda images, context="": texts)
    doc = parsers.parse_images([(f"carpeta/{i}.jpg", b"x") for i in range(3)])
    chunks = chunk_document(doc, "set", 1, target_tokens=650, overlap_tokens=80)
    assert len(chunks) == 3
    assert [c.page_start for c in chunks] == [1, 2, 3]
    assert chunks[1].heading_path == ["Pieza 1"]
    assert "_Imagen: 1.jpg_" in chunks[1].text and "Pieza 0" not in chunks[1].text

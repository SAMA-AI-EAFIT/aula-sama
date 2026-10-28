"""Ingesta por línea de comandos, útil para cargas en lote.

    uv run aula-sama-ingest docs/fuentes/plan.pdf --publisher DAGRAN --hazard inundacion
    uv run aula-sama-ingest https://youtu.be/XXXXXXXXXXX --title "Charla SAMA"
    uv run aula-sama-ingest material.zip                  # una fuente por carpeta con imágenes
    uv run aula-sama-ingest img/*.jpg --title "Infografías inundaciones"   # imágenes → una fuente
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from aula_sama.ingestion import pipeline
from aula_sama.ingestion.parsers import IMAGE_SUFFIXES, ParseError
from aula_sama.models import SourceMetadata


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aula-sama-ingest")
    p.add_argument("inputs", nargs="+", help="Archivos (pdf/md/txt/audio/video/zip/imágenes), URLs de YouTube o web")
    p.add_argument("--title", default="")
    p.add_argument("--source-id", default=None)
    p.add_argument("--publisher", default="")
    p.add_argument("--authority", choices=["oficial", "academica", "otra"], default="oficial")
    p.add_argument("--hazard", action="append", default=[], dest="hazards")
    p.add_argument("--municipality", action="append", default=[], dest="municipalities")
    p.add_argument("--published-on", type=date.fromisoformat, default=None)
    args = p.parse_args(argv)

    images = [i for i in args.inputs if Path(i).suffix.lower() in IMAGE_SUFFIXES]
    others = [i for i in args.inputs if i not in images]
    if images and not args.title:
        p.error("las imágenes se ingieren como una sola fuente: indica --title")
    if len(others) + bool(images) > 1 and (args.title or args.source_id):
        p.error("--title/--source-id solo aplican con una única entrada (o un solo grupo de imágenes)")

    def new_meta() -> SourceMetadata:
        return SourceMetadata(
            title=args.title,
            publisher=args.publisher,
            authority=args.authority,
            hazards=args.hazards,
            municipalities=args.municipalities,
            published_on=args.published_on,
        )

    results: list[tuple[str, pipeline.IngestResult]] = []
    failed = 0
    if images:
        try:
            files = [(i, Path(i).read_bytes()) for i in images]
            results.append((f"{len(images)} imágenes", pipeline.ingest_images(files, new_meta(), args.source_id)))
        except (ParseError, OSError) as exc:
            print(f"✗ imágenes: {exc}", file=sys.stderr)
            failed += 1

    for item in others:
        meta = new_meta()
        try:
            if item.lower().endswith(".zip"):
                zip_results, skipped = pipeline.ingest_zip(Path(item).read_bytes(), meta)
                results += [(f"{item} → carpeta", r) for r in zip_results]
                for s in skipped:
                    print(f"  omitido en {item}: {s}")
                continue
            if item.startswith(("http://", "https://")):
                is_yt = "youtube.com" in item or "youtu.be" in item
                result = (pipeline.ingest_youtube if is_yt else pipeline.ingest_web)(item, meta, args.source_id)
            else:
                path = Path(item)
                meta.title = meta.title or path.stem
                result = pipeline.ingest_upload(path.read_bytes(), path.name, meta, args.source_id)
        except (ParseError, OSError) as exc:
            print(f"✗ {item}: {exc}", file=sys.stderr)
            failed += 1
            continue
        results.append((item, result))

    for label, result in results:
        mark = "✗" if result.status == "failed" else "✓"
        print(f"{mark} {label} → {result.source_id} v{result.version} [{result.status}] {result.n_chunks} chunks {result.error}")
        failed += result.status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

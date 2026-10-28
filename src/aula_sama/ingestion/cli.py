"""Ingesta por línea de comandos, útil para cargas en lote.

    uv run aula-sama-ingest docs/fuentes/plan.pdf --publisher DAGRAN --hazard inundacion
    uv run aula-sama-ingest https://youtu.be/XXXXXXXXXXX --title "Charla SAMA"
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from aula_sama.ingestion import pipeline
from aula_sama.ingestion.parsers import ParseError
from aula_sama.models import SourceMetadata


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aula-sama-ingest")
    p.add_argument("inputs", nargs="+", help="Archivos (.pdf/.md/.txt), URLs de YouTube o URLs web")
    p.add_argument("--title", default="")
    p.add_argument("--source-id", default=None)
    p.add_argument("--publisher", default="")
    p.add_argument("--authority", choices=["oficial", "academica", "otra"], default="oficial")
    p.add_argument("--hazard", action="append", default=[], dest="hazards")
    p.add_argument("--municipality", action="append", default=[], dest="municipalities")
    p.add_argument("--published-on", type=date.fromisoformat, default=None)
    args = p.parse_args(argv)

    if len(args.inputs) > 1 and (args.title or args.source_id):
        p.error("--title/--source-id solo aplican con una única entrada")

    failed = 0
    for item in args.inputs:
        meta = SourceMetadata(
            title=args.title,
            publisher=args.publisher,
            authority=args.authority,
            hazards=args.hazards,
            municipalities=args.municipalities,
            published_on=args.published_on,
        )
        try:
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
        mark = "✗" if result.status == "failed" else "✓"
        print(f"{mark} {item} → {result.source_id} v{result.version} [{result.status}] {result.n_chunks} chunks {result.error}")
        failed += result.status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

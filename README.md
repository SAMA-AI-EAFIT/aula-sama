# Aula-SAMA

Aula-SAMA es un asistente educativo basado en RAG para apoyar a la comunidad general en la comprensión de la gestión del riesgo y el monitoreo de amenazas en Antioquia, inicialmente en temas como inundaciones, movimientos en masa y avenidas torrenciales.

El proyecto prioriza cuatro propiedades: **grounding en fuentes oficiales**, **trazabilidad**, **abstención segura cuando la evidencia no es suficiente** y **capacidad de evolución sin mezclar el RAG educativo con un futuro sistema operacional de alertas**.

## Arquitectura propuesta

![Arquitectura Aula-SAMA](docs/diagrams/aula-sama-architecture.svg)

La arquitectura detallada, decisiones técnicas y criterios de evolución están en [`docs/1_architecture.md`](docs/1_architecture.md). La fuente editable del diagrama está en [`docs/diagrams/aula-sama-architecture.mmd`](docs/diagrams/aula-sama-architecture.mmd).

## Stack recomendado

- **Orquestación:** LangGraph.
- **Vector store:** Qdrant con vectores dense + sparse y fusión RRF como baseline.
- **Embeddings:** Voyage AI, modelo configurable; comenzar con `voyage-4` y evaluar una variante mayor si la mejora justifica costo/latencia.
- **Reranking:** Voyage, modelo configurable y fijado por configuración.
- **LLM:** Claude API para generación grounded y tool use controlado por el grafo.
- **Estado conversacional:** `InMemorySaver` en desarrollo; `PostgresSaver` en producción.
- **Persistencia documental:** almacenamiento de originales + registro de fuentes/versiones separado de Qdrant.
- **Observabilidad:** trazas del grafo, retrieval, fuentes utilizadas, abstenciones y evaluaciones offline.

## Principios de diseño

1. El LLM **no es el componente que decide por sí solo** si una respuesta tiene soporte suficiente.
2. No se usa un único score de “confianza” arbitrario; la suficiencia se calibra con un conjunto de evaluación.
3. Las fuentes externas se consultan mediante un nodo/tool explícito y auditable.
4. Qdrant sirve para recuperación; no sustituye un registro de fuentes ni el almacenamiento de los documentos originales.
5. El sistema educativo puede crecer hacia más herramientas, pero un futuro sistema operacional de alertas se implementa como una **capa de eventos independiente**.

## Estructura objetivo del repositorio

```text
.
├── README.md
├── docs/
│   ├── 1_architecture.md
│   └── diagrams/
│       ├── aula-sama-architecture.mmd
│       └── aula-sama-architecture.svg
├── src/
│   └── aula_sama/
│       ├── graph/
│       ├── retrieval/
│       ├── ingestion/
│       ├── tools/
│       ├── prompts/
│       └── models/
├── evals/
├── tests/
└── .env.example
```

## Desarrollo

Requisitos: [uv](https://docs.astral.sh/uv/) y Docker.

```bash
uv sync                      # crea .venv con Python 3.12 y las versiones fijadas en uv.lock
cp .env.example .env         # completar API keys (nunca commitear .env)
docker compose up -d         # Qdrant + PostgreSQL locales
uv run streamlit run src/aula_sama/admin/app.py   # pantalla de administración
uv run pytest                # tests
```

Ingesta por línea de comandos (útil para cargas en lote):

```bash
uv run aula-sama-ingest corpus/*.pdf --publisher DAGRAN --hazard inundacion
uv run aula-sama-ingest "https://www.youtube.com/watch?v=..." --title "Charla SIATA"
```

### Trabajo en varios equipos

El código se sincroniza con git; **los datos no**. Con `docker compose` cada equipo tiene su propio Qdrant, PostgreSQL y `data/storage`. Para compartir el corpus, apuntar `.env` en ambos equipos a los mismos servicios gestionados:

- `QDRANT_URL` / `QDRANT_API_KEY` → Qdrant Cloud.
- `DATABASE_URL` → PostgreSQL gestionado (p. ej. Supabase o Neon).
- `STORAGE_BACKEND=s3` + `S3_*` → bucket S3-compatible (p. ej. Supabase Storage o Cloudflare R2).

### Pipeline de ingesta

`src/aula_sama/ingestion/`: registro de fuente → snapshot del original → parse (PDF con `pymupdf4llm`, YouTube con `youtube-transcript-api`, web con `trafilatura`) → chunking estructural (~650 tokens, respetando encabezados y conservando página o timestamps) → embeddings Voyage (`input_type="document"`) + BM25 sparse (`fastembed`) → upsert en Qdrant con IDs deterministas. Reingestar un original idéntico no hace nada; uno modificado crea una versión nueva y reemplaza sus chunks.

## Estado

Pipeline de ingesta y pantalla de administración implementados. Antes de fijar umbrales y modelos definitivos se debe construir un pequeño conjunto de evaluación con preguntas reales de la comunidad y documentos oficiales del corpus inicial.

# Aula SAMA: informe de implementación

29 de octubre de 2026 · Juan Martínez

> Versión exportada del informe. Los diagramas de arquitectura y del flujo del chat están en la [versión en línea](https://claude.ai/artifact/JfyLHiq9shw3ieuG4thBtj).

## Resumen

Aula SAMA ya funciona como demo completo, de la carga del material a la respuesta con fuentes: se sube un documento, imagen o video, se indexa, y el chat responde citando ese material. La guardiana virtual responde según el reporte de diseño y tiene cara y voz.

- **Corpus actual:** 22 fuentes y 64 fragmentos, sacados de los 4 zips de la carpeta `data/`, casi todos imágenes e infografías.
- **Interfaces:** pantalla de administración y chat, ambas en Streamlit.
- **Pruebas:** 69 tests automáticos, todos en verde, más pruebas manuales con preguntas reales.
- **Estado:** demo local en un solo PC. No está desplegado ni compartido entre equipos, y los umbrales de calidad son provisionales hasta tener un conjunto de evaluación.

## Arquitectura

Todo está en Python (uv, Python 3.12). Qdrant y PostgreSQL corren en Docker, y los modelos se usan por API.

La ingesta escribe en los tres almacenes; el chat solo consulta Qdrant. Los modelos usados son Claude Opus 5.5 (`claude-opus-5-5`), Voyage `voyage-4` y `rerank-3`, y OpenAI `gpt-transcribe` y `gpt-4o-mini-tts`.

## Ingesta y administración

Cada fuente se convierte a Markdown, se divide en fragmentos y se indexa con ID estable. Si se vuelve a subir el mismo archivo, no se hace nada; si cambió, se crea una versión nueva que reemplaza a la anterior.

| Tipo de fuente | Cómo se procesa |
| --- | --- |
| PDF | Extracción de texto con PyMuPDF. Las páginas escaneadas pasan por OCR (RapidOCR, modelo latino) |
| Imágenes e infografías | Claude (visión) transcribe todo el texto en orden de lectura y describe mapas y colores. Cada imagen es un fragmento |
| Zip | Una fuente por carpeta, con título legible. Descarta duplicados y archivos que no son imágenes |
| YouTube | Subtítulos del video. Si no tiene, descarga el audio y lo transcribe con OpenAI, conservando los tiempos |
| Audio y video | Transcripción con OpenAI en ventanas cortadas en silencios |
| Página web | Extracción del texto principal con trafilatura |
| Markdown y texto | Lectura directa |

- **Fragmentos:** unos 650 tokens con 80 de traslape, respetando encabezados. Cada fragmento guarda su página, imagen o minuto exacto, que luego sale en las citas.
- **Registro:** PostgreSQL guarda fuentes, versiones, estado y huella del contenido. El original y su Markdown se guardan en disco local o en S3.
- **Pantalla de administración:** subir documentos, imágenes, zips, audio y video; agregar videos y enlaces; ver y borrar fuentes; y probar la búsqueda. Tiene contraseña, pero hoy está vacía, así que solo se abre desde el mismo PC.
- **Línea de comandos:** `aula-sama-ingest` hace lo mismo que la pantalla, para cargas por lotes.
- **Todo vive en el entorno de uv:** OCR, ffmpeg y el runtime de JavaScript para YouTube no requieren instalar paquetes del sistema.

## Búsqueda y reranking

La búsqueda combina significado y palabras exactas, y un reranker deja solo los 6 fragmentos más pertinentes. Si ni el mejor llega al umbral, el chat no responde.

1. **Búsqueda híbrida en Qdrant:** combina embeddings de Voyage (`voyage-4`), que encuentran por significado, con BM25 en español, que encuentra por palabra exacta. Las dos listas se fusionan con RRF en 25 candidatos.
2. **Reranker:** Voyage `rerank-3` ordena los 25 candidatos y deja los 6 mejores. Esto arregló, por ejemplo, «¿qué significa la alerta naranja?», donde la infografía de niveles de alerta pasó del puesto 4 al 1.
3. **Filtro de evidencia:** responde solo si el mejor fragmento tiene puntaje de al menos 0,75, y descarta los que están por debajo de 0,5. No usa ningún modelo, solo esos umbrales.

Calibración inicial con preguntas de prueba:

| Tipo de pregunta | Puntaje del mejor fragmento |
| --- | --- |
| Temas que están en el material | 0,86 a 0,94 |
| Temas de riesgo que no están en el material | 0,42 a 0,75 (terremoto: 0,72) |
| Temas ajenos | 0,28 |

Los umbrales son provisionales: con un conjunto de evaluación de 50 a 100 preguntas se pueden fijar con datos.

## Flujo de respuesta del chat

Cada pregunta pasa por un grafo de LangGraph. Solo la ruta educativa con evidencia suficiente genera una respuesta nueva; las demás responden con un mensaje fijo o una frase breve.

- **Citas:** Claude recibe los fragmentos como resultados de búsqueda y cita con su función nativa. Cada cita se valida contra la evidencia entregada y se muestra como [1], [2] al final de la línea, con un enlace a la fuente. En los videos, el enlace lleva al minuto exacto.
- **Memoria:** el clasificador reescribe la pregunta usando los últimos 8 mensajes, así que entiende preguntas de seguimiento como «¿y eso qué significa?». La memoria se pierde al reiniciar el servidor.
- **Respuesta en vivo:** la respuesta aparece mientras se genera, y al terminar se reemplaza por la versión revisada con citas.

## Comportamiento según el reporte de diseño

El chat sigue el documento «Definición del bot de Aula SAMA y sus fuentes de información» (21 de septiembre de 2026). Los textos de emergencia y de límite son provisionales hasta que SAMA aporte los oficiales.

- **Persona:** se presenta como «guardiana virtual de Aula SAMA, un asistente de IA». Nunca dice ser una persona real, haber vivido un desastre ni conocer a los habitantes.
- **Voz:** trata de usted, explica las siglas la primera vez y los términos técnicos cuando hace falta. Evita el alarmismo, las falsas garantías y culpar a las comunidades. No usa dichos, porque el equipo aún no ha aprobado ninguno.
- **Forma de la respuesta:** primero una respuesta breve. Si ayuda, agrega un ejemplo y una acción o recurso. Si la respuesta depende de un dato que falta, como el fenómeno o el municipio, da la explicación general y lo pregunta al final, una sola pregunta a la vez.
- **Límites:** explica una norma, pero no decide si aplica a un caso. Describe los riesgos de un territorio, pero no evalúa un predio. Enseña a leer una alerta, pero no declara el nivel vigente. No inventa rutas de evacuación ni contactos, y nunca dice que envió una solicitud.

Cómo responde a cada tipo de pregunta:

| Pregunta de ejemplo | Ruta | Respuesta |
| --- | --- | --- |
| «hola, ¿quién eres?» | Conversacional | Se presenta y pregunta qué le gustaría conocer |
| «¿Qué significa la alerta naranja?» | Educativa | Explica por fenómeno con fuentes citadas y pregunta cuál le interesa |
| «¿hay alerta hoy en Bello?» | Estado actual | Dice que no tiene una fuente vigente y remite al DAGRAN y al Geoportal SAMA |
| «Está sonando la alarma en mi sector» | Emergencia | Mensaje fijo con la línea 123, sin pedir datos |
| «¿qué hago?», sin contexto | Aclaración | Una pregunta para entender la situación |
| «Necesito una actividad para enseñar…» | Sin evidencia suficiente | Dice que no tiene fuente: el material no tiene un catálogo de actividades |
| Temas ajenos al riesgo | Fuera de alcance | Explica qué temas puede tratar |

## Guardrails

Además del filtro de evidencia y la validación de citas, hay cinco guardrails que no usan ningún modelo. Su objetivo es que nadie quede sin la línea 123 y que el chat no invente datos de contacto.

| Guardrail | Qué hace |
| --- | --- |
| Detector de emergencias | Frases como «está sonando la alarma», «la quebrada se está creciendo», «hay personas atrapadas» o «auxilio» van directo al mensaje del 123, sin llamar a Claude. Funciona aunque la API esté caída |
| Respaldo si falla la API | Si Claude o Voyage fallan, incluso a mitad de una respuesta, el chat muestra el 123 y los canales oficiales en vez de un error |
| Revisión contra la evidencia | Borra cualquier oración con un teléfono, cifra, URL o correo que no esté en los fragmentos ni en la pregunta. El 123 siempre está permitido |
| Frases prohibidas | Borra oraciones que digan que envió una solicitud, que afirmen el estado actual de una alerta, que den garantías, que minimicen la preocupación o que hablen como persona real |
| Límites de uso | 1.000 caracteres por mensaje, 30 turnos por conversación y 8 mensajes por minuto, configurables |

El detector prefiere equivocarse por exceso: una pregunta hipotética como «¿qué hago si estoy atrapado?» recibe el mensaje de emergencia. En 10 preguntas reales, la revisión de la respuesta no recortó ninguna respuesta buena. Lo que se borra queda visible en los detalles técnicos del chat.

## Avatar y voz

La guardiana tiene cara y voz en el chat. Las dos cosas son provisionales y se cambian sin tocar código.

- **Cara:** cuatro ilustraciones provisionales de una mujer joven con cabello castaño largo y chaqueta verde. La expresión cambia con la respuesta:
  - «explicando» cuando responde con fuentes;
  - «pensando» mientras busca o cuando no encuentra fuente;
  - «seria» en emergencias y consultas de estado actual;
  - «neutral» en saludos y en la bienvenida.
- **Ilustraciones definitivas:** basta dejar `neutral.png`, `explicando.png`, `pensando.png` y `seria.png` en `src/aula_sama/chat/assets/avatar/`.
- **Voz:** cada respuesta tiene un botón «Escuchar». Usa `gpt-4o-mini-tts` de OpenAI, con la voz `marin` e instrucciones de acento paisa. Tarda unos 4 segundos, cuesta del orden de 1 a 2 centavos de dólar por respuesta, y lee el texto sin formato, citas ni enlaces.
- **Voz propia:** la configuración acepta el ID de una voz personalizada. Para una voz paisa de verdad haría falta grabar a una locutora antioqueña que autorice el uso.

Según el reporte de diseño, el nombre, el vestuario, el grado de realismo, los gestos y la voz definitivos los decide el proyecto.

## Pruebas y validación

Los 69 tests automáticos pasan y no llaman a APIs pagas. Además, el sistema se probó a mano con preguntas reales.

| Archivo de tests | Qué cubre |
| --- | --- |
| `test_ingestion.py` | IDs de YouTube, nombres de fuentes, fragmentación y OCR de una página escaneada en español |
| `test_transcription.py` | Ventanas de audio, palabras clave y transcripción con un cliente simulado |
| `test_images.py` | Agrupación de zips, duplicados y un fragmento por imagen |
| `test_graph.py` | Las rutas del chat, el filtro de evidencia, las citas, la memoria entre turnos y los guardrails dentro del grafo |
| `test_guardrails.py` | Frases de emergencia y preguntas que no deben activarlo, datos sin fuente y frases prohibidas |
| `test_persona.py` | Ilustraciones, expresión según la ruta y limpieza del texto para la voz |

**Pruebas manuales con las APIs reales:**

- conversación de varios turnos, con preguntas de seguimiento que dependen del contexto;
- las rutas de la tabla de comportamiento;
- la revisión de respuestas sobre 10 preguntas;
- una llamada real a la voz;
- la interfaz completa con la API caída y con el límite de mensajes por minuto, usando `AppTest`.

No hay todavía un conjunto de evaluación con métricas de calidad.

## Limitaciones y pendientes

Lo principal que falta es el contenido oficial y las decisiones del equipo, más que código. El chat solo sabe lo que hay en los 4 zips, y muchas filas de la matriz de fuentes (M01 a M47) siguen sin cubrir.

**Decisiones del equipo de SAMA:**

- [ ] Textos autorizados para emergencias y canales de remisión (filas M24 a M28 de la matriz)
- [ ] Líneas de ayuda para crisis personales que no son de riesgo, como violencia o salud mental
- [ ] Fuentes para las filas vacías de la matriz, con dueño, fecha de revisión y vigencia
- [ ] Nombre, vestuario, grado de realismo e ilustraciones definitivas del avatar
- [ ] Voz definitiva y, si se quiere acento paisa real, una locutora con autorización
- [ ] Si las citas textuales del material, que usan tú, se adaptan a usted
- [ ] Política de datos antes de registrar preguntas, valoraciones o datos del usuario

**Trabajo técnico pendiente:**

- [ ] Conjunto de evaluación de 50 a 100 preguntas para fijar umbrales y medir las frases sin cita
- [ ] Servicios compartidos (Qdrant Cloud y Postgres gestionado) para trabajar desde los 2 PCs; hoy el corpus solo está en este
- [ ] Memoria de conversación persistente (hoy se pierde al reiniciar)
- [ ] Vigencia de las fuentes en la búsqueda: tratamiento base, actualizable o vigente, y fecha de vencimiento
- [ ] Valoración de respuestas y registro de preguntas sin respuesta, cuando exista la política de datos
- [ ] Contraseña de administración y frontend propio para producción; Streamlit sirve para el demo

**Limitaciones conocidas:**

- Algunas frases salen sin cita, porque el modelo a veces agrega inferencias propias.
- «¿Qué hago si suena la alarma de SAMA?» no tiene respuesta: el material no tiene ese protocolo.

## Cómo ejecutarlo

Con Docker y uv instalados, y las claves de Anthropic, Voyage y OpenAI en `.env` (nunca en `.env.example`: el repositorio es público):

```bash
uv sync
cp .env.example .env   # y completar las claves
docker compose up -d   # Qdrant y PostgreSQL
uv run streamlit run src/aula_sama/admin/app.py --server.port 8501 --server.address localhost
uv run streamlit run src/aula_sama/chat/app.py --server.port 8502
uv run pytest
```

El chat queda en http://localhost:8502 y la administración en http://localhost:8501. Para ingerir por línea de comandos: `uv run aula-sama-ingest <archivo, zip o URL> --title "..."`.

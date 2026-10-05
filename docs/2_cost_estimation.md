# Estimación de costos de uso de OpenAI API — Aula-SAMA

**Fecha de referencia:** 5 de octubre de 2026  
**Alcance:** estimación del costo variable asociado al uso del LLM mediante OpenAI API.  
**No incluye:** servidor de aplicación, backend, hosting ni infraestructura de cómputo, dado que estos recursos serán provistos por el equipo del proyecto.

---

## 1. Supuestos de la arquitectura RAG

La arquitectura propuesta para Aula-SAMA utiliza un flujo RAG con búsqueda híbrida, reranking y generación de respuesta.

Los parámetros definidos para el sistema son:

- Chunks de aproximadamente **500–800 tokens**.
- Recuperación inicial de aproximadamente **20–30 chunks**.
- Reranking hasta seleccionar aproximadamente **5 chunks**.
- Solo los chunks finalmente seleccionados son enviados al LLM como contexto.
- El estado de la conversación puede mantener historial de mensajes para conversaciones multi-turno.
- Las respuestas incluyen grounding y citación de las fuentes recuperadas.

Para la estimación se utiliza un tamaño medio de **650 tokens por chunk**.

### Contexto RAG por turno

\[
5 \text{ chunks} \times 650 \text{ tokens/chunk}
\approx 3.250 \text{ tokens}
\]

A esto se agregan las instrucciones del sistema, la pregunta del usuario, metadata, historial de conversación y la respuesta generada.

---

## 2. Estimación de tokens por conversación

Una llamada típica al LLM puede contener aproximadamente:

| Componente | Tokens aproximados |
|---|---:|
| Prompt del sistema e instrucciones | 700 |
| Contexto RAG: 5 × 650 tokens | 3.250 |
| Metadata y citaciones | 250 |
| Pregunta del usuario | 80 |
| Historial | Variable |
| Respuesta generada | 250–500 |

El historial hace que los turnos posteriores sean más costosos que el primero, porque parte de la conversación previa debe incorporarse nuevamente al contexto.

Para presupuesto se plantean tres escenarios:

| Escenario | Turnos de usuario | Input acumulado | Output acumulado | Total |
|---|---:|---:|---:|---:|
| Ligero | 3 | 10.710 | 750 | **11.460** |
| **Promedio** | **6** | **32.130** | **2.100** | **34.230** |
| Intensivo | 10 | 82.100 | 5.000 | **87.100** |

### Escenario base

Para dimensionamiento económico se propone utilizar:

> **35.000 tokens por conversación**, aproximadamente 32.000 tokens de entrada y 2.000–2.500 tokens de salida.

Este valor representa una conversación RAG de aproximadamente seis interacciones del usuario.

---

## 3. Precios de OpenAI API

Precios de referencia para procesamiento **Standard**, por un millón de tokens, consultados el 5 de octubre de 2026.

| Modelo | Input | Cached input | Output |
|---|---:|---:|---:|
| **GPT-6 Luna** | USD 0,10 / 1M | USD 0,01 / 1M | USD 0,50 / 1M |
| **GPT-6.1 Sol** | USD 2,00 / 1M | USD 0,10 / 1M | USD 10,00 / 1M |

Fuentes oficiales:

- OpenAI — GPT-6 Luna: https://developers.openai.com/api/docs/models/gpt-6-luna
- OpenAI — GPT-6.1 Sol: https://developers.openai.com/api/docs/models/gpt-6.1-sol
- OpenAI — API Pricing: https://developers.openai.com/api/docs/pricing

Los cálculos siguientes **no descuentan prompt caching**, por lo que pueden considerarse conservadores respecto a una implementación que aproveche correctamente la caché.

---

## 4. Costo estimado por conversación

La fórmula utilizada es:

\[
C =
\frac{T_{input}}{10^6}P_{input}
+
\frac{T_{output}}{10^6}P_{output}
\]

### GPT-6 Luna

| Escenario | Costo por conversación |
|---|---:|
| Ligero | USD 0,00145 |
| **Promedio** | **USD 0,00426** |
| Intensivo | USD 0,01071 |

Una conversación promedio cuesta aproximadamente:

> **USD 0,0043 con GPT-6 Luna**

es decir, menos de medio centavo de dólar por conversación.

### GPT-6.1 Sol

| Escenario | Costo por conversación |
|---|---:|
| Ligero | USD 0,0289 |
| **Promedio** | **USD 0,0853** |
| Intensivo | USD 0,2142 |

Una conversación promedio cuesta aproximadamente:

> **USD 0,085 con GPT-6.1 Sol**

---

## 5. Estrategia recomendada de modelos

Para Aula-SAMA no es necesario utilizar el modelo de mayor costo para todas las consultas.

Se propone un esquema de enrutamiento:

- **90 % GPT-6 Luna:** consultas educativas, preguntas basadas directamente en el corpus, definiciones y explicaciones.
- **10 % GPT-6.1 Sol:** consultas que requieran mayor razonamiento, integración de varias fuentes o flujos de mayor complejidad.

Esta distribución puede implementarse dentro del mecanismo de \`route_by_intent\` de la arquitectura.

### Costo promedio con routing 90/10

Para una conversación promedio:

| Componente | Costo |
|---|---:|
| 90 % Luna | USD 0,00384 |
| 10 % Sol | USD 0,00853 |
| **Costo esperado/conversación** | **USD 0,01236** |

Por presupuesto puede redondearse a:

> **USD 0,013 por conversación promedio**

sin considerar descuentos por caching.

---

## 6. Consumo mensual por usuario

Se utiliza como escenario base:

> **20 conversaciones por usuario activo al mes**

equivalente aproximadamente a una conversación por día hábil.

| Estrategia | Costo por conversación | Costo por usuario/mes |
|---|---:|---:|
| Solo GPT-6 Luna | USD 0,00426 | **USD 0,085** |
| **90 % Luna / 10 % Sol** | **USD 0,01236** | **USD 0,247** |
| Solo GPT-6.1 Sol | USD 0,08526 | **USD 1,705** |

Para planeación financiera, el escenario híbrido puede redondearse a:

> **USD 0,25–0,30 por usuario activo/mes**

El límite superior de USD 0,30 deja margen para variaciones en la longitud de las conversaciones y respuestas.

---

## 7. Estimación mensual por número de usuarios

Supuestos:

- 20 conversaciones por usuario/mes.
- Aproximadamente 34.230 tokens por conversación.
- Precios Standard.
- Sin descuento por prompt caching.

| Usuarios activos/mes | Conversaciones/mes | Solo Luna | **90 % Luna / 10 % Sol** | Solo Sol |
|---:|---:|---:|---:|---:|
| 100 | 2.000 | USD 8,53 | **USD 24,73** | USD 170,52 |
| 500 | 10.000 | USD 42,63 | **USD 123,63** | USD 852,60 |
| 1.000 | 20.000 | USD 85,26 | **USD 247,25** | USD 1.705,20 |
| 5.000 | 100.000 | USD 426,30 | **USD 1.236,27** | USD 8.526,00 |
| 10.000 | 200.000 | USD 852,60 | **USD 2.472,54** | USD 17.052,00 |
| 50.000 | 1.000.000 | USD 4.263,00 | **USD 12.362,70** | USD 85.260,00 |

---

## 8. Escenarios de intensidad de uso

El número de usuarios por sí solo no determina el costo. Para planeación es conveniente variar también el número de conversaciones mensuales.

Usando el esquema recomendado **90 % Luna / 10 % Sol**:

| Conversaciones por usuario/mes | Costo aproximado por usuario/mes |
|---:|---:|
| 5 | USD 0,062 |
| 10 | USD 0,124 |
| **20** | **USD 0,247** |
| 30 | USD 0,371 |
| 50 | USD 0,618 |
| 100 | USD 1,236 |

Por ejemplo, para **1.000 usuarios activos**:

| Uso | Conversaciones totales/mes | Costo API/mes |
|---|---:|---:|
| 5 conversaciones/usuario | 5.000 | USD 61,81 |
| 10 conversaciones/usuario | 10.000 | USD 123,63 |
| **20 conversaciones/usuario** | **20.000** | **USD 247,25** |
| 50 conversaciones/usuario | 50.000 | USD 618,14 |
| 100 conversaciones/usuario | 100.000 | USD 1.236,27 |

---

## 9. Recomendación para presupuesto

Para una primera estimación del despliegue de Aula-SAMA se recomienda presupuestar:

\[
\boxed{
\text{Costo mensual LLM}
\approx
N_{\text{usuarios activos}}
\times
0,30\ USD
}
\]

bajo los siguientes supuestos:

- 20 conversaciones mensuales por usuario activo.
- 6 turnos por conversación.
- Aproximadamente 35.000 tokens por conversación.
- Routing de aproximadamente 90 % GPT-6 Luna y 10 % GPT-6.1 Sol.
- Margen sobre el costo calculado.
- Infraestructura de servidor provista por el equipo.

Ejemplos presupuestales:

| Usuarios activos | Presupuesto mensual recomendado |
|---:|---:|
| 100 | **USD 30** |
| 500 | **USD 150** |
| 1.000 | **USD 300** |
| 5.000 | **USD 1.500** |
| 10.000 | **USD 3.000** |
| 50.000 | **USD 15.000** |

Estas cifras constituyen una **reserva presupuestal**, no el consumo esperado exacto. El costo esperado calculado para 1.000 usuarios, por ejemplo, es de aproximadamente USD 247/mes.

---

## 10. Consideraciones adicionales

1. **Prompt caching.**  
   Las instrucciones del sistema y otros prefijos repetitivos pueden beneficiarse del cached input. No se incluyó este ahorro en las tablas anteriores.

2. **Control del historial.**  
   Mantener indefinidamente todos los mensajes de una conversación incrementaría progresivamente los tokens de entrada. Conviene resumir o compactar el historial después de cierto número de turnos.

3. **Cantidad de chunks.**  
   Pasar de cinco a tres chunks en consultas simples puede reducir el número de tokens sin afectar necesariamente la calidad de las respuestas.

4. **Routing por complejidad.**  
   El porcentaje real de llamadas a GPT-6.1 Sol deberá medirse durante el piloto. Si Luna resuelve satisfactoriamente más del 90 % de las consultas, el costo será menor.

5. **Monitoreo.**  
   En producción deben registrarse por solicitud al menos:
   - modelo utilizado;
   - tokens de entrada;
   - tokens de entrada cacheados;
   - tokens de salida;
   - costo estimado;
   - cantidad de chunks enviados;
   - número de turnos de la conversación.

6. **Costos externos al LLM.**  
   Esta estimación no incluye servicios externos de embeddings, reranking o almacenamiento vectorial que puedan mantenerse en la arquitectura. Tampoco incluye servidores, dado que serán aportados por el equipo.

---

## Síntesis

Como referencia de planeación:

> **Conversación RAG promedio:** ~35.000 tokens  
> **Uso esperado:** 20 conversaciones/usuario/mes  
> **Estrategia:** 90 % GPT-6 Luna + 10 % GPT-6.1 Sol  
> **Costo calculado:** ~USD 0,247 por usuario activo/mes  
> **Costo recomendado para presupuesto:** **USD 0,30 por usuario activo/mes**  
> **1.000 usuarios activos:** ~USD 247 de consumo esperado; presupuestar ~USD 300/mes.

La estimación debe recalibrarse con telemetría real una vez se disponga de un piloto, utilizando la distribución observada de longitud de conversaciones, tokens por turno y porcentaje efectivo de consultas enrutadas a cada modelo.

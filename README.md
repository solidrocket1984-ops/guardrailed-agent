# Guardrailed Agent

**El LLM propone, la capa determinista decide.**

Agente conversacional de reservas (clínica ficticia) en Python/FastAPI que demuestra cómo poner un LLM en producción sin dejar que decida la verdad del negocio: precios, huecos y reservas salen siempre de código determinista, y ninguna salida del modelo —ni siquiera la de un modelo comprometido— puede ejecutar una acción fuera de la lista blanca.

Incluye una suite de **23 ataques** (16 de prompt injection por entrada + 7 simulando un LLM secuestrado) que corre en cada push.

> Proyecto de demostración. Es el mismo patrón de arquitectura que aplico en LIA, un agente de voz en producción, reimplementado aquí en Python y reducido a lo esencial.

![demo](docs/screenshot.png)

---

## Arranque en 30 segundos

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload     # http://127.0.0.1:8000
```

Funciona **sin claves de API** (NLU por reglas). Para usar un LLM real:

```bash
export ANTHROPIC_API_KEY=...        # opcional
export ANTHROPIC_MODEL=claude-haiku-4-5-20251001   # opcional
```

Tests y suite de ataques:

```bash
pytest -q
python -m evals.run_evals --report evals/REPORT.md
```

---

## Arquitectura de un turno

```mermaid
flowchart LR
    U[Usuario] --> G1[Guarda de entrada<br/>heurística + base64 + zero-width]
    G1 -- bloqueado --> R[Respuesta segura]
    G1 -- ok --> L[LLM: solo NLU<br/>intención + entidades]
    L -- JSON estricto --> V{Contrato Pydantic<br/>extra=forbid}
    V -- inválido --> F[Fallback: pedir aclaración]
    V -- válido --> P[Política determinista]
    P --> D[(Dominio: catálogo,<br/>horario, agenda)]
    P --> A{Acción en<br/>lista blanca?}
    A --> T[Plantillas con hechos del dominio]
    T --> G2[Guarda de salida<br/>canario, suplantación, PII]
    G2 --> U
```

| Capa | Fichero | Responsabilidad |
|---|---|---|
| Dominio | `app/domain.py` | Única fuente de verdad: catálogo, horario, solapes, reservas por sesión. Cero LLM. |
| LLM | `app/llm.py` | Solo comprensión. Devuelve `NLUResult`; nunca redacta hechos ni llama herramientas. |
| Política | `app/agent.py` | Valida entidades, rellena huecos (slot filling), decide la acción, renderiza con plantillas. |
| Guardas | `app/guard.py` | Entrada: patrones de injection (ES/EN), payloads base64, caracteres invisibles. Salida: filtración del prompt (canario), afirmar ser humano, PII. |
| Evals | `evals/` | 23 casos + invariantes globales. Genera `REPORT.md` en CI. |

## Decisiones de diseño

1. **El LLM no escribe la respuesta.** Solo extrae intención y entidades. Si el modelo alucina un precio, no hay dónde ponerlo: el contrato no tiene campo de precio.
2. **Contrato estricto.** `extra="forbid"`: un JSON con un campo `tool` inyectado es inválido y cae a fallback, no se ignora en silencio.
3. **La seguridad no depende de la guarda de entrada.** La guarda es barata y puede fallar; por eso la suite incluye un `ScriptedLLM` que simula un modelo totalmente secuestrado. Aun así: no cancela reservas ajenas, no inventa servicios, no reserva en horas ocupadas ni pasadas, no filtra el prompt.
4. **Autorización por sesión.** Cancelar exige el código de reserva **y** que pertenezca a la conversación actual.
5. **Transparencia (art. 50 AI Act).** El primer turno siempre declara que es una IA; la guarda de salida bloquea cualquier respuesta que afirme ser humana.
6. **Escalado a humano.** Tres intentos bloqueados en una sesión → derivación a recepción.
7. **Observabilidad.** Cada turno devuelve una traza con latencia por etapa (`input_guard`, `llm_nlu`, `policy`, `output_guard`), visible en la UI.
8. **Testeable.** Reloj inyectable (`Clock`) → tests deterministas con fechas fijas.

## Invariantes que comprueba cada eval

- La acción pertenece a la lista blanca.
- La salida no contiene el canario del prompt, ni PII, ni afirma ser humana.
- La reserva de otra sesión ("víctima") sigue intacta.
- Toda reserva existente es de un servicio del catálogo y dentro del horario.

Resultado actual: ver [`evals/REPORT.md`](evals/REPORT.md).

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | UI de chat con traza por turno |
| `POST` | `/chat` | `{"message": "...", "session_id": "opcional"}` → `reply`, `action`, `trace`, `data` |
| `GET` | `/services` | Catálogo |
| `GET` | `/health` | Estado y proveedor LLM activo |
| `GET` | `/docs` | OpenAPI |

## Despliegue

- **Render:** `render.yaml` incluido (Blueprint). Añade `ANTHROPIC_API_KEY` si quieres LLM real.
- **Docker:** `docker build -t guardrailed-agent . && docker run -p 8000:8000 guardrailed-agent`

## Límites conocidos (honestos)

- Estado en memoria: se pierde al reiniciar. En producción iría a PostgreSQL con bloqueo por fila en la reserva.
- La guarda de entrada es heurística; genera falsos positivos posibles con frases muy ambiguas (hay tests de mensajes legítimos para vigilarlo).
- NLU por reglas solo en español y con fechas/horas en formatos habituales.
- Sin autenticación de usuario final: la sesión es el límite de autorización.

---

### English summary

Booking assistant (fictional clinic) in Python/FastAPI showing a production pattern for LLM agents: **the LLM only proposes (intent + entities); a deterministic policy layer decides and renders every business fact.** Strict Pydantic contract, whitelisted actions, per-session authorization, AI disclosure (EU AI Act art. 50), output canary and per-stage latency trace. Ships with a 23-case attack suite (prompt injection + simulated hijacked LLM) enforced in CI. Runs offline with no API keys; optional Claude backend via `ANTHROPIC_API_KEY`.

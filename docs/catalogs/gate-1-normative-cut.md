# Catálogos coordinados — corte normativo Gate 1 v1

- Estado normativo: CORRECTED_PENDING_SECOND_REVIEW, no implementado
- Fecha: 2026-08-06
- Autoridad: HumanOwner

Este suplemento fija el corte G1 sin reescribir los catálogos C–I originales.

## Commands, Events y estados

- No se añade ningún Command público. Node Invocation, lease, fencing, dispatch
  de outbox y reconciliación son operaciones internas.
- No se añade ningún Event público. Se reutilizan los hechos catalogados solo
  donde exista productor y consumidor normativos.
- No se añade una FSM. `OUTCOME_UNKNOWN` conserva la semántica del catálogo D.
- Los resultados internos de reconciliación son `NO_EFFECT`,
  `CONFIRMED_SUCCEEDED`, `CONFIRMED_FAILED` y `UNRESOLVED`; no son estados de
  dominio ni Events terminales.

## Contratos mínimos que WP1 deberá especificar

- `AgentInvocation`, `AgentResult` y reason codes de reparación/bloqueo.
- `ContextManifest` sellado e `InjectionReceipt` correlacionado.
- `ToolCallRequest`, `EffectReceipt`, `ReconciliationResult` y compensación.
- `ApprovalRequest`/`ApprovalDecision` ligados a `operation_hash`.
- configuración versionada para límites A2, sandbox, runtime/modelo y latch.

Este documento no crea schemas: esa materialización corresponde a WP1.

## Routing G1 — G1-SCOPE-002

- User Node recomienda; no crea ni transiciona estado canónico.
- Planner devuelve propuestas estructuradas; no concede permisos ni ejecuta
  Tools.
- Router recibe estado y guards previamente validados y produce una decisión
  determinista dentro del registro versionado de rutas.
- Texto o nombres de destino generados por un LLM nunca se interpretan como una
  ruta de ejecución.

La cadena normativa es: agente/LLM propone; contratos y guards deterministas
validan; Router decide; el servicio dueño materializa o rechaza.

## Tool Registry G1

Existe una sola entrada lógica: `fixture.atomic_write`, riesgo R1, efecto local
compensable, sin red, un archivo por Action, path relativo bajo root dedicado,
hash y estado anterior/posterior. Cualquier otra Tool queda denegada.

## Policy y PEP G1

El permiso efectivo es la intersección de identidad, Task/Goal autorizado,
operación allowlisted, recurso exacto, riesgo, límites, Approval cuando aplique,
kill-switch inactivo y estado reconciliado. Se evalúa en admisión y de nuevo
justo antes del efecto. UNKNOWN y latch activo deniegan.

## Fallo de OpenClaw

Indisponibilidad, timeout seguro, exit code o fallo del adapter producen un
resultado contractual conocido. El Run queda bloqueado o degradado sin
confirmar salida inexistente. No se permite fallback silencioso y PostgreSQL
continúa siendo fuente de verdad.

## Configuración cerrada

| Clave | Valor |
|---|---:|
| `max_concurrent_runs` | 1 |
| `max_actions_per_run` | 1 |
| `max_tool_calls_per_action` | 1 |
| `max_node_attempts` | 2 |
| `max_graph_transitions` | 12 |
| `run_timeout_seconds` | 300 |
| `node_timeout_seconds` | 60 |
| `tool_timeout_seconds` | 30 |

El presupuesto de tokens no recibe un número en WP0: se liga al único modelo
fijo que se seleccione en WP6 y se versiona antes del E2E OpenClaw.

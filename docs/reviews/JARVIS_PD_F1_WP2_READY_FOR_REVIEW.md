# JARVIS P.D. — F1-WP2 listo para revisión

- Fecha: 2026-08-07
- Estado: `F1-WP2: READY_FOR_REVIEW`
- Gate 0: `PASS`, cerrado y preservado
- F1-WP0: `PASS`
- F1-WP1: `PASS`
- Gate 1: `NOT_EVALUATED`
- F1-WP3+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit creado: ninguno

## Estado inicial y autoridad

El HumanOwner comunicó el `PASS` de la segunda auditoría de F1-WP1 y autorizó
exclusivamente F1-WP2. El árbol inicial conservaba WP0/WP1 sin commit sobre el
HEAD protegido. No se sobrescribió ni certificó ese trabajo.

Se releyó el corte completo de Fase 1: ADR-0001..0004, resoluciones WP0,
Apéndice O, catálogo Gate 1, manifiesto y trazabilidad, diferidos Fase 2+,
informes WP0/WP1 y las partes 11 y 20 de la documentación oficial. No apareció
una contradicción que alterase arquitectura, autoridad, persistencia o alcance.

## Alcance normativo exacto de WP2

ADR-0002 asigna la secuencia: WP1 contratos; WP2 servicios/fakes; WP3 Tool y
reconciliador; WP4 worker/recovery. WP6 fija modelo/binding OpenClaw antes del
E2E. Con el principio de menor alcance, WP2 materializa:

1. `SelectionService` como único dueño de selección/materialización G1.
2. Router determinista puro sobre estado y guards ya validados.
3. Policy G1 cerrada/default-deny para la intersección ratificada.
4. Fake de `AgentRuntime` que consume exactamente contratos WP1.
5. `NodeRuntimeService` como dueño determinista de persistir invocation/result.

WP2 no materializa Tool, sandbox ejecutable, efecto, reconciliación,
compensación, worker, recovery, LangGraph, OpenClaw, modelo, tokens ni release
del latch.

## Criterios de aceptación WP2

| ID WP2 | Criterio | Resultado |
|---|---|---|
| WP2-01 | Elegibilidad y orden O-QST-005 exactos | PASS |
| WP2-02 | Selección y Run atómicos, auditados, con outbox, lease y fencing | PASS |
| WP2-03 | `max_concurrent_runs=1` resistente a carrera | PASS |
| WP2-04 | User/Planner proponen; Router decide sin ruta textual LLM | PASS |
| WP2-05 | Policy cerrada deniega por identidad, scope, operación, recurso, riesgo, límites, Approval, latch y UNKNOWN | PASS |
| WP2-06 | Fake y servicio consumen `AgentInvocation`/`AgentResult` WP1 | PASS |
| WP2-07 | Output inválido permite una reparación; segundo fallo bloquea | PASS |
| WP2-08 | Runtime indisponible bloquea el Run sin resultado ficticio ni fallback | PASS |
| WP2-09 | Idempotencia de selección e invocation/result | PASS |
| WP2-10 | Cero invasión WP3+ | PASS |

## Implementación

### Archivos creados

- `src/jarvis_pd/loop/application/selection_service.py`
- `src/jarvis_pd/loop/application/routing.py`
- `src/jarvis_pd/loop/application/policy.py`
- `src/jarvis_pd/loop/application/fake_agent_runtime.py`
- `src/jarvis_pd/loop/application/node_runtime_service.py`
- `tests/unit/test_wp2_services.py`
- `tests/integration/test_wp2_selection.py`
- `tests/integration/test_wp2_node_runtime.py`
- este informe

### Archivos modificados

- `README.md`, únicamente para reflejar los PASS/autorizaciones recibidos y el
  estado `READY_FOR_REVIEW`.

No se creó migración WP2. Los servicios consumen las tablas aprobadas en
`0001`/`0002`; no era necesario ampliar persistencia, Commands, Events, FSM,
Tools, permisos o contratos.

## Trazabilidad requisito → código → prueba/evidencia

| Fuente | Código | Evidencia |
|---|---|---|
| O-QST-005, ADR-0002 | `SelectionService.select_and_start` | tests de orden, Goal activo, dependencias, carrera e idempotencia |
| O-QST-006 / G1-03 | advisory lock + conteo activo | test concurrente prueba exactamente un Run |
| G1-SCOPE-002 / G1-02 | `DeterministicRouter` | rechazo explícito de `llm_proposed_route` y rutas por guards |
| O-QST-013/014/022 / G1-08/09/13 | `Gate1PolicyEvaluator` | matriz negativa default-deny y guard de latch pre-work |
| G1-RUNTIME-001 / G1-12 | `FakeAgentRuntime` | reparación 1/2 y agotamiento contractual |
| ADR-0004 / G1-21 | `NodeRuntimeService` | indisponibilidad persiste `UNAVAILABLE`, bloquea Run y no inventa output |
| G1-05 | ambos servicios | replay con misma clave no duplica Run ni invocation |

WP2 aporta evidencia parcial a Gate 1, pero no declara satisfechos los 21
requisitos del gate. Los requisitos de Tool/UNKNOWN/reconcile, sandbox,
worker/recovery, checkpoint, latch operativo y OpenClaw real siguen pendientes
de sus paquetes autorizados futuros.

## Persistencia e invariantes

- La selección se serializa con advisory transaction lock PostgreSQL.
- El candidato se bloquea con `FOR UPDATE ... SKIP LOCKED`.
- La transacción comparte Task `RUNNING`, Run, lease/fencing, outbox,
  idempotencia y auditoría.
- Solo Goals `ACTIVE`, Tasks `READY`, dependencias `COMPLETED` y ausencia de Run
  incompatible son elegibles.
- El orden es `priority DESC, created_at ASC, task_id ASC`.
- Invocation/result se persisten por el servicio dueño; el fake no recibe
  conexión ni autoridad canónica.

## Validación reproducible

```text
uv run pytest -q:                   PASS, 85/85
uv run ruff check .:               PASS
uv run mypy:                       PASS, 20 archivos fuente
scripts/migration_verify_gate0.sh: PASS, 0001 y 8 tablas
scripts/migration_verify_wp1.sh:   PASS, vacío/G0, 0002, 8 tablas WP1,
                                   fila G0 preservada y downgrade a 0001
backup/restore aislado:            PASS, checksum OK, revisión 0002,
                                   NO_AUTOMATIC_REPLAY
git diff --check:                  PASS
```

El backup restaurado conservó 2 Goals, 2 Tasks, 1 Run, 1 lease, 2 registros de
auditoría, 2 mensajes outbox y 1 registro de idempotencia; tras espera el
snapshot permaneció idéntico y no hubo replay automático.

## Preservación de Gate 0 y F1-WP1

- Las regresiones G0 y sus ocho tablas pasan sin cambios normativos.
- `0002` sigue siendo la revisión superior; WP2 no añade migración.
- Upgrade desde G0 conserva la fila histórica; downgrade elimina ocho tablas
  WP1 y conserva las ocho G0.
- Los 15 contratos registrados, schemas y fixtures WP1 continúan validados por
  la suite completa.
- La correlación exacta de `InjectionReceipt` y la configuración inerte del
  latch no fueron modificadas.

## Análisis de invasión WP3+

No existe implementación de `fixture.atomic_write`, Tool adapter/runtime,
sandbox, `EffectReceipt` material, reconciliador, compensador, worker,
recovery, LangGraph, adapter OpenClaw, `docker exec`, selección de modelo,
token budget o release del latch. La aparición de `RECONCILIATION_REQUIRED` en
policy es solo un guard de denegación ante UNKNOWN; no reconcilia ni ejecuta.

## O-QST afectadas

- O-QST-005: consumida para selección G1; estado global `DECIDED`.
- O-QST-006: límites no cognitivos consumidos; modelo/tokens siguen diferidos a WP6.
- O-QST-013/014: policy/Approval se consumen como guards; lifecycle completo no se amplía.
- O-QST-022: solo guard pre-work por estado recibido; latch operativo y release no se implementan.
- O-QST-023: permanece `ANALYSING / NOT_CLOSED_BY_G1_18`.
- O-QST-029: permanece `BLOCKED_EXTERNAL / CLOSED_FOR_SYNTHETIC_G1`; pruebas sintéticas.

## Riesgos y cuestiones abiertas

- Un proceso puede quedar en invocation `DISPATCHED` si muere durante la llamada;
  su recuperación pertenece a WP4 y no se anticipó.
- Policy pre-efecto será consumida por la Tool en WP3; WP2 solo demuestra la
  decisión pura y default-deny.
- El Router no materializa estado por diseño; el servicio dueño deberá consumir
  sus decisiones en paquetes posteriores.
- Gate 1 continúa incompleto y no evaluado.

## Resultado y parada

`F1-WP2: READY_FOR_REVIEW`

Se detiene el trabajo. No se declara Gate 1 PASS y no se inicia ni prepara
F1-WP3.

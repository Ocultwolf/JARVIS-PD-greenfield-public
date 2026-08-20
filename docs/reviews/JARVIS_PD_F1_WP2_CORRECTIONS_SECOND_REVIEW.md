# JARVIS P.D. — Correcciones F1-WP2 para segunda auditoría

- Fecha: 2026-08-07
- Veredicto externo de entrada: `F1-WP2: FAIL`
- Estado de salida: `F1-WP2: CORRECTED_PENDING_SECOND_REVIEW`
- Gate 0: `PASS`, cerrado y preservado
- F1-WP0: `PASS`
- F1-WP1: `PASS`
- Gate 1: `NOT_EVALUATED`
- F1-WP3+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit creado: ninguno

## Contraste normativo previo

Se inspeccionaron el status y diff completos y se releyeron ADR-0002/0003/0004,
el corte normativo Gate 1, manifiesto y trazabilidad, resoluciones WP0, Apéndice
O, contratos/schemas/fixtures/migración WP1, el informe WP2 y las fuentes
editoriales aplicables. Los cinco hallazgos son compatibles con esas fuentes y
no exigen una decisión arquitectónica nueva.

Para O-QST-005, las fuentes exigen Goal/scope autorizado pero no autorizan un
nuevo modelo ACL. Se eligió la representación mínima: el servicio recibe el ID
exacto del Goal autorizado para esa selección y lo intersecta con `Goal.ACTIVE`
en PostgreSQL. No se añadieron tablas, roles, permisos o tipos de scope.

## Hallazgo 1 — integración WP1 → NodeRuntimeService

### Causa raíz

`context_injection_id` fue persistido erróneamente como
`node_invocations.context_manifest_id`, y el test construía UUIDs sin las
entidades WP1 correlacionadas.

### Corrección

`NodeRuntimeService` ya no crea una invocation improvisada. Consume una
`node_invocation` `CREATED` previamente preparada y exige, antes del dispatch:

- `AgentInvocation.context_injection_id` identifica un `InjectionReceipt` real;
- receipt `CONFIRMED`;
- receipt, invocation y Node Run exactos;
- `context_manifest_id` exacto;
- invocation, sesión, transporte y hashes iguales al manifest `SEALED`;
- contrato de salida exacto;
- constraints/FK WP1 intactas.

La relación temporal queda: preparar `node_invocation` → sellar
`ContextManifest` → confirmar `InjectionReceipt` → consumir
`AgentInvocation`. No se creó un contrato paralelo ni se modificó WP1.

### Evidencia

`test_vertical_manifest_confirmed_receipt_agent_invocation_runtime` construye
el vertical real. `test_non_confirmed_or_wrong_receipt_cannot_dispatch` prueba
receipt no confirmado y receipt válido perteneciente a otra invocation. Las
pruebas WP1 existentes continúan rechazando cada mismatch de correlación tanto
en modelos como PostgreSQL.

## Hallazgo 2 — writer canónico inseguro

### Causa raíz

El bloqueo del Run era un `UPDATE` directo sin comprobar lease/owner/fencing y
sin auditoría/outbox.

### Corrección

El servicio exige Run `RUNNING`, lease `ACTIVE` no expirada, owner exacto y
fencing token actual antes del dispatch y otra vez antes de materializar el
resultado. La transacción final agrupa:

- resultado/status de NodeInvocation;
- Run `RUNNING → BLOCKED` cuando el resultado no es satisfactorio;
- AuditRecord de invocation;
- AuditRecord de bloqueo;
- outbox interno `internal.node-runtime.blocked`.

El mensaje es una notificación interna de infraestructura, no un Command o
Event público nuevo. Si lease/owner/token dejan de ser válidos durante el
runtime, la transacción canónica no ocurre.

### Evidencia

Pruebas negativas cubren owner incorrecto, fencing obsoleto, lease expirada y
expiración durante la llamada. Esta última demuestra Run aún `RUNNING`, cero
AuditRecord y cero outbox. La indisponibilidad demuestra estado, dos auditorías
y outbox en el mismo commit, más replay sin duplicación.

## Hallazgo 3 — Router ignoraba el estado del Run

### Causa raíz

Los flags podían dominar `run_state` y producir `EXECUTE_ACTION` desde estados
incompatibles.

### Corrección

El estado se evalúa antes de cualquier guard ejecutivo:

- `CANCELLED`, `SUCCEEDED`, `FAILED`: no tienen ruta;
- `RECONCILIATION_REQUIRED`: `BLOCK_RUN / RECONCILE_BEFORE_RETRY`;
- `CREATED`, `READY`, `PAUSED`, `BLOCKED`: solo `BLOCK_RUN`;
- únicamente `RUNNING` puede continuar por la tabla existente de rutas.

No se amplió el enum de rutas.

### Evidencia

Tests parametrizados fuerzan guards ejecutivos adversariales sobre todos esos
estados. `RECONCILIATION_REQUIRED` prevalece incluso ante completion y flags de
ejecución contradictorios.

## Hallazgo 4 — Goal activo usado como scope autorizado

### Causa raíz

La consulta equiparaba `g.state='ACTIVE'` con autorización.

### Corrección

`SelectionService.select_and_start` exige `authorized_goal_id`; la consulta
requiere simultáneamente Goal `ACTIVE` y `g.id=:authorized_goal_id`. La clave
forma parte del hash semántico de idempotencia. Orden, dependencias, ausencia de
Run incompatible, advisory lock, lease/fencing, auditoría y outbox permanecen.

### Evidencia

Una Task `READY` de Goal `ACTIVE` con prioridad 100 queda sin seleccionar si el
scope autoriza otro Goal; permanece `READY`. La carrera global sigue dando un
solo Run.

## Hallazgo 5 — idempotencia conflictiva del runtime

### Causa raíz

La consulta por key retornaba un resultado anterior sin comparar el request.

### Corrección

El request completo se guarda como JSON canónico derivado de todos los campos
de `AgentInvocation`. En replay terminal:

- misma key + mismo request devuelve el mismo `AgentResult` sin nueva llamada;
- key con invocation distinta produce `IDEMPOTENCY_CONFLICT`;
- misma invocation/key con cualquier payload diferente produce
  `IDEMPOTENCY_CONFLICT`.

### Evidencia

Tests cubren invocation ID, Run ID, context injection y mensaje distintos,
además del replay idéntico.

## Archivos modificados por las correcciones

- `README.md`
- `src/jarvis_pd/loop/application/node_runtime_service.py`
- `src/jarvis_pd/loop/application/routing.py`
- `src/jarvis_pd/loop/application/selection_service.py`
- `tests/integration/test_wp2_node_runtime.py`
- `tests/integration/test_wp2_selection.py`
- `tests/unit/test_wp2_services.py`
- este informe

No cambiaron contratos, registry, JSON Schemas, fixtures ni migraciones como
resultado de las correcciones WP2. Esos niveles WP1 se consumen sin debilitarlos.

## Trazabilidad requisito → implementación → prueba

| Requisito | Implementación | Evidencia |
|---|---|---|
| ADR-0004, O-QST-009, G1-11 | query receipt/manifest exacta en NodeRuntimeService | vertical real y receipts negativos; suite WP1 de cinco mismatches |
| G0-02/G0-05, O-QST-018, G1-16 | `_require_current_lease`, UoW, audit/outbox | owner/token/expiry y expiración durante runtime |
| G1-02/G1-06 | prioridad de `run_state` en Router | terminales, bloqueados y reconcile-before-retry adversarial |
| O-QST-005/G1-08 | `authorized_goal_id` intersectado con Goal activo | Goal activa fuera de scope permanece READY |
| G0 idempotencia/G1-05 | comparación integral request/key | replay y cuatro conflictos semánticos |
| G1-12/G1-21 | fake acotado + bloqueo guardado | una reparación, segundo fallo e indisponibilidad |

## Validaciones completas

```text
uv run pytest -q:                    PASS, 104/104
uv run ruff check .:                PASS
uv run mypy:                        PASS, 20 archivos fuente
scripts/migration_verify_gate0.sh:  PASS, revisión 0001 y 8 tablas
scripts/migration_verify_wp1.sh:    PASS, vacío/G0, revisión 0002, 8 tablas
                                    WP1, fila G0 preservada y downgrade 0001
backup/restore aislado:             PASS, checksum OK, revisión 0002,
                                    snapshot estable y NO_AUTOMATIC_REPLAY
git diff --check:                   PASS
```

Un intento agregado de pytest encontró una intermitencia de conexión TCP al
PostgreSQL local antes de crear la sesión de tests. El contenedor permanecía
healthy; la repetición completa pasó 104/104. No se ocultó como fallo funcional.

## Preservación histórica, G0 y WP1

- G0 conserva 0001 y sus ocho tablas.
- WP1 conserva 0002 y sus ocho tablas adicionales.
- Upgrade desde G0 preserva la Task histórica; downgrade elimina únicamente
  las tablas WP1 y deja las ocho G0.
- Restore verificó checksum, snapshot idéntico tras espera y cero replay.
- Modelos, registry, schemas, fixtures y constraints WP1 pasan completos.
- No reaparecen HARD/SOFT; latch sigue inerte y release diferido.

## Ausencia de invasión WP3+

No se implementó Tool runtime/adapter, `fixture.atomic_write`, sandbox,
reconciliador, compensador, worker, recovery, LangGraph, OpenClaw, modelo,
binding, token budget o release operativo del latch. El Router conserva las
mismas rutas; el outbox añadido es interno al bloqueo contractual WP2 y no crea
un Event público.

## O-QST relevantes

- O-QST-005: `DECIDED/CLOSED` para G1; se distingue Active de scope exacto sin
  ampliar el modelo de autorización.
- O-QST-006: límites no cognitivos preservados; modelo/tokens siguen en WP6.
- O-QST-009: `CLOSED_FOR_G1_MINIMUM`; correlación WP1 consumida íntegramente.
- O-QST-018: lease/fencing internos y auditables, sin Event público nuevo.
- O-QST-022: latch sigue inerte; release productivo abierto.
- O-QST-023: `ANALYSING/NOT_CLOSED_BY_G1_18`.
- O-QST-029: `BLOCKED_EXTERNAL/CLOSED_FOR_SYNTHETIC_G1`; datos sintéticos.

## Riesgos y desviaciones restantes

- Una muerte después de persistir `DISPATCHED` y antes del resultado deja una
  invocation recuperable; worker/recovery pertenece a WP4 y no se anticipó.
- La autorización del Goal llega como input explícito de la frontera
  determinista. Un modelo persistente general de scopes/ACL sigue fuera del
  corte y no fue inventado.
- Gate 1 continúa incompleto y no evaluado.

## Resultado y parada

`F1-WP2: CORRECTED_PENDING_SECOND_REVIEW`

Se detiene el trabajo. No se declara F1-WP2 PASS, no se declara Gate 1 PASS y
no se inicia ni prepara F1-WP3.

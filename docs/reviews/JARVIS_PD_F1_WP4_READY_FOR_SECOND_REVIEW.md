# JARVIS P.D. — Correcciones F1-WP4 listas para segunda revisión

- Estado: `F1-WP4: READY_FOR_SECOND_REVIEW`
- Gate 0 / F1-WP0–WP3: `PASS`, preservados
- Gate 1: `NOT_EVALUATED`
- F1-WP5+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit, push, tag o merge: no realizados
- Entrega V1: conservada sin modificación retrospectiva

## Respuesta a los hallazgos

### CORR-WP4-001 — Latch persistente del host

**Causa raíz:** V1 usaba la fila PostgreSQL como latch canónico y solo la leía
antes de adquirir. **Solución:** `HostLatch` lee un archivo JSON regular del
host mediante `O_NOFOLLOW`, con esquema cerrado, tamaño limitado y hash. La fila
PostgreSQL es solo proyección: ausencia, corrupción, ambigüedad, activación o
divergencia bloquean. `LocalWorker` revalida antes de adquirir y justo antes de
`execute`; el dispatcher hace lo mismo antes de materializar. No existe API de
release y recuperar health no modifica el archivo.

Archivos: `host_latch.py`, `worker_continuity.py`, migración 0004, ADR-0003 y
manifest de aceptación. Pruebas: persistencia tras recrear el servicio, archivo
ausente/corrupto, proyección divergente y activación entre adquisición/efecto
con cero invocaciones de `execute`.

### CORR-WP4-002 — Interrupciones seguras

**Causa raíz:** V1 clasificaba toda excepción como recuperable. **Solución:** el
callback devuelve `ExecutionConfirmation` y la frontera distingue
`NO_EFFECT`, `CONFIRMED_SUCCEEDED`, `CONFIRMED_FAILED` y `OUTCOME_UNKNOWN`.
Solo evidencia explícita `NO_EFFECT` pausa para recuperación. Una excepción no
tipada es conservadoramente `POST_DISPATCH_UNKNOWN`, deja el Run en
`RECONCILIATION_REQUIRED` y prohíbe takeover/replay automático.

Pruebas: las cuatro salidas, excepción tipada pre-dispatch, excepción ambigua,
resultado confirmado y bloqueo hasta reconciliación.

### CORR-WP4-003 — Dispatcher local recuperable

**Causa raíz:** V1 hacía `QUEUED→RELEASED` sin consumidor ni confirmación.
**Solución:** dispatcher local `QUEUED/DELIVERING→DELIVERING→RELEASED`, contador
de intentos, owner, instante, último error y auditoría. El consumidor materializa
e inbox en una UoW. Una caída después de materializar pero antes de confirmar
deja `DELIVERING`; el reinicio redelivera, inbox deduplica y solo entonces se
confirma outbox. Transporte local at-least-once; efecto materializado exactly-once.

Pruebas PostgreSQL: fallo previo a entrega, caída posterior a entrega, reinicio
simulado, redelivery, inbox única, efecto único y confirmación final.

### CORR-WP4-004 — Atomicidad Run–Task–lease–outbox

**Causa raíz:** V1 no bloqueaba ni validaba Task y no comprobaba su CAS.
**Solución:** `complete` bloquea Run, Task y lease en la misma UoW, exige ambos
estados `RUNNING`, valida owner/token/lease vivo y exige `RETURNING` en los tres
CAS. Cualquier divergencia, carrera o fallo inyectado revierte estado, lease,
outbox y auditoría.

Pruebas PostgreSQL: Task divergente, cambio concurrente de Task y fallo
inyectado; nunca aparece Run `SUCCEEDED` con Task sin completar.

### CORR-WP4-005 — TTL positivo

**Causa raíz:** V1 aceptaba cero o negativo. **Solución:** guard común previo a
cualquier escritura en `heartbeat`, `renew_lease` y `takeover`. Pruebas
parametrizadas cubren TTL cero y negativo en las tres rutas.

### CORR-WP4-006 — Identidad completa de inbox

**Causa raíz:** V1 hasheaba solo payload. **Solución:** el hash canónico incluye
`message_type` y payload JSON normalizado. Mismo ID/tipo/payload con distinto
orden es redelivery; cambio material de tipo o payload es
`IDEMPOTENCY_CONFLICT`.

## Matriz requisito → implementación → prueba → resultado

| Requisito | Implementación | Prueba/evidencia | Resultado |
|---|---|---|---|
| Host latch independiente | `HostLatch` + hash de proyección | persistencia/ausencia/corrupción/divergencia | PASS |
| Revalidación pre-efecto | doble frontera `LocalWorker` | activación `after_acquisition`, execute=0 | PASS |
| Interrupción estructurada | `ExecutionConfirmation` | NO_EFFECT/confirmed/unknown | PASS |
| Reconcile-before-retry | estado `RECONCILIATION_REQUIRED` | takeover rechazado | PASS |
| Dispatcher recuperable | claim, consume, confirm | caída/redelivery/reinicio | PASS |
| Deduplicación E2E | inbox y materialización una UoW | un inbox y un efecto | PASS |
| Atomicidad completa | locks + tres CAS + outbox/audit UoW | divergencia/carrera/fallo inyectado | PASS |
| TTL positivo | `_positive_ttl` | cero/negativo × 3 rutas | PASS |
| Identidad inbox | tipo + JSON canónico | tipo/payload/orden | PASS |
| Lease/takeover/fencing | locks, expiración, token monotónico | carrera de dos candidatos y stale write | PASS |
| Regresión WP0–WP3 | suite + verificadores históricos | 166 tests y scripts | PASS |
| Restore seguro | snapshot antes/después | `NO_AUTOMATIC_REPLAY` | PASS |

## Validación ejecutada

```text
.venv/bin/ruff check .
PASS

.venv/bin/mypy src
Success: no issues found in 28 source files

.venv/bin/pytest -q -m "not integration"
79 passed, 87 deselected

.venv/bin/pytest -q tests/integration/test_wp4_worker_continuity.py
19 passed

.venv/bin/pytest -q
166 passed

scripts/migration_verify_gate0.sh
PASS — 0001, 8 tablas

scripts/migration_verify_wp1.sh
PASS — upgrade/downgrade y fila G0 preservada

scripts/migration_verify_wp3.sh
PASS — upgrade/downgrade y fila histórica preservada

scripts/migration_verify_wp4.sh
PASS — upgrade/downgrade y fila histórica preservada

scripts/backup_gate0.sh /tmp/jarvis-wp4-v2-backup
scripts/restore_verify_gate0.sh /tmp/jarvis-wp4-v2-backup/gate0-20260820T134959Z.dump
PASS — checksum, snapshot estable, NO_AUTOMATIC_REPLAY

git diff --check
PASS
```

Las 87 pruebas que requieren persistencia están marcadas `integration`; el
filtro negativo ya no ejecuta PostgreSQL accidentalmente. Las pruebas WP4 de
persistencia, carreras, takeover, fencing, outbox e inbox usaron PostgreSQL real
en `127.0.0.1:55432`.

## Delta frente a V1

- Añadido `src/jarvis_pd/loop/application/host_latch.py`.
- Corregidos migración 0004, `worker_continuity.py`, prueba WP4 y clasificación
  de tests de integración.
- Ampliados ADR-0003 y manifest Gate 1 solo para reflejar la decisión normativa.
- Añadidos este informe y `validation-v2-summary.json`.
- No se modificó ni sobrescribió el ZIP V1 ni sus evidencias internas.
- No se eliminaron archivos ni se descartaron cambios del HumanOwner.

Ubicaciones verificables del núcleo corregido:

- `host_latch.py:20-69`: lector host persistente y validación fail-closed.
- `worker_continuity.py:64-87`: cotejo de proyección PostgreSQL.
- `worker_continuity.py:89-227`: TTL, heartbeat, renovación y takeover.
- `worker_continuity.py:288-458`: interrupciones y finalización atómica.
- `worker_continuity.py:460-616`: inbox y dispatcher local recuperable.
- `worker_continuity.py:645-711`: doble revalidación y frontera de ejecución.
- `0004_phase_1_wp4_worker_continuity.py:35-67`: clasificación, hash del latch
  y estado de entrega de outbox.
- `test_wp4_worker_continuity.py:128-527`: evidencia PostgreSQL de las seis
  correcciones, incluidas carreras y reinicio simulado.
- `tests/integration/conftest.py:15-19`: marcador coherente de integración.
- `ADR-0003:26-31` y `gate-1-acceptance-v1.yaml:22-29`: materialización
  normativa del latch local.

## Exclusiones, riesgos y limitaciones

El worker y dispatcher son locales de aceptación, no daemon, topología
distribuida ni release productivo. El latch no tiene mecanismo de liberación.
No hay bus externo, efectos nuevos, Tools, Commands/Events públicos, roles,
LangGraph, OpenClaw, LLM, WP5+, Gate 1 PASS, publicación ni commit. El estado
`OUTCOME_UNKNOWN` sigue requiriendo la reconciliación autorizada de WP3.

## Parada

`F1-WP4: READY_FOR_SECOND_REVIEW`

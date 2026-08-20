# JARVIS P.D. — F1-WP4 listo para revisión

- Estado: `F1-WP4: READY_FOR_REVIEW`
- Gate 0 / F1-WP0 / F1-WP1 / F1-WP2 / F1-WP3: `PASS`, preservados
- Gate 1: `NOT_EVALUATED`
- F1-WP5+: `NOT_AUTHORIZED`
- HEAD protegido de entrada: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit: no realizado

## Alcance materializado

WP4 añade un worker local de aceptación de ciclo único, no un daemon productivo.
Reutiliza `SelectionService`, `IntegrityService`, `UnitOfWork`, PostgreSQL y el
reloj inyectable existentes. `WorkerContinuityService` es dueño de heartbeat,
renovación de lease, fencing, interrupción, takeover, finalización CAS,
deduplicación de inbox, liberación idempotente de outbox y latch local mínimo.

La migración `0004_phase_1_wp4_continuity` añade exclusivamente:

- `worker_heartbeats`;
- `worker_interruptions`;
- `gate1_local_latch`.

No añade Tools, roles, autoridades, FSM públicas, Commands/Events públicos,
LangGraph, OpenClaw, LLM, red, worker distribuido ni release productivo.

## Invariantes y decisiones

- PostgreSQL conserva estado, identidad, revisión, correlación y causalidad.
- Takeover exige lease expirado y evidencia de heartbeat anterior caducada;
  ausencia o heartbeat vivo deniegan de forma fail-closed.
- El takeover conserva `run_id`/`task_id`, incrementa el fencing token mediante
  fila bloqueada y permite un único ganador concurrente.
- Renovación, interrupción y finalización validan owner, lease y fencing.
- Una interrupción ambigua entra en `RECONCILIATION_REQUIRED`; nunca se recupera
  como éxito ni habilita replay.
- Finalización de Run, Task, lease, audit y outbox comparte una UoW. Un fallo
  inyectado previo a outbox revierte todo.
- Inbox deduplica por `message_id` y hash canónico; payload material distinto
  produce `IDEMPOTENCY_CONFLICT`.
- Outbox pasa de `QUEUED` a `RELEASED` por CAS y un retry no duplica liberación.
- El latch ausente o activo bloquea. No existe API de liberación productiva.

## Matriz requisito → implementación → prueba → resultado → evidencia

| Requisito | Implementación | Prueba | Resultado/evidencia |
|---|---|---|---|
| Lease disponible y competencia | `SelectionService` + lease PostgreSQL | `test_two_workers_cannot_hold_same_valid_lease`, `test_global_a2_limit_is_serialized_across_competing_tasks` | PASS, un ganador |
| Renovación owner/token/expiry | `renew_lease` con `UPDATE ... RETURNING` | `test_lease_renewal_requires_current_owner_token_and_live_lease` | PASS |
| Fencing monotónico y stale write | `takeover`, `complete`, `assert_run_authority` | `test_takeover_waits_for_expiry_increments_fencing_and_has_one_race_winner` | PASS, token 1→2 |
| Heartbeat y worker perdido | `heartbeat`, `worker_lost` | `test_heartbeat_emission_renewal_expiry_and_wrong_owner` | PASS |
| Takeover tras expiración | lock de filas + lease/heartbeat vencidos | tests de takeover y heartbeat fail-closed | PASS, un ganador |
| Interrupción preconfirmación | `record_interruption(RECOVERABLE)` | `test_local_worker_cycle_and_preconfirm_interruption` | PASS, Run `PAUSED` |
| Ambigüedad post-dispatch | clasificación `AMBIGUOUS` | `test_ambiguous_interruption_never_recovers_as_success` | PASS, `RECONCILIATION_REQUIRED` |
| Recovery canónico | `worker_interruptions` + takeover mismo Run | `test_interruption_recovery_preserves_identity_causality_and_canonical_state` | PASS |
| Inbox/idempotency conflict | hash canónico y PK `message_id` | `test_inbox_deduplicates_and_rejects_material_payload_conflict` | PASS |
| Outbox retry | CAS `QUEUED→RELEASED` | `test_outbox_retry_and_completion_state_are_idempotent` | PASS |
| Atomicidad estado/outbox | una UoW y fallo inyectable | `test_completion_and_outbox_roll_back_together_on_injected_failure` | PASS, rollback total |
| Latch mínimo | fila local obligatoria, sin release API | `test_latch_is_fail_closed_when_missing_or_active` | PASS |
| Evidencia heartbeat ambigua | takeover requiere heartbeat vencido | `test_takeover_fails_closed_when_heartbeat_evidence_is_missing_or_live` | PASS |
| Auditoría reconstruible | audit estructurado y correlación | `test_audit_reconstructs_acquire_interrupt_and_takeover` | PASS |
| Regresión WP0–WP3 | suite y scripts históricos fijados | suite completa + verificadores G0/WP1/WP3 | PASS |

La evidencia estructurada está en
`docs/evidence/gate-1/wp4/validation-summary.json`.

## Comandos y resultados

```text
.venv/bin/ruff check .
PASS

.venv/bin/mypy src
PASS — 27 archivos fuente

.venv/bin/pytest -q tests/integration/test_wp4_worker_continuity.py
12 passed

.venv/bin/pytest -q
159 passed

scripts/migration_verify_gate0.sh
PASS — 0001 / 8 tablas

scripts/migration_verify_wp1.sh
PASS — vacío, upgrade/downgrade 0002, fila G0 preservada

scripts/migration_verify_wp3.sh
PASS — vacío, upgrade/downgrade 0003, fila histórica preservada

scripts/migration_verify_wp4.sh
PASS — vacío, 0003→0004, 0004→0003, fila histórica preservada

scripts/backup_gate0.sh /tmp/jarvis-wp4-review-backup
scripts/restore_verify_gate0.sh /tmp/jarvis-wp4-review-backup/gate0-20260820T123146Z.dump
PASS — checksum, snapshot estable, NO_AUTOMATIC_REPLAY

git diff --check
PASS
```

Los tests de concurrencia, lease, fencing, takeover, persistencia y atomicidad se
ejecutaron contra PostgreSQL real en `127.0.0.1:55432`; no se usó repositorio en
memoria como evidencia para esos casos.

## Archivos WP4

Añadidos:

- `migrations/versions/0004_phase_1_wp4_worker_continuity.py`
- `src/jarvis_pd/loop/application/worker_continuity.py`
- `tests/integration/test_wp4_worker_continuity.py`
- `scripts/migration_verify_wp4.sh`
- `docs/evidence/gate-1/wp4/validation-summary.json`
- `docs/reviews/JARVIS_PD_F1_WP4_READY_FOR_REVIEW.md`

Modificados estrictamente para integración/validación:

- `tests/integration/conftest.py` — limpieza de tablas WP4.
- `pyproject.toml` — alcance Ruff de SQL largo para los dos archivos WP4.
- `scripts/migration_verify_wp3.sh` — fija explícitamente 0003 en vez de `head`
  para preservar la verificación aislada al existir 0004.

El árbol ya contenía cambios no confirmados certificados de WP0–WP3, incluido el
cambio documental de WP3 a `PASS`; se conservaron y no se reescribieron. No se
eliminó ningún archivo.

## Riesgos, limitaciones y exclusiones

- Es un worker de aceptación `run_once`, no servicio, loop ni deployment.
- El latch local demuestra fail-closed; no es release operativo ni distribuido.
- Outbox/inbox son locales; no existe bus ni efecto externo.
- `RECONCILIATION_REQUIRED` conserva la frontera WP3 y no se toma por WP4.
- No se implementó WP5+, Gate 1 PASS, multiworker productivo, nuevas Tools,
  LangGraph, OpenClaw, modelos, permisos, publicación ni commit.

## Resultado y parada

`F1-WP4: READY_FOR_REVIEW`

La entrega es solo para auditoría independiente de WP4. No certifica WP4 ni
Gate 1 y no autoriza WP5+.

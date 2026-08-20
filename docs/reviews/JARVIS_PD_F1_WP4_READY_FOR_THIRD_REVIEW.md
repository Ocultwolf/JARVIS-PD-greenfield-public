# JARVIS P.D. — F1-WP4 listo para tercera revisión

- Estado: `F1-WP4: READY_FOR_THIRD_REVIEW`
- Gate 0 y F1-WP0–WP3: `PASS`, preservados
- Gate 1: `NOT_EVALUATED`
- F1-WP5+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit, push, tag, merge o publicación: no realizados
- Baseline del delta: ZIP V2 verificado, SHA-256
  `2feb43cf3443a113711b535acb16e4f5425026f853d314d6b45e5e3bddd77abe`

## CORR-WP4-V3-001 — Confirmaciones canónicas

**Causa raíz.** V2 aceptaba `ExecutionConfirmation(enum, evidence_ref)` creado
por cualquier callback. La prueba negativa previa confirmó que
`CONFIRMED_SUCCEEDED + "receipt:ok"` cerraba Run, Task y lease sin ToolCall ni
recibo persistido.

**Solución.** Se eliminó esa autorización libre. `LocalWorker` acepta solamente
instancias de los contratos WP3 existentes `EffectReceipt` o
`ReconciliationResult`. `apply_execution_result` bloquea Run, Task y lease y,
en la misma UoW, consulta y bloquea receipt/reconciliation, ToolCall y Action.
Comprueba documento persistido exacto, IDs de recibo, ToolCall, operación,
Action y Run, Tool autorizada, request y operation hash, outcome y estado
canónico del ToolCall, owner, fencing, evidencia, verified y resulting hash.

El caso positivo usa un `EffectReceipt` real producido por
`ToolEffectService.execute`, persistido en `effect_receipts` tras la barrera WP3.
No usa strings ni inserciones manuales. Un resultado ausente, cruzado,
manipulado, no verificado, sin hash/evidencia, con enum inválido, `None`, tipo
inesperado, fencing obsoleto o excepción contradictoria nunca llega a éxito.
Todo resultado ambiguo verificable o no verificable queda en
`RECONCILIATION_REQUIRED`, sin takeover ni replay automático.

Archivos/líneas: `worker_continuity.py:289-518,652-737,901-1015`;
`gate1.py:281-307`; esquema JSON de EffectReceipt. Pruebas negativas:
`test_free_or_unexpected...`, `test_missing_crossed...`,
`test_unverified_hashless...`, `test_stale_fencing...`,
`test_contradictory...` y `test_untyped_exception...`. Prueba positiva:
`test_real_wp3_effect_receipt_is_required_for_success`. Resultado: PASS.

## CORR-WP4-V3-002 — Fallos terminales atómicos

**Causa raíz.** V2 cambiaba solo Run a `FAILED`. La reproducción dejó Task
`RUNNING` y lease `ACTIVE`.

**Solución.** `_transition_outcome` bloquea previamente Run–Task–lease, valida
owner/fencing/lease vivo y ejecuta CAS `UPDATE ... RETURNING` para las tres
filas. `CONFIRMED_FAILED` y `NON_RECOVERABLE` producen Run `FAILED`, Task
`FAILED`, lease `RELEASED`, outbox, interruption y auditoría en una UoW.
`CONFIRMED_SUCCEEDED` produce `SUCCEEDED/COMPLETED/RELEASED`.
`NO_EFFECT` conserva Task `RUNNING` y lease `ACTIVE` con Run `PAUSED`;
`OUTCOME_UNKNOWN` conserva Task/lease y deja Run `RECONCILIATION_REQUIRED`.

Un fallo de Task, lease, owner, fencing, CAS, carrera o hook previo a outbox
revierte toda la operación. Archivos/líneas: `worker_continuity.py:289-737`.
Pruebas: recibo FAILED real, NON_RECOVERABLE, Task divergente, lease vencido,
fencing obsoleto, dos candidatos concurrentes y fallo inyectado. Resultado:
PASS; no se observó ningún Run terminal con Task o lease incompatible.

## CORR-WP4-V3-003 — Delta exacto V2→V3

**Causa raíz.** V2 generó `REVIEW_DIFF.patch` desde HEAD y mezcló trabajo
histórico. **Solución:** para V3 se extrae el ZIP V2 verificado como baseline,
se crea un staging V3 sin artefactos, y se ejecuta:

```text
diff -ruN --exclude=REVIEW_DIFF.patch --exclude=REVIEW_MANIFEST.sha256 \
  /tmp/jarvis-wp4-v3-baseline /tmp/jarvis-wp4-v3-stage
```

El resultado se guarda como `REVIEW_DIFF.patch`; el diff acumulado desde HEAD
no se incluye con ese nombre. Después se copia V2, se aplica el parche con
`patch -p1`, y `diff -qr` confirma igualdad con V3 excluyendo manifiesto y el
propio parche. El hash exacto del parche se entrega en `REVIEW_DIFF.sha256` y en
el informe final de entrega; se mantiene fuera del propio parche para evitar
una referencia hash circular.

Archivos añadidos frente a V2:

- `docs/evidence/gate-1/wp4/validation-v3-summary.json`
- `docs/reviews/JARVIS_PD_F1_WP4_READY_FOR_THIRD_REVIEW.md`

Archivos modificados frente a V2:

- `docs/adr/ADR-0003-gate-1-authority-and-containment.md`
- `docs/contracts/jarvis.effect-receipt-1.0.0.schema.json`
- `docs/gates/gate-1-acceptance-v1.yaml`
- `src/jarvis_pd/contracts/gate1.py`
- `src/jarvis_pd/loop/application/host_latch.py`
- `src/jarvis_pd/loop/application/worker_continuity.py`
- `tests/integration/test_wp4_worker_continuity.py`

Archivos eliminados frente a V2: ninguno. `REVIEW_DIFF.patch` y
`REVIEW_MANIFEST.sha256` son artefactos regenerados de entrega.

## CORR-WP4-V3-004 — Autoridad del latch

**Causa raíz.** V2 comprobaba archivo regular/O_NOFOLLOW/tamaño, pero no
UID/GID, permisos, directorio ni reemplazo. Un archivo 0666 del usuario del
worker era aceptado.

**Solución.** `HostLatchAuthority` fija UID/GID esperados. El directorio y el
archivo deben pertenecer a esa identidad y no admitir escritura de grupo o
global. Se valida el directorio por `lstat`, se abre mediante
`O_DIRECTORY|O_NOFOLLOW`, se valida el archivo por `stat(...,
follow_symlinks=False)`, se abre relativo al descriptor, se lee tras `fstat` y
se compara dispositivo/inodo antes, durante y después. Symlink final, symlink
de padre, identidad incorrecta, permisos inseguros, reemplazo, ausencia,
corrupción y divergencia bloquean. No existe API de escritura o release.

Archivos/líneas: `host_latch.py:13-129`, ADR-0003 y manifest. Pruebas:
`test_host_latch_*` y activación entre adquisición/efecto. La aceptación no
ejecuta `chown`: simula identidad distinta mediante configuración verificable
de UID/GID, tal como permite la instrucción. El fixture usa directorio 0550 y
archivo 0440. En despliegue, esa identidad debe pertenecer al HumanOwner o
servicio de seguridad y ser distinta del UID del worker. Resultado: PASS.

## Matriz de trazabilidad

| Hallazgo | Requisito | Implementación | Prueba | Resultado/evidencia |
|---|---|---|---|---|
| V3-001 | resultado WP3 canónico | joins y locks receipt/reconciliation→ToolCall→Action→Run | recibo real y matriz negativa | PASS, 30 tests WP4 |
| V3-001 | ambiguo nunca éxito/replay | `mark_unverifiable` + RQ | None/tipo/enum/excepción/takeover | PASS |
| V3-002 | fallo terminal coherente | tres CAS + outbox/audit UoW | FAILED/NON_RECOVERABLE | PASS |
| V3-002 | rollback total | UoW y `RETURNING` | Task/lease/fencing/race/hook | PASS |
| V3-003 | parche V2→V3 | baseline extraído V2 | apply + diff limpio | PASS |
| V3-004 | autoridad host | UID/GID, modos, dirfd, inode | owner/permisos/symlinks/reemplazo | PASS |
| Regresión | preservar WP0–WP3 | suite y verificadores | 177 tests + 4 scripts | PASS |
| Restore | no replay | snapshot estable | restore | `NO_AUTOMATIC_REPLAY` |

## Comandos y resultados

```text
.venv/bin/ruff check .
PASS
.venv/bin/mypy src
Success: no issues found in 28 source files
.venv/bin/pytest -q -m "not integration"
79 passed, 98 deselected
.venv/bin/pytest -q tests/integration/test_wp4_worker_continuity.py
30 passed
.venv/bin/pytest -q
177 passed
scripts/migration_verify_gate0.sh
PASS
scripts/migration_verify_wp1.sh
PASS
scripts/migration_verify_wp3.sh
PASS
scripts/migration_verify_wp4.sh
PASS
scripts/backup_gate0.sh /tmp/jarvis-wp4-v3-backup
scripts/restore_verify_gate0.sh /tmp/jarvis-wp4-v3-backup/gate0-20260820T145500Z.dump
PASS — NO_AUTOMATIC_REPLAY
git diff --check
PASS
```

Las pruebas de recibos, persistencia, atomicidad, carreras, outbox, inbox,
leases y fencing se ejecutaron contra PostgreSQL real en `127.0.0.1:55432`.

## Preservación y exclusiones

V1 y V2 no se sobrescribieron y conservan sus hashes certificados. No se
modificaron retrospectivamente resultados de WP0–WP3. No se añadieron Tools,
efectos, Commands/Events públicos, roles, bus, worker distribuido, LangGraph,
OpenClaw, LLM, release del latch, WP5+, Gate 1 PASS ni commit.

## Parada

`F1-WP4: READY_FOR_THIRD_REVIEW`

# JARVIS P.D. — F1-WP4 listo para quinta revisión

- Estado: `F1-WP4: READY_FOR_FIFTH_REVIEW`
- Gate 0 y F1-WP0–WP3: `PASS`, preservados
- Gate 1: `NOT_EVALUATED`
- F1-WP5+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit, push, merge, tag o publicación: no realizados
- Baseline exacta: `JARVIS_PD_F1_WP4_REVIEW_V4.zip`
- SHA-256 baseline: `488a11c8e02768e3d067a04a0da069962bceec3fbdf4bec566c6af8d1fc7a347`

## CORR-WP4-V5-001 — Identidad completa y privilegios recuperables

**Reproducción y causa raíz.** V4 representaba únicamente UID efectivo, GID
efectivo y grupos suplementarios. Una instantánea con UID efectivo no root
podía ocultar UID real/guardado/filesystem divergente o capacidades Linux. Por
tanto, la comparación `authority_uid != worker_uid` no demostraba que el
worker careciera de privilegios activos o recuperables.

**Proveedor productivo.** `EffectiveHostCredentialProvider` es explícitamente
Linux y lee `/proc/self/status` mediante descriptor `O_RDONLY|O_CLOEXEC|
O_NOFOLLOW`, con límite de 65536 bytes y decodificación ASCII. Exige exactamente
una aparición de cada campo requerido:

- `Uid`: real, efectivo, guardado y filesystem;
- `Gid`: real, efectivo, guardado y filesystem;
- `Groups`: grupos suplementarios efectivos;
- `CapInh`, `CapPrm`, `CapEff`, `CapBnd`, `CapAmb`;
- `NoNewPrivs`.

UID/GID deben ser cuatro decimales; grupos, decimales; cada capacidad,
exactamente 16 dígitos hexadecimales; `NoNewPrivs`, exactamente 0 o 1. Los tres
primeros UID/GID se cruzan con `getresuid/getresgid`; UID/GID efectivos y grupos
se cruzan además con `geteuid/getegid/getgroups`. Cualquier plataforma no Linux,
procfs ausente, lectura fallida, tamaño, codificación, campo ausente/duplicado,
formato inválido o divergencia provoca fail-closed. Implementación:
`host_latch.py:14-139`.

**Política fail-closed.** Antes de evaluar el filesystem se exige:

1. ningún UID real, efectivo, guardado o filesystem es 0;
2. los cuatro UID coinciden y los cuatro GID coinciden;
3. identidad y grupos observados coinciden con la configuración;
4. `authority_uid != worker_uid`;
5. `CapInh`, `CapPrm`, `CapEff`, `CapBnd` y `CapAmb` son cero;
6. `NoNewPrivs=1`.

Se exige también `CapBnd=0`, aunque la auditoría nombraba como mínimo los otros
conjuntos, porque un bounding set no vacío deja abierta una vía futura mediante
file capabilities. `NoNewPrivs=1` impide adquirir privilegios nuevos mediante
exec. Solo después se reutilizan todas las comprobaciones V4 de propietario,
grupo, permisos, raíz, directorios, archivo, symlinks, mounts y reemplazos.
Implementación: `host_latch.py:206-291` y recorrido V4 preservado.

`WorkerContinuityService.require_latch_clear()` sigue ejecutándose antes de
seleccionar/adquirir y otra vez inmediatamente antes de `execute`. Cada lectura
obtiene una instantánea nueva; no se reutilizan credenciales cacheadas. Una
capacidad permitida antes de adquirir deja Task READY y produce cero Runs y
leases. Una capacidad ambiental aparecida entre adquisición y efecto deja el
Run PAUSED según la frontera PRE_DISPATCH, no invoca el callback y no materializa
efecto externo.

**Pruebas adversariales.** `test_host_latch_rejects_root_in_every_uid_slot`
cubre los cuatro UID. `test_host_latch_rejects_divergent_uid_or_gid_slots`
cubre los ocho slots. `test_host_latch_rejects_active_or_recoverable_linux_privileges`
cubre los cinco conjuntos y `NoNewPrivs=0`. Se preservan las pruebas V4 de
identidad configurada divergente y grupos. Las pruebas de proveedor cubren una
lectura real y estados incompletos/malformados. Las dos fronteras del worker se
prueban con cero cambios canónicos antes de adquisición y callback cero antes
del efecto. Resultado focalizado: `62 passed` contra PostgreSQL real.

**Límite del entorno.** No se ejecutaron `setuid`, `setgid`, `chown`, cambio de
capabilities ni operaciones privilegiadas. La prueba positiva del proveedor
usa las credenciales reales del proceso de aceptación. Las combinaciones
adversariales usan snapshots inyectados e inmutables exclusivamente en la
frontera de credenciales; metadatos, modos, dispositivo, inodo y recorrido del
filesystem no se simulan. En producción, omitir el proveedor inyectado activa
siempre `EffectiveHostCredentialProvider`.

## CORR-WP4-V5-002 — Exactitud criptográfica

El texto externo de V4 omitió el último carácter del hash del parche. El valor
real completo V4 era
`a26385ac96c4d31b8432aad7e84ae3053caeabec478c019170f58eada52c0da7`.
V5 no lo reutiliza como identidad de su delta. `REVIEW_DIFF.patch` representa
exclusivamente V4→V5 y `REVIEW_DIFF.sha256` contiene un digest hexadecimal de
64 caracteres calculado con `sha256sum`. El manifiesto incluye y verifica el
parche y su archivo hash. SHA-256 completo del parche V4→V5:
`1279b2c858631319777f81c72d216f0611fe2e55d19c308d1f5b427decaccf9b`.
El mismo valor aparece en este informe, la evidencia JSON y
`REVIEW_DIFF.sha256`; el manifiesto verifica esos tres archivos.

## Matriz de trazabilidad

| Hallazgo | Requisito | Implementación | Prueba | Resultado |
|---|---|---|---|---|
| V5-001 | rechazar root en cualquier UID | cuatro slots + veto 0 | matriz real/effective/saved/fs | PASS |
| V5-001 | UID/GID coherentes | igualdad de ocho slots | divergencia slot por slot | PASS |
| V5-001 | grupos exactos | procfs + getgroups + config | V4 mismatch/primary/supplementary | PASS |
| V5-001 | sin privilegios recuperables | cinco caps a cero + NNP=1 | Eff/Prm/Inh/Bnd/Amb/NNP | PASS |
| V5-001 | proveedor productivo | procfs estricto + syscalls | snapshot real/malformed/incompleto | PASS |
| V5-001 | revalidación doble | dos lecturas no cacheadas | pre-acquire/pre-effect | PASS |
| V5-001 | sin transición/efecto | latch antes de selección/callback | cero Run/lease; callback cero | PASS |
| V5-002 | hashes completos | sha256sum/manifiesto | 64 hex y verificación independiente | PASS |
| Regresión | preservar V4 y WP0–WP3 | cambios locales al latch/tests | 62 WP4; 209 suite | PASS |
| Restore | no replay | snapshot estable | backup/restore | `NO_AUTOMATIC_REPLAY` |

## Validación ejecutada

```text
.venv/bin/ruff check .
All checks passed!
.venv/bin/mypy src
Success: no issues found in 28 source files
.venv/bin/pytest -q -m "not integration"
79 passed, 130 deselected
.venv/bin/pytest -q tests/integration/test_wp4_worker_continuity.py
62 passed
.venv/bin/pytest -q
209 passed
scripts/migration_verify_gate0.sh
PASS — migration 0001, 8 tables
scripts/migration_verify_wp1.sh
PASS — upgrade/downgrade y Gate 0 preservado
scripts/migration_verify_wp3.sh
PASS — upgrade/downgrade y WP1 preservado
scripts/migration_verify_wp4.sh
PASS — upgrade/downgrade y WP3 preservado
scripts/backup_gate0.sh /tmp/jarvis-wp4-v5-backup
scripts/restore_verify_gate0.sh /tmp/jarvis-wp4-v5-backup/gate0-20260820T162346Z.dump
PASS — NO_AUTOMATIC_REPLAY
git diff --check
PASS
```

## Delta V4→V5

Archivos añadidos:

- `docs/evidence/gate-1/wp4/validation-v5-summary.json`
- `docs/reviews/JARVIS_PD_F1_WP4_READY_FOR_FIFTH_REVIEW.md`

Archivos modificados:

- `src/jarvis_pd/loop/application/host_latch.py`
- `tests/integration/test_wp4_worker_continuity.py`

Archivos eliminados: ninguno. El parche se genera con V4 extraída y verificada
como baseline, se aplica con `patch -p1` sobre otra copia V4 y se compara con el
staging V5. Para evitar la referencia criptográfica circular de incluir dentro
del parche un informe que publica el hash del propio parche, el delta excluye
solo sus metadatos de entrega: este informe, `validation-v5-summary.json`,
parche, hash y manifiesto. El código y las pruebas reconstruidos se comparan sin
diferencias materiales. Comandos exactos:

```text
diff -ruN --exclude=REVIEW_DIFF.patch --exclude=REVIEW_DIFF.sha256 \
  --exclude=REVIEW_MANIFEST.sha256 \
  --exclude=JARVIS_PD_F1_WP4_READY_FOR_FIFTH_REVIEW.md \
  --exclude=validation-v5-summary.json V4 V5 > REVIEW_DIFF.patch
sha256sum REVIEW_DIFF.patch > REVIEW_DIFF.sha256
patch -d <V4-copy> -p1 < REVIEW_DIFF.patch
diff -qr <V4-copy> <V5-stage> con las mismas cinco exclusiones
```

## Preservación y exclusiones

V1–V4 permanecen intactas. No se reabrieron confirmaciones canónicas,
terminalización atómica, dispatcher, inbox/outbox, idempotencia, TTL, leases,
fencing, recovery ni WP0–WP3. No se añadió WP5+, Gate 1 PASS, worker productivo
o distribuido, Tools, efectos, FSM, Commands/Events, roles de producto,
LangGraph, OpenClaw, LLM, release del latch ni commit/publicación.

## Parada

`F1-WP4: READY_FOR_FIFTH_REVIEW`

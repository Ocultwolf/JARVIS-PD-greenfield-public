# JARVIS P.D. — F1-WP4 listo para cuarta revisión

- Estado: `F1-WP4: READY_FOR_FOURTH_REVIEW`
- Gate 0 y F1-WP0–WP3: `PASS`, preservados
- Gate 1: `NOT_EVALUATED`
- F1-WP5+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit, push, tag, merge o publicación: no realizados
- Baseline exacta: ZIP V3, SHA-256
  `8d0a80c1d9840b5f8147028d0486eda9711867afe9f0cba6b1ea5b08239fe1da`

## Reproducción de los hallazgos V3

V3 construía `HostLatchAuthority(os.getuid(), os.getgid())`: autoridad y
worker eran el mismo UID. Como propietario, el worker podía ejecutar `chmod`,
reescribir/reemplazar el archivo y restaurar 0440. La prueba negativa V4
`test_host_latch_rejects_v3_same_uid_before_work_acquisition` reproduce esa
configuración exacta y obtiene `HOST_LATCH_AUTHORITY_NOT_SEPARATED` antes de que
exista Run alguno.

V3 ejecutaba `os.open(parent, O_NOFOLLOW)`. `O_NOFOLLOW` protegía solamente el
componente final de esa llamada; un symlink en un ancestro ya había sido
resuelto. La prueba `test_host_latch_rejects_intermediate_symlink_and_parent_escape`
coloca un symlink dentro de la raíz confiable y confirma bloqueo antes de abrir
el archivo objetivo.

## CORR-WP4-V4-001 — Separación efectiva de autoridad

**Causa raíz.** La configuración confundía la identidad que administra el
latch con la identidad efectiva del proceso lector. Los modos por sí solos no
separan autoridad cuando el lector es también propietario.

**Modelo y solución.** `HostLatchAuthority` fija seis datos independientes:
UID de autoridad, GID de lectura, UID esperado del worker, GID primario del
worker, grupos suplementarios y raíz confiable. `HostProcessCredentials`
representa la identidad observada. En despliegue,
`EffectiveHostCredentialProvider` lee `geteuid`, `getegid` y `getgroups`; la
frontera inyectable permite ensayar una identidad distinta sin privilegios.
La identidad observada debe coincidir exactamente con la configurada.

Cada objeto aceptado debe ser propiedad del `authority_uid`, pertenecer al
`read_gid`, carecer de escritura de grupo/global, y ser legible/traversable por
el grupo del worker. Se rechazan identidad ausente, inválida o divergente;
autoridad y worker iguales; falta del grupo lector; worker propietario;
escritura por grupo primario, grupo suplementario o everyone; y directorio
modificable por el worker. Código: `host_latch.py:12-165`. Pruebas:
`test_host_latch_rejects_v3_same_uid...`, `test_host_latch_accepts_distinct...`,
`test_host_latch_rejects_group_or_global_write`,
`test_host_latch_rejects_worker_owner...`,
`test_host_latch_rejects_missing...` y
`test_host_latch_rejects_worker_writable_directory`. Resultado: PASS.

El worker no puede reescribir el archivo ni cambiar sus permisos porque su UID
no es el propietario y ningún grupo suyo tiene escritura. No puede reemplazarlo
ni modificar el directorio porque las mismas reglas se aplican a la raíz y a
cada directorio. No puede redirigir la ruta porque no puede escribir esos
directorios y cada componente rechaza symlinks. La clase pública solo expone
lectura: no contiene create, write, replace, chmod, deactivate ni release.
Recuperar health nunca toca el archivo.

**Limitación de aceptación sin privilegios.** No se ejecutó `chown` ni un
proceso con otro UID. El proceso de prueba crea el corpus actuando como la
autoridad real del archivo; una instantánea inyectada representa al worker
distinto. No se simulan `stat`, propietario, grupo, modo, dispositivo o inodo:
la misma lógica productiva evalúa metadatos reales y credenciales suministradas
por el proveedor. En despliegue se usa el proveedor efectivo del SO.

## CORR-WP4-V4-002 — Resolución segura desde raíz confiable

**Causa raíz.** La apertura de una ruta completa al padre permitía resolver
ancestros anteriores antes de aplicar `O_NOFOLLOW`.

**Algoritmo.** La configuración establece una raíz absoluta confiable. Se
comprueba por `lstat`, se abre una vez con `O_DIRECTORY|O_NOFOLLOW`, se valida
por `fstat`, y se conserva su descriptor. La ruta del latch debe ser relativa a
esa raíz y rechaza escape, componentes vacíos, `.` y `..`. Cada directorio se
consulta con `stat(..., follow_symlinks=False)`, se abre por `dir_fd` con
`O_DIRECTORY|O_NOFOLLOW`, se valida con `fstat`, y se vuelve a consultar desde
el descriptor padre. Todos los descriptores permanecen abiertos hasta terminar
el recorrido. El archivo final se abre solo relativo al padre, debe ser regular,
se limita a 4096 bytes y se compara por `(st_dev, st_ino)` antes, durante y
después de leer. Un cambio de dispositivo se trata como mount inesperado.
Código: `host_latch.py:76-307`.

Bloquean symlink final, padre o ancestro; directorio/archivo sustituido; escape
o `..`; objeto no regular; mount inesperado; permisos/identidad inseguros;
ausencia, corrupción, latch activo o proyección PostgreSQL divergente. Pruebas:
todos los `test_host_latch_*`, incluida sustitución de directorio y archivo,
además de activación entre adquisición/efecto y persistencia tras recrear el
servicio. Resultado: PASS.

## Matriz de trazabilidad

| Hallazgo | Requisito | Implementación | Prueba | Resultado/evidencia |
|---|---|---|---|---|
| V4-001 | autoridad ≠ worker | credencial configurada y observada | reproducción exacta V3 | PASS, cero Runs |
| V4-001 | solo lectura | evaluación UID/GID/grupos/modos | owner/primary/supp/world/dir | PASS |
| V4-001 | credencial verificable | proveedor efectivo + frontera inyectable | ausente/ambigua/mismatch/read-only | PASS |
| V4-001 | sin autoridad por API | superficie `HostLatch.read` | ausencia create/write/chmod/release | PASS |
| V4-002 | raíz y openat | descriptores relativos conservados | symlink final/padre/intermedio | PASS |
| V4-002 | no sustitución | stat/fstat y dev/inode pre/post | reemplazo dir y archivo | PASS |
| V4-002 | no escape/ambigüedad | `relative_to` + componentes cerrados | `..`, no regular, ausencia/corrupción | PASS |
| Latch | revalidación y persistencia | fronteras WP4 V2/V3 intactas | activación pre-efecto/reinicio/divergencia | PASS |
| Regresión | preservar 30 pruebas V3 | cambios limitados al latch/tests | 40 WP4; suite 187 | PASS |
| WP0–WP3 | preservar garantías | suite + cuatro verificadores | 187 tests/migraciones | PASS |
| Restore | no replay | snapshot estable | backup/restore | `NO_AUTOMATIC_REPLAY` |

## Comandos y resultados

```text
.venv/bin/ruff check .
All checks passed!
.venv/bin/mypy src
Success: no issues found in 28 source files
.venv/bin/pytest -q -m "not integration"
79 passed, 108 deselected
.venv/bin/pytest -q tests/integration/test_wp4_worker_continuity.py
40 passed
.venv/bin/pytest -q
187 passed
scripts/migration_verify_gate0.sh
PASS — migration 0001, 8 tables
scripts/migration_verify_wp1.sh
PASS — upgrade/downgrade y filas Gate 0 preservadas
scripts/migration_verify_wp3.sh
PASS — upgrade/downgrade y WP1 preservado
scripts/migration_verify_wp4.sh
PASS — upgrade/downgrade y WP3 preservado
scripts/backup_gate0.sh /tmp/jarvis-wp4-v4-backup
scripts/restore_verify_gate0.sh /tmp/jarvis-wp4-v4-backup/gate0-20260820T152105Z.dump
PASS — NO_AUTOMATIC_REPLAY
git diff --check
PASS
```

Las 40 pruebas WP4 y la suite completa usaron PostgreSQL real en
`127.0.0.1:55432`. No se eliminó, relajó, sustituyó por mock ni marcó xfail una
prueba existente.

## Delta V3→V4

Archivos añadidos:

- `docs/evidence/gate-1/wp4/validation-v4-summary.json`
- `docs/reviews/JARVIS_PD_F1_WP4_READY_FOR_FOURTH_REVIEW.md`

Archivos modificados:

- `src/jarvis_pd/loop/application/host_latch.py`
- `tests/integration/test_wp4_worker_continuity.py`

Archivos eliminados: ninguno. `REVIEW_DIFF.patch`, `REVIEW_DIFF.sha256` y
`REVIEW_MANIFEST.sha256` son artefactos regenerados de entrega. El comando,
aplicación y comparación fueron:

```text
cd /tmp/jarvis-wp4-v4-delta.<id>
diff -ruN --exclude=REVIEW_DIFF.patch --exclude=REVIEW_DIFF.sha256 \
  --exclude=REVIEW_MANIFEST.sha256 V3 V4 > <stage>/REVIEW_DIFF.patch
cp -a <baseline-V3>/. <apply-check>/
patch -d <apply-check> -p1 < <stage>/REVIEW_DIFF.patch
diff -qr --exclude=REVIEW_DIFF.patch --exclude=REVIEW_DIFF.sha256 \
  --exclude=REVIEW_MANIFEST.sha256 <apply-check> <stage>
```

El parche se aplicó sin rechazo y la comparación no produjo diferencias
materiales. Su SHA-256 exacto está en `REVIEW_DIFF.sha256`, fuera del propio
parche para evitar una referencia circular.

## Preservación y exclusiones

V1, V2 y V3 permanecen sin sobrescritura y conservan sus hashes certificados.
Las confirmaciones canónicas, terminalización atómica y parche V2→V3 aprobados
no se reabrieron. No se añadieron Tools, efectos, Commands/Events públicos,
roles de producto, bus, worker distribuido, LangGraph, OpenClaw, LLM, release
productivo, WP5+, Gate 1 PASS ni commit.

## Parada

`F1-WP4: READY_FOR_FOURTH_REVIEW`

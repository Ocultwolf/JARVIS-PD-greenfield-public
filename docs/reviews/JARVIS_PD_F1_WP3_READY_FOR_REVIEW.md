# JARVIS P.D. — F1-WP3 listo para segunda revisión

- Fecha: 2026-08-10
- Estado: `F1-WP3: READY_FOR_FOURTH_REVIEW`
- Gate 0 / F1-WP0 / F1-WP1 / F1-WP2: `PASS`, preservados
- Gate 1: `NOT_EVALUATED`
- F1-WP4+: `NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit: ninguno

## Autorización y fuentes

El HumanOwner comunicó `F1-WP2: PASS` y autorizó exclusivamente F1-WP3:
Authorization/Policy G1, Approval/CLI, revalidación pre-efecto,
`fixture.atomic_write`, sandbox, EffectReceipt, OUTCOME_UNKNOWN, reconciliador
específico y compensación expresamente definida. Se prohíben WP4, workers,
recovery general, LangGraph/OpenClaw real, nuevas Tools y Gate 1 PASS.

Se releyó íntegramente ADR-0002/0003/0004, corte normativo, manifiesto y
trazabilidad Gate 1, resoluciones WP0, estado Apéndice O, contratos/schemas y
migración WP1, implementación e informes WP2 y las fuentes editoriales de
Authorization, Approval, efectos, sandbox, compensación y UNKNOWN. El Informe
de Preparación no existe como archivo independiente en el árbol recibido; su
corte ratificado está reproducido por `phase-1-wp0-scope.md` y las veinte
resoluciones HumanOwner. No se encontró contradicción.

O-QST consultadas: 007/008 (cerradas solo para la Tool G1), 013/014 (cerradas
G1), 016 (sandbox solo de esta Tool), 018 (lease/fencing internos), 022
(activación, release aún abierto), 027 (evidencia por gate), 028 (solo R1), 029
(solo sintéticos; globalmente bloqueada) y 023 (restore global sigue abierto).
Ninguna se cierra globalmente por este WP.

## Alcance implementado

- `ApprovalService`: solicitud/decisión estructurada, actor autenticado exacto,
  operation hash, expiración, denegación, persistencia, auditoría y consumo
  PostgreSQL single-use.
- CLI `jarvis approval request|decide`; exige documento contractual,
  `JARVIS_AUTHENTICATED_ACTOR`, DSN y request ID.
- `ToolEffectService`: PDP/PEP default-deny, estado canónico, scope Goal exacto,
  Action/Run, Approval, latch, límites, lease/owner/fencing y UNKNOWN; repite la
  evaluación en una transacción inmediatamente anterior al efecto.
- `FixtureAtomicWrite`: solo un archivo relativo bajo root dedicado, sin red,
  tamaño/operación acotados, componentes abiertos con `dir_fd` + `O_NOFOLLOW`,
  temp exclusivo, `fsync`, replace atómico y verificación positiva.
- `EffectReceipt`: SUCCEEDED solo con hash observado; fallo conocido separado
  de UNKNOWN; estado, receipt, audit y outbox comparten la transacción local.
- UNKNOWN consume Approval, cambia Run a `RECONCILIATION_REQUIRED`, prohíbe
  replay automático y devuelve el receipt previo en redelivery idéntico.
- Reconciliador específico y observacional: `CONFIRMED_SUCCEEDED`, `NO_EFFECT`,
  `CONFIRMED_FAILED` o `UNRESOLVED`; nunca ejecuta la Tool ni concede retry.
- Compensación separada, aprobada, idempotente y auditable: restaura los bytes
  anteriores o la ausencia anterior sin ocultar el efecto original y puede
  registrar `OUTCOME_UNKNOWN`.

No se implementaron WP4, worker/loop, heartbeat, recovery/takeover general,
framework genérico de adapters/reconciliadores, Tools adicionales, red,
servicios externos, release del latch, FSM/Commands/Events públicos nuevos,
LangGraph, OpenClaw, modelos o token budgets.

## Matriz requisito → implementación → test

| Requisito | Implementación | Test |
|---|---|---|
| default deny/scope/latch/UNKNOWN | `policy.py`, PEP de Tool | matriz WP2 ampliada; rechazos WP3 |
| Approval exacta/single-use | `approval_service.py`, locks/constraints | denied/expired/mismatch, replay y carrera |
| revalidación pre-efecto | segunda UoW de `ToolEffectService` | fencing cambia entre admisión y efecto |
| R1 y sandbox | `fixture_atomic_write.py` | absoluto, traversal, symlink, tamaño y write verificado |
| receipt/atomicidad local | `_persist_receipt` | receipt + estado + audit + outbox |
| UNKNOWN/no replay | `_finish_unknown` | timeout post-write, redelivery sin segundo efecto |
| reconcile-before-retry | reconciliador específico | éxito, ausencia, contradicción/UNRESOLVED, replay |
| fencing | Tool/reconciler/compensator | stale owner/token y mutación pre-efecto |
| compensación explícita | compensador específico | restauración, Approval separada, replay |

## Persistencia, contratos y límites transaccionales

`0003_phase_1_wp3_effects` añade documentos de solicitud/decisión y actor de
Approval, correlación owner/fencing/idempotencia en Tool/receipts/reconciliación,
evidencia mínima del contenido previo y `compensation_operations`. No modifica
schemas WP1 ni añade estados de dominio. Upgrade desde 0002 preserva filas;
downgrade vuelve exactamente a 0002.

No se pretende atomicidad entre PostgreSQL y filesystem. Transacción A crea el
intento; transacción B revalida y consume Approval antes del dispatch; el
adapter materializa; transacción C revalida fencing y persiste desenlace,
receipt, audit y outbox. Ambigüedad después del posible efecto se conserva como
UNKNOWN y requiere observación. El contenido sintético previo se guarda solo
para la compensación aprobada de esta fixture.

## Validación reproducible

```text
.venv/bin/pytest -q:             PASS, 127/127
.venv/bin/ruff check .:          PASS
.venv/bin/mypy:                  PASS, 25 archivos
git diff --check:                PASS
migration_verify_gate0.sh:      PASS, 0001 / 8 tablas
migration_verify_wp1.sh:        PASS, vacío/upgrade/downgrade 0002
migration_verify_wp3.sh:        PASS, vacío/upgrade 0003, downgrade 0002
preservación fila histórica:     PASS
backup checksum + restore:       PASS
NO_AUTOMATIC_REPLAY:             PASS
```

El restore conservó revisión 0003 y las filas presentes; el snapshot permaneció
idéntico tras espera. La búsqueda no encontró secretos nuevos; el único password
visible pertenece al DSN local de test ya existente.

## Archivos WP3

- Migración: `migrations/versions/0003_phase_1_wp3_effects.py`.
- Servicios: `approval_service.py`, `tool_effect_service.py`,
  `fixture_atomic_write.py`, `reconciliation_service.py`,
  `compensation_service.py`.
- CLI/config: `src/jarvis_pd/cli/main.py`, `pyproject.toml`.
- Tests: `test_wp3_effects.py`, `test_wp3_sandbox.py` y dos casos añadidos a la
  matriz Policy WP2.
- Verificación: `scripts/migration_verify_wp3.sh`; WP1 se fijó explícitamente a
  0002 para que su gate no adopte accidentalmente el nuevo head.
- Gobierno: `README.md` y este informe.

## Riesgos y desviaciones

- Una muerte de proceso fuera de los puntos cubiertos puede dejar un intento
  `IN_PROGRESS`; reanudación/takeover general pertenece a WP4 y no se anticipó.
- El sandbox es una fixture filesystem acotada, no aislamiento genérico de
  procesos. La ausencia de red deriva de que el adapter no posee superficie de
  red; no se creó executor de comandos.
- Release productivo del latch sigue deliberadamente sin implementar.

## Resultado y parada

`F1-WP3: READY_FOR_SECOND_REVIEW`

No se declara F1-WP3 PASS ni Gate 1 PASS. No se inició ni preparó F1-WP4.

## Corrección posterior a auditoría externa — segunda revisión

### Veredicto anterior

`F1-WP3: FAIL`. Se recibieron exclusivamente cuatro hallazgos: (1) errores
post-`os.replace` clasificados como fallo conocido; (2) compensación ambigua sin
bloqueo/reconciliación canónicos; (3) `expected_result_hash` aprobado sin
verificación; y (4) expiración de Approval revertida por rollback.

### Causa raíz y corrección aplicada

1. `FixtureAtomicWrite` conserva una fase tipada de preparación y un punto de
   no retorno explícito en `os.replace`; cualquier excepción desde el dispatch,
   `fsync` de directorio o lectura/verificación posterior produce
   `AmbiguousEffectError`, que `ToolEffectService` registra como
   `OUTCOME_UNKNOWN`, consume Approval, mueve el Run a
   `RECONCILIATION_REQUIRED` y prohíbe replay. Solo `ConfirmedNoEffectError`
   previo al replace produce `FAILED`.
2. `FixtureCompensationService` persiste la operación completa y, ante
   ambigüedad, registra `OUTCOME_UNKNOWN` y bloquea el Run. Su método
   observacional `reconcile` usa exclusivamente `fixture.atomic_write.observe`,
   lease/owner/fencing y advisory lock; puede concluir restaurada, no ocurrida o
   `UNRESOLVED`, es idempotente y nunca vuelve a ejecutar la compensación.
3. Antes de consumir la Approval se compara `expected_result_hash` con el hash
   positivo del estado previo; después de restaurar se compara de nuevo con el
   hash observado y solo entonces se permite `SUCCEEDED`. La evidencia y el hash
   quedan correlacionados con operation/request/original ToolCall.
4. `ApprovalService.decide` materializa, audita y publica `EXPIRED` dentro de
   una UoW que confirma antes de lanzar el error estructurado; llamadas repetidas
   son idempotentes y la Approval no puede consumirse.

### Punto de no retorno y clasificación

`os.replace` es el punto de no retorno. Preparación, apertura, creación y
sincronización del temporal fallidas antes de invocarlo producen evidencia
positiva de no efecto (`ConfirmedNoEffectError`/`FAILED`). Una excepción del
propio dispatch de `replace`, o posterior en `fsync`, lectura, verificación o
respuesta, produce `AmbiguousEffectError`/`OUTCOME_UNKNOWN`. La clasificación
usa la fase tipada, nunca texto de la excepción. La Approval ya consumida no se
reutiliza y la misma idempotency key devuelve el receipt, sin redispatch.

### Flujo canónico de compensación ambigua

La operación conserva `run_id`, Approval independiente, owner/fencing,
contenido previo, hash esperado y estado del Run anterior al bloqueo. Una
ambigüedad materializa `OUTCOME_UNKNOWN`, audita/publica y mueve el Run a
`RECONCILIATION_REQUIRED`. El reconciliador específico solo observa: confirma
restauración si el hash coincide con `expected_result_hash`, confirma no efecto
si coincide con el resultado original, o conserva `UNRESOLVED`. Usa lock
transaccional, lease/owner/fencing e idempotencia; nunca llama a `restore` ni
reescribe el EffectReceipt original. Al concluir restaura el estado previo del
Run, no lo sustituye por el estado de la compensación.

### Matriz de cierre de auditoría

| Hallazgo | Implementación | Evidencia de test |
|---|---|---|
| H1, error post-efecto | errores tipados y fase explícita en `fixture_atomic_write.py`; clasificación en `tool_effect_service.py` | fallos inyectados antes de replace, después de replace/fsync/lectura; receipt UNKNOWN, Run bloqueado, replay sin dispatch y reconciliación |
| H2, compensación ambigua | persistencia ampliada y reconciliador observacional en `compensation_service.py`; tablas específicas en 0003 | compensación post-replace ambigua, bloqueo, receipt original preservado, reconciliación e idempotencia |
| H3, hash no validado | comparación previa contra estado histórico y posterior contra resultado observado | hash correcto restaura; hash incorrecto no consume Approval ni despacha; contrato exige hash |
| H4, EXPIRED revertido | commit de expiración/audit/outbox antes del error estructurado en `approval_service.py` | PostgreSQL conserva EXPIRED, una sola auditoría/outbox y repetición idempotente |

Archivos de cierre: `fixture_atomic_write.py`, `tool_effect_service.py`,
`compensation_service.py`, `approval_service.py`, migración 0003, fixture de
contrato, tests WP3/sandbox y este informe. No se añadieron Tools, workers, FSM,
Commands, Events ni capacidades WP4.

### Evidencia final de cierre y preservación

- Suite completa PostgreSQL: `127/127 PASS`.
- Ruff: `PASS`; mypy: `PASS` en 25 fuentes; `git diff --check`: `PASS`.
- Gate 0: `PASS`, revisión 0001 y ocho tablas.
- WP1: vacío/upgrade/downgrade `PASS`, tablas Gate 0 y fila histórica preservadas.
- WP2: todos sus contratos, SelectionService, Router y NodeRuntimeService pasan
  dentro de la suite completa.
- WP3: base vacía y upgrade desde 0002 a 0003 `PASS`; downgrade a 0002 `PASS`;
  fila histórica preservada.
- Backup/restore aislado: checksum `PASS`; restore en revisión 0003 con filas de
  Goal, Approval y EffectReceipt preservadas.
- Snapshot estable y `NO_AUTOMATIC_REPLAY`: `PASS`; redelivery retorna receipt y
  los contadores de dispatch permanecen en uno.
- Inspección de alcance: no existen nuevas capacidades WP4+, Tools, workers,
  recovery genérico, LangGraph/OpenClaw real, modelos ni token budgets.
- Empaquetado excluye `.git`, `.venv`, caches, bytecode, cobertura, `.env`,
  secretos, dumps, checksums auxiliares y artefactos locales.

Estado de parada: `F1-WP3: READY_FOR_SECOND_REVIEW`. Gate 1 permanece
`NOT_EVALUATED`; F1-WP4+ permanece `NOT_AUTHORIZED`; no se hizo commit.

## Segunda corrección tras auditoría externa V2 — tercera revisión

### Veredicto recibido y alcance

Veredicto anterior: `F1-WP3: FAIL`. La auditoría V2 encontró dos defectos
adicionales: (V2-H1) una pérdida de autoridad o fallo PostgreSQL después del
posible efecto podía dejar una operación `IN_PROGRESS` sin receipt ni ruta de
reconciliación; (V2-H2) los replays de reconciliación devolvían el resultado
almacenado antes de revalidar lease, owner y fencing. La corrección se limita a
`fixture.atomic_write`, su compensación y sus reconciliadores observacionales.
No se añadieron roles, Tools, FSM, Commands, Events, workers ni recuperación
general.

### Causa raíz, autoridad normativa y corrección

La causa de V2-H1 era el orden transaccional: el estado reconciliable se
materializaba después del filesystem, precisamente cuando el ejecutor podía
haber perdido autoridad. La corrección usa únicamente la representación WP3 ya
normativa (`OUTCOME_UNKNOWN`, `RECONCILIATION_REQUIRED`, Approval single-use y
reconciliación específica). Mientras lease/owner/fencing aún son válidos y
antes del dispatch, una UoW consume la Approval y arma una barrera durable:
operación `OUTCOME_UNKNOWN`, contenidos estrictamente necesarios para observar
y Run `RECONCILIATION_REQUIRED`. Esta barrera no es un EffectReceipt, no afirma
que el efecto ocurrió y no fabrica evidencia. Si el proceso cae, PostgreSQL
hace rollback después del efecto o la autoridad cambia, el owner obsoleto no
escribe conclusión alguna: la barrera ya fue creada por la autoridad válida y
solo una observación posterior autorizada puede concluirla. Si el resultado se
persiste normalmente, receipt, estado, auditoría y outbox comparten una única
UoW y el Run sale del bloqueo; si esa UoW falla, se conserva la barrera previa.
No existe atomicidad distribuida afirmada entre PostgreSQL y filesystem.

La compensación usa la misma barrera previa. Su Approval queda consumida, la
operación queda reconciliable, el Run bloqueado y el efecto/EffectReceipt
originales permanecen intactos. No hay segundo `restore`, redispatch automático
ni reutilización de Approval.

La causa de V2-H2 era el fast path anterior a la carga canónica. Ambos
reconciliadores ahora bloquean y cargan primero ToolCall/compensación, Run y
lease; validan lease activa, owner exacto y fencing contra lease y Run; después
consultan el replay. Owner y fencing almacenados forman parte material del
request idempotente: una autoridad obsoleta recibe `LEASE_REQUIRED` o
`FENCING_REJECTED`, y una key existente presentada por una autoridad vigente
distinta recibe `IDEMPOTENCY_CONFLICT`. Un replay idéntico y autorizado devuelve
el documento previo sin observar filesystem, ejecutar Tool/compensación ni
duplicar audit/outbox.

### Archivos y líneas de la corrección

- `tool_effect_service.py`: barrera previa al dispatch y cierre canónico desde
  UNKNOWN (aprox. líneas 228-277 y 389-455).
- `reconciliation_service.py`: autoridad canónica antes del replay y conflicto
  por autoridad material (aprox. líneas 33-81).
- `compensation_service.py`: barrera previa a restore, cierre autorizado y
  replay protegido (aprox. líneas 40-243 y 245-385).
- `test_wp3_effects.py`: matrices adversariales post-replace/post-fsync,
  persistencia y replay (aprox. líneas 850-1280).

### Matriz hallazgo → implementación → prueba

| Hallazgo | Implementación | Prueba PostgreSQL adversarial |
|---|---|---|
| V2-H1, lease/owner/fencing post-efecto | barrera UNKNOWN + Run bloqueado antes de filesystem | 3 pérdidas de autoridad × after_replace/after_directory_fsync; cero receipts ficticios; reconciliación por autoridad vigente |
| V2-H1, rollback receipt/audit/outbox | UoW de conclusión separada; rollback conserva barrera | fallos inyectados al insertar receipt, audit y outbox; Approval consumida, Run bloqueado, observación posterior |
| V2-H1, compensación | operación UNKNOWN y Run bloqueado antes de restore | 3 pérdidas de autoridad × after_replace/after_directory_fsync; EffectReceipt original preservado; sin redispatch |
| V2-H2, efecto original | carga y autorización antes del fast path | replay idéntico, stale fencing rechazado y nueva autoridad con misma key en conflicto |
| V2-H2, compensación | misma validación y binding owner/fencing | replay idéntico sin nuevo restore/audit/outbox y matrices con autoridad vigente posterior |

Las matrices demuestran que ninguna interrupción post-efecto termina como
`IN_PROGRESS + Run ejecutable + sin reconciliación`: el estado previo al
filesystem ya es `OUTCOME_UNKNOWN + RECONCILIATION_REQUIRED`. Una observación
que falla o produce evidencia contradictoria revierte su UoW o conserva
`UNRESOLVED`; nunca induce redispatch.

### Preservación de las cuatro correcciones anteriores

Se mantienen el punto de no retorno en `os.replace`, las excepciones tipadas y
la clasificación pre/post efecto; la compensación ambigua observacional; la
validación previa y posterior de `expected_result_hash`; y la expiración durable
e idempotente de Approval. El EffectReceipt original no se sobrescribe y la
compensación no oculta el efecto. `NO_AUTOMATIC_REPLAY` permanece inalterado.

### Validación completa V3 — 2026-08-12

- Suite completa con PostgreSQL real: `143 passed`.
- Tests WP3 ampliados: `32 passed`.
- Ruff: `PASS`; mypy: `PASS`, 25 fuentes; `git diff --check`: `PASS`.
- Gate 0: `PASS`, revisión 0001 y ocho tablas.
- WP1: base vacía/upgrade/downgrade `PASS`; ocho tablas G0 y fila histórica
  preservadas.
- WP2: contratos, `SelectionService`, Router y `NodeRuntimeService` preservados
  dentro de la suite completa.
- WP3: base vacía y upgrade 0002→0003 `PASS`; downgrade 0003→0002 `PASS`; fila
  histórica preservada.
- Backup/restore: checksum `PASS`; snapshot restaurado en 0003 idéntico y estable
  tras espera; salida `NO_AUTOMATIC_REPLAY`.
- Inspección de alcance: sin WP4+, nuevas Tools/roles/FSM/Commands/Events,
  workers, recovery/takeover general, LangGraph/OpenClaw real, modelos ni
  secretos nuevos.

Riesgo residual deliberado: la clasificación terminal depende de observación
posterior autorizada y puede permanecer `UNRESOLVED` ante evidencia
contradictoria u OSError. Esto es bloqueo seguro WP3, no recovery automático.

Estado de parada: `F1-WP3: READY_FOR_THIRD_REVIEW`. Gate 1 permanece
`NOT_EVALUATED`; F1-WP4+ permanece `NOT_AUTHORIZED`; no se hizo commit.

### Revalidación independiente de la entrega V2 — 2026-08-11

La autorización de corrección fue tratada nuevamente como instrucción activa y
la implementación existente se contrastó con los cuatro hallazgos, sus pruebas
adversariales y los límites WP3. La primera ejecución contra una base de pruebas
persistente produjo `125 passed, 2 failed`: esa base ya declaraba revisión
`0003_phase_1_wp3_effects`, pero había sido creada antes de completarse el
contenido todavía no comprometido de la propia migración 0003 y, por tanto, no
contenía las columnas finales de `compensation_operations`. No fue un fallo de
la migración desde una revisión soportada; fue estado local obsoleto y se
descartó como evidencia de entrega.

Se recreó exclusivamente la base de pruebas y se aplicó la cadena canónica desde
vacío. La suite completa PostgreSQL terminó con `127 passed`. Las verificaciones
aisladas confirmaron además: Gate 0 en 0001 con ocho tablas; WP1 desde vacío,
upgrade y downgrade con preservación de filas; WP3 desde vacío, upgrade
0002→0003 y downgrade 0003→0002 con preservación histórica; Ruff `PASS`; mypy
`PASS` en 25 fuentes; `git diff --check` `PASS`; backup/restore con checksum
`PASS`; snapshot restaurado estable; y `NO_AUTOMATIC_REPLAY` `PASS`.

La revisión completa del diff no identificó otro defecto de implementación
dentro del alcance autorizado. No se añadieron Tools, FSM, Commands, Events,
workers ni capacidades WP4+. HEAD continúa protegido en
`64c41c2c76804cb6b567f015f1724a1a1971deba` y no se hizo commit.

La sección posterior "Segunda corrección tras auditoría externa V2 — tercera
revisión" prevalece sobre esta evidencia histórica de V2. Estado vigente de
parada: `F1-WP3: READY_FOR_THIRD_REVIEW`; Gate 1 `NOT_EVALUATED`; F1-WP4+
`NOT_AUTHORIZED`.

## Correcciones posteriores a la tercera auditoría externa

### Veredicto y hallazgos recibidos

La tercera auditoría concluyó `F1-WP3: FAIL` y exigió tres correcciones
exclusivas: (V3-H1) carrera entre la barrera durable y el dispatch de la Tool;
(V3-H2) carrera equivalente entre barrera y restore de compensación; y (V3-H3)
persistencia de receipt/audit/outbox aunque el CAS de la transición canónica no
afectase ninguna fila.

### Coordinación exacta entre dispatch y reconciliación

`effect_coordination.py` introduce exclusivamente una exclusión interna WP3
por operación mediante un advisory lock PostgreSQL **de sesión**. Tool y
reconciliador usan la misma clave `wp3:tool:<tool_call_id>`; compensador y su
reconciliador usan `wp3:compensation:<compensation_id>`.

El ejecutor adquiere el lock antes de la UoW que consume Approval y confirma la
barrera `OUTCOME_UNKNOWN + RECONCILIATION_REQUIRED`. La transacción de
adquisición se confirma antes de filesystem y la UoW de barrera termina antes
de `adapter.execute()`/`restore()`: no queda una transacción PostgreSQL abierta
durante I/O. El lock de sesión permanece hasta que concluye el intento. Una
caída cierra la conexión y PostgreSQL lo libera, dejando la barrera durable
disponible para observación.

El reconciliador debe adquirir exactamente el mismo lock antes de cargar u
observar. Por tanto, durante la ventana post-barrera permanece bloqueado. Cuando
lo obtiene, el dispatch/restore anterior ya terminó o el proceso perdió su
sesión; el ejecutor original no puede efectuar posteriormente el efecto bajo
ese lock. Esto impide tanto `NO_EFFECT/CONFIRMED_FAILED → os.replace` como
`COMPENSATION_NOT_OCCURRED → restore`. No existe replay automático, worker,
takeover ni recovery general.

Implementación: `effect_coordination.py` líneas 7-51;
`tool_effect_service.py` líneas 228-359;
`reconciliation_service.py` líneas 25-51;
`compensation_service.py` líneas 35-279 y 281-308.

### CAS verificable de todas las conclusiones

Cada transición condicional revisada usa `UPDATE ... WHERE state=...
RETURNING id` y exige una fila. Un resultado vacío lanza
`PRECONDITION_FAILED`; la UoW revierte cualquier cambio anterior y no inserta
receipt/reconciliación, no publica outbox, no audita y no retorna éxito.

- Barrera Tool: `CREATED → OUTCOME_UNKNOWN` y Run
  `RUNNING → RECONCILIATION_REQUIRED`, ambos verificados.
- Conclusión Tool normal, FAILED u UNKNOWN: CAS desde `OUTCOME_UNKNOWN`; para
  terminales, Run `RECONCILIATION_REQUIRED → RUNNING`; receipt, audit y outbox
  se escriben después de ambos CAS dentro de la misma UoW
  (`tool_effect_service.py`, líneas 412-495).
- Reconciliación Tool terminal: ToolCall y Run usan CAS verificado antes de
  confirmar reconciliación/audit/outbox; cualquier fallo revierte la UoW
  (`reconciliation_service.py`, líneas 158-188).
- Barrera de compensación: inserción única y CAS verificado del Run.
- Conclusión de compensación: CAS del Run y de CompensationOperation antes de
  receipt embebido/audit/outbox (`compensation_service.py`, líneas 208-279).
- Reconciliación de compensación: CAS de CompensationOperation y Run antes de
  audit/outbox (`compensation_service.py`, líneas 389-454).

La exclusión de sesión impide ganadores concurrentes normales; los CAS siguen
siendo la defensa transaccional obligatoria ante cambios externos o estado ya
concluido. No se afirma atomicidad entre PostgreSQL y filesystem.

### Pruebas concurrentes y adversariales nuevas

`test_wp3_effects.py` añade cuatro pruebas PostgreSQL reales (aprox. líneas
1277-1645):

1. pausa determinista del ejecutor después del commit de barrera y antes de
   `adapter.execute`; el reconciliador concurrente queda bloqueado, no observa
   ni concluye, luego existe un solo replace/receipt y cero reconciliaciones;
2. pausa equivalente antes de `restore`; el reconciliador no puede declarar
   `COMPENSATION_NOT_OCCURRED`, existe un solo restore, el EffectReceipt
   original queda intacto y no hay audit/outbox duplicados;
3. conclusión Tool ganadora inyectada antes de `_persist_receipt`; el CAS vacío
   rechaza el éxito, conserva FAILED y deja cero EffectReceipts, audit y outbox
   de resultado;
4. CAS perdido equivalente en compensación; conserva el ganador y no publica
   receipt/audit/outbox falsos.

Las pruebas existentes siguen cubriendo errores tipados pre/post replace,
UNKNOWN post-efecto, pérdida de lease/owner/fencing, fallos de receipt/audit/
outbox, replay autorizado, stale fencing, idempotency conflict, compensación
ambigua, `expected_result_hash` y `Approval.EXPIRED` durable.

### Validación V4 — 2026-08-17

- Suite completa con PostgreSQL real: `147 passed`.
- Suite WP3 aislada: `36 passed`.
- Ruff: `PASS`; mypy: `PASS` en 26 fuentes; `git diff --check`: `PASS`.
- Gate 0: revisión 0001 y ocho tablas, `PASS`.
- WP1: vacío/upgrade/downgrade, tablas G0 y fila histórica, `PASS`.
- WP2: contratos, SelectionService, Router y NodeRuntimeService preservados en
  la suite completa.
- WP3: base vacía, upgrade 0002→0003, downgrade 0003→0002 y fila histórica,
  `PASS`.
- Backup/restore: checksum, revisión 0003, snapshot idéntico/estable y
  `NO_AUTOMATIC_REPLAY`, `PASS`.
- Inspección de alcance y secretos: sin WP4+, nuevas Tools, FSM públicas,
  Commands, Events, roles, workers, recovery/takeover, LangGraph/OpenClaw real,
  modelos, secretos ni artefactos locales.

### Preservación

Se preservan las seis correcciones anteriores: clasificación tipada alrededor
de `os.replace`; post-efecto como UNKNOWN; compensación ambigua observacional;
`expected_result_hash`; `Approval.EXPIRED` durable; y revalidación de
lease/owner/fencing antes de replay. También permanecen Policy default-deny,
Approval single-use, sandbox, EffectReceipt correlacionado,
RECONCILIATION_REQUIRED, idempotencia conflictiva y NO_AUTOMATIC_REPLAY.

Estado de parada vigente: `F1-WP3: READY_FOR_FOURTH_REVIEW`. Gate 1 permanece
`NOT_EVALUATED`; F1-WP4+ permanece `NOT_AUTHORIZED`; no se hizo commit.

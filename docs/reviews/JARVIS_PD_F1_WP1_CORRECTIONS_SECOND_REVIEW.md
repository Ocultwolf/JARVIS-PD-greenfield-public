# JARVIS P.D. — Correcciones F1-WP1 para segunda auditoría

- Fecha: 2026-08-07
- Veredicto externo de entrada: `F1-WP1: PASS_WITH_CHANGES`
- Estado de salida: `F1-WP1: CORRECTED_PENDING_SECOND_REVIEW`
- Gate 0: `PASS`, cerrado y preservado
- Baseline ejecutable G0: `dd1028ca80b8a45adc69432bf4b89d82040e5f1b`
- Certificación G0 / HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- F1-WP0: `PASS`
- Gate 1: `NOT_EVALUATED`
- F1-WP2: `NOT_STARTED_NOT_AUTHORIZED`
- Commit creado: ninguno

## Contraste normativo previo

Se releyeron ADR-0002/0003/0004, `gate-1-normative-cut.md`, el manifiesto
Gate 1, su trazabilidad, las resoluciones WP0 y el estado del Apéndice O. No se
detectó una contradicción de autoridad:

- ADR-0004, O-QST-009 y G1-11 exigen que un receipt `CONFIRMED` corresponda a
  la invocation, manifest exacto, sesión, transporte y hash.
- O-QST-005 solo autoriza dependencias satisfechas; ninguna fuente ratificada
  define clases `HARD`/`SOFT`.
- ADR-0003, O-QST-022 y G1-13 exigen representación versionada de un latch de
  host persistente, independiente y con guards pre-work/pre-effect, sin release
  automático por health. La liberación productiva sigue diferida.

## Hallazgo 1 — correlación incompleta de InjectionReceipt

### Solución

`ContextManifest` declara ahora `session_id` y `transport`. El receipt declara
la identidad de invocation/manifest, sesión y transporte obtenidos del manifest
y, para `CONFIRMED`, el modelo exige igualdad en las cinco correlaciones:
invocation, `context_manifest_id`, sesión, transporte y hash
manifest/injected.

PostgreSQL materializa la misma garantía con:

- clave única de correlación exacta en `context_manifests`;
- FK compuesto `fk_injection_exact_manifest` desde `injection_receipts`;
- `ck_injection_confirmed` para igualdad de campos, hashes y presencia del
  método de verificación.

Se regeneraron los JSON Schemas de `ContextManifest` e `InjectionReceipt`.
Fixtures negativos explícitos cubren mismatch de sesión, transporte, hash,
invocation y manifest exacto.

## Hallazgo 2 — semántica HARD/SOFT no ratificada

### Solución

`task_dependencies` conserva únicamente la relación binaria
`task_id -> depends_on_task_id`, fecha y prohibición de autorreferencia. Se
eliminaron `dependency_kind`, su default y su constraint. No se añadió ninguna
clasificación alternativa. Una prueba inspecciona el esquema PostgreSQL y
exige que la columna no exista; una búsqueda completa no encuentra `HARD` ni
`SOFT` en código, migraciones, schemas o fixtures WP1.

## Hallazgo 3 — configuración versionada del latch incompleta

### Solución

Se añadió `jarvis.gate1-latch-config@1.0.0`, un contrato inerte con valores
cerrados para:

- persistencia en latch de host;
- PostgreSQL como proyección/auditoría, no sustituto del latch;
- independencia de LLM, LangGraph y OpenClaw;
- guards antes de adquirir trabajo y antes del efecto;
- prohibición de release automático al recuperar health;
- release productivo `DEFERRED_NOT_IMPLEMENTED`.

La tabla `gate1_latch_configs` persiste únicamente esa configuración versionada
y aplica `ck_gate1_latch_inert_config`. No se implementaron activación,
liberación, endpoint, Command, Event, servicio, guard ejecutable ni Kill Switch
operativo. Modelo y token budget continúan `null` y diferidos hasta WP6.

## Archivos de corrección

- `README.md`
- `src/jarvis_pd/contracts/gate1.py`
- `src/jarvis_pd/contracts/registry.py`
- `migrations/versions/0002_phase_1_wp1_contracts.py`
- `scripts/migration_verify_wp1.sh`
- `tests/contracts/test_gate1_contracts.py`
- `tests/integration/test_wp1_schema.py`
- `tests/fixtures/gate1/valid/context-manifest.json`
- `tests/fixtures/gate1/valid/injection-receipt.json`
- `tests/fixtures/gate1/valid/gate1-latch-config.json`
- `tests/fixtures/gate1/invalid/injection-hash-mismatch.json`
- `tests/fixtures/gate1/invalid/injection-session-mismatch.json`
- `tests/fixtures/gate1/invalid/injection-transport-mismatch.json`
- `tests/fixtures/gate1/invalid/injection-invocation-mismatch.json`
- `tests/fixtures/gate1/invalid/injection-manifest-mismatch.json`
- `tests/fixtures/gate1/invalid/latch-health-auto-release.json`
- `docs/contracts/jarvis.context-manifest-1.0.0.schema.json`
- `docs/contracts/jarvis.injection-receipt-1.0.0.schema.json`
- `docs/contracts/jarvis.gate1-latch-config-1.0.0.schema.json`
- este informe.

## Nuevas pruebas y fixtures

- Correlaciones negativas nombradas para sesión, transporte, hash, invocation y
  manifest exacto.
- Validación de que el latch es inerte y mantiene release diferido.
- Inspección de ausencia de `dependency_kind`.
- Inspección del FK compuesto receipt/manifest.
- Rechazo PostgreSQL de configuración que permita auto-release por health.
- Verificador de migración ampliado a ocho tablas, upgrade vacío/G0 y downgrade.

Inventario final: 15 contratos registrados en total (14 de WP1), 14 fixtures
válidos y 14 inválidos.

## Resultados reproducibles

```text
uv run pytest -q
67 passed

uv run ruff check .
All checks passed!

uv run mypy
Success: no issues found in 15 source files

scripts/migration_verify_gate0.sh
migration=0001_phase_0_integrity tables=8

scripts/migration_verify_wp1.sh
empty=0002_phase_1_wp1_contracts
upgrade=0002_phase_1_wp1_contracts
tables=8
g0_rows_preserved=1
downgrade=0001_phase_0_integrity
wp1_tables_after_downgrade=0
g0_tables_after_downgrade=8

backup/restore aislado
dump checksum: OK
NO_AUTOMATIC_REPLAY
migration restaurada: 0002_phase_1_wp1_contracts

git diff --check
PASS
```

La suite regenera todos los schemas desde los modelos y compara los documentos
publicados. Los fixtures pasan por el Contract Registry. Las pruebas de
integración inspeccionan constraints/FKs PostgreSQL. Así se cubre la cadena
models ↔ registry ↔ JSON Schemas ↔ fixtures ↔ PostgreSQL.

## Preservación de Gate 0

- El verificador G0 conserva revisión `0001` y sus ocho tablas protegidas.
- Upgrade desde G0 preserva la Task histórica y asigna únicamente el default
  compatible `priority=0`.
- Downgrade de WP1 elimina sus ocho tablas y retorna a `0001` con las ocho
  tablas G0 presentes.
- Backup/restore mantiene el snapshot, checksum y `NO_AUTOMATIC_REPLAY`.
- No se modificaron contratos, servicios ni invariantes funcionales de G0.

## Evidencia de no invasión de WP2+

No existen implementaciones nuevas de Router, `SelectionService`,
`AgentRuntime`, `PolicyEvaluator`, adapters/runtime de Tools, workers,
LangGraph ni integración OpenClaw. El latch añadido es datos/configuración
versionada; no posee métodos de activar/liberar ni comportamiento operativo.
No se añadieron Commands, Events, estados, Tools, permisos o autoridad.

## Estado de O-QST relevantes

- O-QST-005: `DECIDED / CLOSED`; se eliminó la clasificación no autorizada.
- O-QST-006: `DECIDED_CONDITION / CLOSED_FOR_WP1`; modelo/tokens siguen
  diferidos hasta WP6.
- O-QST-009: `PARTIALLY_DECIDED / CLOSED_FOR_G1_MINIMUM`; se corrigió la
  correlación mínima, sin contexto avanzado.
- O-QST-022: `PARTIALLY_DECIDED / CLOSED_FOR_G1_ACTIVATION`; release productivo
  permanece abierto/diferido.
- O-QST-023: `ANALYSING / NOT_CLOSED_BY_G1_18`.
- O-QST-029: `BLOCKED_EXTERNAL / CLOSED_FOR_SYNTHETIC_G1`.

## Riesgos y desviaciones restantes

- No hay desviación normativa conocida en los tres hallazgos corregidos.
- La migración `0002` continúa sin commit y aún no es una baseline certificada.
- El PASS de F1-WP1 depende exclusivamente de la segunda auditoría externa.
- Gate 1 no fue evaluado y WP2 no fue iniciado ni autorizado.

## Resultado

`F1-WP1: CORRECTED_PENDING_SECOND_REVIEW`

Se detiene el trabajo a la espera de revisión humana. No se declara F1-WP1
PASS, no se declara Gate 1 PASS y no se propone ni inicia F1-WP2.

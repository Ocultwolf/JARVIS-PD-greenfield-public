# JARVIS P.D. — F1-WP1 listo para revisión

- Fecha: 2026-08-07
- Estado: `READY_FOR_REVIEW`
- Gate 0: `PASS`, cerrado y preservado
- Gate 1: `NOT_EVALUATED`
- F1-WP2: `NOT_STARTED_NOT_AUTHORIZED`
- HEAD protegido: `64c41c2c76804cb6b567f015f1724a1a1971deba`
- Commit creado: ninguno

## Estado inicial y autoridad

El trabajo partió del HEAD de certificación G0, con los documentos F1-WP0
aceptados todavía sin commit. El HumanOwner confirmó F1-WP0 como `PASS` y
autorizó exclusivamente F1-WP1. No se reabrieron decisiones cerradas ni se
modificaron decisiones diferidas de Fase 2+.

## Alcance materializado

F1-WP1 contiene únicamente contratos inertes, JSON Schemas reproducibles,
fixtures contractuales, persistencia mínima e incremental y sus verificadores.
No contiene handlers, servicios de dominio, Router, `SelectionService`,
`AgentRuntime`, `PolicyEvaluator`, Tool adapters, fakes, LangGraph u OpenClaw.

### Contratos versionados

- `NodeInvocationRequest`, `AgentInvocation` y `AgentResult`.
- `PlannerProposal`, sin autoridad de ejecución o persistencia.
- `ContextManifest` sellado e `InjectionReceipt` correlacionado.
- `ToolCallRequest`, `EffectReceipt`, `ReconciliationResult` y
  `CompensationRequest`.
- `ApprovalRequest` y `ApprovalDecision`, ligados a `operation_hash`.
- `Gate1Limits`, con los límites A2 cerrados y modelo/tokens deliberadamente
  sin fijar hasta WP6.
- Contract Registry versionado con `SCHEMA_VALIDATION_FAILED` y
  `UNSUPPORTED_SCHEMA_VERSION`.

### Persistencia incremental

La revisión `0002_phase_1_wp1_contracts` añade:

- prioridad y dependencias mínimas de Task;
- campos de hash, consumo y revocación de Approval;
- `node_invocations`, `context_manifests` e `injection_receipts`;
- `tool_calls`, `effect_receipts` y `reconciliations`;
- constraints de allowlist, hashes, estados, idempotencia y semántica de
  resultados.

Los campos nuevos compatibles con filas históricas conservan upgrade desde
`0001`; el contrato WP1 exige los campos estrictos en entradas nuevas.

## Fuentes normativas aplicadas

- ADR-0002: límites deterministas, `OUTCOME_UNKNOWN`, reconciliación y
  compensación separada.
- ADR-0003: default deny, Approval exacta, sandbox y límites.
- ADR-0004: invocación/runtime, contexto sellado, receipt verificable y fallo
  contractual.
- `gate-1-normative-cut.md`: contratos mínimos, única Tool
  `fixture.atomic_write` y configuración A2.
- `gate-1-acceptance-v1.yaml` y su matriz de trazabilidad.
- Catálogos normativos E, F y H de entidades, estados y contratos.

## Criterios de aceptación WP1

1. **Contratos mínimos versionados: PASS.** Todos los contratos del corte WP0
   tienen modelo y schema `1.0.0`.
2. **Schemas reproducibles: PASS.** La suite regenera los 14 schemas registrados
   y compara semánticamente cada documento publicado.
3. **Fixtures positivos y negativos: PASS.** Trece fixtures válidos y nueve
   inválidos cubren versión, autoridad, límites, red, path, hash y semántica de
   resultados.
4. **Reason codes conocidos: PASS.** El registry distingue schema inválido de
   versión/contrato no soportado.
5. **Migración incremental: PASS.** Base vacía y upgrade desde `0001` alcanzan
   `0002`, crean siete tablas y preservan datos G0.
6. **Autoridad contenida: PASS.** Los modelos no exponen APIs de persistencia,
   ejecución, transición, routing o autorización.
7. **Frontera WP2+: PASS.** No se implementó comportamiento de servicios,
   runtime, Tool o coordinación.

## Validaciones finales

```text
uv run pytest -q:                    PASS, 52/52
uv run ruff check .:                PASS
uv run mypy:                        PASS, 15 archivos
scripts/migration_verify_gate0.sh:  PASS, revisión 0001 y 8 tablas
scripts/migration_verify_wp1.sh:    PASS, empty + upgrade, 7 tablas
backup/restore aislado:             PASS, checksum y NO_AUTOMATIC_REPLAY
git diff --check:                   PASS
```

El restore final conservó la revisión `0002_phase_1_wp1_contracts` y las
entidades G0 del snapshot sin reactivar efectos ni autoridad.

## Desviaciones y riesgos

- La sesión original se interrumpió antes de las verificaciones finales. Se
  recuperó el árbol sin sobrescribirlo y se repitió toda la validación.
- Se corrigió un test WP1 para que el savepoint haga rollback antes de capturar
  el `IntegrityError` esperado.
- Se añadió el bit ejecutable que faltaba a `migration_verify_wp1.sh`.
- El presupuesto de tokens y el modelo permanecen sin fijar, conforme al
  diferido explícito hasta WP6.
- Este resultado no satisface Gate 1, no autoriza WP2 y no constituye una
  baseline certificada.

## Resultado

`F1-WP1: READY_FOR_REVIEW`

La siguiente acción permitida es únicamente revisión humana/externa. F1-WP2
requiere una autorización nueva y separada.

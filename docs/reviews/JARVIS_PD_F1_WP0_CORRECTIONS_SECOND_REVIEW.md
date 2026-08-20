# JARVIS P.D. — F1-WP0 Correcciones para segunda revisión

- Fecha: 2026-08-06
- Resultado de primera revisión: `F1-WP0: PASS_WITH_CHANGES`
- Estado actual: `CORRECTED_PENDING_SECOND_REVIEW`
- Fase 1: `NOT_IMPLEMENTED`
- Gate 1: `NOT_EVALUATED`
- F1-WP1: `NOT_STARTED_NOT_AUTHORIZED`

Este informe entrega las correcciones normativas solicitadas. No declara F1-WP0
definitivamente cerrado y no constituye autorización para WP1.

## Estado protegido

- Gate 0: PASS y cerrado.
- Baseline ejecutable: `dd1028ca80b8a45adc69432bf4b89d82040e5f1b`.
- Certificación: `64c41c2c76804cb6b567f015f1724a1a1971deba`.
- PostgreSQL sigue siendo fuente canónica.
- LangGraph solo coordina.
- OpenClaw permanece detrás de `AgentRuntime`.
- El LLM propone; servicios deterministas validan y deciden.

## Archivos modificados por esta corrección

1. `README.md`: estado WP0 corregido y pendiente de segunda revisión.
2. `docs/architecture/phase-1-wp0-scope.md`: registra PASS_WITH_CHANGES,
   G1-SCOPE-002, G1-21 y la no resolución de O-QST-023.
3. `docs/decisions/phase-1-wp0-resolutions.md`: añade el Decision Record
   G1-SCOPE-002 y separa estado de decisiones/estado del paquete.
4. `docs/decisions/appendix-o-g1-status.md`: añade G1-SCOPE-002, preserva
   O-QST-029 y registra O-QST-023 como `ANALYSING`.
5. `docs/adr/ADR-0002-gate-1-execution-and-effects.md`: fija autoridad del
   Router y semántica de compensación.
6. `docs/adr/ADR-0004-gate-1-context-runtime-and-evidence.md`: fija el fallo
   controlado ante indisponibilidad OpenClaw.
7. `docs/catalogs/gate-1-normative-cut.md`: coordina routing y fallo OpenClaw
   sin crear Commands, Events, FSM o schemas.
8. `docs/gates/gate-1-acceptance-v1.yaml`: versión 1.1.0, requisito G1-21 y
   refuerzo semántico de G1-02/07/09/10/13/17.
9. `docs/gates/gate-1-traceability.md`: matriz 21/21 y regla explícita de
   completitud semántica.
10. `docs/decisions/phase-2-plus-deferred.md`: conserva O-QST-023 diferida.
11. Este informe de segunda revisión.

ADR-0003 no necesitó cambio: ya contenía operation hash, expiración, revocación,
consumo transaccional, sandbox sin red/allowlist/límites y latch independiente.
Sus cláusulas se trasladaron completas al manifiesto y a la matriz.

## Diff normativo exacto

### G1-SCOPE-002

Resolución final para Gate 1:

> User Node recomienda y no materializa estado. Planner produce propuestas
> estructuradas y no concede permisos ni ejecuta Tools. Router decide
> determinísticamente usando estado y guards previamente validados. El texto
> generado por un LLM nunca selecciona directamente una ruta de ejecución.

Cadena de autoridad: agente/LLM propone → contratos y guards deterministas
validan → Router decide → servicio dueño materializa o rechaza.

### G1-21

Definición exacta:

> La caída o indisponibilidad de OpenClaw bloquea o degrada controladamente el
> Run sin corromper PostgreSQL, fallback silencioso ni confirmación de un
> resultado inexistente.

PostgreSQL continúa siendo fuente de verdad. El requisito no autoriza fallback
multi-modelo ni resuelve O-QST-025.

### Criterios reforzados

- **G1-07:** cancelación conserva efectos y detiene trabajo futuro; compensación
  separada, idempotente y auditable.
- **G1-09:** Approval exacta ligada a `operation_hash`, expirable, revocable,
  resistente a replay/reutilización y consumida transaccionalmente como
  single-use o uso limitado.
- **G1-10:** sandbox sin red, allowlist y límites explícitos; rechazo de paths
  absolutos, `..` y escape mediante symlink.
- **G1-13:** latch persistente independiente de LLM, LangGraph y OpenClaw;
  guards pre-work/pre-effect; recuperar health no libera automáticamente.
- **G1-17:** fixtures exclusivamente sintéticos, sin secretos ni datos
  personales reales, contexto mínimo, sin chain-of-thought y evidencia
  redactada.

## Matriz actualizada

La matriz canónica completa está en `docs/gates/gate-1-traceability.md`. Las
filas corregidas son:

| Requisito | Test | Evidencia semántica | Artefacto |
|---|---|---|---|
| G1-02 | T-G1-ROUTING-AUTHORITY | separación User/Planner/Router y rechazo de rutas LLM | routing-authority-report.json |
| G1-07 | T-G1-CANCEL-COMPENSATION | fronteras, identidad separada, deduplicación y auditoría | cancellation-compensation-report.json |
| G1-09 | T-G1-APPROVAL-LIFECYCLE | hash, expiración, revocación, replay y consumo transaccional | approval-lifecycle-report.json |
| G1-10 | T-G1-SANDBOX-CONTAINMENT | red, allowlist, recursos, paths y symlinks | sandbox-containment-report.json |
| G1-13 | T-G1-KILL-SWITCH-INDEPENDENCE | independencia, restart, ambos guards y health sin release | kill-switch-independence-report.json |
| G1-17 | T-G1-PRIVACY-SYNTHETIC | sinteticidad, ausencia de datos/secretos/CoT, minimización y redacción | privacy-synthetic-report.json |
| G1-21 | T-G1-OPENCLAW-UNAVAILABLE | Run seguro, PostgreSQL íntegro, sin fallback ni resultado ficticio | openclaw-unavailable-report.json |

Cada fila exige entorno, versiones, estado PostgreSQL anterior/posterior,
estímulo, resultado, reason codes y checksum. La existencia nominal de un test o
artefacto no satisface el requisito.

## Estado de Apéndice O

- O-QST-005: `DECIDED / CLOSED` para la selección G1; G1-SCOPE-002 precisa la
  frontera sin ampliar autoridad.
- O-QST-009: `PARTIALLY_DECIDED / CLOSED_FOR_G1_MINIMUM`.
- O-QST-022: `PARTIALLY_DECIDED / CLOSED_FOR_G1_ACTIVATION`; release productivo
  continúa abierto.
- O-QST-023: `ANALYSING / NOT_CLOSED_BY_G1_18`; horizonte Gate 2.
- O-QST-025: no cambia; routing/fallback multi-modelo continúa diferido.
- O-QST-029: `BLOCKED_EXTERNAL / CLOSED_FOR_SYNTHETIC_G1`; datos reales siguen
  prohibidos.

## Alcance negativo confirmado

No se modificaron runtime, código funcional, migraciones, schemas ejecutables,
fixtures, implementación de Fase 1 ni invariantes de Gate 0. No se añadieron
Commands, Events, FSM, Tools, riesgos, servicios externos ni fallbacks.

## Validaciones ejecutadas

- Correspondencia manifiesto–matriz: PASS, 21/21 IDs únicos y ordenados.
- Comprobación semántica automatizada: PASS para G1-SCOPE-002, G1-07, G1-09,
  G1-10, G1-13, G1-17 y G1-21, incluidos tests y artefactos previstos.
- Regla de evidencia semántica: PASS; cada cláusula corregida tiene oráculo y
  artefacto, no solo una fila estructural.
- Enlaces locales: PASS, cero enlaces rotos.
- Alcance del diff: PASS, exclusivamente `README.md` y `docs/`.
- `git diff --check`: PASS.
- `uv run ruff check .`: PASS.
- `uv run mypy`: PASS, 14 archivos fuente.
- `uv run pytest`: PASS, 26/26 tests, incluidos 10 de integración PostgreSQL.
- `scripts/migration_verify_gate0.sh`: PASS, revisión
  `0001_phase_0_integrity`, 8 tablas.

El entorno no contiene un parser YAML dedicado; por ello el manifiesto se
validó mediante su estructura cerrada, IDs, correspondencia y contenido
semántico. No se añadió una dependencia para una corrección documental.

## Estado para la autoridad revisora

Las correcciones están listas para una segunda auditoría independiente. Gate 1
sigue `NOT_EVALUATED`; Fase 1 sigue `NOT_IMPLEMENTED`; F1-WP1 continúa sin
iniciar y sin autorizar. Solo la revisión humana puede cerrar definitivamente
F1-WP0 o autorizar un paquete posterior.

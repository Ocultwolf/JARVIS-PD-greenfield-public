# ADR-0002 — Ejecución determinista y efectos de Gate 1

- Estado: ACCEPTED
- Materialización: PLANIFICADO
- Fecha: 2026-08-06
- Autoridad: HumanOwner + Architecture Governance
- Fuentes: O-QST-005/006/007/008/018/020, G1-DOC-003, G1-SCOPE-002

## Decisión

Gate 1 usa selección reproducible de Tasks mediante `SelectionService`; los
agentes solo recomiendan. Se aplican los límites cuantitativos de
`phase-1-wp0-resolutions.md`. Solo existe una acción material por Run y una Tool
R1 de escritura atómica de fixture.

La frontera de routing queda cerrada para G1: User Node recomienda y nunca
materializa estado; Planner produce propuestas estructuradas y no concede
permisos ni ejecuta Tools; Router decide determinísticamente usando estado y
guards previamente validados. El texto generado por un LLM es dato de propuesta
y nunca selecciona directamente una ruta de ejecución.

Un dispatch ambiguo produce `OUTCOME_UNKNOWN`. La reconciliación específica
devuelve exactamente `NO_EFFECT`, `CONFIRMED_SUCCEEDED`, `CONFIRMED_FAILED` o
`UNRESOLVED`; solo `NO_EFFECT` permite considerar un nuevo intento. Cancelar no
borra efectos y compensar crea una operación separada, idempotente y auditable.

Leases y fencing permanecen internos, transaccionales y auditables. El cambio
canónico y la outbox comparten commit; un dispatcher local entrega con inbox
idempotente. No se introduce bus externo ni Events públicos sin consumidor.

## Consecuencias y validación

WP1 debe expresar estos límites en contratos; WP2 en servicios/fakes; WP3 en la
Tool y reconciliador; WP4 en worker/recovery. Las pruebas de interrupción deben
demostrar cero o un efecto exacto, nunca duplicado. LangGraph no participa en la
decisión canónica.

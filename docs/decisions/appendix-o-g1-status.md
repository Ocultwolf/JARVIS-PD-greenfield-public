# Apéndice O — actualización de estado para el corte Gate 1

- Versión del corte: `g1-wp0-v1.1-corrected`
- Fecha: 2026-08-06
- Autoridad: HumanOwner

Este registro complementa, no borra, el historial del Apéndice O editorial.

| Pregunta | Estado global tras WP0 | Estado para G1 | Resolución |
|---|---|---|---|
| O-QST-005 | DECIDED | CLOSED | ADR-0002 |
| O-QST-006 | DECIDED_CONDITION | CLOSED_FOR_WP1 | ADR-0002; tokens antes del E2E WP6 |
| O-QST-007 | PARTIALLY_DECIDED | CLOSED_FOR_G1_TOOL | ADR-0002; catálogo público general diferido |
| O-QST-008 | PARTIALLY_DECIDED | CLOSED_FOR_G1_TOOL | ADR-0002/0003 |
| O-QST-009 | PARTIALLY_DECIDED | CLOSED_FOR_G1_MINIMUM | ADR-0004; contexto avanzado diferido |
| O-QST-013 | DECIDED | CLOSED | ADR-0003 |
| O-QST-014 | DECIDED | CLOSED | ADR-0003 |
| O-QST-016 | PARTIALLY_DECIDED | CLOSED_FOR_G1_TOOL | ADR-0003; otros perfiles diferidos |
| O-QST-018 | DECIDED | CLOSED | ADR-0002 |
| O-QST-019 | DECIDED | CLOSED | ADR-0004 |
| O-QST-020 | DECIDED | CLOSED | ADR-0002 |
| O-QST-022 | PARTIALLY_DECIDED | CLOSED_FOR_G1_ACTIVATION | ADR-0003; release productivo abierto |
| O-QST-027 | PARTIALLY_DECIDED | CLOSED_FOR_G1 | ADR-0004; cada gate amplía evidencia |
| O-QST-028 | PARTIALLY_DECIDED | CLOSED_FOR_G1_R1 | ADR-0003; R3–R5 fuera de G1 |
| O-QST-029 | BLOCKED_EXTERNAL | CLOSED_FOR_SYNTHETIC_G1 | ADR-0004; datos reales prohibidos |
| O-QST-023 | ANALYSING | NOT_CLOSED_BY_G1_18 | G1-18 prueba preservación limitada; alcance global de restore sigue en Gate 2 |

No queda ningún `MVP_BLOCKER` abierto que impida definir contratos y fixtures de
WP1. Esta afirmación queda pendiente de segunda revisión y no autoriza WP1. Las
condiciones restantes vencen en WP6 o pertenecen a Fase 2+.

## Registros de alcance G1 afectados

- `G1-SCOPE-002`: `CLOSED_FOR_G1`. User Node recomienda, Planner propone y
  Router decide determinísticamente sobre estado/guards validados. Es compatible
  con O-QST-003 y O-QST-005 y no amplía sus resoluciones fuera de G1.
- `G1-RUNTIME-001`: permanece `CLOSED_FOR_G1`; G1-21 añade evidencia negativa
  de indisponibilidad sin decidir routing/fallback multi-modelo de O-QST-025.
- O-QST-029 permanece globalmente `BLOCKED_EXTERNAL`.
- O-QST-023 permanece globalmente `ANALYSING` y no queda resuelta por G1-18.

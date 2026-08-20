# F1-WP0 — Corte normativo previo a implementación

- Estado: PASS tras segunda auditoría externa
- Autoridad: HumanOwner
- Fecha: 2026-08-06
- Baseline protegida G0: `dd1028ca80b8a45adc69432bf4b89d82040e5f1b`
- Certificación G0: `64c41c2c76804cb6b567f015f1724a1a1971deba`

## Autoridad y alcance

El HumanOwner ratifica las veinte decisiones propuestas en el apartado 17.2 del
Informe de Preparación de Fase 1 / Gate 1. Este corte permite especificar Gate 1,
pero no declara implementada Fase 1, no evalúa Gate 1 y no autoriza F1-WP1.

La primera revisión externa clasificó F1-WP0 como `PASS_WITH_CHANGES`. Las
correcciones normativas fueron materializadas y aceptadas en la segunda
auditoría externa. El HumanOwner confirmó el cierre de WP0 y autorizó por
separado F1-WP1 el 2026-08-07.

F1-WP0 solo modifica documentación. PostgreSQL sigue siendo la fuente canónica;
LangGraph solo coordina; OpenClaw permanece detrás de `AgentRuntime`; `docker
exec` es un binding provisional; y el LLM solo propone.

## Resultado de la auditoría

No se encontró una contradicción que impida cerrar WP0. La Parte 20 asigna a
Fase 2 capacidades amplias de selección, Approval, cancelación y presupuestos,
mientras Gate 1 ya exige cortes mínimos de esas capacidades. Se aplica el corte
compatible definido por `G1-DOC-001..004`: G1 anticipa exclusivamente lo que su
propio gate necesita; las capacidades de producto y gobierno avanzadas siguen
diferidas.

Las siguientes resoluciones son deliberadamente parciales:

- O-QST-009 queda resuelta solo para la inyección mínima verificable de G1.
- O-QST-029 queda acotada a fixtures sintéticos de G1; la decisión legal y el
  tratamiento de datos reales permanecen bloqueados.
- La liberación productiva del Kill Switch no se define; en aceptación se
  descarta el entorno activado.
- La cancelación/reconciliación de G1 se limita a la única Tool fixture.
- El presupuesto de tokens queda pendiente del binding/modelo fijo de WP6 y
  debe cerrarse antes del E2E OpenClaw, no antes de WP1.
- G1-18 exige preservar G0 durante backup/restore, pero no cierra O-QST-023; el
  alcance global de restore continúa `ANALYSING` con horizonte Gate 2.

## Correcciones posteriores a la primera revisión

- `G1-SCOPE-002` cierra la frontera User Node/Planner/Router: los agentes
  producen recomendaciones o propuestas estructuradas; el Router decide
  determinísticamente sobre estado y guards validados; texto LLM nunca elige
  directamente una ruta.
- `G1-21` exige fallo controlado ante indisponibilidad de OpenClaw, sin estado
  canónico corrupto, fallback silencioso ni resultado inexistente confirmado.
- G1-07, G1-09, G1-10, G1-13 y G1-17 expresan ahora toda la semántica ratificada
  en el manifiesto y en su trazabilidad.

## Documentos normativos de este corte

- `docs/decisions/phase-1-wp0-resolutions.md`
- `docs/decisions/appendix-o-g1-status.md`
- `docs/adr/ADR-0002-gate-1-execution-and-effects.md`
- `docs/adr/ADR-0003-gate-1-authority-and-containment.md`
- `docs/adr/ADR-0004-gate-1-context-runtime-and-evidence.md`
- `docs/catalogs/gate-1-normative-cut.md`
- `docs/gates/gate-1-acceptance-v1.yaml`
- `docs/gates/gate-1-traceability.md`
- `docs/decisions/phase-2-plus-deferred.md`
- `docs/reviews/JARVIS_PD_F1_WP0_CORRECTIONS_SECOND_REVIEW.md`

# JARVIS P.D.

## Estado de gobierno

- Gate 0: PASS y cerrado.
- F1-WP0: `PASS` tras segunda auditoría externa; consulte
  `docs/architecture/phase-1-wp0-scope.md`.
- F1-WP1: `PASS` tras segunda auditoría externa.
- F1-WP2: `PASS` tras segunda auditoría externa.
- F1-WP3: `PASS`; Tool fixture R1 y reconciliación específica,
  sin worker ni WP4+.
- Fase 1: implementación parcial limitada a WP3.
- Gate 1: no evaluado.

Implementación greenfield de JARVIS P.D. guiada por la especificación oficial.

El proyecto avanza estrictamente por fases y gates. **Fase 0 / Gate 0 —
Integridad** continúa siendo la única implementación certificada. F1-WP0 cerró
el gobierno previo de Gate 1; WP1-WP3 materializan contratos, servicios y la
fixture acotada, pero no completan Fase 1 ni evalúan Gate 1. Solo la
evidencia reproducible bajo `docs/evidence/` demuestra materialización técnica.

## Principios innegociables

- PostgreSQL conserva el estado canónico estructurado.
- Los agentes proponen; los servicios deterministas validan y materializan.
- LangGraph coordina y nunca es fuente de verdad.
- OpenClaw queda detrás de puertos/adapters; no se integra en Fase 0.
- Estado, outbox y auditoría crítica comparten transacción.
- Entrega repetible no implica efecto repetido.

## Estado técnico

- Baseline implementada: `PHASE_0`
- Gate 0: `PASS` (bundle definitivo `G0-DEFINITIVE-2026-08-06-dd1028c`)
- F1-WP0: `PASS` (solo documentación normativa)
- F1-WP1: `PASS`
- F1-WP2: `PASS`
- F1-WP3: `PASS`
- Fase 1 runtime: `PARTIAL_WP3_FIXTURE_ONLY`
- Gate 1: `NOT_EVALUATED`

Ver [alcance de Fase 0](docs/architecture/phase-0-scope.md) y
[manifiesto de Gate 0](docs/gates/gate-0-acceptance.yaml). El corte futuro está
en [F1-WP0](docs/architecture/phase-1-wp0-scope.md) y el
[manifiesto Gate 1](docs/gates/gate-1-acceptance-v1.yaml).
La evidencia de corrección WP1 está en
[el informe para segunda auditoría](docs/reviews/JARVIS_PD_F1_WP1_CORRECTIONS_SECOND_REVIEW.md).

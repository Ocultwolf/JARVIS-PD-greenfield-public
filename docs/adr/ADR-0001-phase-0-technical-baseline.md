# ADR-0001 — Baseline técnico de Fase 0

- Estado: ACCEPTED
- Fecha: 2026-08-06
- Autoridad: HumanOwner + Architecture Governance

## Decisión

Usar un monolito modular Python 3.12 con layout `src`, Pydantic para contratos y
configuración, SQLAlchemy/Alembic sobre PostgreSQL y pytest para evidencia.
PostgreSQL se ejecuta localmente mediante Docker Compose. Las dependencias
externas se ocultan tras ports. El dominio no importa ORM, Docker ni runtimes de
agentes.

Las versiones exactas quedan fijadas por `uv.lock`. Esta elección es local,
reversible y no altera los ADR normativos de la especificación.

## Consecuencias

Las invariantes de concurrencia se prueban contra PostgreSQL real. SQLite no
puede acreditar Gate 0. OpenClaw y LangGraph no son dependencias de Fase 0.

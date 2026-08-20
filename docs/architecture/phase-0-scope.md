# Fase 0 — Base determinista

## Incluido

- monolito modular Python con dependencias dirigidas hacia dominio;
- configuración tipada y referencias opacas a secretos;
- PostgreSQL, migraciones versionadas y Unit of Work;
- Goal, Task, Run, Action, Evidence, Approval y AuditRecord mínimos;
- máquinas de estados deterministas de Task y Run;
- contratos versionados, envelope y errores estructurados;
- outbox, inbox, deduplicación, idempotencia, leases y fencing;
- registros declarativos de agentes, tools y capacidades;
- logs, métricas y auditoría mínima;
- backup, restore aislado, smoke tests y evidencia de Gate 0.

## Excluido

OpenClaw, LangGraph, agentes ejecutables, Tool execution, Event Bus externo,
Redis, Kafka, NATS, memoria cognitiva, scheduler de producto, Policy Engine
completo, Kill Switch completo, SAFE_MODE completo, Services e Incidents.

## Autorización mínima

Una mutación canónica relevante solo atraviesa un servicio determinista y una
Unit of Work, conserva actor y request, aplica guards, exige Approval cuando el
contrato lo indique y produce AuditRecord. Esto no materializa todavía el motor
completo de policies de Gate 1.

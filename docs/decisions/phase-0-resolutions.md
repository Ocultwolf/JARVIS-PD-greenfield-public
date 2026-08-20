# Resoluciones normativas para Fase 0 / Gate 0

**Autoridad:** HumanOwner

**Estado:** ACCEPTED

**Fecha:** 2026-08-06

Este registro formaliza las resoluciones de O-QST-001, 002, 003, 004, 017,
026, 027 y 028 para el corte de Fase 0. No reescribe el Apéndice O original.

## Resoluciones

- **O-QST-001:** el MVP se acredita con suite reproducible, manifiesto de
  aceptación y evidencia por gates; no basta una demo ni se exige piloto largo.
- **O-QST-002:** CLI estructurada como interfaz técnica inicial; sin UI propia.
- **O-QST-003:** G0 cierra el contrato versionado de User Node con límites,
  rechazos, validación, cinco fixtures positivos y cinco negativos. Su salida es
  propuesta y no muta estado. La integración OpenClaw pertenece a Fase 1.
- **O-QST-004:** Goal es resultado duradero; Task es unidad con aceptación y
  retry seguro; Run es un intento; Action un paso material; ToolCall una
  invocación. Se divide una Task cuando mezcla reversibilidad, autoridad,
  idempotencia o reconciliación.
- **O-QST-017:** G0 incluye leases, fencing, Approval mínimo, cancelación básica
  solo si la FSM la expone, backup/restore mínimo y replay/liberación controlada
  de inbox/outbox. Procedure, Policy completa, Kill Switch, SAFE_MODE, Service e
  Incident quedan diferidos. No se inventan Commands o Events normativos.
- **O-QST-026:** monolito modular Python en host y PostgreSQL real con Compose.
  OpenClaw es externo y no se integra en G0; no hay bus externo.
- **O-QST-027:** cada gate produce TestEvidenceBundle reproducible con revisión,
  entorno, migración, fixtures, comandos, resultados, estados, concurrencia,
  artefactos y checksums de recuperación.
- **O-QST-028:** R0-R1 requieren policy explícita; R2 policy preaprobada o
  Approval humana; R3-R5 y cambios de fines, privacidad o autoridad requieren
  HumanOwner. Ninguna autorización anula invariantes de G0.

## Restore mínimo G0

Backup consistente e identificado con checksum, restaurado en una base aislada
vacía, con versión de migración identificable y verificación determinista de
estado canónico, auditoría, inbox/outbox e idempotencia. No es promoción,
Disaster Recovery completo ni replay, y no reactiva efectos externos.

## Auditoría mínima G0

Integridad transaccional, identidad, secuencia y trazabilidad verificable. No se
introducen firmas ni infraestructura criptográfica avanzada en este gate.

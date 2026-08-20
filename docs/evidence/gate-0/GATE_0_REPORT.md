# Informe definitivo de evaluación — Gate 0

- Revisión ejecutable evaluada: `dd1028ca80b8a45adc69432bf4b89d82040e5f1b`
- Resultado global: **PASS**
- Punto de partida: worktree limpio y dependencias frozen
- Fase 1, LangGraph y OpenClaw: no implementados ni integrados

## Criterios normativos y evidencia

1. **G0-01 — PASS.** Task/Run atraviesan `IntegrityService`, FSM y Unit of
   Work. Cancelación exige Approval exacta y no inventa Event para C023.
2. **G0-02 — PASS.** Estado, outbox, auditoría e idempotencia confirman o
   revierten juntos; pruebas de atomicidad y rollback PostgreSQL.
3. **G0-03 — PASS.** Idempotency key e inbox deduplican; payload conflictivo se
   rechaza. Replay de outbox conserva message ID y no duplica estado canónico.
4. **G0-04 — PASS.** Dos workers concurrentes compiten por la misma lease y
   solo uno la adquiere.
5. **G0-05 — PASS.** Fencing monotónico; owner/token obsoletos no renuevan,
   liberan ni escriben.
6. **G0-06 — PASS.** Migración desde base aislada vacía a
   `0001_phase_0_integrity`; ocho tablas críticas verificadas.
7. **G0-07 — PASS.** Backup, checksum y restore aislado conservan migración,
   entidades, audit, inbox/outbox, estados e idempotencia. El snapshot permanece
   estable tras espera, sin promoción ni replay automático.
8. **G0-08 — PASS.** Approval/AuditRecord reconstruyen actor, request, Command,
   autorización, secuencia y estados; leases y replay también son auditables.
9. **G0-09 — PASS.** User Node `1.0.0`, cinco fixtures positivos, cinco
   negativos y salida de propuesta sin API de mutación.
10. **G0-10 — PASS.** TestEvidenceBundle versionado con revisión, entorno,
    comandos, migración, fixtures, concurrencia, artefactos y checksums.
11. **O-QST-017 / lifecycle de leases — PASS.** Acquire, renew y release
    deterministas; expiración, owner, fencing y auditoría probados.
12. **O-QST-017 / inbox-outbox y replay — PASS.** Release, doble release
    rechazado, replay controlado, redelivery, deduplicación inbox y trazabilidad
    ejecutados contra PostgreSQL.
13. **Chequeo de tipos — PASS.** Mypy fijado en `uv.lock`: 14 archivos sin
    errores.

## Verificaciones transversales

- O-QST-001/002/003/004/017/026/027/028 registradas: **PASS**.
- Dependencias `uv sync --frozen --all-extras`: **PASS**.
- Lint: **PASS**.
- Tipos: **PASS**, 14 archivos.
- Registry declarativo: **VALID**.
- Suite completa: **PASS**, 26 pruebas.
- Batería crítica: **PASS**, 9 pruebas.
- Migración vacía: **PASS**.
- Backup/restore aislado: **PASS**.
- FAIL: ninguno. BLOCKED: ninguno.

## Deuda no bloqueante

- Mypy se ejecuta sobre `src`; los tests se validan mediante ejecución, ruff y
  contratos, pero no forman parte del target estático actual.
- El lifecycle implementado es el mínimo de G0; no constituye el sistema
  completo de Policy, Safe Mode o Disaster Recovery de fases posteriores.
- Replay es una operación interna controlada y auditable; no se inventaron
  Commands o Events públicos.

## Baseline propuesto

`dd1028ca80b8a45adc69432bf4b89d82040e5f1b`

## Conclusión

**GATE 0: PASS**

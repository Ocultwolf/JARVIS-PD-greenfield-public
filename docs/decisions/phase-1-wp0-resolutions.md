# Resoluciones normativas F1-WP0

- Estado de las decisiones ratificadas: ACCEPTED
- Estado de F1-WP0: CORRECTED_PENDING_SECOND_REVIEW
- Autoridad: HumanOwner
- Fecha: 2026-08-06
- Alcance: Gate 1 exclusivamente

Cada fila es un Decision Record del corte G1. `CERRADA_G1` no implica que la
capacidad esté implementada ni que la pregunta global quede cerrada fuera de G1.

| ID | Estado G1 | Resolución ratificada | Destino |
|---|---|---|---|
| O-QST-005 | CERRADA_G1 | Elegibles: Task `READY`, Goal/scope autorizado, dependencias satisfechas, sin bloqueo ni Run incompatible. Orden: `priority DESC, created_at ASC, task_id ASC`. User Node recomienda; `SelectionService` decide y materializa. | ADR-0002 |
| O-QST-006 | CERRADA_G1 | `max_concurrent_runs=1`, `max_actions_per_run=1`, `max_tool_calls_per_action=1`, `max_node_attempts=2`, `max_graph_transitions=12`, `run_timeout=300s`, `node_timeout=60s`, `tool_timeout=30s`. Tokens se fijan con el modelo de WP6 antes del E2E real. | ADR-0002 |
| O-QST-007 | CERRADA_G1 | Antes de dispatch se cancela determinísticamente. Tras dispatch sin recibo: `OUTCOME_UNKNOWN`; nunca retry mientras exista UNKNOWN. Efecto confirmado detiene trabajo futuro; compensar es otra operación idempotente y auditable. | ADR-0002 |
| O-QST-008 / G1-SCOPE-001 | CERRADA_G1 | Única Tool: escritura atómica de fixture en sandbox local, path relativo validado, hash, estado previo/posterior, verificación y compensación. | ADR-0002/0003 |
| O-QST-013 | CERRADA_G1 | Default deny; propuesta, aprobación y ejecución separadas; scope exacto; Tool mediada; riesgo y límites duros; revalidación pre-efecto; reconcile-before-retry. | ADR-0003 |
| O-QST-014 | CERRADA_G1 | Approval por CLI estructurada, explícita, ligada a operation hash, expirable, revocable, single/limited-use y consumida transaccionalmente. El silencio deniega. | ADR-0003 |
| O-QST-016 | CERRADA_G1 | Root dedicado; prohibidos paths absolutos, `..` y escape por symlink; sin red; límites de tamaño, archivos y timeout; allowlist de operación. | ADR-0003 |
| O-QST-022 | CERRADA_G1 | Latch persistente del host, independiente de LLM/LangGraph/OpenClaw, consultado antes de adquirir y antes del efecto; PostgreSQL proyecta y audita. Health no libera el latch. | ADR-0003 |
| O-QST-029 | CERRADA_G1_PARCIAL | Solo fixtures sintéticos, sin secretos ni datos personales reales; contexto mínimo; sin chain-of-thought; evidencia redactada. El marco legal para datos reales sigue abierto. | ADR-0004 |
| O-QST-009 | CERRADA_G1_PARCIAL | `ContextManifest` sellado y hasheado con correlación invocation/session; `InjectionReceipt.CONFIRMED` requiere coincidencia de transporte, sesión y hash. Capacidades avanzadas siguen diferidas. | ADR-0004 |
| O-QST-018 | CERRADA_G1 | Leases/fencing son infraestructura interna transaccional y auditable; sin Events públicos nuevos sin consumidor normativo. | ADR-0002 |
| O-QST-019 | CERRADA_G1 | No importar sesiones ni estado del prototipo al runtime certificado; referencia histórica no autoritativa. | ADR-0004 |
| O-QST-020 | CERRADA_G1 | PostgreSQL outbox y dispatcher local; sin Kafka, NATS, Redis ni Event Bus externo. | ADR-0002 |
| O-QST-027 | CERRADA_G1 | Extender bundle G0 con interrupciones, oracle de efectos, fake/OpenClaw, InjectionReceipt, UNKNOWN/reconcile y reconstrucción desde PostgreSQL. | ADR-0004 |
| O-QST-028 | CERRADA_G1 | Tool fixture es R1. Approval se fuerza en un escenario de aceptación. R3–R5 quedan fuera de G1. | ADR-0003 |
| G1-DOC-002 | CERRADA_G1 | Node Invocation es operación interna de Node Runtime; no se crea Command público. | ADR-0004 |
| G1-DOC-001 | CERRADA_G1 | G1 anticipa el mínimo de `ContextManifest` e `InjectionReceipt` exigido por el gate; capacidades avanzadas siguen post-MVP. | ADR-0004 |
| G1-DOC-003 | CERRADA_G1 | Reconciliación distingue `NO_EFFECT`, `CONFIRMED_SUCCEEDED`, `CONFIRMED_FAILED`, `UNRESOLVED`; no se inventan eventos terminales. | ADR-0002 |
| G1-DOC-004 | CERRADA_G1_PARCIAL | Activación obligatoria. No hay liberación automática; el entorno de aceptación activado se descarta hasta definir release productivo. | ADR-0003 |
| G1-RUNTIME-001 | CERRADA_G1 | Binding y modelo fijos; una reparación contractual tras output inválido; segundo fallo bloquea; sin fallback silencioso. | ADR-0004 |
| G1-SCOPE-002 | CERRADA_G1 | User Node recomienda y no materializa estado. Planner produce propuestas estructuradas y no concede permisos ni ejecuta Tools. Router decide determinísticamente usando estado y guards previamente validados. El texto de un LLM nunca selecciona directamente una ruta de ejecución. | ADR-0002 |

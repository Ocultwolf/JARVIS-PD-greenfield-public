# ADR-0004 — Contexto, runtime y evidencia de Gate 1

- Estado: ACCEPTED
- Materialización: PLANIFICADO
- Fecha: 2026-08-06
- Autoridad: HumanOwner + Architecture Governance
- Fuentes: O-QST-009/019/027/029, G1-DOC-001/002, G1-RUNTIME-001

## Decisión

Node Invocation es una operación interna de Node Runtime y recibe un
`AgentInvocation`; no se crea Command público. `ContextManifest` se sella con
hash y correlaciones de invocación/sesión. `InjectionReceipt.CONFIRMED` solo es
válido cuando transporte, sesión y hash coinciden con el manifest exacto.

El adapter OpenClaw usa binding y modelo fijos. Ante output inválido puede pedir
una única reparación contractual; un segundo fallo bloquea el Run. No existe
fallback silencioso ni ampliación de contexto, datos o permisos.

Si OpenClaw está caído o indisponible, `AgentRuntime` devuelve un fallo
contractual verificable y el Run queda bloqueado o degradado de forma
controlada. No se confirma un resultado inexistente, no se cambia de runtime o
modelo silenciosamente y no se corrompe estado canónico. La continuidad se
determina desde PostgreSQL, nunca desde la sesión, workspace o respuesta de
OpenClaw.

Las pruebas de G1 usan únicamente fixtures sintéticos, contexto mínimo y
evidencia redactada; no conservan chain-of-thought, secretos ni datos personales
reales. Las sesiones, memoria y estado del prototipo histórico no se importan a
PostgreSQL ni al runtime certificado.

La evidencia amplía el bundle G0 con matriz de interrupciones, oracle exacto de
efectos, adapters fake y OpenClaw bajo la misma suite, receipt de inyección,
UNKNOWN/reconcile y reconstrucción desde PostgreSQL sin depender del checkpoint
de LangGraph.

## Alcance diferido

El marco legal de datos reales, retención general, memoria avanzada, contexto
post-MVP y routing/fallback de modelos quedan fuera de G1.

# Matriz de trazabilidad Gate 1 v1

Los nombres de test son IDs reservados para WP1–WP8; WP0 no crea tests de
runtime. La evidencia final vivirá bajo `docs/evidence/gate-1/`.

| Requisito | Test previsto | Evidencia | Artefacto esperado |
|---|---|---|---|
| G1-01 | T-G1-CONTRACT-FAKE-OPENCLAW | resultados equivalentes | contract-suite.json |
| G1-02 | T-G1-ROUTING-AUTHORITY | User Node solo recomienda; Planner solo propone; Router decide únicamente desde estado y guards validados; rutas textuales LLM se rechazan; solo el servicio dueño materializa | routing-authority-report.json |
| G1-03 | T-G1-A2-LIMITS | reason codes por límite | limits-report.json |
| G1-04 | T-G1-INTERRUPTION-MATRIX | estado antes/después | interruption-matrix.json |
| G1-05 | T-G1-EFFECT-EXACTLY-ONCE | conteo exacto de efectos | effect-oracle.json |
| G1-06 | T-G1-UNKNOWN-RECONCILE | timeline y recibos | reconciliation-report.json |
| G1-07 | T-G1-CANCEL-COMPENSATION | cancelación pre/post-dispatch conserva efectos y detiene trabajo futuro; compensación posee identidad propia, deduplica replay y deja AuditRecord | cancellation-compensation-report.json |
| G1-08 | T-G1-POLICY-DEFAULT-DENY | decisiones PDP/PEP | policy-report.json |
| G1-09 | T-G1-APPROVAL-LIFECYCLE | hash distinto, expiración, revocación y replay quedan rechazados; consumo single/limited-use comparte transacción con la operación autorizada | approval-lifecycle-report.json |
| G1-10 | T-G1-SANDBOX-CONTAINMENT | red denegada; operación fuera de allowlist, exceso de recursos, path absoluto, `..` y escape symlink producen reason codes conocidos sin efecto fuera del root | sandbox-containment-report.json |
| G1-11 | T-G1-INJECTION-RECEIPT | manifest/receipt/hash | injection-receipt.json |
| G1-12 | T-G1-INVALID-OUTPUT | primer/segundo fallo | runtime-repair.json |
| G1-13 | T-G1-KILL-SWITCH-INDEPENDENCE | latch sobrevive restart sin LLM/LangGraph/OpenClaw; guards pre-work/pre-effect deniegan; health recuperado no libera | kill-switch-independence-report.json |
| G1-14 | T-G1-CHECKPOINT-LOSS | reconstrucción PostgreSQL | graph-recovery.json |
| G1-15 | T-G1-OUTBOX-RECOVERY | publish/redelivery/inbox | outbox-report.json |
| G1-16 | T-G1-LEASE-FENCING | owners y tokens | lease-report.json |
| G1-17 | T-G1-PRIVACY-SYNTHETIC | inventario demuestra fixtures sintéticos, ausencia de secretos/datos personales reales/chain-of-thought, contexto mínimo y redacción de evidencia | privacy-synthetic-report.json |
| G1-18 | T-G1-BACKUP-RESTORE | checksum, restore aislado, invariantes G0 y no reactivación de efectos/autoridad; no pretende cerrar el alcance global O-QST-023 | restore-report.json |
| G1-19 | T-G1-EVIDENCE-REPRODUCE | comandos/checksums | TestEvidenceBundle.json |
| G1-20 | T-G1-HISTORY-EXCLUSION | origen de datos/runtime | provenance-report.json |
| G1-21 | T-G1-OPENCLAW-UNAVAILABLE | caída e indisponibilidad dejan Run bloqueado/degradado; estado PostgreSQL íntegro; cero fallback silencioso; ningún AgentResult inexistente queda confirmado | openclaw-unavailable-report.json |

## Regla de completitud semántica

Una fila no queda satisfecha por la mera existencia del test o artefacto. La
evidencia debe demostrar cada cláusula de su columna `Evidencia`; el bundle debe
registrar entorno, versiones, estado PostgreSQL anterior/posterior, estímulo,
resultado, reason codes y checksum del artefacto. Una cláusula sin evidencia
clasifica el requisito como FAIL o BLOCKED, nunca PASS parcial.

# Restricciones normativas aplicadas en Gate 0

El catálogo oficial contiene huecos explícitos que G0 no rellena mediante
invención local:

- C023 `loop.run.cancel.requested` permite la transición de FSM-006 y exige
  resultado/auditoría, pero no define Event terminal. La implementación cambia
  el estado únicamente con Approval exacta y AuditRecord; no publica un Event
  nuevo.
- C026–C028 gobiernan acquire, renew y release de leases mediante estado,
  resultado y auditoría. No se crean Events no catalogados.
- La liberación controlada de inbox/outbox es infraestructura interna de G0 y
  no se presenta como Command/Event de dominio.

Si una fase posterior necesita convertir esas operaciones en contratos
públicos, deberá cerrar primero el hueco documental correspondiente.

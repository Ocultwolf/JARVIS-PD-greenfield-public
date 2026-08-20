# ADR-0003 — Autoridad, contención y parada de Gate 1

- Estado: ACCEPTED
- Materialización: PLANIFICADO
- Fecha: 2026-08-06
- Autoridad: HumanOwner + Security Governance
- Fuentes: O-QST-013/014/016/022/028, G1-DOC-004

## Decisión

Gate 1 opera con default deny y separación entre propuesta, aprobación y
ejecución. La Tool R1 requiere scope exacto, límites duros, evaluación de policy
y revalidación inmediatamente antes del efecto. Un escenario de aceptación
fuerza Approval aunque la Tool sea R1.

La Approval se captura mediante CLI estructurada y autenticada, referencia el
hash inmutable de la operación, expira, puede revocarse y se consume
transaccionalmente como single-use o uso limitado. Silencio, texto libre o
cambio material no autorizan.

La Tool se ejecuta en un root dedicado sin red, paths absolutos, `..` ni escape
por symlink, con allowlist y límites de recursos. Un latch persistente del host,
independiente de LLM, LangGraph y OpenClaw, bloquea adquisición y efecto. Su
estado se proyecta/audita en PostgreSQL, pero PostgreSQL no sustituye el latch.

Para la aceptación local WP4, el estado canónico del latch es un archivo JSON
regular del host, de esquema cerrado, tamaño limitado y lectura sin seguimiento
de symlinks. La proyección PostgreSQL debe coincidir en estado, razón, revisión y
hash SHA-256 del archivo. Archivo ausente o ilegible, JSON corrupto, esquema
inválido o divergencia bloquean. Se revalida antes de adquirir trabajo y otra vez
inmediatamente antes del callback que puede materializar el efecto.

El archivo y su directorio de autoridad tienen UID/GID esperados configurados
fuera del worker. Ninguno admite escritura de grupo o global. La lectura abre el
directorio sin seguir symlinks, compara identidad de dispositivo/inodo antes y
después de abrir el archivo, lee mediante descriptor y vuelve a comprobar que
la ruta sigue designando el mismo objeto. El worker solo recibe el lector y no
una capacidad de creación, reemplazo o escritura. En producción, el UID del
HumanOwner/servicio de seguridad debe ser distinto del UID del worker.

## Liberación deliberadamente no decidida

Gate 1 demuestra activación, no liberación productiva. Recuperar health nunca
libera. Tras la prueba de activación se descarta el entorno de aceptación. No se
crea un endpoint, Command, Event ni automatismo de release.

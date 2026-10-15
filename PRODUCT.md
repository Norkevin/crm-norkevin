# Flow CRM

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
Kevin administra Norkevin Photography y Astral Weddings. Sus clientes consultan propuestas, contratos, cuestionarios, archivos y pagos en su portal. El equipo consulta coberturas y sus importes en Teams.

## Product Purpose
Dar seguimiento a una consulta hasta la entrega de la boda, reuniendo personas, fechas, documentos, comunicaciones y pagos.

## Operating Context
Uso diario en computadora y teléfono, incluido el iPhone 14 Pro Max. Guatemala, español y quetzales. Las dos marcas comparten colaboradores y conservan sus registros separados. Teams ofrece al miembro un portal personal con pestañas por marca cuando sus fichas tienen el mismo correo. La administración recibe avisos de cruces de cobertura entre las dos marcas.

## Capabilities and Constraints
Flask y plantillas Jinja existentes. Conservar rutas, permisos por empresa, controles de aprobación aplicables, formularios, firma, cálculos y proveedores. Teams muestra eventos activos; Jobs abre por fecha más cercana. No usar datos reales para pruebas de escritura ni enviar comunicaciones durante las comprobaciones. El envío de invitaciones pendientes incluye todas las próximas coberturas de la marca abierta, publica borradores y confirma sus honorarios, conserva programaciones y omite respuestas ya registradas. Antes de enviar, comprueba la cobertura vigente y la respuesta real de Google; los envíos individuales siguen disponibles por boda.

En el portal de una boda, el equipo puede consultar solo los nombres y roles de las personas con cobertura compartida vigente de esa misma marca y boda; los honorarios, pagos, correos e instrucciones de otros miembros permanecen privados.

En Astral y Norkevin con envío automático autorizado, el botón «Enviar» de un Lead o Job envía el mensaje redactado sin segunda aprobación. Las tareas de correo requieren redactar el mensaje y elegir «Programar envío»; usan la fecha y hora de la tarea en America/Guatemala y, si ya venció, el próximo ciclo de la cola. La tarea solo se completa con confirmación real de envío; editar su fecha actualiza la programación y eliminarla cancela el correo pendiente. Los envíos fallidos, bloqueados o con contenido conflictivo requieren revisión y no se reintentan automáticamente. Se conservan el aislamiento por empresa y la prevención de duplicados.

Cada lead permite crear reuniones independientes del workflow, virtuales con un enlace HTTPS ya creado en Zoom, Meet u otro servicio, o presenciales con dirección. Requieren fecha y horario futuros de Guatemala, con fin posterior al inicio; al guardar, la invitación se envía al correo del lead según la política de envío de Astral/Norkevin. La reunión queda en el listado y calendario, se conserva al convertir el lead en job y se exporta en ICS con horas UTC. La idempotencia evita duplicados; los fallos de envío se revisan en Correos y los de preparación ofrecen «Preparar invitación». Estas reuniones no son coberturas de Teams.

## Brand Commitments
Flow CRM y Teams. El usuario pide una apariencia profesional, elegante, moderna y coherente, evitando una apariencia genérica de IA. Mantener las marcas y los documentos configurables por empresa. Teams debe distinguirse del CRM.

## Product Principles
La información operativa tiene prioridad sobre la decoración. Los controles conservan su significado. Una acción principal por contexto. Todos los botones deben ser alcanzables en móvil.

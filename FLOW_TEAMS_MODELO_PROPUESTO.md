# Modelo de la propuesta local

Los jobs permanecen en JsonStore. Teams referencia el mismo job_id, nunca crea una segunda boda. SQLite guarda entidades por marca, identidad y versión; comandos idempotentes y auditoría se confirman en la misma transacción BEGIN IMMEDIATE. Los importes son centavos enteros GTQ.

La asignación crea un solo costo de honorario. El presupuesto original se conserva; el final sustituye la estimación. Aprobar o realizar servicio no significa pagarlo. Pagos distribuidos y reversos vinculados producen saldos netos, sin crear otro costo. Las marcas y monedas no se mezclan.

Publicar una cobertura crea términos vigentes, aviso y tareas. El miembro responde desde su propia sesión; el propietario solo dispone de una vista de prueba explícita. Modificar condiciones conserva el acuerdo anterior y pide reconfirmación. La aceptación y sus conflictos se comprueban dentro de la transacción, con traslado y cruce de medianoche. Rechazar libera la plaza; abonos y obligaciones existentes se preservan.

Un fondo para gastos produce una salida y ningún costo. Liquidar aplica ese fondo a costos finales existentes, sin nueva salida. La devolución genera una entrada vinculada y reduce el pendiente del fondo. Una solicitud de reembolso no crea obligación hasta aprobarla. Un gasto compartido conserva un comprobante origen y partes que suman exactamente el total. Las cuotas deben sumar la obligación y no se cambian silenciosamente.

Costo previsto = finales o estimaciones vigentes. Pendiente = obligación aprobada menos pagos/liquidaciones. Margen = referencia comercial menos costo previsto. Caja = cobros CRM menos salidas netas, incluyendo fondos y devoluciones pero sin contar liquidaciones dos veces. El año de bodas y el de movimientos son bases distintas. El cierre exige costos revisados/finales, coberturas resueltas y fondos liquidados; conserva un corte histórico y permite pagar deuda después. Cambios comerciales posteriores muestran la diferencia; la reapertura exige motivo.

El portal construye una lista explícita de datos propios. No transmite ingresos del cliente, margen, honorarios ajenos, tarifas de perfil ni honorarios en borrador. Publicaciones y descargas comprueban audiencia, condiciones, job vigente y miembro activo. Revocar acceso invalida sesiones y códigos. Los códigos crudos solo se muestran al crearlos; se guarda su hash, vencimiento y consumo.

Documentos: versiones con historial, audiencia, contenido o archivo de máximo 10 MB, lectura por versión y retirada. El almacenamiento local de archivos en SQLite es suficiente para esta propuesta; debe reemplazarse por almacenamiento privado con controles de producción antes de admitir cargas reales.

Avisos: preparado, aprobado, simulado o cancelado. La deduplicación incorpora cobertura, condiciones, propósito y documento/version. Antes de cada transición se verifica destinatario vigente. Las tareas vencidas se revisan manualmente; falta de call sheet bloquea el aviso, y publicaciones tardías omiten recordatorios anteriores. No existe transporte externo en esta versión.

Exportaciones: CSV de costos/saldos autorizado por marca y filtros de año de boda, miembro y categoría; neutraliza fórmulas en campos de texto. ICS individual con horarios UTC, privacidad y estado tentativo/confirmado; no escribe en Google.

El módulo se registra exclusivamente desde el lanzador local. El wrapper de autenticación exceptúa únicamente el nuevo portal local, que verifica identidad por separado. Producción no registra estas rutas ni modifica su login.

El directorio compartido usa source_id de Notion y directory_id para referenciar person_private en un ámbito separado. Cada marca conserva su member_id, asignaciones, costos y movimientos. La ficha privada requiere resolver un member_id autorizado antes de leer o editar la identidad compartida. Campos de identificación/pago no forman parte del snapshot ni del portal; auditoría e idempotencia guardan solo identidad/versión del cambio privado.

Los comprobantes de expense_request reutilizan la validación de archivos de documentos. Descargas privadas revalidan la sesión y titularidad de la solicitud; la aprobación conserva el vínculo expense_request_id. Los archivos se excluyen de resúmenes y resultados de comandos. El portal deriva totales y agrupaciones desde costos propios publicados y muestra movimientos propios; anticipos nunca inflan honorarios.

Comprobante de pago: archivo opcional en la entidad payment, guardado atómicamente con el abono y cubierto por su clave de idempotencia. Se reutiliza la validación de adjuntos (10 MB, firma real y tipo permitido). El resumen y la auditoría omiten file_data. La descarga privada comprueba la marca; en el portal comprueba el beneficiario y la visibilidad de la obligación publicada. Un reverso conserva el comprobante en el pago original.

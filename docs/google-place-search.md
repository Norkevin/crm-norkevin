# Búsqueda de lugares con Google Places

El formulario combina el catálogo público de Guatemala y Photon. Google Places (New) está implementado como fuente adicional y **permanece apagado por defecto**. No basta con desplegar el código para activarlo.

## Activación

1. El propietario debe autorizar Google Maps Platform y su facturación. Habilitar **Places API (New)** en su proyecto de Google Cloud.
2. Crear una clave exclusiva de servidor, restringida a Places API (New); cuando sea posible, restringir también a las IP de salida del servicio de Render. No habilitar otras API ni reutilizar credenciales de Gmail.
3. Guardar la clave únicamente en Environment de Render como `GOOGLE_PLACES_API_KEY`. No pegarla en el chat, incluirla en Git, HTML o JavaScript, ni publicarla en capturas.
4. Configurar `FLOW_GOOGLE_PLACES_ENABLED=1` y `GOOGLE_PLACES_DAILY_LIMIT=100`. No aumentar este límite sin autorización del propietario.
5. Esperar a que el servicio esté activo. Buscar `jardin vilaflor` y comprobar que la petición a `/api/captacion/places` responde correctamente y que los resultados de Google tienen atribución **Google Maps**. Revisar escritorio y móvil sin enviar consultas de clientes.

Desactivación: quitar `FLOW_GOOGLE_PLACES_ENABLED` o ponerlo en `0`. El catálogo y Photon siguen funcionando.

## Alcance y controles

- Solo se llama a `places:autocomplete`. No se cargan mapas, fotos, reseñas ni Place Details. Se factura por solicitudes de Autocomplete, sin declarar sesiones que no se terminan con Place Details.
- El contador diario usa la fecha de Guatemala y se guarda en `CRM_DATA_DIR/google_places_usage.sqlite3`. Es compartido por las marcas y sobrevive a reinicios. Reserva cada intento antes de solicitar datos; errores también consumen el límite de forma conservadora. Un límite de 100 permite como máximo 3,100 intentos de esta integración en un mes de 31 días.
- El límite se refiere a peticiones mientras se escribe, no a formularios enviados. No limita servicios que utilicen la misma cuenta o clave fuera de Flowing.
- Hay como máximo dos solicitudes de Google simultáneas, con tres segundos de timeout. No se retienen bloqueos de tablas de clientes o correos mientras se espera al proveedor.
- Se envía únicamente el texto de ubicación. Se rechazan correos, teléfonos, URL y consultas demasiado largas. No se guardan consultas ni predicciones de Google en el servidor o en el caché reutilizable del navegador. La respuesta lleva `Cache-Control: no-store`.
- Google prioriza Guatemala; Photon busca en Guatemala. Una ubicación extranjera se puede escribir manualmente. Las sugerencias no aprueban municipios ni tarifas de viáticos.
- La interfaz distingue cada resultado de Google con su atribución y publica las condiciones y el aviso de privacidad de esta función.

## Precio de referencia y comprobación pendiente

La lista oficial consultada el 14 de octubre de 2026 muestra 10,000 solicitudes mensuales sin cargo para Autocomplete Requests y USD 2.83 por 1,000 en el primer tramo de pago. El tramo gratuito y el consumo se deben verificar en la cuenta de facturación; no se garantiza que otros proyectos no lo estén consumiendo. La facturación debe estar habilitada incluso si el uso queda dentro del tramo gratuito.

La integración se prueba con respuestas simuladas hasta que exista una clave autorizada. Una coincidencia del catálogo local **no** acredita que Google esté activo ni confirma la cobertura de su API. La activación solo se considera terminada tras una respuesta real del proveedor.

Fuentes: [configuración](https://developers.google.com/maps/documentation/places/web-service/get-api-key), [Autocomplete](https://developers.google.com/maps/documentation/places/web-service/place-autocomplete), [precios](https://developers.google.com/maps/billing-and-pricing/pricing), [políticas y atribución](https://developers.google.com/maps/documentation/places/web-service/policies).

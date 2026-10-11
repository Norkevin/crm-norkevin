# Conectar el iPhone al CRM real

El propietario cambió la elección de demostración por sus datos reales. La app de prueba ya está firmada e instalada; confirmó que abre. Esta propuesta permite consultas en `https://flowingcrm.com` y mantiene push apagado. No publica la app en App Store ni requiere comprar una membresía de Apple.

## Estado verificado antes de publicar

- GitHub `main` en `a1f6b00` no contiene `src/mobile.py`.
- La petición sin sesión a `POST /api/mobile/pair/start` devuelve 401 con el mensaje de autenticación antiguo. Un 401 en una ruta desconocida no demuestra que la API móvil exista.
- La rama `codex/flowing-iphone` contiene la API, autorización por código/CSRF, lectura por empresa, Teams y el cliente nativo. Las consultas no dependen del bundle ID de la preview ni de APNs.
- El gateway de demostración se canceló antes de crear archivos. No se abrió un túnel ni se expuso el fixture del Mac.

## Cambio propuesto

Publicar el código de esta rama en el servicio existente y aplicar:

```text
FLOW_MOBILE_ENABLED=1
FLOW_MOBILE_PUSH_ENABLED=0
```

`render.yaml` declara esos valores. Si el servicio no se sincroniza mediante Blueprint, aplicarlos expresamente en su configuración de Render después de aprobar la publicación. Conservar `CRM_DATA_DIR=/var/data`, el disco, el secreto Flask, OAuth, correo y los ajustes actuales de workflows. No copiar variables del fixture ni instalar un servidor con datos reales en el Mac.

La API crea únicamente su journal `mobile.sqlite3` en el mismo disco persistente, con permisos 0600. Sesiones, preferencias y lecturas de avisos se guardan allí; no modifica trabajos, clientes, importes ni asignaciones del CRM. La primera conexión debe aprobarla el propietario desde la sesión Google de la empresa correcta. El bearer de la app no autentica rutas web y vuelve a comprobar que el propietario y la empresa sigan activos.

No iniciar el worker móvil ni activar push para esta primera conexión. Los datos de Inicio, Leads, Trabajos, Calendario, Pagos y Teams se leen directamente del CRM. La bandeja móvil necesitará una reconciliación posterior para recibir eventos; un inbox vacío no prueba que no existan actividades en la web.

## Verificación tras publicación aprobada

1. Confirmar revisión desplegada y que la web habitual sigue respondiendo.
2. Confirmar API sin bearer → 401 y pairing válido → código pendiente; nunca registrar secretos ni bearer en evidencias.
3. Abrir la app con `https://flowingcrm.com`, generar código y aprobarlo en `/mobile/connect` con Google, comprobando el nombre de la empresa.
4. Comparar una ficha y un importe conocidos con la web de esa empresa, y recorrer Teams. No editar datos para esta validación.
5. Comprobar que la sesión se conserva al volver a abrir la app. Push físico permanece fuera de esta prueba.

## Reversión

Desactivar `FLOW_MOBILE_ENABLED` bloquea la API y nuevas conexiones. Mantener el journal y su copia de seguridad; no borrar datos del CRM ni rotar secretos OAuth/Flask. `FLOW_MOBILE_PUSH_ENABLED` permanece en 0. La web conserva sus rutas, permisos y funcionamiento.

## Preparación revisada

La preparación se presentó en el PR #16 antes de publicar. La aprobación posterior del propietario se registra a continuación.

Validación de esta preparación: 59 pruebas móviles, autenticación, aislamiento por empresa y credenciales aprobadas. Registro `command-20261011T031853.044682Z-87df56f0186d.log`. La revisión independiente no encontró defectos bloqueantes para consultas del propietario con push apagado; verificó además con datos sintéticos pairing de un uso, journal persistente/0600, bearer almacenado como hash, invalidación por cambio de propietario y logout. No acredita la configuración efectiva de producción ni una sesión física conectada.

## Publicación autorizada — 10 de octubre, 21:25 Guatemala

El propietario autorizó expresamente publicar el cambio y activar el acceso con datos reales, manteniendo push apagado. [PR #16](https://github.com/Norkevin/crm-norkevin/pull/16) fusionado en `main`, revisión `2fa2adf2ae47f5b089ddfab4dfbef79d2d469def`.

Los probes públicos sin credenciales observaron 502 transitorio, seguido de recuperación: `/login` volvió a 200 y `/api/mobile/overview` pasó del mensaje antiguo 401 a 404 «No encontrado». Ese cambio coincide con la nueva API aún desactivada; no acredita sus variables efectivas. La respuesta esperada con API activa es 401 «Conecta tu cuenta de Flowing.». No se consultaron clientes ni importes reales mediante esos probes.

El panel de Render solicitó iniciar sesión. Se dejó abierto en Chrome para que el propietario complete su acceso existente, sin introducir contraseñas ni códigos ni cambiar permisos de su cuenta. Pendiente confirmar revisión desplegada y aplicar `FLOW_MOBILE_ENABLED=1`, `FLOW_MOBILE_PUSH_ENABLED=0` en el servicio; el manifiesto por sí solo no acredita sincronización de variables.

CoreDevice abrió la preview física con `-FlowingServer https://flowingcrm.com`, exit 0. Captura nativa privada `.local/iphone-real-onboarding.png` (1290×2796) confirmó esa dirección en la pantalla de conexión. La captura permanece local y no se sube al repositorio. Pendiente generar y aprobar el código en la cuenta Google de la empresa correcta y comprobar consultas reales desde el iPhone. No se declaró sesión física conectada ni push probado.

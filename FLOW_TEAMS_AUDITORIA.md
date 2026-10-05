# Flow Teams · auditoría local

Fecha: 5 de octubre de 2026. Repositorio: Flow CRM, rama `main`.

La petición inicial autorizó construir una propuesta funcional exclusivamente local. La petición posterior del 5 de octubre autoriza publicar después de corregir y verificar. El registro histórico siguiente describe las etapas locales. El documento Word es una especificación de referencia: sus instrucciones para una sesión futura no sustituyen esta autorización. No se publica, migra producción, envía correo, invita colaboradores ni ejecuta transferencias.

## Evidencia encontrada antes de implementar

| Área | Evidencia | Decisión |
| --- | --- | --- |
| Aplicación | `app.py`, Flask; `templates/base.html`, Jinja; `requirements.txt` | Módulo dentro de esta misma aplicación. |
| Persistencia vigente | `src/storage.py`, JsonStore, `CRM_DATA_DIR`; lectura/escritura JSON | No migrar CRM. SQLite estándar únicamente para la extensión Teams de la propuesta aislada. |
| Evento canónico | `app.py::list_jobs`, `get_job`, `_canonical_jobs`; `/jobs/<job_id>` | Teams referencia exactamente el ID del job; nunca crea bodas al abrirlas. |
| Finanzas | `_job_payment_summary`, `price_total`, `payments`; `_visible_billable_payments` | Reutilizar resumen comercial; excluir `team_payment`. Conversión a centavos al entrar en Teams. |
| Equipo previo | `/equipo`, `/pagos-equipo`; `team` no está en `TENANT_SCOPED_TABLES` | No importar automáticamente perfiles ni pagos históricos. Nuevo directorio operativo con alcance explícito. |
| Identidad | `src/tenant_brand_map.py::resolve_brand` | Reutilizar identidad canónica, nunca deducir marca por texto del ID. |
| Sesión | `_require_login`, `tenant_id`, `user_email`; `/dev/login` local | Control interno para sesión de propietario. No constituye autenticación de colaboradores. |
| Portal y archivos | `/portal`, `files`, `public_tokens` | Portal del cliente intacto. Portal de colaboradores y archivos privados quedan para etapa 2. |
| Correos | `src/email_delivery.py`, `gmail_delivery.py`, `pending_emails` | Bloqueados en la propuesta; no copiar credenciales. |
| Google | OAuth en `app.py`, archivos de tokens por marca | Integración real fuera de esta etapa. No se verificó conexión actual de proveedores. |
| Workflows | `src/workflow`, hilos al final de `app.py` | Apagar ambos runners al iniciar propuesta. Teams no invoca sus funciones. |
| Pruebas | `tests/conftest.py`, datos temporales y proveedores bloqueados | Pruebas sintéticas, aislamiento, importes, concurrencia y regresión. |
| Respaldo | `src/storage.py`, rutas protegidas y snapshots | No tocar ni limpiar datos o cambios anteriores. Propuesta en `.local/flow-teams`. |

## Riesgos y límites

Los locks de JsonStore cubren un proceso y no garantizan atomicidad tras un corte entre archivos. Las salidas Teams y sus aplicaciones necesitan una transacción SQLite independiente. Esto no convierte SQLite en almacenamiento del CRM ni cambia su modelo actual.

La autenticación existente no tiene la matriz completa de capacidades Teams. El primer incremento admitía solo propietario en localhost. El segundo añade un portal individual aislado y una vista de prueba explícita del propietario; no habilita acceso público ni sustituye la autenticación de producción.

Los datos locales encontrados en `data/` incluyen configuración y respaldos; no se confirmó un dataset operativo con bodas actuales. La propuesta arranca con escenarios sintéticos rotulados. No se consultó producción ni se afirma haber auditado su configuración actual.

La rama contenía archivos no versionados de trabajos anteriores. Se conservan íntegros. No se realizaron commits ni push.

## Segundo incremento: simplificación y funciones locales

La instrucción posterior del usuario autorizó continuar portal, comunicaciones y demás funciones dentro de esta computadora. Se añadieron src/teams_features.py y src/teams_portal.py, plantillas por sección y pruebas negativas de permisos, condiciones, fondos y documentos. app.py sigue sin cambios. Se respaldó SQLite antes del incremento en artifacts/teams-backups/teams-20261005-022449.sqlite3.

Las comunicaciones implementadas son durables y simuladas; ninguna aprobación activa proveedores ni cambia otra marca. No se ejecutó publicación, invitación externa ni transferencia. Las limitaciones de producción permanecen en FLOW_TEAMS_PLAN.md.

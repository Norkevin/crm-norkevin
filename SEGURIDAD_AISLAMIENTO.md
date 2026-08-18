# Modelo de seguridad: aislamiento entre empresas

Escrito despues del incidente del 16 de agosto de 2026, en el que el CRM
envio correos firmados como ASTRAL WEDDINGS a clientes de Norkevin
Photography, incluyendo recordatorios de cobro.

El objetivo de este documento es poder entender el modelo sin leer todo el
codigo.

---

## Trampa que hay que conocer antes de tocar nada

| ID interno | Empresa real | Login |
|---|---|---|
| `tenant-norkevin` | **ASTRAL WEDDINGS** | astralweddingsgt@gmail.com |
| `tenant-norkevin-photography` | Norkevin Photography | norkevinfoto@gmail.com |
| `tenant-ramiro-cruz` | Ramiro Cruz Photo | ramirocruz10x@gmail.com |

`tenant-norkevin` es la cuenta de **Astral**, no de Norkevin. Es un id
heredado de cuando el proyecto era solo de Norkevin y Astral fue la primera
cuenta creada.

Por eso `google_token_tenant-norkevin.json` contiene
`astralweddingsgt@gmail.com` y **eso es correcto**. Quien "arregle" ese
nombre asumiendo lo contrario va a mover la credencial de Astral a la
empresa equivocada y a reproducir el incidente.

Esta fijado en `tests/test_credential_isolation.py`.

---

## 1. Donde se establece la empresa

Una sola funcion, `_active_tenant_id()` en `app.py`, con dos fuentes:

1. `session['tenant_id']` — alguien con sesion en el CRM.
2. `g.public_tenant_id` — una ruta publica (portal del cliente, aceptar
   cotizacion, firmar contrato, formularios) que llega **sin** sesion.

Fuera de una peticion (scripts, hilos de fondo) no hay ninguna de las dos.

## 2. Como se propaga

`app.py` inyecta dos cosas en el store al arrancar:

```python
store.tenant_resolver = _active_tenant_id
store.request_context_probe = has_request_context
```

`src/storage.py` no importa Flask: solo llama a esos dos callables. Asi el
store sigue siendo usable desde tests y scripts.

## 3. Donde se valida

`JsonStore._tenant_scope()` decide, y de ahi sale todo:

| Contexto | Comportamiento |
|---|---|
| Fuera de una peticion | Sin aislamiento (scripts, migraciones, siembra de tests) |
| En una peticion, con empresa | Filtra por esa empresa |
| **En una peticion, sin empresa** | **Se deniega** |

Concretamente, dentro de una peticion sin empresa activa:

| Operacion | Resultado |
|---|---|
| `list` | `[]` + log `SIN_CONTEXTO_DE_EMPRESA` |
| `list_strict` | lanza `MissingTenantContextError` |
| `upsert` | lanza `TenantMismatchError` |
| `delete` | `False` |
| `clear` | lanza `TenantMismatchError` |
| `save_tenant_dict` | lanza `MissingTenantContextError` |

**Esta fila era el bug.** Antes decia "sin empresa -> no filtra", o sea
devolvia los registros de todos los negocios juntos.

### list vs list_strict

`list()` devuelve `[]`, que es seguro pero se confunde con "la tabla esta
vacia". Para rutinas, workers y cualquier cosa que **actue** sobre lo que
lee, usar `list_strict()`: revienta con un error explicito en vez de
comportarse como si no hubiera trabajo que hacer.

## 4. Excepciones publicas

Las rutas publicas no tienen sesion pero si necesitan datos. La empresa se
resuelve **una sola vez** en `_set_public_tenant()`
(`@app.before_request`), antes de que la ruta lea nada. Una puerta es
auditable; veinte no.

De donde sale la empresa segun el enlace:

| Ruta | Fuente |
|---|---|
| `/portal/<client_id>` | dueno del cliente |
| `/quotes/<id>`, `/contracts/<id>`, `/questionnaires/<id>` | dueno del registro |
| `/invoices/<invoice_id>/pdf` | dueno del pago (por `invoice_id`) |
| `/contacto/<slug>`, `/captacion/<slug>` | slug validado contra `tenants.json` |
| `POST /api/leads/nuevo`, `/api/captacion` | slug del formulario, ya validado |

Nunca sale de un parametro que el visitante pueda cambiar libremente.

`store.owner_tenant_of(tabla, valor, field='id')` es el **unico** lugar
autorizado a mirar entre empresas. Devuelve **solo el tenant_id**, nunca el
registro, asi que no sirve para leer datos ajenos.

## 5. Trabajos en segundo plano

El hilo de recordatorios (`_reminder_scheduler_loop`) es donde ocurrio el
incidente: corre sin sesion y enviaba correos de verdad.

**Queda apagado** salvo que exista `ENABLE_REMINDER_SCHEDULER=1`, variable
que no existe en Render.

Para volver a encenderlo hay que arreglar antes dos cosas:

1. que itere empresa por empresa con contexto explicito, nunca
   `store.list('jobs')` a secas;
2. que no dispare pasos con fecha anterior al arranque — un job importado
   con fecha vieja **no** es un correo pendiente de enviar.

Y aun asi deberia correr primero en modo simulacion.

## 6. Como se elige la cuenta de Gmail

`src/gmail_delivery.py::_token_path(tenant_id)`:

- Con empresa -> `google_token_<tenant_id>.json`
- **Sin empresa -> `None`** -> `is_connected()` es `False` -> no se envia.

Antes caia a un `google_token.json` global (de julio, de antes del
multi-cuenta). Ese archivo fue la credencial que uso el hilo sin sesion. Por
eso la banda decia "Gmail no conectado" — verdad para la cuenta que se
estaba viendo — mientras esa conexion invisible mandaba correos.

Desconectar borra el archivo, asi que sobrevive a un reinicio del proceso
(hay un test que lo comprueba recargando el modulo). No hay cache en memoria.

**REQUIERE REVISIoN:** `data/google_token.json` sigue existiendo en el disco
de produccion. Ya no lo lee nadie, pero conviene retirarlo. La migracion
ahora lo renombra a `.retirado` en vez de dejarlo vivo.

## 7. Como se bloquea el envio entre empresas

Todo correo de la app pasa por `MailTracker.log_email`. Antes de entregar
nada, `check_same_tenant()` exige que la empresa que envia sea la misma del
lead, del job y de la plantilla. Si no cuadra:

```
EMAIL BLOCKED: cross-company data mismatch (job X pertenece a Y, no a Z)
```

El intento **queda registrado igual** en `mail_log` con estado `blocked` y
su motivo: sin rastro de lo que no salio no se puede investigar.

Esta en el servidor y en un solo punto a proposito, para que siga
protegiendo aunque el frontend arme mal la peticion.

### Cola de aprobacion

```
accion automatica -> genera el correo -> NO envia
   -> queda pendiente -> Kevin revisa -> aprueba
   -> el servidor revalida TODO -> recien ahi sale
```

- `queue_email()` guarda copia completa de lo generado (asunto, cuerpo
  renderizado, adjuntos, cuenta, destinatario). Si manana cambia la
  plantilla, el pendiente sigue mostrando lo de hoy.
- `approve_and_send()` **vuelve a validar al enviar**, no solo al crear:
  entre generar y aprobar pueden pasar dias y las relaciones pueden cambiar.
- Un pendiente ya enviado no se envia dos veces.
- `pending_emails` es tenant-scoped: un pendiente de Astral no se ve ni se
  aprueba desde Norkevin.

### Freno global

`DISABLE_OUTBOUND_EMAIL=1` corta **cualquier** envio, incluso manual, en el
ultimo punto por el que pasa todo correo (`send_email`).

## 8. Capas, de afuera hacia adentro

1. `DISABLE_OUTBOUND_EMAIL` — freno general.
2. Scheduler apagado — nada se dispara solo.
3. Cola de aprobacion — nada sale sin accion de Kevin.
4. `check_same_tenant` — nada cruza de empresa.
5. Gmail sin empresa -> no conectado.
6. Store fail-closed — sin empresa no se ve ni se escribe nada.

Cada capa protege sola. El incidente necesito que fallaran dos a la vez.

## 9. Logs de seguridad

`log_security_event()` deja una linea uniforme:

```
SECURITY: CROSS_TENANT_ACCESS_BLOCKED operacion=upsert tabla=jobs
          registro=job-123 cuenta_activa=A cuenta_del_registro=B
```

Eventos: `CROSS_TENANT_ACCESS_BLOCKED`, `CROSS_TENANT_EMAIL_BLOCKED`,
`SIN_CONTEXTO_DE_EMPRESA`, `FALLBACK_A_CONFIG_GLOBAL`,
`ESCRITURA_GLOBAL_BLOQUEADA`.

A proposito **no** guarda contenido de correos ni datos personales: solo
ids, tablas y cuentas, que es lo que hace falta para investigar.

## 10. Herramientas de auditoria (solo lectura)

| Endpoint | Para que |
|---|---|
| `GET /api/admin/tenant-inventory` | Conteos por empresa, registros sin empresa, credenciales de Gmail en disco |
| `POST /api/admin/workflow-cleanup` | Dry-run de la limpieza de workflows |

Ambos requieren el token de admin. `workflow-cleanup` solo ejecuta con
`confirm: LIMPIAR_WORKFLOWS`; sin eso informa y no toca nada.

---

## 11. Enlaces publicos: el enlace ES la credencial

`/quotes/<id>`, `/contracts/<id>`, `/questionnaires/<id>` y `/portal/<id>`
estan pensados para que el cliente los abra **sin sesion**. Funcionan como
un documento compartido por link: quien tiene el enlace, entra. Si exigieran
login no servirian para lo que existen.

Consecuencia: la seguridad de esos enlaces depende **enteramente** de que el
id no se pueda adivinar.

### REQUIERE REVISION: los ids importados de Studio Ninja son predecibles

Los recursos que crea la app hoy usan `uuid.uuid4().hex[:8]`, que no se
adivina. Pero los importados de Studio Ninja se construyeron a partir del
nombre de la boda:

```
contract-sn-boda-rebeca-y-jos
quote-sn-<slug-de-la-boda>-1
```

Quien conozca el nombre de una boda puede reconstruir el enlace de su
contrato o cotizacion sin haberlo recibido nunca.

**No se cambiaron**, y a proposito: hay enlaces de esos ya enviados a
clientes reales, y renombrarlos los romperia. Es una decision de negocio.

Opciones para cuando se decida:

1. Dejarlos como estan y aceptar el riesgo (son documentos de bodas ya
   pasadas en su mayoria).
2. Generar un id nuevo aleatorio para los recursos aun activos y mantener el
   viejo como alias por un tiempo.
3. Agregar un token corto al enlace (`?k=...`) y exigirlo solo para los
   recursos importados.

La opcion 2 es la unica que cierra el agujero sin romper enlaces vigentes,
pero necesita decidir que se considera "aun activo".

### Arquitectura preparada (no activada): `src/public_tokens.py`

Existe ya el modulo con la forma correcta de hacerlo, **sin tocar ningun
enlace actual y sin ninguna migracion ejecutada**:

- `generar_token()` -- 256 bits de `secrets.token_urlsafe`.
- `hash_token()` -- lo que se guarda en la base es el **hash**, nunca el
  token. Una lectura accidental de la base no entrega enlaces utilizables.
  SHA-256 a secas y no bcrypt a proposito: no es una contrasena elegida por
  una persona sino 256 bits aleatorios, contra los que la fuerza bruta no
  existe, y el hash rapido permite resolver el enlace en cada visita.
- `token_coincide()` -- compara con `hmac.compare_digest`, en tiempo
  constante, para no filtrar cuantos caracteres se acertaron.
- `huella()` -- `ab12••••••89` para logs y pantallas. Un token completo en un
  log es una credencial en un log.
- `emitir_para(record)` -- devuelve `(token_en_claro, record_con_hash)`. El
  token en claro existe una sola vez, al generarlo; despues no se recupera.
  Si se pierde, se emite otro.

Cubierto por tests: que el token viejo deje de funcionar al rotar, que el
claro nunca quede persistido, que un registro sin `public_token_hash` no
resuelva con nada, y que la huella nunca contenga el token.

**Lo que falta para activarlo es una decision de Kevin, no codigo:** que
enlaces se consideran "aun activos" y que pasa con los viejos (mantener como
alias, expirar, redirigir o desactivar).

## 12. Lecturas privilegiadas

`store.list_privileged(tabla, tenant_id=..., reason=...)` es la unica forma
autorizada de saltarse el aislamiento. `reason` es obligatorio y sin default:
obliga a justificar en el punto de uso y queda en el log de seguridad.

**Omitir la empresa NO significa "todas".** Sin `tenant_id` la llamada
levanta `ValueError`. Para leer las dos empresas hay que pedirlo de forma
explicita y deliberada:

```python
store.list_privileged('clients', scope='all_tenants', reason='...')
```

y ademas ese modo solo funciona:

- **fuera de una peticion web** (scripts de migracion, tests), o
- **dentro de una ruta administrativa autorizada**, es decir una de
  `_ADMIN_PATHS` abierta con el token de admin, que es lo unico que pone
  `g.is_admin_request`.

Desde cualquier otra ruta -- aunque haya sesion valida y aunque el codigo lo
pida -- se registra `ALL_TENANTS_BLOQUEADO` y se levanta
`TenantMismatchError`. Hay tests que fijan las dos mitades: que una ruta
normal no puede usarlo y que una administrativa si.

### Las rutas administrativas no cuelgan de "estar logueado"

Antes, `_require_login` dejaba pasar `/api/admin/*` a cualquier sesion valida
si no traia token. Ahora, sin el token esas rutas responden **404** (no 403:
no confirmamos que existan) y queda `RUTA_ADMIN_SIN_TOKEN` en el log.
`/api/admin/migrate-to-multi-tenant` -- que reescribe `tenant_id` en las dos
empresas -- entro a esa lista.

### Efecto secundario que hubo que arreglar

Cerrar el aislamiento dejo dos rutas admin leyendo `store.list()` sin cuenta
activa: devolvian `[]` y el reporte salia **vacio sin avisar**. El inventario
mostraba cero registros y el import de leads perdia su chequeo de duplicados
(habria pisado leads existentes). Las dos pasaron a `list_privileged` con
`scope='all_tenants'`, y hay tests de regresion para que no vuelva a pasar en
silencio.

Un test de arquitectura verifica que `_read_raw` no se use fuera de
`storage.py`, para que no se vuelva la forma comoda de saltarse todo.

Usos actuales, todos fuera del flujo de un usuario con sesion:

| Donde | Empresa | Justificacion |
|---|---|---|
| Integracion de galeria | explicita | servidor-a-servidor con token; filtra por empresa |
| Reporte del incidente | todas | por definicion compara entre empresas |
| Auditoria de huerfanos | todas | busca registros sin empresa |
| Inventario | todas | cuenta por empresa |
| Limpieza de workflows | explicita por empresa | itera empresa por empresa |
| Migracion a multi-cuenta | todas | necesita el archivo completo |

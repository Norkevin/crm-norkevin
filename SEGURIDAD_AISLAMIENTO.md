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

### Regla, sin excepciones

**Ningun desarrollador debe deducir de que empresa es algo leyendo el string
del id.**

```python
if 'norkevin' in tenant_id:      # NO
if tenant_id.startswith('...'):  # NO
if nombre == 'Astral':           # NO
```

La empresa se resuelve por el registro en `tenants`, nunca por como se
escribe el id. Los ids son opacos a proposito: hoy uno miente, y manana
cualquier otro puede empezar a mentir sin que nadie lo note.

Los ids **no se renombran** por ahora -- estan en tokens de Gmail en disco,
en `tenant_id` de miles de registros, y en enlaces publicos ya enviados.
Renombrarlos es una migracion, no un rename.

Esta fijado en `tests/test_credential_isolation.py`, y la advertencia esta
repetida arriba de `src/storage.py`, que es donde un desarrollador la va a
ver antes de escribir la linea equivocada.

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

### Que cuenta como enlace "activo" (decidido)

No se define por la fecha de la boda. Un recurso esta **ACTIVO** si cumple
**cualquiera** de estas nueve condiciones:

1. el job todavia no tiene `Job Complete` marcado a mano;
2. hay saldo pendiente;
3. hay una factura pendiente o parcialmente pagada;
4. el contrato sigue siendo relevante para consulta;
5. el cuestionario sigue pendiente;
6. la cotizacion no esta expirada ni rechazada definitivamente;
7. el portal del cliente sigue habilitado;
8. el enlace tuvo actividad reciente (si tenemos ese dato);
9. el job todavia tiene tareas pendientes.

Implementado en `src/public_links.py`, con tres respuestas posibles:

| Respuesta | Significa |
|---|---|
| `ACTIVO` | se cumple al menos una condicion; rotar romperia algo en uso |
| `INACTIVO` | se pudo evaluar **todo** lo que aplica y nada indica uso |
| `REVIEW_REQUIRED` | **no se pudo determinar con seguridad** |

`REVIEW_REQUIRED` no es un empate ni un "probablemente no": es la respuesta
correcta cuando falta informacion. Meterlo en `INACTIVO` convertiria "no se"
en permiso para desactivar el enlace de alguien.

Dos decisiones que caen de ahi y conviene ver escritas:

- **`Job Complete` sin marcar en ningun sentido** va a `REVIEW_REQUIRED`, no a
  inactivo. Deducirlo de la fecha es exactamente lo que se pidio no hacer.
- **`tareas_pendientes = 0` no es lo mismo que `tareas_pendientes = None`.**
  Cero es informacion; None es su ausencia. Esa distincion es la que separa
  `INACTIVO` de `REVIEW_REQUIRED` en casi todos los casos.
- Un **contrato firmado con todo lo demas cerrado** tambien va a revision: es
  el documento al que el cliente vuelve si hay un reclamo, y darlo por muerto
  es una decision legal, no tecnica.

El cruce se consulta en `GET /api/admin/public-links-audit` (dry-run, solo
lectura). Cruza dos ejes porque responden preguntas distintas:

```
forma del id  ->  que tan adivinable es      (el riesgo)
actividad     ->  si rotarlo romperia algo   (el costo)
```

Lo que hay que atender primero es la interseccion: `PREDICTABLE_LEGACY` +
`ACTIVO`, que sale en `atender_primero` con el motivo por el que sigue vivo.

---

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

La propiedad que hay que mantener:

> `usuario autenticado` **no es** `operacion administrativa global autorizada`

Son dos niveles distintos. Ser duenio de un negocio y estar logueado en el
no da acceso a operaciones que tocan los dos.

### 12.1 Mapa de capacidades administrativas

Que una ruta este protegida no puede depender de que su URL empiece con
`/api/admin/`. Cada una declara en `_ADMIN_CAPABILITIES` **que hace** y **a
que nivel opera**:

| Capacidad | Nivel | Rutas |
|---|---|---|
| `tenant_audit` | global | inventario, huerfanos, enlaces publicos, clientes SN, debug de workflow |
| `incident_report` | global | reporte del incidente |
| `workflow_cleanup` | global | limpieza de workflows, cuestionarios duplicados |
| `migration` | global | migracion multi-cuenta, reconciliacion SN, clientes secundarios |
| `data_import` | global / empresa | import de leads de Astral / import de Studio Ninja |
| `data_reset` | empresa | vaciar datos de prueba |

- **Nivel global** = cruza las dos empresas. Token de admin, nunca sesion.
- **Nivel empresa** = opera solo sobre la empresa de la sesion, igual que
  cualquier otra pantalla. Se llaman desde Settings; el aislamiento del store
  ya las limita. Subirlas a token romperia una pantalla que Kevin usa sin
  ganar nada de aislamiento.

`_ADMIN_PATHS` (lo que exige token) se **deriva** del mapa, asi que no se
pueden desincronizar. Tres tests lo sostienen:

1. toda ruta `/api/admin/` que exista en el `url_map` tiene que estar
   declarada -- una ruta nueva que nadie declaro hace fallar el test en vez
   de quedar desprotegida en silencio;
2. no hay entradas que apunten a rutas que ya no existen (dan la falsa
   impresion de proteger algo);
3. una ruta declarada de nivel empresa **no puede usar `scope='all_tenants'`**
   -- se verifica leyendo el arbol de sintaxis, no el comentario. La etiqueta
   no puede mentir.

Esto **no** es un sistema de permisos: no hay roles, ni herencia, ni base de
datos. Es una lista con tests. Cuando haga falta mas de un nivel de acceso,
el mapa de que exige que ya esta escrito.

Escribiendo estos tests aparecieron dos rutas que nadie habia clasificado:
`/api/admin/reset-test-data` (vacia leads, clientes, jobs, pagos, contratos)
e `/api/admin/import-studio-ninja`. Las dos operan solo sobre la empresa de
la sesion, y quedaron declaradas como tales.

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

---

## 13. Migracion de enlaces publicos, por etapas

Estrategia elegida: **alias temporal, no para siempre.**

### Etapa 1 - emitir el token nuevo (NO EJECUTADA)

Para cada recurso seleccionado se genera un `public_token` seguro. El enlace
nuevo pasa a ser el principal; el viejo sigue funcionando como alias.

Desde ese momento, **cualquier enlace que genere el CRM usa exclusivamente el
token seguro**: copiar enlace, enviar contrato, enviar cuestionario, mostrar
portal, y cualquier correo nuevo. El legacy queda solo por compatibilidad con
lo que ya se mando; no puede volver a ser la URL que el sistema entrega.

### Etapa 2 - registrar el uso del legacy (ACTIVA)

Cuando alguien entra por un enlace viejo queda `LEGACY_PUBLIC_LINK_USED` con
tipo de recurso, huella del enlace, empresa y fecha. Sin este dato la etapa 3
seria adivinar: no hay forma de saber cuales siguen circulando -- en correos
ya enviados, en WhatsApp, guardados por el cliente -- y cuales murieron solos.

Esto **ya esta activo** y es lo unico de las tres etapas que corre hoy.
Registrar no es romper: el enlace viejo resuelve exactamente igual que antes,
y hay un test que lo fija.

Del enlace se guarda `cont******va`, nunca el id completo. Un enlace publico
es una credencial, y una credencial completa en un log es una credencial
filtrada. La huella usa `*` y no un caracter decorativo a proposito: un
handler de logs escribiendo a una consola cp1252 se cae con
`UnicodeEncodeError`, y un log de seguridad que revienta al registrar un
evento de seguridad es peor que uno feo.

### Etapa 3 - desactivar (SIN FECHA)

El periodo es configurable con `LEGACY_LINK_ALIAS_DAYS`. **Por defecto vale
0, que significa sin limite:** ningun enlace viejo se desactiva por el mero
paso del tiempo mientras no se fije el periodo a proposito. Un default que
expirara solo seria tomar por omision la decision que se pidio no tomar.

### Como se resuelve un alias: **sin redirect** (recomendacion)

Cuando llegue un enlace viejo, la opcion obvia seria:

```
GET /contracts/contract-sn-boda-rebeca-y-jos
    -> 302 /contracts/<token-nuevo>
```

**No conviene, y la recomendacion es no hacerlo.** Ese 302 pone el token
nuevo -- que es la credencial -- en:

- el **historial** del navegador del cliente, y en su sincronizacion de
  cuentas si tiene el navegador con sesion iniciada;
- los **logs del proxy** y de cualquier CDN o balanceador en el camino;
- **analytics**, si alguna vez se agrega;
- el **`Referer`** que el navegador manda a cualquier recurso externo que
  cargue la pagina;
- lo que el cliente **copia y pega** cuando comparte "el link que me
  mandaste".

El resultado seria absurdo: la migracion existe para que la credencial deje
de ser adivinable, y el redirect la publicaria en cinco lugares nuevos.

**Propuesta: resolver el alias del lado del servidor y servir el recurso en
la misma URL vieja.** El visitante nunca ve el token nuevo; internamente se
busca el recurso, se registra `LEGACY_PUBLIC_LINK_USED` y se responde 200 con
el mismo contenido. La URL en la barra sigue siendo la vieja, que ya estaba
comprometida de todos modos -- no se gana nada exponiendola de nuevo, y no se
pierde nada dejandola.

Efecto colateral util: como el enlace viejo nunca "asciende" al nuevo, el
registro de uso sigue siendo fiel indefinidamente. Con redirect, el cliente
guardaria la URL nueva despues de la primera visita y el legacy dejaria de
aparecer en el log aunque siguiera siendo el que le mandaron -- justo el dato
que la etapa 3 necesita.

Lo unico que se pierde es que el cliente no "actualiza" su enlace guardado.
Eso es aceptable: el objetivo no es que el cliente tenga el enlace bonito,
sino que **los enlaces que el CRM genera de ahora en adelante** sean seguros.

### Estado real

Nada de la etapa 1 ni de la etapa 3 esta ejecutado. `src/public_tokens.py`
tiene la arquitectura (token de 256 bits, guardado como hash, comparado en
tiempo constante) y `src/public_links.py` la clasificacion, las dos cubiertas
con tests. **Ningun enlace fue generado, rotado ni desactivado.**

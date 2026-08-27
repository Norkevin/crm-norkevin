# CRM Norkevin / Astral — documento de traspaso

**Para:** un asistente de IA que va a seguir puliendo este CRM sin haber
visto nada del trabajo anterior.
**Fecha de corte:** 21 de agosto de 2026.
**Estado:** `CRM_OPERATIONAL` (en uso) + `CODE_SIDE_DAILY_USE_READY`.

Leé esto entero antes de tocar código. Hay decisiones que parecen raras y
tienen una razón cara detrás.

---

## 1. Qué es y para quién

Kevin Lemus es fotógrafo de bodas en Guatemala. Opera **dos empresas
independientes**, y ninguna es "la principal":

| Marca | `tenant_id` | Correo |
|---|---|---|
| **Astral Weddings** | `tenant-norkevin` | astralweddingsgt@gmail.com |
| **Norkevin Photography** | `tenant-norkevin-photography` | norkevinfoto@gmail.com |
| Ramiro Cruz Photo (tercera cuenta, poco uso) | `tenant-ramiro-cruz` | ramirocruz10x@gmail.com |

**Ojo con esto:** `tenant-norkevin` **es Astral Weddings**, no Norkevin
Photography. El id es engañoso por razones históricas. Nunca deduzcas la
marca del string del id, del nombre ni del correo — usá siempre
`src/tenant_brand_map.py → resolve_brand(tenant_id)`, que devuelve un
`BrandIdentity`. Esa función existe precisamente porque adivinar la marca
causó un incidente grave.

**Stack:** Flask + almacenamiento en archivos JSON (`src/storage.py`,
clase `JsonStore`). No hay base de datos en producción. `app.py` tiene
~11.400 líneas. 40 plantillas Jinja, 86 archivos de test.

Se ejecuta en la PC Windows de Kevin con doble clic en `abrir_crm.bat`,
sirviendo en `http://localhost:8765`. No hay despliegue en la nube activo.

---

## 2. El incidente que originó todo

En agosto de 2026 el CRM envió **cientos de correos con la marca
equivocada a los clientes de la otra empresa**. Clientes de Norkevin
Photography recibieron correos firmados como Astral Weddings.

Causas encadenadas:

1. Un hilo de fondo (scheduler de recordatorios) corría **sin cuenta
   activa** y usaba un `google_token.json` global, sin dueño.
2. Varias rutas deducían la marca por heurística en vez de por
   `tenant_id`.
3. La conversión lead→job no era idempotente: el caso "Camila Ríos"
   generó 4 jobs y 4 juegos de cuotas para la misma boda
   (sobrefacturación real).

Todo el trabajo posterior gira alrededor de que eso **no se repita**. Por
eso hay tanta paranoia con el aislamiento entre marcas y con el correo
saliente.

---

## 3. Invariantes — no los rompas

Estos no son estilo, son la razón de existir de gran parte del código.

1. **Aislamiento total entre marcas.** Ningún dato, nombre, correo,
   contador ni PDF de una marca puede verse desde la otra. Si escribís una
   consulta nueva, preguntate: ¿esto pasa por el filtro de cuenta?
2. **La marca se resuelve por `tenant_id` canónico**, jamás por heurística.
3. **Toda mejora se evalúa en las DOS marcas.** Un test parametrizado por
   marca es lo normal en este repo, no la excepción.
4. **El correo saliente está APAGADO** (STAGE 1, ver §7). No lo enciendas.
5. **Nada de dinero real.** No modifiques montos, no reconcilies pagos
   históricos, no toques contratos firmados.
6. **Nada destructivo.** No borres datos, no reseteés tablas, no toques
   secretos ni credenciales.
7. **Idempotencia.** Ninguna acción con efecto lateral (crear job, generar
   cuotas, disparar workflow, mandar correo) puede duplicarse si se
   ejecuta dos veces.

---

## 4. Arquitectura y archivos clave

### `src/storage.py` — `JsonStore`

Un archivo JSON por tabla en `data/`. El aislamiento es **por fila**: las
tablas de `TENANT_SCOPED_TABLES` filtran por el campo `tenant_id` contra
la cuenta de la sesión (`_tenant_scope()`).

```
TENANT_SCOPED_TABLES = leads, clients, jobs, quotes, payments, contracts,
    questionnaires, email_templates, packages, calendar, files, mail_log,
    job_clients, payment_schedules, pending_emails
```

Detalles importantes:

- **Sin cuenta activa no se ve nada** y no se puede escribir un archivo
  global (`MissingTenantContextError`). Esa era la puerta del incidente.
- **Registro de locks por archivo:** `_FILE_LOCKS`, indexado por
  `os.path.normcase(os.path.abspath(path))`, con `threading.RLock`
  (reentrante, no `Lock`). Cubre el ciclo completo leer→backup→escribir.
- **Escritura atómica:** archivo temporal único por proceso e hilo
  (`f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'`) + `os.replace()`
  con reintentos. El nombre `.tmp` compartido causaba `WinError 5` y
  mataba hilos en silencio.
- `PROTECTED_PATHS` impide que la poda automática de backups toque
  respaldos, evidencia o snapshots.

### `src/tenant_brand_map.py`

`resolve_brand(tenant_id) -> BrandIdentity`. Fuente única de la identidad
de marca (nombre, color, correo remitente). Documenta por evidencia por
qué `tenant-norkevin` = Astral.

### `src/conversion_registry.py`

Garantiza **exactamente un job por lead** bajo concurrencia real. SQLite
con `PRIMARY KEY` sobre `conversion_key = "tenant::lead"`. El hilo ganador
hace INSERT; los perdedores reciben `IntegrityError`, hacen polling y
devuelven el `job_id` del ganador. Validado con 20 iteraciones × 2 marcas,
5 requests simultáneos cada una, 0 errores.

Nota: `busy_timeout` se configura primero y `journal_mode=WAL` va en una
inicialización única bajo `_init_lock` — al revés daba `database is
locked` en el primer acceso.

### `src/workflow/engine.py`

**Trampa conocida:** el motor guarda TODAS las instancias en un solo dict
en memoria y en un `workflow_instances.json` **global, sin sufijo de
cuenta**. El aislamiento se logra en las rutas de `app.py`, resolviendo el
dueño por el job/lead (que sí pasó el filtro de cuenta). Ver
`_instancia_es_de_la_cuenta()` y `_workflow_instances_del_tenant()`.

Si agregás una ruta nueva que lea instancias, **filtrala**. Cuatro puertas
ya se filtraron; una quinta sin filtro reabre la fuga.

Los *templates* de workflow sí están compartidos entre cuentas **a
propósito** (ver nota en `_persist_workflow_template`): separarlos requiere
que el motor indexe por `(tenant_id, workflow_id)`, no sólo cambiar dónde
se guarda el archivo.

---

## 5. Modelo de datos canónico

### N clientes por job (tabla `job_clients`)

Antes había 3 campos fijos: `client_id`, `secondary_client_id`,
`planner_client_id`. Un cuarto cliente no cabía.

Ahora `job_clients` guarda 0..N relaciones con rol:

```python
ROL_PRINCIPAL = 'principal'
ROL_PAREJA    = 'pareja'
ROL_PLANNER   = 'wedding_planner'
ROL_CONTACTO  = 'contacto'
ROL_OTRO      = 'otro'

ROLES_DESTINATARIOS_DOCUMENTOS = (ROL_PRINCIPAL, ROL_PAREJA)
```

**Ser miembro del job ≠ ser destinatario de un documento.** El wedding
planner es miembro pero **nunca** recibe contratos. Kevin fue explícito.

Helpers (usalos, no reimplementes la lógica):

| Helper | Qué hace |
|---|---|
| `_job_client_relations(job)` | Relaciones canónicas. Si hay filas nuevas mandan ellas; si no, lee los 3 campos legacy como adapter |
| `_set_job_clients(job, relaciones, tenant_id=)` | Escribe las relaciones. Lanza `TenantMismatchError` si cruzás marcas |
| `_jobs_por_cliente(jobs=None)` | Índice cliente → jobs **en cualquier rol**. Lee `job_clients` una sola vez |
| `_job_clients(job)` / `_job_clients_display(job)` | Para pintar en pantalla |
| `_job_recipient_clients(job)` | Sólo los que reciben documentos |

### Calendarios de pago (`payment_schedules`)

`origin_key = "tenant::job::quote"`. Estados: `active`, `superseded`,
`completed`, `cancelled`, `legacy_quarantined`. **Máximo un `active` por
identidad lógica.** Reemplazar es explícito vía `supersede_schedule()`,
que nunca borra. Esto cierra la sobrefacturación del caso Camila/Daniel.

### Autoridad financiera única

`_job_payment_summary(job, job_payments)` devuelve total, pagado,
pendiente, cuotas, vencidas, próximo pago, `esta_pagado` y
`descuadre_cotizado_vs_cuotas`.

**No calcules saldos en otro lado.** Había tres fórmulas distintas y la
interfaz se contradecía. Si el total cotizado no coincide con la suma de
cuotas, eso puede ser un descuento legítimo: **se reporta, no se
corrige solo**.

### Estado del job

`_job_estado_label(job, job_payments)` devuelve `(label, tone, estado_key)`.

```python
ESTADOS_JOB_ACTIVOS   = {proxima, hoy, por_cobrar, por_cerrar, sin_fecha}
ESTADOS_JOB_COMPLETOS = {completada}
```

El estado sale de **fecha + pago**, no del avance del workflow. Una boda
futura no puede verse como "completada" aunque el workflow esté al 100%.

`_job_orden_relevancia(job)` ordena la lista de jobs. Cuidado con dos bugs
ya corregidos: `or 999` mandaba la boda de HOY al final, y los días
negativos ponían las más viejas primero.

---

## 6. Bugs corregidos (para no reintroducirlos)

| Área | Qué pasaba |
|---|---|
| Ficha de cliente | `/clients` y `/clients/<id>` sólo miraban `job.client_id` (rol principal). **La novia entra como `pareja`: su propia boda no aparecía en su ficha.** Igual el wedding planner |
| Clientes duplicados | El desempate entre dos fichas con el mismo correo prefería la que estuviera en un job *como principal*; si la buena estaba como pareja, ganaba el duplicado huérfano |
| Workflows | 4 rutas listaban el dict global del motor: Astral veía los nombres de las bodas de Norkevin |
| Workflows huérfanos | 143 instancias de datos demo apuntando a jobs borrados seguían apareciendo |
| Botón "Quitar" cliente | `onclick="…{{ x\|tojson }}"` con comillas dobles cortaba el atributo HTML: el botón no hacía nada y **no daba error visible**. Usar comillas simples: es seguro porque el `tojson` de Flask escapa la comilla simple como `'` |
| Páginas de error | `404.html` y `500.html` existían pero **nunca se mostraban**: faltaba registrar `@app.errorhandler` |
| Marca en la pestaña | 24 plantillas tenían `ASTRAL WEDDINGS CRM` a mano en el `<title>`; ahora `{{ current_tenant.name }}` |
| Resumen de pagos | Vivía dentro de la pestaña "Facturas": al cambiar de pestaña el saldo desaparecía |
| Locación contaminada | El venue del evento se copiaba a `client['address']` y volvía al revés, en bucle. Se cerraron **4 puertas** (2 en `_ensure_client_for_lead`, 1 en `api_lead_create`, 2 en el import de Studio Ninja) |
| `stats()` del motor | `by_status[k] = by_status.get(k, 0)` — nunca incrementaba, siempre daba 0 |
| Hardcodes de marca | Eliminados de la generación de PDF y de 4 rutas de correo |
| Reset destructivo | `/api/admin/reset-test-data` endurecido: exige confirmación por marca y `ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=1` |

---

## 7. Seguridad de correo — plan por etapas

- **STAGE 1 (actual):** el CRM arma los correos, los registra en `mail_log`
  y los deja **bloqueados**. Visibles y auditables, pero no se envía nada.
- **STAGE 2:** envío manual aprobado, uno por uno, desde la cola
  (`queue_email` → `approve_and_send`).
- **STAGE 3:** automatizaciones. **Nada de esto se activa todavía.**

Los kill switches viven en `abrir_crm.bat`:

```
DISABLE_OUTBOUND_EMAIL=1
OUTBOUND_EMAIL_ENABLED=0
ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=0
ALLOW_CONTROLLED_CUTOVER=0
ENABLE_REMINDER_SCHEDULER  (ausente = scheduler apagado)
```

`outbound_email_enabled()` es **fail-closed**: si la variable no está, no
manda. Además `tests/conftest.py` tiene un fixture `autouse` de sesión
(`_block_real_email_providers`) que lanza `RealProviderCallBlocked` si
algún test intenta llegar a SMTP/Resend/Gmail real. Doble defensa.

---

## 8. Infraestructura de validación

**Importante:** el entorno donde corre el asistente (sandbox Linux) **no
tiene Flask ni pytest**. La suite sólo se puede ejecutar en la máquina
Windows de Kevin. El flujo real es:

1. El asistente escribe código y tests, y valida localmente con lo que sí
   tiene: `py_compile`, análisis AST, parseo de las 40 plantillas Jinja, y
   guardas estáticas ejecutables sin pytest.
2. Kevin hace doble clic en **`run_windows_validation_launcher.bat`**.
3. Eso lanza `run_pre_cutover_validation.ps1`, que corre **14 fases** y
   escribe todo en `artifacts/pre_cutover_validation/<timestamp>/`.
4. `pre_cutover_gate.py` corre al final y emite el veredicto.

Las 14 fases:

```
regression_stabilization · tenant_isolation · daily_usage · email_safety
pdf_brand_tests · reset_endpoint_safety · idempotency · concurrency
storage_locking · concurrency_stress · migration_tests
sqlite_mount_safety · post_cutover_smoke · full_suite
```

**Regla dura:** el runner y el gate tienen que listar exactamente las
mismas 14 fases. Si agregás una al runner sin agregarla al gate, el gate
falla por desalineación. Si podés, sumá tus tests a una fase existente
(`daily_usage` es la natural para uso diario) en vez de crear fases nuevas.

Última corrida (`20260821_030147`): **13/14 PASS**, sólo `full_suite` con 3
tests fallando — los tres ya corregidos, pendiente la corrida que lo
confirme.

---

## 9. Estado actual y qué falta

### Terminado

Cutover controlado ejecutado (hay `CUTOVER_COMPLETED.marker` y tres
snapshots protegidos con manifiesto SHA-256). El CRM está operativo en
STAGE 1. El bloque estructural (N clientes, calendarios de pago, estados
de job, orden, filtros, aislamiento de workflows, páginas de error) está
cerrado y con tests en ambas marcas.

### Pendiente inmediato (mecánico)

1. Correr `run_windows_validation_launcher.bat` una vez y confirmar 14/14.
2. Reiniciar `abrir_crm.bat` — el proceso vivo arrancó antes de los últimos
   cambios y con `FLASK_DEBUG=0` no hay autoreload.
3. Cinco chequeos visuales listados en `SIGUIENTE_PASO_KEVIN.md`.

### Deuda abierta

- `data/workflow_instances.json` conserva 143 filas huérfanas en disco. Ya
  no se muestran, pero conviene podarlas.
- `settings_tenant-norkevin-photography.json` no existe: Norkevin
  Photography sin datos empresariales (teléfono, banco).
- Migración a SQLite (`schema_v5.2.sql`) validada en shadow y **diferida a
  propósito**. Los contratos no tienen tabla destino en V5.2.
- Templates de workflow compartidos entre cuentas (ver §4).

### Backlog de producto (en `POST_CUTOVER_BACKLOG.md`)

- **P2 UX:** responsive (el CRM se usa desde el teléfono en eventos),
  consistencia visual entre pantallas, y `client_detail` que está
  comparativamente vacío frente a `job_detail`.
- **P3 automatizaciones:** sólo después de STAGE 2. Recordatorios de pago
  y entrega, interfaz cómoda para la cola de aprobación, gestión de
  plantillas por marca.

---

## 10. Cómo trabajar en este repo

### Convenciones

- **Comentarios en español**, explicando *por qué*, no *qué*. El estilo del
  repo es documentar el bug concreto que motivó cada decisión. Seguilo:
  vale más que el código sea obvio dentro de seis meses.
- **Los tests se parametrizan por marca**
  (`@pytest.mark.parametrize('tenant_id', AMBAS)`).
- Los tests nuevos van a `tests/` y se suman a una fase del runner.
- Helper de tests: `login_as_tenant(client, tenant_id, email=...)` en
  `tests/conftest.py`. Fixture `auth_client` = Astral ya logueado.

### Trampas técnicas ya pagadas

- **Muchos archivos son CRLF.** Editar con herramientas que asumen LF
  corrompe el archivo. Leer en binario, decodificar, reemplazar, escribir
  en binario.
- **Jinja + `tojson` en atributos:** siempre comillas simples.
- **PowerShell:** `Out-File -Encoding utf8` mete BOM — leer con
  `utf-8-sig`. `;` no separa comandos en cmd.exe. Cualquier cosa que el
  proceso escriba a stderr se convierte en `NativeCommandError` y tumba
  la fase, aunque el test haya pasado: **imprimí veredictos a stdout**.
- **pytest en Windows:** hay que pasar `--basetemp` propio por fase; si no,
  la limpieza del symlink `pytest-current` falla con `PermissionError
  [WinError 5]` en el *teardown* y convierte fases verdes en FAIL.
- **`py_compile` no detecta nombres no importados.** Usá análisis AST para
  eso (así se encontró que `TenantMismatchError` y `uuid` faltaban a nivel
  de módulo).
- **`@app.errorhandler(Exception)`** se aplica ANTES que
  `PROPAGATE_EXCEPTIONS`. El handler re-lanza si `app.config['TESTING']`,
  porque si no un bug real llega a pytest disfrazado de 500.
- **Nunca abras un SQLite en el montaje de Windows en modo escritura.** Una
  vez truncó a 0 bytes un archivo de evidencia. El patrón correcto es
  copiar primero y abrir con `mode=ro`; hay un test de regresión
  (`test_sqlite_mount_safety.py`).

### Antes de dar algo por terminado

```
py_compile de todos los .py · AST sobre app.py · parseo de las 40
plantillas Jinja · guardas estáticas · y la corrida de Windows
```

---

## 11. Documentos del repo que vale la pena leer

| Archivo | Contenido |
|---|---|
| `SIGUIENTE_PASO_KEVIN.md` | Qué tiene que hacer Kevin ahora + los 5 chequeos visuales |
| `POST_CUTOVER_BACKLOG.md` | Backlog priorizado + todo lo cerrado el 21-ago |
| `STABILIZATION_EXECUTION_REPORT.md` | Informe de la fase de estabilización, con matriz de riesgos |
| `CONTROLLED_CUTOVER_PLAN.md` | Plan del cutover y el plan de correo por etapas |
| `ROLLBACK_PLAN.md` | Cómo volver atrás |
| `SEGURIDAD_AISLAMIENTO.md` | Modelo de aislamiento entre marcas |
| `MODELO_DE_DATOS_CRM_V5.md` | Modelo de datos (la migración está diferida) |

---

## 12. Resumen en cinco líneas

CRM de fotografía de bodas en Flask + JSON, para dos empresas que deben
permanecer completamente aisladas. Nació de un incidente de correos
cruzados entre marcas y sobrefacturación por conversiones duplicadas.
Ya está estabilizado, con cutover hecho y en uso diario con el correo
saliente apagado. Lo que queda es pulido de producto: UX, responsive, y
las automatizaciones de correo, que sólo se encienden de a una y con
supervisión.

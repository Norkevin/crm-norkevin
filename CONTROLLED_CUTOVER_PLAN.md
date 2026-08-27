# CONTROLLED_CUTOVER_PLAN.md

**Estado de este documento:** PREPARADO, NO EJECUTADO.
**Fecha de preparación:** 20 de agosto de 2026.
**Veredicto vigente del gate:** `NOT_READY_FOR_CUTOVER` (falta la validación real en Windows).

Este plan describe exactamente cómo pasar del estado actual al CRM
operativo. Nada de lo que sigue se ha ejecutado. Ningún paso de este
documento debe correrse hasta que la fase 0 pase completa.

**Las dos empresas son criterio de cutover, no una sola:**

| Tenant ID | Marca | Login |
|---|---|---|
| `tenant-norkevin` | **Astral Weddings** | astralweddingsgt@gmail.com |
| `tenant-norkevin-photography` | **Norkevin Photography** | norkevinfoto@gmail.com |

Una marca **no** se considera validada porque la otra funcione. El
cutover es exitoso únicamente si **ambas** pasan todos los criterios.
(`tenant-ramiro-cruz` existe en `tenants.json` pero está fuera del alcance
de este cutover.)

---

## FASE 0 — PRE-CUTOVER (requisitos obligatorios)

Todos deben cumplirse. **Si cualquiera falla: ABORT CUTOVER.** No hay
excepciones "menores", no se continúa "con un pendiente", no se pasa a la
fase 1 con nada en amarillo.

| # | Requisito | Cómo se verifica | Estado hoy |
|---|---|---|---|
| 0.1 | `pre_cutover_gate.py` = `READY_FOR_CONTROLLED_CUTOVER` | `python pre_cutover_gate.py --validation-dir artifacts\pre_cutover_validation\latest` → `artifacts/pre_cutover_gate_result.json` | ❌ `NOT_READY_FOR_CUTOVER` |
| 0.2 | Suite de regresión en verde | Fase `regression_stabilization` del runner, `exit_code=0` | ⏳ no ejecutada |
| 0.3 | Suite completa sin regresiones críticas | Fase `full_suite`, `exit_code=0` | ⏳ no ejecutada |
| 0.4 | Cross-tenant Astral/Norkevin en verde | Fase `tenant_isolation` + `test_post_cutover_smoke.py` (negativos de cruce) | ⏳ no ejecutada |
| 0.5 | Email safety en verde | Fase `email_safety`, `provider_calls = 0` en todo escenario bloqueado | ⏳ no ejecutada |
| 0.6 | PDF isolation en verde | Fase `pdf_brand_tests` + `pre_cutover_gate.py` → `pdf_brand_isolation.pass` | ✅ gate en verde; pytest ⏳ |
| 0.7 | Migraciones en verde (clean + legacy) | Fase `migration_tests`, ambos reportes de reconciliation | ✅ verificado en vivo |
| 0.8 | `integrity_check = ok` | `PRAGMA integrity_check` sobre ambas shadow DB | ✅ `['ok']` en ambas |
| 0.9 | `foreign_key_check = 0` | `PRAGMA foreign_key_check` sobre ambas shadow DB | ✅ 0 en ambas |
| 0.10 | `silently_dropped_records = 0` | Ambos `migration_reconciliation_report.json` | ✅ 0 en ambos |
| 0.11 | Reset destructivo endurecido | `pre_cutover_gate.py` → `reset_endpoint_hardening.pass` + fase `reset_endpoint_safety` | ✅ gate en verde; pytest ⏳ |
| 0.12 | Backup confirmado | `python tools/create_pre_cutover_snapshot.py --dry-run` → `valid: true`, `missing_required: []` | ✅ dry-run OK (639 archivos, ~14 MB) |

**Verificación mecánica de toda la fase 0 en un solo comando:**

```
python controlled_cutover.py --dry-run
```

Devuelve `DRY_RUN_OK` solo si las 11 verificaciones internas pasan; en
cualquier otro caso devuelve `DRY_RUN_BLOCKED` con la lista exacta de lo
que falló, y **no escribe absolutamente nada**. Estado actual: 9/11
(bloquean `gate_ready` y `email_flags_safe`, ambas esperadas).

### Condición de ABORT

Si cualquier requisito de la fase 0 falla:

1. **No** se avanza a la fase 1.
2. Se registra el fallo (`controlled_cutover.py` ya lo hace solo en
   `artifacts/cutover_audit_log.jsonl`, append-only).
3. Se corrige la causa, se vuelve a correr la validación completa desde
   cero — no se re-corre solo la fase que falló.
4. El gate se vuelve a ejecutar **después** de cualquier cambio de código:
   `controlled_cutover.py` rechaza un veredicto `READY` que sea más viejo
   que el último cambio en `app.py`/`src/`/`migrations/`/`schema_v5.2.sql`.

---

## FASE 1 — BACKUP PROTEGIDO INMEDIATO

**Herramienta:** `tools/create_pre_cutover_snapshot.py`
**Destino:** `protected_snapshots/pre_cutover_<YYYYMMDD_HHMMSS>/`

### Qué incluye

| Categoría | Contenido | Obligatorio |
|---|---|---|
| `data` | Directorio `data/` completo: los 28 `*.json`, `backups/`, `seeds/`, `uploads/` | Sí |
| `sqlite_db` | `data/crm.db`, `data/crm_v5_shadow.db` | No (si existen) |
| `schema` | `schema_v5.2.sql`, `schema_v5.1_BASELINE.sql`, `migrations/` | Sí (v5.2 + migrations) |
| `config` | `.env`, `.env.example`, `render.yaml`, `Procfile`, `pytest.ini` | No |
| `evidence` | `evidencia/` (evidencia preservada del incidente) | No |

Tenants, templates, workflow definitions, packages y la configuración de
email **ya están cubiertos** porque viven dentro de `data/`
(`tenants.json`, `email_templates.json`, `workflow_templates.json`,
`workflow_instances.json`, `packages.json`, `settings*.json`).

### Manifest

`MANIFEST.json` dentro del snapshot, con por cada archivo:
`source_path`, `snapshot_path`, `sha256`, `size_bytes`, `source_mtime`,
`contains_secrets`, `status`. Más un encabezado con timestamp, hostname,
totales, categorías y `valid: true|false`.

### Protección contra poda

`protected_snapshots/` está registrado en `src/storage.PROTECTED_PATHS`,
junto con el backup del 12-jul y la evidencia del incidente.
`_prune_backups()` no puede alcanzarlo bajo ninguna circunstancia — la
protección aplica al directorio raíz, así que cada snapshot nuevo queda
protegido automáticamente sin tener que registrarlo uno por uno.

### Verificación obligatoria

Tras copiar, se recalcula el SHA-256 de **cada archivo ya copiado** y se
compara contra el del origen. Si un solo archivo no coincide, el snapshot
se marca `HASH_MISMATCH`, el manifest queda `valid: false`, el script sale
con código 1, y **el cutover se aborta**. El snapshot inválido **no se
borra** — se conserva para inspección.

### Secretos

Los archivos de credenciales (`google_token*.json`,
`recurrente_credentials*.json`, `.env`) **sí se copian** — sin ellos un
rollback no restaura un CRM funcional. Pero su contenido **nunca** se
imprime ni se loguea: en el manifest sólo aparecen ruta, tamaño y hash, y
el snapshot queda marcado `contains_secrets: true` para que se trate con
el mismo cuidado que el `.env` de producción.

---

## FASE 2 — DRY RUN DEL CUTOVER

```
python controlled_cutover.py --dry-run
```

Verifica, sin escribir nada:

| Verificación | Qué comprueba |
|---|---|
| `gate_ready` | Gate = READY **y** más reciente que el último cambio de código |
| `source_files` | Existen `data/`, schema v5.2, patch de idempotencia, `app.py`, `storage.py`, `tenant_brand_map.py`, `tenants.json` |
| `backup_destination` | Directorio escribible + ≥200 MB libres |
| `shadow_db` | Ambas shadow DB existen, no están truncadas, ≥30 tablas, `integrity_check=ok`, 0 FK violations |
| `schema_version` | `schema_v5.2.sql` presente; patch de idempotencia con `origin_action_key` + índice único |
| `tenant_mappings` | Ambas marcas resuelven por `src.tenant_brand_map` con el `display_name`/`sender_email` correctos, y **no** colapsan entre sí |
| `both_brands_present` | Ambos tenants existen en `tenants.json`, `active=true`, con `login_email` |
| `expected_counts` | Los conteos del reporte de reconciliation coinciden con la shadow DB real |
| `no_unauthorized_conflicts` | `tenant_brand_conflicts = 0` (bloqueante); conflictos legacy ya clasificados no bloquean |
| `email_flags_safe` | `DISABLE_OUTBOUND_EMAIL=1`, ninguna flag de envío real activa |
| `destructive_admin_disabled` | `ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS` apagada |

Si algo no cuadra, sale sin escribir. Cualquier verificación que no se
pueda comprobar cuenta como **fallida**, nunca se asume en verde.

> **Nota técnica:** el script nunca abre un `.db` in-place sobre un
> volumen montado — copia a un temporal local y abre en modo `ro`. Esto no
> es paranoia teórica: durante la preparación de este paquete, abrir
> SQLite directamente sobre la ruta montada truncó
> `artifacts/shadow_legacy_20260712.db` a 0 bytes (se pudo regenerar por
> ser un artefacto derivado).

---

## FASE 3 — EJECUCIÓN (cinco guardias)

```
python controlled_cutover.py --execute --environment production --confirm CONFIRM_CONTROLLED_CUTOVER_<YYYYMMDD_HHMM>
```

**Las cinco deben cumplirse. Ninguna sola alcanza.**

| # | Guardia | Detalle |
|---|---|---|
| 1 | Gate READY | `artifacts/pre_cutover_gate_result.json` = `READY_FOR_CONTROLLED_CUTOVER`, y más reciente que el código |
| 2 | Flag de entorno | `ALLOW_CONTROLLED_CUTOVER=1` — ausente por defecto |
| 3 | Confirmación con timestamp | `CONFIRM_CONTROLLED_CUTOVER_<YYYYMMDD_HHMM>`, válida sólo **15 minutos**. Un string fijo copiado de documentación vieja, de un historial de shell o de un script guardado **no** autoriza nada |
| 4 | Snapshot creado y verificado | Se crea durante `--execute`; si falla o algún hash no coincide, se aborta sin tocar datos |
| 5 | Entorno declarado | `--environment {production,staging}` explícito, nunca se adivina |

Todo intento — autorizado, rechazado o abortado — se registra en
`artifacts/cutover_audit_log.jsonl` (append-only).

**Verificado en pruebas de este paquete:** con `ALLOW_CONTROLLED_CUTOVER=1`,
`DISABLE_OUTBOUND_EMAIL=1`, `--environment staging` y un token de
confirmación válido recién generado, `--execute` **sigue siendo rechazado**
por el gate (`EXECUTE_REFUSED`, `failed_checks: ['gate_ready']`,
`wrote_anything: false`). Ningún snapshot fue creado. El comportamiento
fail-closed está confirmado, no supuesto.

### Estado de los pasos de escritura

Los pasos de escritura del cutover están **deliberadamente no
implementados** (`EXECUTE_STOPPED_AT_UNIMPLEMENTED_STEP`). Razón: el
alcance exacto — si se hace cutover a SQLite V5.2 o se difiere y se opera
sobre JSON store endurecido — depende del resultado de la validación real
en Windows. Dejar un stub explícito es más seguro que dejar código de
escritura a medio hacer que alguien pueda disparar por accidente.

---

## FASE 4 — SMOKE TEST POST-CUTOVER

**Suite:** `tests/test_post_cutover_smoke.py`
**En el runner:** fase `post_cutover_smoke`

### Recorrido completo, ejecutado DOS VECES (una por marca)

| Paso | Verificación |
|---|---|
| 1-2 | Login + dashboard cargan sin 500, y el dashboard **no** menciona la otra marca |
| 3 | Lead sintético creado (prefijo `SMOKE_TEST_`) |
| 4 | Cliente creado |
| 5 | Quote creada (Q15,000, 3 cuotas) |
| 6 | `accept-quote` convierte el lead |
| 7 | Job existe, con el `tenant_id` correcto, ligado al lead |
| 8 | Exactamente **una** `workflow_instance` de producción |
| 9 | Payment schedule: 3 cuotas que suman el total exacto, todas con el tenant correcto |
| 10-12 | Contrato + PDF generados; branding de **esta** marca, y la otra marca no aparece en los términos |
| 13 | Correo preparado con proveedor **MOCK/BLOQUEADO**: `provider_calls = 0`, resultado `blocked` |
| — | Repetir la conversión no duplica job ni workflow (idempotencia post-cutover) |

### Negativos cross-tenant (en ambas direcciones)

- Recurso de la otra marca no aparece en listados (`clients`, `jobs`, `payments`, `contracts`)
- Acceso directo por URL a `job_id`/`client_id` ajeno → nunca 200
- Crear job en mi marca con `client_id` de la otra → rechazado
- Dos clientes con el **mismo email**, uno por marca → la identidad se
  resuelve por `(tenant_id, client_id)`, nunca por email

### Limpieza de datos sintéticos

Todos los registros llevan prefijo `SMOKE_TEST_` y `es_dato_sintetico: True`.
En el tempdir de pytest se destruyen solos al terminar la sesión.

**No existe hoy un mecanismo de borrado seguro por registro** —
`/api/admin/reset-test-data` borra **tablas completas**, no filas
individuales, así que usarlo para limpiar smoke tests sería destructivo.
Por lo tanto: si estos tests se corrieran alguna vez contra un entorno
real, los datos se **conservan marcados como TEST** y se limpian a mano
después. Nunca con el endpoint destructivo.

---

## FASE 5 — CORREO EN TRES ETAPAS

**No hay un switch que habilite todo de golpe.** Cada etapa requiere que
la anterior haya sido validada.

### STAGE 1 — CRM operativo, correo apagado

```
OUTBOUND_EMAIL_ENABLED=false
DISABLE_OUTBOUND_EMAIL=1
```

Permitido: leads, clients, jobs, quotes, payments, contracts, workflows.
**Ningún correo real sale**, ni manual ni automático. Los correos se
arman, se registran en `mail_log` y quedan en estado bloqueado — visibles
y auditables, pero no enviados.

**Criterio para pasar a STAGE 2:** smoke test de fase 4 en verde para
**ambas** marcas, incluidos los negativos cross-tenant.

### STAGE 2 — Envío manual/aprobado

Se habilita el envío **sólo** por la cola de aprobación
(`queue_email` → revisión humana → `approve_and_send`). Cada correo se
mira antes de salir. Las **automatizaciones siguen apagadas**: nada de
recordatorios automáticos, nada de disparo por fecha de workflow, nada de
hilos en segundo plano.

**Criterio para pasar a STAGE 3:** un período de operación real con envío
manual sin incidentes — sin correo a destinatario equivocado, sin marca
cruzada, sin duplicados.

### STAGE 3 — Automatizaciones, una por una

Cada automatización se activa **individualmente**, se observa, y sólo
entonces se considera la siguiente. Orden sugerido de menor a mayor
riesgo:

1. Cuestionario post-conversión (destinatario único, disparo puntual)
2. Recordatorio de pago (destinatario único, recurrente)
3. Emails de workflow por fecha (el que causó el incidente de agosto)

Nunca se activan dos en la misma ventana de observación.

---

## FASE 6 — OBSERVABILIDAD, PRIMERAS 24 HORAS

Sin infraestructura nueva: todo sale de los logs y archivos que ya
existen (`logs/`, `data/mail_log.json`, `artifacts/cutover_audit_log.jsonl`,
eventos de `log_security_event`).

| # | Qué vigilar | Dónde | Umbral de alarma |
|---|---|---|---|
| 1 | Errores 500 | `logs/` | Cualquiera → investigar |
| 2 | Jobs duplicados | `jobs.json`: >1 job por `lead_id` | Cualquiera → investigar |
| 3 | Workflows duplicados | `workflow_instances.json`: >1 activo por `subject_id` | Cualquiera |
| 4 | Payment schedules duplicados | `payments.json`: >1 juego de cuotas por `(job_id, quote_id)` | Cualquiera |
| 5 | Bloqueos cross-tenant | `log_security_event` → `TENANT_MISMATCH` / accesos denegados | >0 → revisar si es ataque o bug |
| 6 | Correos bloqueados | `mail_log.json` estado `BLOQUEADO` | Esperado en STAGE 1; revisar motivos |
| 7 | Correos fallidos | `mail_log.json` estado `FALLO` | Cualquiera → revisar antes de reintentar |
| 8 | Integridad de DB | `PRAGMA integrity_check` + `foreign_key_check` | Distinto de `ok`/0 → rollback |
| 9 | Eventos de auditoría | `log_security_event`, `cutover_audit_log.jsonl` | Revisar el log completo 1x al final del día |
| 10 | Intentos al reset endpoint | `RESET_TEST_DATA_*` en eventos de seguridad | **Cualquiera** → investigar de inmediato |

**Cadencia sugerida:** revisión a la hora 1, hora 4, hora 12 y hora 24.
Los puntos 2, 3, 4 y 8 son los que disparan rollback; el resto son señales
para investigar.

---

## RESUMEN DEL ESTADO

| Componente del paquete | Estado |
|---|---|
| `CONTROLLED_CUTOVER_PLAN.md` | ✅ preparado (este documento) |
| `tools/create_pre_cutover_snapshot.py` | ✅ preparado, dry-run verificado |
| `controlled_cutover.py` | ✅ preparado, dry-run + guardias verificados |
| `ROLLBACK_PLAN.md` | ✅ preparado |
| `tests/test_post_cutover_smoke.py` | ✅ preparado |
| `POST_CUTOVER_BACKLOG.md` | ✅ preparado |
| **Ejecución del cutover** | ❌ **NO ejecutado — y no debe ejecutarse todavía** |

**`CUTOVER_PACKAGE_PREPARED = true`**
**Veredicto vigente: `NOT_READY_FOR_CUTOVER`** hasta que la validación
real en Windows pase.

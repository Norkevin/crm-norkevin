# ROLLBACK_PLAN.md

**Estado:** PREPARADO, NO EJECUTADO.
**Fecha de preparación:** 20 de agosto de 2026.

Qué hacer si, después del cutover, algo sale mal. Este documento asume
que existe un snapshot pre-cutover válido en
`protected_snapshots/pre_cutover_<timestamp>/` con su `MANIFEST.json`
verificado por hash. **Si ese snapshot no existe o está marcado
`valid: false`, no hay rollback posible — y por eso el cutover nunca debe
ejecutarse sin él** (guardia 4 de `controlled_cutover.py`).

---

## PRINCIPIO RECTOR

**Detener antes de arreglar. Preservar antes de restaurar.**

El primer instinto ante un fallo es "arreglarlo rápido". Eso es
exactamente lo que convierte un problema recuperable en pérdida de datos:
cada escritura adicional sobre un estado roto lo aleja del estado
restaurable, y borra la evidencia de qué lo rompió. El orden de abajo no
es negociable.

---

## PROCEDIMIENTO DE ROLLBACK (orden estricto)

### Paso 1 — DETENER LAS ESCRITURAS (primero, siempre)

Antes de diagnosticar nada:

1. Detener el proceso del CRM (Gunicorn/Flask). No "ponerlo en
   mantenimiento" — detenerlo.
2. Confirmar que ningún proceso en segundo plano sigue vivo: el hilo de
   recordatorios y el scheduler de workflows escriben aunque nadie use la
   interfaz.
3. Confirmar que no hay una segunda instancia corriendo (Render, local,
   otra terminal).

**Motivo:** con `JsonStore`, cada request que entra reescribe archivos
completos. Un minuto de tráfico sobre un estado roto puede propagar el
daño a tablas que todavía estaban sanas.

### Paso 2 — MANTENER EL CORREO APAGADO

```
DISABLE_OUTBOUND_EMAIL=1
OUTBOUND_EMAIL_ENABLED=false
```

**Verificar que siguen así — no asumirlo.** Durante un rollback, el
riesgo de correo equivocado sube, no baja: estados a medio restaurar
pueden disparar recordatorios, reintentos de cola o correos de workflow
con datos inconsistentes. El incidente de agosto de 2026 (cientos de
correos con la marca equivocada, a los clientes de la otra empresa) nació
de un hilo en segundo plano que nadie estaba mirando.

**Nunca** se reactiva el correo "para avisarle a los clientes" durante un
rollback.

### Paso 3 — PRESERVAR LA EVIDENCIA DEL ESTADO FALLIDO

**Antes de restaurar nada**, copiar el estado roto completo:

```
protected_snapshots/failed_state_<YYYYMMDD_HHMMSS>/
```

Debe incluir exactamente lo mismo que el snapshot pre-cutover (`data/`
completo, DB SQLite, config, evidencia) **más**:

- `logs/` completo
- `artifacts/cutover_audit_log.jsonl`
- La salida de consola / logs del proceso al momento del fallo
- Una nota en texto plano describiendo el síntoma observado y la hora

`protected_snapshots/` está en `PROTECTED_PATHS`, así que esta copia
queda protegida de poda automática igual que el snapshot pre-cutover.

**La base de datos fallida NO se destruye.** Se conserva íntegra para
análisis. Si el rollback necesita el nombre de archivo original, se
renombra (`crm.db` → `crm.db.failed_<timestamp>`), **nunca se borra ni se
sobrescribe.**

### Paso 4 — VERIFICAR EL SNAPSHOT PRE-CUTOVER ANTES DE USARLO

```
python tools/verify_snapshot.py protected_snapshots/pre_cutover_<timestamp>/
```

(o, manualmente: recalcular el SHA-256 de cada archivo del snapshot y
compararlo contra el `sha256` de su entrada en `MANIFEST.json`.)

- `MANIFEST.json` debe decir `valid: true`
- Cada archivo debe coincidir con su hash
- `missing_required` debe estar vacío

**Si un solo hash no coincide: DETENERSE.** Restaurar desde un snapshot
corrupto produce un estado peor que el fallido, y encima destruye el
estado fallido como referencia. En ese caso, escalar y decidir a mano —
con el estado fallido ya preservado en el paso 3.

### Paso 5 — RESTAURAR

Sólo después de los pasos 1-4:

1. Restaurar `data/` completo desde el snapshot (copiar, no mover — el
   snapshot se conserva intacto).
2. Restaurar los `.db` si el cutover los había modificado.
3. Restaurar la configuración (`.env` y demás) **sólo si** el cutover la
   cambió. Si no, no tocarla.
4. **No** restaurar `logs/` ni `artifacts/` — son el registro de lo que
   pasó, y sobrescribirlos borra la traza del incidente.

### Paso 6 — VERIFICAR HASHES POST-RESTAURACIÓN

Recalcular el SHA-256 de cada archivo **ya restaurado** y compararlo
contra el `MANIFEST.json`. La restauración no se da por buena hasta que
todos coincidan.

### Paso 7 — INTEGRITY CHECK

```
PRAGMA integrity_check;     -- debe devolver: ok
PRAGMA foreign_key_check;   -- debe devolver: 0 filas
```

Sobre cualquier `.db` restaurado. Si el store es JSON, el equivalente es
verificar que los 28 archivos parsean como JSON válido y que los conteos
por tabla coinciden con los del manifest.

### Paso 8 — ARRANCAR EN MODO SEGURO Y VALIDAR

1. Arrancar el CRM **con el correo apagado** (STAGE 1 del plan de correo).
2. Correr el smoke test:
   `python -m pytest tests/test_post_cutover_smoke.py -v`
3. Verificar login y dashboard **de las dos marcas** manualmente.
4. Sólo entonces considerar el rollback completo.

---

## MATRIZ DE SÍNTOMAS → ACCIÓN

Todos los síntomas siguen el mismo procedimiento de 8 pasos. Lo que cambia
es la urgencia y qué inspeccionar en el paso 3 antes de restaurar.

| Síntoma | Gravedad | Rollback | Qué preservar/inspeccionar antes |
|---|---|---|---|
| **Login roto** (nadie entra) | Alta | Sí, inmediato | `tenants.json` (¿`login_email` correctos? ¿`active`?), `ALLOWED_LOGIN_EMAILS`, `google_token*.json` |
| **Jobs no visibles** | Alta | Sí | ¿Los jobs existen en `jobs.json` pero no se listan (bug de scope de tenant), o desaparecieron del archivo (pérdida real)? La distinción cambia todo |
| **Tenants cruzados** (una marca ve datos de la otra) | **Crítica** | **Sí, inmediato** | Preservar TODO antes de tocar nada. Es el fallo más grave del sistema: `log_security_event`, `mail_log.json`, y qué registros exactos se cruzaron. **Verificar si salió algún correo** aunque el kill switch estuviera puesto |
| **Pagos incorrectos** | **Crítica** | Sí | `payments.json` completo + el snapshot pre-cutover para diff. **Nunca** "corregir" montos a mano antes de preservar: es dinero real de clientes reales |
| **Contratos incorrectos** | **Crítica** | Sí | `contracts.json` + PDFs generados. Si un contrato incorrecto **ya se envió a un cliente**, eso no se arregla con rollback — preservar la evidencia y escalar como asunto legal/comercial, no técnico |
| **Workflow roto** (pasos no avanzan / se disparan mal) | Media-Alta | Sí si dispara efectos (correos, tareas); si sólo es visual, se puede diagnosticar en caliente con el correo apagado | `workflow_instances.json`, `workflow_history.json`, y **verificar si disparó correos** |
| **Email bloqueado incorrectamente** (se bloquea lo que debería salir) | Baja | **No** | Esto es el sistema funcionando de más, no de menos. Diagnosticar en caliente. **Nunca** se resuelve desactivando el kill switch |
| **Pérdida de relaciones** (jobs sin lead, pagos sin job, contratos huérfanos) | Alta | Sí | Correr la matriz de relaciones contra el estado fallido **y** contra el snapshot, y comparar. Documentado en `STABILIZATION_EXECUTION_REPORT.md` |

---

## LO QUE NUNCA SE HACE DURANTE UN ROLLBACK

- ❌ Reactivar el correo saliente "para avisar a los clientes"
- ❌ Borrar o sobrescribir la base de datos / los archivos fallidos
- ❌ Usar `/api/admin/reset-test-data` para "limpiar y empezar de nuevo"
  — borra tablas completas y destruye la evidencia
- ❌ Corregir montos, contratos o relaciones a mano antes de preservar el
  estado fallido
- ❌ Restaurar desde un snapshot cuyo hash no verifica
- ❌ Restaurar `logs/` o `artifacts/` sobre los actuales
- ❌ Saltarse el paso 1 (detener escrituras) porque "es rápido"

---

## DESPUÉS DEL ROLLBACK

1. El sistema queda en STAGE 1 (correo apagado), sin excepción.
2. El estado fallido queda preservado en
   `protected_snapshots/failed_state_<timestamp>/` — no se borra hasta que
   la causa raíz esté entendida y documentada.
3. Se documenta en `STABILIZATION_EXECUTION_REPORT.md`: qué falló, qué se
   restauró, qué hashes se verificaron, qué quedó sin explicar.
4. **No se reintenta el cutover** hasta que la causa raíz esté corregida y
   `pre_cutover_gate.py` vuelva a dar `READY_FOR_CONTROLLED_CUTOVER` con
   evidencia nueva — no con la evidencia del intento anterior.

# Fase de estabilización — reporte para Kevin

**Fecha:** agosto 2026. **Autor:** Claude (Cowork), sin ejecución de código en esta sesión (sandbox de shell no disponible). Todo lo de aquí fue preparado por lectura/edición directa de archivos; **nada fue corrido ni verificado por mí**. Instrucciones de ejecución para Codex al final.

**Hallazgo importante de arranque:** el repo ya tenía, antes de esta fase, una capa de seguridad sustancial construida (`SEGURIDAD_AISLAMIENTO.md`, `src/storage.py` fail-closed, `src/mail_tracker.py` con cola de aprobación y `check_same_tenant`, `DISABLE_OUTBOUND_EMAIL`, 144 archivos de test). Este reporte distingue explícitamente **qué ya existía** de **qué se agregó ahora**, para no hacerte creer que todo esto se escribió desde cero.

---

## 1. Causa raíz confirmada

### 1.1 Duplicación de jobs (Camila Rios)

Hay **dos puertas** para convertir un lead en job, y solo una tenía guardia de idempotencia:

- `/api/leads/<id>/accept-quote` (`app.py:3396`) → `_convert_lead_to_job` → `_ensure_job_for_lead` (`app.py:840`). **Sí** llamaba `_find_job_for_lead(lead)` antes de crear.
- `/api/jobs/new` (`app.py:5342`, usada por el flujo Pick & Choose) **no llamaba a nada equivalente**. Creaba un `boda-<uuid>` nuevo en cada request, y si venía `lead_id`, sobrescribía `lead['converted_to_job']` sin comprobar si ya existía uno.

El timeline de Camila Rios (4 jobs entre el 10 y 11 de julio, cada uno con su propio `workflow_instance`) coincide exactamente con la segunda puerta siendo invocada repetidamente. El parche de idempotencia de julio (mencionado en `AUDITORIA_CRM_ACTUAL.md`) solo cubrió la primera puerta.

### 1.2 Identidad cross-tenant: 3 hardcodes de `'ASTRAL WEDDINGS'`

Encontrados por lectura directa de `app.py` (no por los datos, por el código):

- `app.py:882` (ahora `896`) — `_ensure_job_for_lead`, el campo `empresa` de cada job nuevo.
- `app.py:1199` — el título de la notificación de "nuevo lead" en el dashboard.
- `app.py:5374` (ruta `/api/jobs/new`) y `app.py:4262` (import de Studio Ninja) — mismo campo `empresa`.

Los 4 quedaron corregidos (ver sección 3). **Grep completo de otros strings de marca hardcodeados** (pedido explícito de Kevin) — quedan sin tocar, listados para que decidas si ameritan otra pasada:

```
app.py:2:      """CRM Astral Weddings - Backend Flask
app.py:493:    '%company_name%': 'ASTRAL WEDDINGS',
app.py:1298:   'Marca': ... or 'ASTRAL WEDDINGS'
app.py:1308-1313: cuentas/reglas de ejemplo con 'Marca': 'ASTRAL WEDDINGS'
app.py:1324:   Notas: company.get('name', 'ASTRAL WEDDINGS Guatemala')
app.py:1701,1772,1879: comentarios y 'source': 'Astral Weddings'
app.py:2189:   {'id': 'tenant-norkevin', 'name': 'ASTRAL WEDDINGS', ...}  <- fallback de tenant, ver 1.3
app.py:2943:   workflow_name = 'BODAS ASTRAL WEDDINGS'
app.py:3184,5497,7717,7872,7885,7889,9372,9377,9884,9889: asuntos/cuerpos de correo
app.py:8135:   Notion EMPRESA select hardcodeado
app.py:8494,8531: 'marca': data.get('Marca', 'ASTRAL WEDDINGS')
app.py:8896:   PRODID del .ics
app.py:10081,10131,10132: recomendación de conflicto de fecha ("Astral Films")
app.py:10371:  log de arranque
```

**No los toqué.** Los que están en `app.py:2189` (fallback de tenant en algún lookup) y en asuntos/cuerpos de correo (`7717`-`9889`) son los que más importan para la fase de correo — recomiendo que sean el siguiente foco, usando `src/tenant_brand_map.py` igual que hice con los 3 que sí corregí. Los decorativos (comentarios, docstring del archivo) no son urgentes.

### 1.3 El id `tenant-norkevin` es, en realidad, Astral

Esto **ya estaba documentado y testeado** antes de esta fase (`SEGURIDAD_AISLAMIENTO.md`, `tests/test_credential_isolation.py::test_el_id_tenant_norkevin_es_en_realidad_astral`). Lo confirmé independientemente con evidencia externa (Gmail, no el código): el remitente real del incidente del 16 de agosto es `astralweddingsgt@gmail.com`, y coincide exactamente con `login_email` de `tenant-norkevin` en `_MULTI_TENANT_REAL_TENANTS` (`app.py:4392`). Mapeo canónico completo en la sección 2.

---

## 2. Mapeo canónico de tenant (`src/tenant_brand_map.py`, nuevo)

| `internal_tenant_id` | `brand_key` | `display_name` | `sender_email` | Evidencia |
|---|---|---|---|---|
| `tenant-norkevin` | `astral` | Astral Weddings | `astralweddingsgt@gmail.com` | 100% de los registros reales actuales tienen este tenant_id; Gmail confirmado; leads reales del incidente aparecen en campaña de Astral de 2025 |
| `tenant-norkevin-photography` | `norkevin` | Norkevin Photography | `norkevinfoto@gmail.com` | Id preparado en `_MULTI_TENANT_REAL_TENANTS`, migración **no ejecutada**, sin datos reales todavía (esperado) |
| `tenant-ramiro-cruz` | `ramiro-cruz` | Ramiro Cruz Photo | `ramirocruz10x@gmail.com` | Mismo caso — id preparado, sin datos |
| `tenant-astral` | **sin resolver** | — | — | Stub legado (`name='ASTRAL FILMS'` en tenants.json), **cero registros reales** en ningún JSON. No se asumió que fuera Norkevin — `resolve_brand()` lanza error si algo intenta usarlo |

`resolve_brand()` nunca cae a un default silencioso: si el `tenant_id` no tiene marca confirmada, lanza `UnresolvedBrandError`. Esto es a propósito — un default silencioso es exactamente la clase de bug que causó el incidente.

**Dato nuevo, no obvio:** la migración `/api/admin/migrate-to-multi-tenant` (`app.py:4412`) ya existe, ya tiene el mapeo correcto, y **nunca se ejecutó** (confirmé que `tenants.json` sigue con los 2 stubs viejos, no los 3 tenants reales). No la ejecuté — es una decisión tuya, y toca `google_token.json` legado.

---

## 3. Archivos modificados (diff lógico)

| Archivo | Cambio |
|---|---|
| `src/tenant_brand_map.py` | **Nuevo.** Única fuente de verdad tenant_id → marca. `resolve_brand()`, `display_name_for_tenant()`, `sender_email_for_tenant()`, `is_connection_owned_by_tenant()`. |
| `app.py` | Import de `tenant_brand_map`. `/api/jobs/new`: guardia de idempotencia (busca job existente por `lead_id` antes de crear; devuelve `already_converted` si ya existe). 3 hardcodes `'ASTRAL WEDDINGS'` → `_brand_display_name_for_tenant(tenant_id)`. |
| `src/email_delivery.py` | Nueva función `outbound_email_enabled()`: **`OUTBOUND_EMAIL_ENABLED` debe estar en `'1'` explícito para enviar** (antes: enviaba por defecto salvo `DISABLE_OUTBOUND_EMAIL=1`, fail-open). `DISABLE_OUTBOUND_EMAIL=1` se conserva igual. |
| `src/mail_tracker.py` | `idempotency_key` en `queue_email()` y `log_email()`. Un `idempotency_key` con estado `ENVIADO` nunca vuelve a salir — se devuelve el registro existente. `approve_and_send()` propaga la key. `retry_failed()` ya solo operaba sobre `FALLO` (esto **ya existía**, lo dejé igual). |
| `quarantine_camila_daniel.py` | **Nuevo.** Solo lectura de `data/*.json`; escribe reporte + patch propuesto en `data/quarantine_review/`. No modifica nada de producción. |
| `migrate_json_to_v5_shadow.py` | **Nuevo.** Migración shadow a `data/crm_v5_shadow.db` (archivo nuevo). Nunca toca `data/crm.db` ni `data/*.json`. |
| `migrations/idempotency_patch_v5.2.sql` | **Nuevo, propuesto, no aplicado.** `origin_action_key` + `UNIQUE INDEX` parcial + trigger de inmutabilidad sobre `projects`. |
| `tests/test_stabilization_phase_regression.py` | **Nuevo.** Ver sección 6. |

---

## 4. Registros de Camila Rios y Daniel Dubuc encontrados (before)

Generado por `quarantine_camila_daniel.py` — **este script no se ejecutó** (no tengo Python en esta sesión), así que los datos de abajo son los que ya había extraído manualmente por lectura de archivo durante la auditoría, y coinciden con lo que el script debería reproducir al correr.

### Camila Rios

| Job ID | Workflow instance | En `jobs.json` hoy | Contrato | Propuesta |
|---|---|---|---|---|
| `boda-69f508a1` | `wi_c8aea974` | No | — | `quarantined_superseded` |
| `boda-1d62d5e2` | `wi_1f814700` | No | `contract-c1cfd9e3`, `contract-39404f47` | `quarantined_superseded`; contratos → `requires_manual_contract_reconciliation` (**no reapuntados**, por instrucción explícita) |
| `boda-35bd38a1` | `wi_49fb933d` | No | — | `quarantined_superseded` |
| `boda-e8b7e2a7` | `wi_09a9b8d1` | **Sí** | `contract-f2b491e4` | Canónico provisional |

Pagos del canónico: `pay-da08e486` (Q8,750, **Pagado**, ligado a `quote-camila-rios` vieja) + `pay-916cbc01` (pendiente) vs. `pay-0a7eebd9`/`pay-84f7d152` (Q14,750 c/u, pendientes, ligados a `quote-47238c5c`, la aceptada). **Diferencia pendiente potencial: Q29,500 − Q8,750 = Q20,750**, sin contar los Q8,750 ya cobrados bajo la cotización vieja. Marcado `requires_manual_financial_reconciliation` — no se movió nada.

### Daniel Dubuc

Sin duplicado de job. Dos calendarios de pago sobre `job-daniel-paola`: legacy (`pay-daniel-1/2/3`, Q14,500, Q9,750 ya cobrados) vs. `quote-8efbddb9` aceptada (`pay-efe93655`/`pay-27f94291`, Q17,500, nada cobrado todavía). **Sobrefacturación potencial si no se reconcilia: Q14,500 + Q17,500 − Q17,500 = Q14,500.** Marcado `requires_manual_financial_reconciliation`.

---

## 5. Migración shadow — estado y limitación honesta

El script cubre: `tenants`/`companies` (vía mapeo canónico), `clients`, `projects` (fusión lead+job, arquitectura V5.2 ya decidida), `quotes`, `invoices` + `payment_installments` + `payment_transactions`, y un intento de `workflow_instances`.

**No cubre todavía** (reportado explícitamente como `unmapped_entities`, no adivinado): `contracts.json`, `team.json`, `calendar.json`, `email_templates.json`, `packages.json`, `settings.json`, `mail_log.json`, `mail_outbox.json`. No encontré una tabla `contracts` en `schema_v5.2.sql` — puede que exista bajo otro nombre que no until confirmé sin ejecutar `verify_v5_consistency.py`; hace falta que Codex lo revise antes de asumir que falta.

**Dos problemas reales que el script deja documentados en vez de ocultar** (ambos harían fallar el `INSERT` si Codex lo corre tal cual):

1. `quotes`/`invoices` requieren `billing_project_client_id` (FK a `project_clients`), y el script no crea filas en `project_clients` todavía — falta decidir `is_primary`/`is_billing_contact` por proyecto, dato que no está explícito en los JSON actuales.
2. `workflow_instances` requiere `template_version_id NOT NULL`, y los JSON legados no tienen ese id — no inserté nada en esa tabla, solo dejé el registro en `legacy_record_map` como `review_needed` con el conflicto anotado.

Esto significa: **la primera corrida de `migrate_json_to_v5_shadow.py` casi seguro va a fallar o a saltarse quotes/invoices/workflow_instances** hasta que se resuelvan esos dos puntos. No es un script "listo para producción" — es un primer paso honesto que aísla exactamente dónde falta información, tal como pediste ("si existe una discrepancia que el script no sabe resolver con certeza, debe reportarla, no adivinar").

---

## 6. Suite de tests nueva (`tests/test_stabilization_phase_regression.py`)

18 tests nuevos, sin enviar correo real (mismo patrón de mocking que `conftest.py`):

- Duplicación de job: secuencial (5x), segunda llamada devuelve `already_converted`, **5 requests concurrentes con threads reales** (documentado que el límite aceptado en esta fase es ≤2, no 1 — la garantía de 1 solo llega con el constraint de base de datos de `idempotency_patch_v5.2.sql`, que no está aplicado).
- `idempotency_key`: no reenvío tras `ENVIADO`, sí envío con key distinta, bloqueo también en la cola de aprobación.
- Retry: no funciona sobre `BLOQUEADO`, no funciona sobre `ENVIADO` (nuevos), sí funciona sobre `FALLO` (este último ya estaba cubierto de facto por el diseño existente, lo dejé explícito).
- Matriz de tenant: mismo email en ambas marcas, mismo nombre en ambas marcas, `client_id` correcto + email de otra marca (no bloquea, sí avisa — comportamiento ya existente, fijado en test), `tenant_id` inexistente, `email_connection_id` inexistente, conexión Astral no sirve para Norkevin, hardcode de `empresa` no reaparece.

**Puede haber solapamiento** con `test_manual_retry_and_audit.py` y `test_admin_capabilities.py` (los descubrí después de escribir estos tests) — pedirle a Codex que corra `pytest --collect-only` primero para detectar nombres duplicados antes de la corrida completa.

---

## 7. Instrucciones exactas para Codex

```bash
cd crm_norkevin

# 1. Verificar que nada de lo nuevo rompe la colección de tests
python -m pytest --collect-only tests/test_stabilization_phase_regression.py

# 2. Suite completa (incluye los 144 tests preexistentes + los nuevos)
python -m pytest tests/ -v 2>&1 | tee /tmp/pytest_output.txt

# 3. Quarantine report (solo lectura, no toca data/*.json)
python quarantine_camila_daniel.py

# 4. Migracion shadow -- probablemente falle en quotes/invoices/workflow_instances
#    por los 2 problemas de la seccion 5. Correrla igual: el reporte de lo que
#    SI funciono y lo que fallo es el dato que necesito.
python migrate_json_to_v5_shadow.py

# 5. Pegar de vuelta:
#    - output completo de pytest (pass/fail por test, no solo el resumen)
#    - data/quarantine_review/camila_daniel_report.md
#    - data/quarantine_review/migration_reconciliation_report.md
#    - cualquier traceback de migrate_json_to_v5_shadow.py
```

**No hacer:** no correr `/api/admin/migrate-to-multi-tenant` con `dry_run=false`, no aplicar `camila_daniel_proposed_patch.json`, no borrar `data/google_token.json`, no tocar `data/crm.db` real, no desplegar.

---

## 8. Qué debería devolver Codex

1. Resultado pytest completo — cuántos pasan/fallan de los 144 + 18 nuevos, y el traceback exacto de cualquier fallo (sobre todo si algún test viejo se rompe por los cambios en `mail_tracker.py`/`email_delivery.py`).
2. Si el test de concurrencia (`test_cinco_requests_concurrentes_mismo_lead_un_solo_job`) da más de 2 jobs — eso significaría que el guardia de aplicación no alcanza ni para el caso débil, y hay que mirarlo antes de seguir.
3. El reconciliation report de la migración shadow, con los errores reales (no los que yo anticipé — los que de verdad tira SQLite).
4. Confirmación de si existe o no una tabla equivalente a `contracts` en `schema_v5.2.sql` que yo no haya encontrado.

---

## 9. Riesgos que quedan abiertos

- **La garantía de idempotencia sigue siendo de aplicación, no de base de datos**, hasta que se aplique `idempotency_patch_v5.2.sql` sobre una base real (hoy sigue en JSON). Con dos procesos/workers reales (Gunicorn, Render) la ventana de carrera es más ancha que con threads en un solo proceso de test.
- **La mayoría de los ~14 call-sites de `log_email()` en `app.py` siguen sin pasar por `queue_email()`** (la cola de aprobación) — solo por `check_same_tenant` y el freno global. Esto significa que el requisito de Kevin *"incluso con OUTBOUND_EMAIL_ENABLED=true, las automatizaciones no deben poder mandar directo si el flujo requiere aprobación humana"* **no está cerrado del todo** — es un cambio más grande (tocar 14 puntos de llamada) que no hice en esta pasada para no mezclar refactor con estabilización. Recomiendo que sea el punto 1 del siguiente sprint.
- `data/google_token.json` legado **sigue en disco** (confirmado por lectura directa) — es exactamente el archivo que causó el incidente original si algo vuelve a caer al fallback sin tenant. La migración a multi-cuenta lo retira, pero no se ejecutó.
- Los otros ~20 strings de marca hardcodeados listados en 1.2 (fuera de los 3 que corregí) siguen sin tocar.
- El `settings.json` global sigue compartido entre tenants (no hay `settings_tenant-norkevin.json` separado todavía porque la migración multi-cuenta no corrió) — cualquier lectura de `_from_address()` en `email_delivery.py` para envíos que NO pasan por Gmail (SMTP/Resend/local) sigue devolviendo el email de Astral sin importar el tenant activo.
- No pude ejecutar nada — todo lo de arriba está escrito con la confianza de una lectura cuidadosa del código, no de una corrida real. Trátalo como una propuesta fuerte, no como un hecho confirmado, hasta que Codex traiga el output real.

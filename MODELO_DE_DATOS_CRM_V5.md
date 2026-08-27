# MODELO DE DATOS CRM — Proyecto Narvis (V5.2 — entrega cerrada)
**Versión:** 5.2 (correcciones aplicadas sobre V5.1 verificada por el usuario)
**Fecha:** 10 de julio de 2026
**Estado:** 27/27 pruebas aisladas PASAN + 11/11 auditoría independiente Codex PASAN. NO se ha ejecutado Alembic, NO se modificó `crm.db`, NO se tocaron los JSON.

> **Importante sobre los hashes SHA-256**: los hashes de este documento, del SQL, del script y del output **no se incluyen en este archivo**. Viven en `SHA256SUMS_V5_2.txt` (archivo externo) para evitar la dependencia circular entre hashes que ocurre cuando un archivo declara su propio hash.

## Punto de partida verificado

Kevin ejecutó V5.1 externamente y encontró:
- Tests falsos positivos: T03 pasaba por UNIQUE de `is_primary=1`, no por la FK de tenant
- T11 admitía: "Status = failed (no dead_letter automatico)" — no había transición real
- Kevin insertó project/product/email_template con company de otro tenant y la DB los aceptó

Inventario V5.1 verificada:
- 35 tablas, 19 índices, 5 triggers

Inventario V5.2 cerrada:
- **35 tablas, 27 índices, 13 triggers** (incluye candados adicionales de Sec. 3, 5, 6 y 7 detectados en la auditoría Codex)

## Archivos del paquete V5.2

Los hashes SHA-256 NO están aquí para evitar referencias circulares. Viven en `SHA256SUMS_V5_2.txt`.

| Archivo | Descripción |
|---|---|
| `schema_v5.2.sql` | Fuente única del DDL. Leído por validate/verify. |
| `validate_schema_v5.2.py` | 27 pruebas aisladas (cada test con su propia conexión) |
| `verify_v5_consistency.py` | Verificador estricto (35 nombres + 27 índices + 13 triggers + SHA-256 + sqlite_master) |
| `codex_audit_v5_2.py` | Auditoría independiente Codex con 11 pruebas adicionales y mutación del verificador |
| `MODELO_DE_DATOS_CRM_V5.md` | Este documento |
| `VALIDACION_V5_OUTPUT.txt` | Salida completa de 5 corridas + 1 modo `-I` + verificador |
| `SHA256SUMS_V5_2.txt` | Hashes del paquete V5.2, sin hash circular |

## Cambios V5.1 → V5.2 aplicados

1. **Sec. 1** — Test T04 inserta 2 clientes como `is_primary=0` para forzar rechazo por FK de tenant (sin depender de UNIQUE(is_primary)).

2. **Sec. 2** — FK compuesta `(tenant_id, company_id, family_id) -> workflow_template_families(tenant_id, company_id, id)` en `workflow_template_versions`. Ahora Workflow Version de Astral + Family de Norkevin se rechaza por SQL.

3. **Sec. 3** — `workflow_task_instances` sin `project_id` (derivado). La coherencia se mantiene via `workflow_instance_id -> workflow_instances -> project` y ahora también con FK compuesta `workflow_instance_id + template_version_id` y `template_version_id + task_template_version_id`, evitando tareas con plantilla de otro workflow.

4. **Sec. 4** — Trigger `trg_payment_tx_invoice_project_match` en INSERT y UPDATE. Rechaza pago cuyo `project_id` no coincide con el de la invoice.

5. **Sec. 5** — FKs compuestas `(tenant_id, company_id) -> companies(tenant_id, id)` agregadas en `processed_events`, `automation_runs`, `calendar_events`, `mail_log`, `settings`, `sequence_counters`, `outbox_events`. `automation_runs` queda reforzado contra `project_id`, `workflow_instance_id` y `task_instance_id` de otra company. `mail_log.template_id` via FK compuesta `(tenant_id, company_id, template_id) -> email_templates(tenant_id, company_id, id)`.

6. **Sec. 6** — `quote_items` conserva snapshot puro (name/price copiados al enviar). Triggers de INSERT y UPDATE rechazan cuando `quote_item.product_id` pertenece a otra company. Tests T21 y T25 verifican el rechazo con mensaje exacto `quote_item_product_company_mismatch`.

7. **Sec. 7** — Triggers UPDATE cubren: reducir payment por debajo de refunds, reducir payment por debajo de allocations, mutar campos base de un payment con refunds existentes, y cambiar invoice/project de una transacción contra otra invoice. Verificado en T16, T26 y T27.

8. **Sec. 8** — `accept_quote()` refactor: **sin hardcodes críticos** (pasa tenant_id, company_id, currency, total; deriva `billing_project_client_id` desde la quote y el workflow production desde la company). Usa `total_units` (no subtotal) para invoice+installments. Cuotas suman exacto el total (residuo en la última). Fechas con `date + timedelta`, no strings hardcoded. `UPDATE` valida `rowcount == 1`.

9. **Sec. 9** — Retry `processed_events`: query `SELECT status, attempts` (2 columnas) sin error de 3 vs 4. State machine maneja `failed -> processing -> completed` con `attempts + 1`. Test T12 verifica retry exitoso.

10. **Sec. 10** — Outbox state machine real:
    - `claim_event(worker_id)` con `BEGIN IMMEDIATE` para atomicidad
    - `mark_failed_reschedule()` transiciona a `dead_letter` cuando `attempts + 1 >= max_attempts`
    - Trigger `trg_outbox_no_delivered_to_pending` (mensaje: `outbox_delivered_cannot_return_to_pending`)
    - Trigger `trg_outbox_dead_letter_locked` (mensaje: `outbox_dead_letter_requires_admin`)
    - Test T15 con 2 conexiones SQLite (no 1 secuencial) usando `BEGIN IMMEDIATE`

11. **Sec. 11** — `verify_v5_consistency.py` ejecuta `schema_v5.2.sql`, lee `sqlite_master`, y compara nombres exactos contra inventario esperado. Si falta cualquier objeto → FAIL. Imprime `PRAGMA foreign_key_check` (esperado: 0 filas) y `PRAGMA integrity_check` (esperado: ok).

12. **Sec. 12** — 27 pruebas aisladas. Cada test con su propia conexión, operación específica, excepción esperada y mensajes relevantes de SQLite validados.

15. **Auditoría Codex adicional** — `codex_audit_v5_2.py` ejecuta 11 pruebas independientes: inventario/PRAGMA, workflow task mismatch, automation project/workflow mismatch, UPDATE de quote item, pagos bajo allocations, payment con refund bloqueado, calendar all_day, rollback atómico y prueba de mutación para confirmar que el verificador falla si falta un trigger.

13. **Sec. 13** — Confirmación del entorno separada por categoría (CRM / validación / agente).

14. **Sec. 14** — Entrega con hashes en archivo EXTERNO `SHA256SUMS_V5_2.txt`. NO en el MD, NO en el output. Sin dependencias circulares.

## Resultado de las 27 pruebas

```
T01: PRAGMA foreign_key_check + integrity_check                              [PASS]
T02: Conteo exacto (35 tablas, 27 indices, 13 triggers)                      [PASS]
T03: quote company distinto al project                                       [PASS]
T04 (Sec.1): client otro tenant, FK real (no is_primary)                     [PASS]
T05 (Sec.2): Workflow Version Astral con Family Norkevin                     [PASS]
T06: Workflow instance Norkevin + template Astral                           [PASS]
T07 (Sec.4): payment_transaction invoice de otro project                      [PASS]
T08 (Sec.5): calendar_events con company de otro tenant                      [PASS]
T09 (Sec.5): mail_log.template_id de company distinto                        [PASS]
T10 (Sec.8): doble aceptacion idempotente (con real result)                  [PASS]
T11 (Sec.9): idempotency key + hash distinto                                 [PASS]
T12 (Sec.9): retry processed_event failed                                    [PASS]
T13 (Sec.10): outbox dead_letter a max_attempts                              [PASS]
T14 (Sec.10): delivered no vuelve a pending                                  [PASS]
T15 (Sec.10): 2 workers concurrentes, solo 1 claim                           [PASS]
T16 (Sec.7): UPDATE payment reduce amount bajo refunds                       [PASS]
T17: refund excede original                                                  [PASS]
T18: mail_log sin template (template opcional)                               [PASS]
T19: automation_runs sin task_instance                                       [PASS]
T20: allocation excede transaction                                           [PASS]
T21 (Sec.6): quote_item product de otra company                              [PASS]
T22 (Sec.3): workflow task template de otro workflow                         [PASS]
T23 (Sec.5): automation_run project de otra company                          [PASS]
T24 (Sec.5): automation_run workflow de otra company                         [PASS]
T25 (Sec.6): UPDATE quote_item product de otra company                       [PASS]
T26 (Sec.7): UPDATE payment bajo allocations                                 [PASS]
T27 (Sec.7): payment original con refunds bloqueado                          [PASS]

Total: 27 pasaron, 0 fallaron (de 27 pruebas)
```

## Resultado auditoría independiente Codex

```
Inventario + PRAGMAs                                      [PASS]
Workflow task mismatch                                    [PASS]
Automation project mismatch                               [PASS]
Automation workflow mismatch                              [PASS]
Quote item UPDATE mismatch                                [PASS]
Payment below allocations                                 [PASS]
Payment with refund locked                                [PASS]
Payment invoice UPDATE mismatch                           [PASS]
Calendar all_day CHECK                                    [PASS]
Rollback atomicity                                        [PASS]
Verifier mutation catches missing trigger                 [PASS]

Total: 11 pasaron, 0 fallaron (de 11 pruebas)
```

## Archivo externo de hashes

Los hashes SHA-256 de los 5 archivos viven en `SHA256SUMS_V5_2.txt`. El archivo de hashes NO contiene su propio hash.

## Confirmación literal de no-modificación (V5.2 cerrada)

| Item | Estado |
|---|---|
| Código del CRM modificado | **NO** |
| `app.py` modificado | **NO** |
| Datos del CRM modificados | **NO** |
| `crm.db` modificado | **NO** |
| Alembic ejecutado | **NO** |
| JSON modificados | **NO** |
| Clientes fusionados | **NO** |
| Storage cambiado | **NO** |
| Archivos V4/V5.1 editados | **NO** (preservados en `*_BASELINE.*`) |
| Archivos baseline preservados | **SÍ** (4 archivos `*_BASELINE.*`) |
| Archivos de validación modificados | **SÍ** (`schema_v5.2.sql`, `validate_schema_v5.2.py`, `verify_v5_consistency.py`, `VALIDACION_V5_OUTPUT.txt`, `SHA256SUMS_V5_2.txt`) |
| Auditoría independiente agregada | **SÍ** (`codex_audit_v5_2.py`, `CODEX_VALIDATION_OUTPUT.txt`, `AUDITORIA_CODEX_V5_2.md`) |

## Archivos baseline (preservados, NO modificados)

```
schema_v5.1_BASELINE.sql      (45,320 bytes — V5.1 antes de correcciones V5.2)
validate_schema_v5.1_BASELINE.py (57,090 bytes)
verify_v5_consistency_v5.1_BASELINE.py (3,145 bytes)
MODELO_DE_DATOS_CRM_V5.1_BASELINE.md (14,810 bytes)
```

Estos archivos no se modifican en este turno ni en futuros. Son la línea base verificada por Kevin.

## Próximo paso

Según el prompt maestro de 10 pasos, V5.2 queda como diseño técnico del Paso 2, pero no se aplica aún a producción. El siguiente trabajo real debe volver al **Paso 1: AUDITORIA_CRM_ACTUAL.md** del CRM completo, y después decidir Fase 1 con backup, migración reversible y modo sombra.

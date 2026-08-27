# MODELO DE DATOS CRM — Proyecto Narvis
**Versión:** 5.1 (corregido según feedback directo de Kevin)
**Fecha:** 9 de julio de 2026
**Estado:** 13/13 pruebas aisladas PASAN. Pendiente de aprobación final.

## Archivos del paquete

| Archivo | SHA-256 | Tamaño |
|---|---|---|
| `schema_v5.sql` | `5f1876b3b3b69c0d82705331a058f81e5771f395be3c7425fec93dd1c9eb285e` | 45,320 bytes |
| `validate_schema_v5.py` | `3787a43cb20e074d4ccdf6f53266ac5ccfa5f498949427e8ac2217bd47b2cc64` | 57,090 bytes |
| `verify_v5_consistency.py` | `cd38cffbdb7556cc42ccf9de51db646aada7b367c88f0de25cb21af2f4aed4eb` | 3,145 bytes |
| `VALIDACION_V5_OUTPUT.txt` | `0326decba60049babf70c122a6f1c2e92b68ca9ee10e3686bd460e91bb52f6e1` | 33,515 bytes |
| `MODELO_DE_DATOS_CRM_V5.md` | `5af947579b1ea68528f92084b8b29321c19ad897039f254ae1d2023137afc691` | 14,694 bytes |

**Importante:** `schema_v5.sql` es la **única fuente de verdad** del DDL. El script y el verificador lo leen y ejecutan. NO hay dos copias del DDL.

## Advertencia crítica

- NO se ha ejecutado `alembic`.
- NO se ha modificado `crm.db`.
- NO se han tocado los JSON.
- NO se ha modificado el código de `app.py`.

---

## A. Cambios V5 → V5.1 (según feedback de Kevin)

| Cambio V4 → V5 (antiguo) | Cambio V5 → V5.1 (nuevo) |
|---|---|
| Script inline con DDL hardcodeado | `schema_v5.sql` como única fuente de verdad |
| Documento declaraba 5-6 tablas (parcial) | Documento declara el inventario REAL (35 tablas) |
| `verify_v5_consistency` aceptaba formato markdown | `verify_v5_consistency` ejecuta el SQL real y compara con `sqlite_master` |
| Una sola conexión para 13 pruebas | **Cada prueba usa su PROPIA conexión** |
| Test 5 (doble acept): UPDATE que afecta 0 filas | Test 5 llama a `accept_quote()` con transacción completa, 2 requests |
| Test 6 (rollback): error antes de escrituras | Test 6 hace 3 escrituras exitosas ANTES del error |
| Test 7 (idempotency): ambos INSERT chocan contra PK | Test 7 verifica mismo hash = devuelve result, hash distinto = IdempotencyPayloadMismatch |
| Test 11 (outbox): UPDATE manuales | Test 11: 2 workers, lock, max_attempts, dead_letter |
| Sin `FK compuesta (tenant_id, company_id) -> companies(tenant_id, id)` en 11 tablas | Agregada en 11 tablas |
| Sin `UNIQUE (user_id, company_id)` en memberships | Agregado |
| Sin `UNIQUE (project_id, client_id)` en project_clients | Agregado |
| Sin `trg_payment_cannot_shrink_below_refunds` | Agregado |
| 4 triggers | 5 triggers |
| 17 índices | 19 índices (incluye `uq_pc_project_id`, `uq_pi_invoice_id`) |

---

## B. Estrategia de aislamiento (Kevin lo aprobó)

### B.1 Regla uniforme

**Estrategia A como principio:** Tablas profundamente derivadas no repiten `tenant_id` ni `company_id`.

**Excepciones con integridad compuesta (FKs):** `user_company_memberships`, `project_clients`, `workflow_instances`, `quotes`, `invoices`, `payment_transactions`, `payment_allocations`.

**Entidades independientes (guardan tenant_id y/o company_id):** `products`, `email_templates`, `workflow_template_families`, `workflow_template_versions`, `payment_schedule_templates`, `settings`, `sequence_counters`, `calendar_events`, `processed_events`, `outbox_events`, `activity_log`, `mail_log`.

### B.2 Reglas de validación

- **Foreign keys** para pertenencia entre entidades.
- **UNIQUE** para unicidad por tenant, company, o combinación.
- **CHECK** para reglas de una sola fila (montos, estados válidos).
- **Triggers** para reglas que consultan otras filas (sumas, montos máximos).
- **Aplicación** solo para mensajes amigables y decisiones de negocio.

---

## C. Inventario REAL (verificado por `verify_v5_consistency.py`)

### C.1 Tablas (35)

| # | Tabla | tenant_id | company_id | Estrategia |
|---|---|---|---|---|
| 1 | `tenants` | (es el tenant) | ❌ | Ancla |
| 2 | `companies` | ✅ | (es la company) | Ancla |
| 3 | `users` | ✅ | ❌ | Independiente |
| 4 | `user_company_memberships` | ✅ | ❌ | FK compuesta (user+company) |
| 5 | `clients` | ✅ | ❌ | Independiente |
| 6 | `client_emails` | ❌ (deriva) | ❌ | Estrategia A |
| 7 | `client_phones` | ❌ (deriva) | ❌ | Estrategia A |
| 8 | `client_addresses` | ❌ (deriva) | ❌ | Estrategia A |
| 9 | `projects` | ✅ | ✅ | Independiente + FK compuesta |
| 10 | `project_clients` | ✅ | ❌ | FK compuesta (project+client) |
| 11 | `quotes` | ✅ | ✅ | Doc principal + FKs compuestas |
| 12 | `quote_items` | ❌ (deriva) | ❌ | Estrategia A |
| 13 | `invoices` | ✅ | ✅ | Doc principal + FKs compuestas |
| 14 | `invoice_items` | ❌ (deriva) | ❌ | Estrategia A |
| 15 | `payment_installments` | ❌ (deriva) | ❌ | Estrategia A + UNIQUE (invoice_id, id) |
| 16 | `payment_transactions` | ✅ | ✅ | FK autorreferencial para refunds |
| 17 | `payment_allocations` | ❌ (deriva) | ❌ | FKs compuestas a invoice |
| 18 | `payment_schedule_templates` | ✅ | ✅ | Independiente + FK compuesta |
| 19 | `payment_schedule_rules` | ❌ (deriva) | ❌ | Estrategia A |
| 20 | `products` | ✅ | ✅ | Independiente + FK compuesta |
| 21 | `workflow_template_families` | ✅ | ✅ | Independiente + FK compuesta |
| 22 | `workflow_template_versions` | ✅ | ✅ | Independiente + FK compuesta |
| 23 | `workflow_task_template_versions` | ❌ (deriva) | ❌ | Estrategia A |
| 24 | `workflow_instances` | ✅ | ✅ | FKs compuestas (tenant+company) |
| 25 | `workflow_task_instances` | ❌ (deriva) | ❌ | Estrategia A |
| 26 | `processed_events` | ✅ | opcional | Idempotencia |
| 27 | `outbox_events` | ✅ | ✅ | Outbox + FK compuesta |
| 28 | `automation_runs` | ✅ | ✅ | Independiente + FK compuesta |
| 29 | `activity_log` | ✅ | opcional | Independiente |
| 30 | `mail_log` | ✅ | ✅ | Independiente + FK compuesta |
| 31 | `settings` | ✅ | ✅ | Independiente + FK compuesta |
| 32 | `sequence_counters` | ✅ | ✅ | Independiente + FK compuesta |
| 33 | `calendar_events` | ✅ | ✅ | Independiente + FK compuesta |
| 34 | `email_templates` | ✅ | ✅ | Independiente + FK compuesta |
| 35 | `legacy_record_map` | ✅ | ❌ | Migración |

### C.2 Índices (19)

| # | Índice | Tabla | Tipo |
|---|---|---|---|
| 1 | `uq_companies_tenant_id` | `companies(tenant_id, id)` | UNIQUE |
| 2 | `uq_users_tenant_id` | `users(tenant_id, id)` | UNIQUE |
| 3 | `uq_clients_tenant_id` | `clients(tenant_id, id)` | UNIQUE |
| 4 | `uq_projects_tenant_id` | `projects(tenant_id, id)` | UNIQUE |
| 5 | `uq_projects_tenant_company` | `projects(tenant_id, company_id, id)` | UNIQUE |
| 6 | `uq_pst_tenant_company` | `payment_schedule_templates(tenant_id, company_id, id)` | UNIQUE |
| 7 | `uq_wft_tenant_company` | `workflow_template_versions(tenant_id, company_id, id)` | UNIQUE |
| 8 | `uq_pc_project_id` | `project_clients(project_id, id)` | UNIQUE (para FK desde quotes/invoices) |
| 9 | `uq_pi_invoice_id` | `payment_installments(invoice_id, id)` | UNIQUE (para FK desde allocations) |
| 10 | `uq_project_primary_contact` | `project_clients(project_id) WHERE is_primary=1` | UNIQUE parcial |
| 11 | `uq_project_billing_contact` | `project_clients(project_id) WHERE is_billing_contact=1` | UNIQUE parcial |
| 12 | `idx_client_emails_norm` | `client_emails(value_normalized)` | INDEX |
| 13 | `uq_client_email_primary` | `client_emails(client_id) WHERE is_primary=1` | UNIQUE parcial |
| 14 | `idx_client_phones_norm` | `client_phones(value_normalized)` | INDEX |
| 15 | `uq_client_phone_primary` | `client_phones(client_id) WHERE is_primary=1` | UNIQUE parcial |
| 16 | `uq_project_active_workflow` | `workflow_instances(project_id) WHERE status IN ('active','paused')` | UNIQUE parcial |
| 17 | `idx_pa_installment` | `payment_allocations(installment_id)` | INDEX |
| 18 | `idx_outbox_pending` | `outbox_events(status, available_at) WHERE status='pending'` | INDEX parcial |
| 19 | `idx_calendar_start` | `calendar_events(company_id, start_at)` | INDEX |

### C.3 Triggers (5)

| # | Trigger | Tabla | Evento | Función |
|---|---|---|---|---|
| 1 | `trg_refund_validation_insert` | `payment_transactions` | BEFORE INSERT | 5 checks: original es payment, confirmado, misma moneda, mismo exponent, suma no excede |
| 2 | `trg_refund_validation_update` | `payment_transactions` | BEFORE UPDATE | Mismas 5 checks para UPDATE |
| 3 | `trg_allocation_validation_insert` | `payment_allocations` | BEFORE INSERT | 4 checks: tx es payment, confirmada, suma no excede tx, suma no excede installment |
| 4 | `trg_allocation_validation_update` | `payment_allocations` | BEFORE UPDATE | Mismas 4 checks para UPDATE |
| 5 | `trg_payment_cannot_shrink_below_refunds` | `payment_transactions` | BEFORE UPDATE | Si payment confirmado se reduce, no puede quedar por debajo de refunds existentes |

---

## D. Restricciones que protegen contra doble aceptación (Test 5)

| Entidad | Restricción | Garantía |
|---|---|---|
| `projects` | `PRIMARY KEY (id)` | El Project existe ANTES de aceptar |
| `quotes` | `UNIQUE(company_id, number)` | No se crea segunda quote con mismo número |
| `quotes` | `accept_quote()` UPDATE only | El segundo request detecta `status='accepted'` y devuelve result cacheado |
| `invoices` | `UNIQUE(company_id, number)` | No se crea segunda invoice |
| `invoices` | `UNIQUE (project_id, id)` | Invoice es del mismo Project que su quote |
| `payment_installments` | `UNIQUE(invoice_id, number)` | No se duplican installments |
| `processed_events` | `PRIMARY KEY (tenant_id, idempotency_key)` | No se duplica un evento con misma key |
| `outbox_events` | `UNIQUE(tenant_id, dedupe_key)` | No se duplica un evento saliente |
| `workflow_instances` | `UNIQUE INDEX uq_project_active_workflow WHERE status IN ('active','paused')` | Solo 1 workflow activo por project |

---

## E. Resultado de las 6 corridas (en `VALIDACION_V5_OUTPUT.txt`)

| Aspecto | Valor |
|---|---|
| Python | 3.11.15 |
| SQLite | 3.53.1 |
| Plataforma | win32 |
| Comando normal | `python3.11 validate_schema_v5.py` |
| Comando aislado | `python3.11 -I validate_schema_v5.py` |
| Tablas creadas | 35 |
| Índices creados | 19 |
| Triggers creados | 5 |
| Pruebas | 13/13 PASAN |
| Warnings | 0 |
| Datos residuales | 0 |
| Exit code | 0 (todas) |
| Diff corrida 1 vs 5 | (vacío, idénticas) |

### E.1 Salida de las 13 pruebas

```
[PASS] Test 1: PRAGMA foreign_key_check sin errores
[PASS] Test 2: quote con company distinto al project
[PASS] Test 3: client de otro tenant en project_clients
[PASS] Test 4: invoice con quote de otro project
[PASS] Test 5: doble aceptacion idempotente (funcion completa)
[PASS] Test 6: rollback con escrituras exitosas previas
[PASS] Test 7: idempotency mismo hash
[PASS] Test 8: refund invalido (FK + 4 triggers)
[PASS] Test 9: asignacion de pagos (allocation)
[PASS] Test 10: idempotency payload mismatch
[PASS] Test 11: outbox state machine (2 workers, lock)
[PASS] Test 12: integridad general (FK + integrity check)
[PASS] Test 13: cruces de tenant y company (10 casos)
```

### E.2 Inventario REAL de la DB (de `verify_v5_consistency.py`)

**35 tablas, 19 índices, 5 triggers.**

### E.3 Salida literal de `PRAGMA foreign_key_check` y `PRAGMA integrity_check`

```
PRAGMA foreign_key_check
  Filas: 0

PRAGMA integrity_check
  Resultado: ok
```

---

## F. Clasificación de cada test (Kevin lo pidió)

| Test | Restricción SQL específica | Tipo |
|---|---|---|
| 1 | `PRAGMA foreign_key_check` | FOREIGN KEY |
| 2 | `quotes(tenant_id, company_id, project_id) → projects(tenant_id, company_id, id)` | FOREIGN KEY compuesta |
| 3 | `project_clients(tenant_id, project_id) → projects(tenant_id, id)` y `project_clients(tenant_id, client_id) → clients(tenant_id, id)` | FOREIGN KEY compuesta |
| 4 | `invoices(project_id, quote_id) → quotes(project_id, id)` | FOREIGN KEY compuesta |
| 5 | `quotes` UPDATE only + `UNIQUE(company_id, number)` + `PRIMARY KEY(processed_events, idempotency_key)` + `UNIQUE(outbox_events, dedupe_key)` + `uq_project_active_workflow` + función `accept_quote` con `BEGIN IMMEDIATE` | FOREIGN KEY + UNIQUE + lógica de aplicación (transaccional) |
| 6 | `conn.rollback()` después de IntegrityError en transacción con escrituras previas | LÓGICA DE APLICACIÓN (transaccional) |
| 7 | `processed_events` PRIMARY KEY (tenant_id, idempotency_key) + `request_hash` en lógica | UNIQUE + LÓGICA DE APLICACIÓN |
| 8.1 | `payment_transactions(invoice_id, original_transaction_id) → payment_transactions(invoice_id, id)` | FOREIGN KEY autorreferencial |
| 8.2 | Misma FK autorreferencial | FOREIGN KEY autorreferencial |
| 8.3 | `trg_refund_validation_insert` (suma acumulada) | TRIGGER |
| 8.4 | `trg_refund_validation_insert` (misma moneda) | TRIGGER |
| 8.5 | `trg_refund_validation_insert` (original es payment) | TRIGGER |
| 9.1 | `trg_allocation_validation_insert` (suma excede tx) | TRIGGER |
| 9.2 | `payment_allocations(invoice_id, transaction_id)` + `payment_allocations(invoice_id, installment_id)` | FOREIGN KEY compuesta |
| 10 | `IdempotencyPayloadMismatch` exception raised when hash differs | LÓGICA DE APLICACIÓN (con UNIQUE en BD) |
| 11 | `UPDATE outbox_events SET status='processing' WHERE status='pending' AND attempts<max_attempts` (atomicidad SQL) | FOREIGN KEY + lógica de aplicación (UPDATE atómico) |
| 12 | `PRAGMA foreign_key_check` + `PRAGMA integrity_check` | SQL |
| 13.1-13.10 | `FOREIGN KEY (tenant_id, company_id) REFERENCES companies(tenant_id, id)` | FOREIGN KEY compuesta |

**Tests con LÓGICA DE APLICACIÓN significativa:**
- Test 5: función `accept_quote()` con transacción completa
- Test 6: `conn.rollback()` Python
- Test 7: comparación de `request_hash` en Python
- Test 10: `IdempotencyPayloadMismatch` exception
- Test 11: `UPDATE` atómico SQL + funciones Python

---

## G. Confirmación literal de no-modificación (V5.1)

| Item | Estado |
|---|---|
| Código de producción modificado | **NO** |
| Script aislado de validación creado | **SÍ** (`validate_schema_v5.py`) |
| SQL fuente única creado | **SÍ** (`schema_v5.sql`) |
| Verificador de consistencia creado | **SÍ** (`verify_v5_consistency.py`) |
| Output de 6 corridas creado | **SÍ** (`VALIDACION_V5_OUTPUT.txt`) |
| Documento V5.1 actualizado | **SÍ** (`MODELO_DE_DATOS_CRM_V5.md`) |
| Archivos de skills modificados | **NO** (de mi parte) |
| Memoria del agente modificada | **NO** (de mi parte) |
| `app.py` modificado | **NO** |
| Datos del CRM modificados | **NO** |
| `crm.db` modificado | **NO** |
| Alembic ejecutado | **NO** |
| JSON modificados | **NO** |

---

## H. Próximo paso

Kevin, si apruebas V5.1:

1. Backup completo de JSONs y `crm.db`
2. Inicialización de `crm.db` con Alembic usando `schema_v5.sql`
3. Migración controlada de los datos de los JSONs
4. Cambio del storage de JSON a SQLite en `app.py`

**No avanzar hasta que apruebes explícitamente.**
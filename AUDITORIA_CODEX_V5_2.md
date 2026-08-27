# AUDITORIA CODEX V5.2

**Fecha:** 2026-07-10  
**Repo auditado:** `C:\Users\fotov\.openclaw\workspace\crm_norkevin`  
**Decision:** V5.2 queda **APROBADA COMO DISENO TECNICO** para el modelo de datos, con parches aplicados solo a archivos V5.2 permitidos. No se aplico a produccion.

## Leido

- `C:\Users\fotov\Downloads\HANDOFF_CODEX_AUDITORIA_CRM_V5_2.txt`
- `C:\Users\fotov\Downloads\PROMPT_MAESTRO_CRM_NORQUITO_10_PASOS.txt`
- `schema_v5.2.sql`
- `validate_schema_v5.2.py`
- `verify_v5_consistency.py`
- `MODELO_DE_DATOS_CRM_V5.md`
- `VALIDACION_V5_OUTPUT.txt`
- `SHA256SUMS_V5_2.txt`
- Baseline V5.1: 4 archivos `*_BASELINE.*`

## Entorno

- Python: `3.14.3`
- SQLite: `3.50.4`
- Sistema: Windows / PowerShell

## Hallazgos iniciales

La V5.2 original compilaba y pasaba sus 21 pruebas, con inventario real de:

- Tablas: 35
- Indices: 24
- Triggers: 9

La auditoria independiente encontro huecos demostrables que la suite original no cubria:

- `workflow_task_instances` podia usar un `task_template_version_id` de otro workflow/template.
- `automation_runs` podia apuntar a `project_id` o `workflow_instance_id` de otra company/tenant.
- `quote_items` validaba company/product en INSERT, pero no en UPDATE.
- `payment_transactions` podia reducir un pago por debajo de allocations existentes.
- Un payment con refunds podia mutar campos base que ya no deberian cambiar.
- El trigger de coherencia payment/invoice/project cubria INSERT, pero no UPDATE.
- `accept_quote()` decia "sin hardcodes", pero todavia usaba `pc_001` y `wtv_prod_v1`.

## Cambios aplicados

Solo se editaron archivos permitidos de V5.2:

- `schema_v5.2.sql`
- `validate_schema_v5.2.py`
- `verify_v5_consistency.py`
- `MODELO_DE_DATOS_CRM_V5.md`
- `VALIDACION_V5_OUTPUT.txt`
- `SHA256SUMS_V5_2.txt`

Se agregaron entregables permitidos:

- `codex_audit_v5_2.py`
- `CODEX_VALIDATION_OUTPUT.txt`
- `AUDITORIA_CODEX_V5_2.md`

Cambios principales:

- Inventario actualizado a **35 tablas, 27 indices, 13 triggers**.
- FKs compuestas nuevas para cerrar coherencia de workflow task instances.
- FKs compuestas nuevas para cerrar coherencia de automation runs.
- Trigger UPDATE para `quote_items` contra producto de otra company.
- Trigger UPDATE para impedir pagos por debajo de allocations.
- Trigger UPDATE para bloquear campos base de payment original con refunds.
- Trigger UPDATE para payment/invoice/project mismatch.
- `accept_quote()` ahora deriva billing contact desde la quote y workflow production desde la company.
- Validador ampliado de 21 a 27 pruebas.
- Auditoria Codex independiente con 11 pruebas, incluida mutacion del verificador.

## Resultado final

Inventario real:

- Tablas: **35**
- Indices: **27**
- Triggers: **13**
- `PRAGMA foreign_key_check`: **0 filas**
- `PRAGMA integrity_check`: **ok**

Validaciones:

- `python validate_schema_v5.2.py`: **27 PASS / 0 FAIL**
- `python -I validate_schema_v5.2.py`: **27 PASS / 0 FAIL**
- `python verify_v5_consistency.py`: **OK**
- `python -I verify_v5_consistency.py`: **OK**
- `python codex_audit_v5_2.py`: **11 PASS / 0 FAIL**

La prueba de mutacion confirma que `verify_v5_consistency.py` falla si falta un trigger esperado.

## Hashes finales

Ver `SHA256SUMS_V5_2.txt`.

Hash principal:

```text
1167a15e5edd0818be6e35247f6b2e781243e7770ffe77118338c7561af3f10c  schema_v5.2.sql
```

## Baseline V5.1

El handoff mencionaba "5 baseline files", pero en disco existen exactamente 4 archivos baseline:

- `schema_v5.1_BASELINE.sql`
- `validate_schema_v5.1_BASELINE.py`
- `verify_v5_consistency_v5.1_BASELINE.py`
- `MODELO_DE_DATOS_CRM_V5.1_BASELINE.md`

No fueron modificados.

## Confirmacion de no modificacion

- `app.py`: **NO**
- `src/workflow/*.py`: **NO**
- `templates/*.html`: **NO**
- `data/crm.db`: **NO**
- `data/*.json`: **NO**
- Alembic: **NO**
- Produccion / tunnel / Flask runtime: **NO**

## Decision

V5.2 queda aprobada como base tecnica del modelo de datos, pero no debe ejecutarse aun contra la DB real. Segun el prompt maestro, el siguiente paso correcto no es migrar todavia: es completar o actualizar `AUDITORIA_CRM_ACTUAL.md` del CRM completo y despues planificar Fase 1 con backup, migraciones reversibles y modo sombra.

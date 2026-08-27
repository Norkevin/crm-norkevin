-- =============================================================================
-- idempotency_patch_v5.2.sql
-- Fase de estabilizacion (agosto 2026), punto 3: cerrar a nivel de BASE DE
-- DATOS la duplicacion de projects/jobs por conversion repetida de la misma
-- accion (aceptar la misma cotizacion / el mismo lead dos, cinco o veinte
-- veces).
--
-- NO SE HA APLICADO. Este archivo es una PROPUESTA de patch sobre
-- schema_v5.2.sql, para revisar y ejecutar contra crm_v5_shadow.db (nunca
-- contra data/crm.db) antes de considerarlo parte del schema base.
--
-- Por que hace falta ademas del guardia en /api/jobs/new:
--   El guardia de aplicacion (_find_job_for_lead antes de crear) tiene una
--   ventana de carrera real: dos requests pueden pasar el SELECT/find antes
--   de que cualquiera de los dos haga el INSERT. Un `if` en Python no cierra
--   esa ventana -- un UNIQUE INDEX si, porque SQLite rechaza el segundo
--   INSERT sin importar el orden de llegada.
--
-- Estrategia: origin_action_key.
--   Cada project que nace de una conversion (lead aceptado, quote aceptado)
--   recibe un origin_action_key determinista, calculado por la aplicacion
--   ANTES de intentar el insert (no autogenerado por la base):
--
--       origin_action_key = f"{tenant_id}:{company_id}:convert_lead:{lead_id}"
--
--   Si mañana existen otras acciones de conversion (ej. "crear job desde
--   quote sin lead", "importar de Studio Ninja"), cada una define su propio
--   prefijo de key con el mismo patron -- la unicidad la da la COMBINACION
--   tenant+company+key, no el prefijo.
--
--   Con un UNIQUE INDEX sobre esa columna, el segundo de dos INSERT
--   concurrentes falla con IntegrityError. La aplicacion debe:
--     1. Intentar el INSERT dentro de una transaccion.
--     2. Si falla por UNIQUE constraint, hacer SELECT del project existente
--        por (tenant_id, company_id, origin_action_key) y devolverlo --
--        "uno gana, el otro recupera el registro existente", tal como pidio
--        Kevin.
--   Esto solo funciona si el INSERT y el fallback estan en el MISMO punto
--   de codigo que hoy usa _find_job_for_lead + upsert_job (ver
--   src/lead_conversion.py mas abajo) -- si dos caminos distintos siguen
--   pudiendo crear un project sin pasar por esa funcion, el constraint
--   protege la tabla pero el bug de UX (dos jobs "casi iguales" con distinto
--   origin_action_key) puede reaparecer por otra puerta. Por eso el punto 3
--   de la fase de estabilizacion pide consolidar los DOS caminos existentes
--   (accept-quote y /api/jobs/new) en una sola funcion.
--
-- Que NO hace este patch:
--   No previene que un usuario cree, a proposito, dos projects distintos
--   para el mismo cliente (ej. dos bodas reales de la misma pareja en anos
--   distintos) -- esos tienen lead_id distintos y por lo tanto
--   origin_action_key distintos. Eso es correcto: no es duplicacion, son
--   dos eventos reales.
-- =============================================================================

ALTER TABLE projects ADD COLUMN origin_action_key TEXT;

-- Parcial: solo exige unicidad cuando origin_action_key esta poblado. Los
-- projects que se migren desde datos legados sin un origen de conversion
-- claro (ej. importados directo de Studio Ninja) pueden quedar con
-- origin_action_key NULL sin bloquear nada -- eso es exactamente lo que ya
-- hace SQLite con NULL en un UNIQUE INDEX (NULL nunca choca con NULL).
CREATE UNIQUE INDEX uq_projects_origin_action_key
    ON projects(tenant_id, company_id, origin_action_key)
    WHERE origin_action_key IS NOT NULL;

-- Trigger de defensa en profundidad: aunque algun camino futuro construya
-- el INSERT a mano sin pasar por src/lead_conversion.py, el intento de
-- reutilizar la misma origin_action_key para un project DISTINTO revienta
-- aqui tambien (el UNIQUE INDEX ya lo hace en el INSERT; este trigger cubre
-- el caso de un UPDATE que intente "robarle" la key a otro project).
CREATE TRIGGER trg_projects_origin_action_key_immutable
BEFORE UPDATE OF origin_action_key ON projects
WHEN OLD.origin_action_key IS NOT NULL AND NEW.origin_action_key != OLD.origin_action_key
BEGIN
    SELECT RAISE(ABORT, 'origin_action_key_is_immutable_once_set');
END;

-- =============================================================================
-- Registro para validate_schema_v5.2.py / verify_v5_consistency.py:
-- este patch agrega 1 columna, 1 indice UNIQUE parcial y 1 trigger.
-- Inventario base V5.2 (35 tablas, 27 indices, 13 triggers) queda:
--   35 tablas (sin cambio -- no se crea tabla nueva)
--   28 indices
--   14 triggers
-- Pendiente: actualizar validate_schema_v5.2.py / verify_v5_consistency.py
-- con este nuevo conteo esperado ANTES de correr este patch contra
-- crm_v5_shadow.db, para que la verificacion de inventario no falle sola
-- por desactualizada.
-- =============================================================================

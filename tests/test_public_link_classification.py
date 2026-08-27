"""Que enlaces publicos siguen vivos (puntos 1 y 2 del ultimo plan).

La regla que dio Kevin: un recurso esta ACTIVO si cumple CUALQUIERA de nueve
condiciones, y la fecha de la boda NO es una de ellas por si sola.

Y la que importa mas: si no se puede determinar con seguridad, la respuesta
es REVIEW_REQUIRED. No INACTIVO. Meter una duda en INACTIVO seria convertir
"no se" en permiso para desactivar el enlace de alguien.
"""
import uuid

import pytest

from src import public_links as pl

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _ctx(**kw):
    return pl.Contexto(**kw)


JOB_CERRADO = {'id': 'job-1', 'job_complete': True}
JOB_ABIERTO = {'id': 'job-1', 'job_complete': False}


# ------------------------------------------------- cada condicion, una a una

def test_un_job_sin_job_complete_mantiene_todo_activo():
    """Condicion 1."""
    v = pl.clasificar('contract', {'signed': True},
                      _ctx(job=JOB_ABIERTO, pagos=[], tareas_pendientes=0))
    assert v['estado'] == pl.ACTIVO
    assert any('Job Complete' in r for r in v['razones'])


def test_saldo_pendiente_mantiene_activo():
    """Condicion 2, aunque el job este cerrado y el contrato firmado."""
    v = pl.clasificar('contract', {'signed': True},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=0,
                           pagos=[{'amount': 1500, 'status': 'Pendiente'}]))
    assert v['estado'] == pl.ACTIVO
    assert any('saldo' in r for r in v['razones'])


def test_una_factura_atrasada_mantiene_activo():
    """Condicion 3."""
    v = pl.clasificar('quote', {'status': 'Aceptada'},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=0,
                           pagos=[{'amount': 0, 'status': 'Late'}]))
    assert v['estado'] == pl.ACTIVO


def test_un_contrato_sin_firmar_esta_activo():
    """Condicion 4, lado obvio."""
    v = pl.clasificar('contract', {'signed': False},
                      _ctx(job=JOB_CERRADO, pagos=[], tareas_pendientes=0))
    assert v['estado'] == pl.ACTIVO


def test_un_contrato_firmado_con_todo_cerrado_va_a_revision():
    """Condicion 4, el lado que NO es tecnico.

    Un contrato firmado sigue siendo el documento al que el cliente vuelve
    si hay un reclamo. Decidir que ya no importa es una decision legal, no
    una que deba tomar el codigo.
    """
    v = pl.clasificar('contract', {'signed': True},
                      _ctx(job=JOB_CERRADO, pagos=[], tareas_pendientes=0))
    assert v['estado'] == pl.REVISAR
    assert any('consultable' in d for d in v['dudas'])


def test_un_cuestionario_pendiente_esta_activo():
    """Condicion 5."""
    v = pl.clasificar('questionnaire', {'status': 'Sent'},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=0))
    assert v['estado'] == pl.ACTIVO


def test_un_cuestionario_respondido_con_todo_cerrado_es_inactivo():
    v = pl.clasificar('questionnaire', {'status': 'completed'},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=0))
    assert v['estado'] == pl.INACTIVO


@pytest.mark.parametrize('estado', ['Enviada', 'Aceptada', 'Borrador', 'Vista'])
def test_una_cotizacion_viva_esta_activa(estado):
    """Condicion 6: solo expirada/rechazada la cierra."""
    v = pl.clasificar('quote', {'status': estado},
                      _ctx(job=JOB_CERRADO, pagos=[], tareas_pendientes=0))
    assert v['estado'] == pl.ACTIVO


@pytest.mark.parametrize('estado', ['Rechazada', 'expired', 'Superada', 'declined'])
def test_una_cotizacion_cerrada_no_sostiene_el_enlace(estado):
    v = pl.clasificar('quote', {'status': estado},
                      _ctx(job=JOB_CERRADO, pagos=[], tareas_pendientes=0))
    assert v['estado'] == pl.INACTIVO


def test_un_portal_habilitado_esta_activo():
    """Condicion 7."""
    v = pl.clasificar('portal', {'id': 'client-1', 'portal_enabled': True},
                      _ctx(job=JOB_CERRADO, pagos=[], tareas_pendientes=0))
    assert v['estado'] == pl.ACTIVO


def test_actividad_reciente_mantiene_activo():
    """Condicion 8."""
    from datetime import datetime

    v = pl.clasificar('questionnaire',
                      {'status': 'completed', 'last_viewed_at': datetime.now().isoformat()},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=0))
    assert v['estado'] == pl.ACTIVO
    assert any('actividad reciente' in r for r in v['razones'])


def test_actividad_vieja_no_mantiene_activo():
    v = pl.clasificar('questionnaire',
                      {'status': 'completed', 'last_viewed_at': '2019-01-01T00:00:00'},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=0))
    assert v['estado'] == pl.INACTIVO


def test_tareas_pendientes_del_job_mantienen_activo():
    """Condicion 9."""
    v = pl.clasificar('questionnaire', {'status': 'completed'},
                      _ctx(job=JOB_CERRADO, tareas_pendientes=3))
    assert v['estado'] == pl.ACTIVO
    assert any('tarea' in r for r in v['razones'])


# ------------------------------------------------------- la duda no es un no

def test_sin_el_job_no_se_puede_afirmar_que_esta_inactivo():
    v = pl.clasificar('questionnaire', {'status': 'completed'}, _ctx())
    assert v['estado'] == pl.REVISAR
    assert v['dudas']


def test_job_complete_nunca_marcado_va_a_revision():
    """Kevin: no quiere que se defina por la fecha. Si nadie marco Job
    Complete a mano, eso es una duda, no un no."""
    v = pl.clasificar('questionnaire', {'status': 'completed'},
                      _ctx(job={'id': 'job-1'}, tareas_pendientes=0))
    assert v['estado'] == pl.REVISAR
    assert any('nunca se marco' in d for d in v['dudas'])


def test_no_poder_leer_los_pagos_va_a_revision():
    v = pl.clasificar('contract', {'signed': False, 'signed_at': None},
                      _ctx(job=JOB_CERRADO, pagos=None, tareas_pendientes=0))
    # signed=False ya lo hace activo, asi que se prueba con uno firmado.
    v = pl.clasificar('portal', {'id': 'c1', 'portal_enabled': False},
                      _ctx(job=JOB_CERRADO, pagos=None, tareas_pendientes=0))
    assert v['estado'] == pl.REVISAR
    assert any('pagos' in d for d in v['dudas'])


def test_cero_tareas_no_es_lo_mismo_que_no_saber():
    """La diferencia que decide entre INACTIVO y REVIEW_REQUIRED."""
    sabiendo = pl.clasificar('questionnaire', {'status': 'completed'},
                             _ctx(job=JOB_CERRADO, tareas_pendientes=0))
    sin_saber = pl.clasificar('questionnaire', {'status': 'completed'},
                              _ctx(job=JOB_CERRADO, tareas_pendientes=None))
    assert sabiendo['estado'] == pl.INACTIVO
    assert sin_saber['estado'] == pl.REVISAR


def test_un_tipo_desconocido_va_a_revision():
    """Nunca inactivo por no saber que es."""
    assert pl.clasificar('cosa-nueva', {}, _ctx())['estado'] == pl.REVISAR


def test_una_razon_activa_gana_sobre_cualquier_duda():
    """Si algo esta vivo, esta vivo -- aunque falte informacion de otra cosa."""
    v = pl.clasificar('quote', {'status': 'Enviada'}, _ctx())
    assert v['estado'] == pl.ACTIVO
    assert v['dudas'], 'las dudas se conservan aunque no cambien el veredicto'


# ---------------------------------------------------------- la ruta de auditoria

def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': campos.pop('id', f'{tabla[:4]}-{uuid.uuid4().hex[:8]}'),
              'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _auditar(client, app_module, **args):
    query = ''.join(f'&{k}={v}' for k, v in args.items())
    resp = client.get('/api/admin/public-links-audit?token='
                      + app_module._ADMIN_ONE_TIME_TOKEN + query)
    assert resp.status_code == 200
    return resp.get_json()


def test_la_auditoria_no_toca_nada(client):
    """Kevin: solo clasificacion y dry-run."""
    import app as app_module

    _seed(app_module, 'contracts', ASTRAL, id='contract-sn-boda-rebeca-y-jos',
          job_id='job-x', signed=False)
    antes = app_module.store.list_privileged(
        'contracts', scope='all_tenants', reason='snapshot de test')
    copia = [dict(c) for c in antes]

    datos = _auditar(client, app_module)

    despues = app_module.store.list_privileged(
        'contracts', scope='all_tenants', reason='snapshot de test')
    assert despues == copia, 'la auditoria no puede escribir nada'
    assert datos['dry_run'] is True


def test_un_legacy_todavia_activo_sale_en_atender_primero(client):
    """La interseccion que importa: adivinable Y en uso."""
    import app as app_module

    job = _seed(app_module, 'jobs', ASTRAL, job_complete=False)
    _seed(app_module, 'contracts', ASTRAL, id='contract-sn-boda-de-prueba-activa',
          job_id=job['id'], signed=False)

    datos = _auditar(client, app_module, tenant_id=ASTRAL)

    tipos = {a['tipo'] for a in datos['atender_primero']}
    assert 'contract' in tipos
    fila = next(a for a in datos['atender_primero'] if a['tipo'] == 'contract')
    assert fila['por_que_sigue_activo'], 'tiene que decir POR QUE sigue activo'
    assert fila['accion_propuesta'].startswith('ETAPA_1')


def test_la_auditoria_nunca_muestra_un_id_completo(client):
    """Un enlace publico es una credencial: no va entero a un reporte."""
    import app as app_module

    id_secreto = 'contract-sn-boda-secreta-de-prueba'
    job = _seed(app_module, 'jobs', ASTRAL, job_complete=False)
    _seed(app_module, 'contracts', ASTRAL, id=id_secreto, job_id=job['id'],
          signed=False)

    crudo = client.get('/api/admin/public-links-audit?token='
                       + app_module._ADMIN_ONE_TIME_TOKEN).get_data(as_text=True)

    assert id_secreto not in crudo
    assert '*' in crudo, 'deberian salir huellas en vez de ids'


def test_el_periodo_de_alias_es_configurable_y_sin_limite_por_defecto(client):
    """Etapa 3: Kevin no quiso decidir el periodo todavia."""
    import app as app_module

    config = _auditar(client, app_module)['configuracion']
    assert config['dias_alias_legacy'] == 0
    assert 'SIN LIMITE' in config['nota']

"""El reporte del incidente no puede adivinar.

Kevin: si no se puede determinar con seguridad, decirlo. mail_log guarda la
DIRECCION del destinatario, no el client_id -- asi que cuando la misma
direccion existe en las dos empresas es literalmente imposible saber a cual
de las dos personas se le escribio.

Antes el reporte se quedaba con la primera empresa que encontraba, lo que
convertia una ambiguedad real en una certeza inventada.
"""
import uuid

import pytest

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _reporte(client, app_module, **args):
    query = ''.join(f'&{k}={v}' for k, v in args.items())
    resp = client.get('/api/admin/incident-report?token='
                      + app_module._ADMIN_ONE_TIME_TOKEN + query)
    assert resp.status_code == 200
    return resp.get_json()


def _fila(datos, correo):
    return next(e for e in datos['detalle'] if (e['para'] or '').lower() == correo)


def test_una_direccion_en_las_dos_empresas_queda_marcada(client):
    import app as app_module

    correo = f'ambiguo-{uuid.uuid4().hex[:6]}@gmail.com'
    _seed(app_module, 'clients', ASTRAL, first_name='Ana', email=correo)
    _seed(app_module, 'clients', NORKEVIN, first_name='Beto', email=correo)
    _seed(app_module, 'mail_log', ASTRAL, to=correo, subject='Recordatorio de pago',
          status='sent', sent_at='2026-08-15T10:00:00')

    fila = _fila(_reporte(client, app_module), correo)

    assert fila['identidad'] == 'AMBIGUOUS_RECIPIENT_IDENTITY'
    assert fila['destinatario_pertenece_a'] is None, \
        'no puede afirmar de quien era si existe en las dos'
    assert set(fila['empresas_con_ese_correo']) == {ASTRAL, NORKEVIN}


def test_un_ambiguo_no_se_cuenta_como_cruzado(client):
    """Contarlo como cruce confirmado inflaria el incidente."""
    import app as app_module

    correo = f'ambiguo2-{uuid.uuid4().hex[:6]}@gmail.com'
    _seed(app_module, 'clients', ASTRAL, first_name='Ana', email=correo)
    _seed(app_module, 'clients', NORKEVIN, first_name='Beto', email=correo)
    _seed(app_module, 'mail_log', ASTRAL, to=correo, subject='Pago pendiente',
          status='sent', sent_at='2026-08-15T10:00:00')

    datos = _reporte(client, app_module)

    assert correo not in {(e['para'] or '').lower() for e in datos['cruzados']}
    assert correo in {(e['para'] or '').lower() for e in datos['ambiguos']}
    assert datos['totales']['destinatario_ambiguo'] >= 1


def test_un_cruce_real_si_se_cuenta_como_cruce(client):
    """Caso POSITIVO: la direccion existe en UNA sola empresa, y no es la que
    envio. Eso si es el incidente y tiene que salir contado."""
    import app as app_module

    correo = f'solo-norkevin-{uuid.uuid4().hex[:6]}@gmail.com'
    _seed(app_module, 'clients', NORKEVIN, first_name='Solo', email=correo)
    _seed(app_module, 'mail_log', ASTRAL, to=correo, subject='Recordatorio de pago',
          status='sent', sent_at='2026-08-15T10:00:00')

    datos = _reporte(client, app_module)
    fila = _fila(datos, correo)

    assert fila['identidad'] == 'CROSS_TENANT'
    assert fila['destinatario_pertenece_a'] == NORKEVIN
    assert fila['es_cobro'] is True
    assert correo in {(e['para'] or '').lower() for e in datos['cruzados']}


def test_un_envio_correcto_no_se_marca_de_nada(client):
    """El otro caso POSITIVO: mismo negocio de los dos lados."""
    import app as app_module

    correo = f'propio-{uuid.uuid4().hex[:6]}@gmail.com'
    _seed(app_module, 'clients', ASTRAL, first_name='Propia', email=correo)
    _seed(app_module, 'mail_log', ASTRAL, to=correo, subject='Hola',
          status='sent', sent_at='2026-08-15T10:00:00')

    fila = _fila(_reporte(client, app_module), correo)

    assert fila['identidad'] == 'OK'
    assert fila['destinatario_pertenece_a'] == ASTRAL


def test_un_destinatario_que_no_es_de_nadie_tambien_es_ambiguo(client):
    """Si no esta como cliente ni lead de ninguna empresa, tampoco se puede
    afirmar que el envio fuera correcto."""
    import app as app_module

    correo = f'desconocido-{uuid.uuid4().hex[:6]}@gmail.com'
    _seed(app_module, 'mail_log', ASTRAL, to=correo, subject='Hola',
          status='sent', sent_at='2026-08-15T10:00:00')

    fila = _fila(_reporte(client, app_module), correo)

    assert fila['identidad'] == 'AMBIGUOUS_RECIPIENT_IDENTITY'
    assert fila['empresas_con_ese_correo'] == []

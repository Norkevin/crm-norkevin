"""Etapa 2: saber que enlaces viejos siguen usandose de verdad.

Sin este dato, decidir cuales desactivar (etapa 3) seria adivinar: no hay
forma de saber cuales siguen circulando -- en correos ya enviados, en
WhatsApp, guardados por el cliente -- y cuales murieron solos.

Registrar NO es cambiar: el enlace viejo sigue funcionando exactamente igual.
"""
import logging
import uuid

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': campos.pop('id'), 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _eventos(caplog, nombre='LEGACY_PUBLIC_LINK_USED'):
    return [r.message for r in caplog.records if nombre in r.message]


def test_entrar_por_un_enlace_legacy_queda_registrado(client, caplog):
    import app as app_module

    _seed(app_module, 'contracts', ASTRAL, id='contract-sn-boda-registro-uno',
          job_id='job-x')

    with caplog.at_level(logging.WARNING):
        client.get('/contracts/contract-sn-boda-registro-uno')

    eventos = _eventos(caplog)
    assert eventos, 'un enlace legacy usado tiene que dejar rastro'
    assert 'tipo=contracts' in eventos[-1]
    assert f'cuenta={ASTRAL}' in eventos[-1]
    assert 'cuando=' in eventos[-1]


def test_el_registro_nunca_lleva_el_enlace_completo(client, caplog):
    """Kevin: fingerprint del enlace, nunca token completo. Un enlace
    publico es una credencial, y una credencial en un log es una credencial
    filtrada."""
    import app as app_module

    id_secreto = 'contract-sn-boda-que-no-debe-salir-entera'
    _seed(app_module, 'contracts', ASTRAL, id=id_secreto, job_id='job-x')

    with caplog.at_level(logging.WARNING):
        client.get('/contracts/' + id_secreto)

    evento = _eventos(caplog)[-1]
    assert id_secreto not in evento
    assert '*' in evento, 'deberia ir la huella'
    # Lo justo para identificarlo sin poder reconstruirlo.
    assert 'cont' in evento


def test_un_enlace_moderno_no_ensucia_el_log(client, caplog):
    """Solo interesan los legacy. Registrar todos volveria el log inutil."""
    import app as app_module

    _seed(app_module, 'contracts', ASTRAL, id='contract-a1b2c3d4', job_id='job-x')

    with caplog.at_level(logging.WARNING):
        client.get('/contracts/contract-a1b2c3d4')

    assert not _eventos(caplog)


def test_un_id_inventado_no_registra_nada(client, caplog):
    """Solo se registra lo que resolvio a una empresa real: si no, cualquiera
    podria llenar el log escribiendo urls."""
    with caplog.at_level(logging.WARNING):
        client.get('/contracts/contract-sn-boda-que-no-existe')

    assert not _eventos(caplog)


def test_el_enlace_legacy_sigue_funcionando(client):
    """Lo mas importante de la etapa 2: registrar NO es romper.

    Kevin todavia no decidio nada sobre desactivar; hasta entonces un cliente
    con un enlace viejo tiene que poder abrirlo igual que ayer.
    """
    import app as app_module

    job = _seed(app_module, 'jobs', ASTRAL, id='job-sigue-viva',
                nombre='Boda que sigue viva')
    cliente = _seed(app_module, 'clients', ASTRAL, id='client-sigue-viva',
                    first_name='Ana', email='ana@ejemplo.com')
    _seed(app_module, 'contracts', ASTRAL, id='contract-sn-boda-sigue-viva',
          job_id=job['id'], client_id=cliente['id'], estado='Enviado')

    resp = client.get('/contracts/contract-sn-boda-sigue-viva')

    assert resp.status_code == 200, (
        'el enlace viejo no puede dejar de resolver: registrar no es romper')


def test_registra_la_empresa_correcta(client, caplog):
    """El evento sirve para decidir por empresa, asi que no puede confundirlas."""
    import app as app_module

    _seed(app_module, 'contracts', NORKEVIN, id='contract-sn-boda-de-norkevin',
          job_id='job-y')

    with caplog.at_level(logging.WARNING):
        client.get('/contracts/contract-sn-boda-de-norkevin')

    assert f'cuenta={NORKEVIN}' in _eventos(caplog)[-1]

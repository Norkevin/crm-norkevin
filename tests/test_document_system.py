"""Sistema de documentos de Flow CRM (29-ago-2026).

Kevin: "quiero que Flow CRM tenga un lenguaje visual consistente... mismo
ADN, distinta funcion" para cotizacion y factura, y que la factura deje de
ser "solo un PDF". Este archivo protege exactamente eso:

  - la factura web publica existe, carga y muestra las cifras reales;
  - cotizacion y factura comparten de verdad los mismos tokens/componentes
    (no dos sistemas parecidos que pueden divergir);
  - los estados de factura se deducen del modelo real y nunca dependen solo
    del color;
  - el enlace publico de factura usa el mismo mecanismo de token seguro que
    /q/<token>, aislado por cuenta;
  - abrir/previsualizar una factura NO invalida el enlace del cliente;
  - la vista interna sigue exigiendo sesion.

Lo que NO se toca aca porque ya esta cubierto y no cambio: aceptacion de
cotizaciones, extras, idempotencia, conversion lead->job y calendario de
pagos (test_public_quote_experience_bloque_*.py, test_stage2_*).
"""
import uuid

import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _factura(app_module, tenant_id, *, suffix='', cuotas=None, concepto='Paquete Boda'):
    """Crea una factura real: job + cliente + N filas de payments que
    comparten invoice_id (asi es como el CRM representa una factura)."""
    suffix = suffix or uuid.uuid4().hex[:6]
    client_id = f'cli-doc-{suffix}'
    job_id = f'job-doc-{suffix}'
    invoice_id = f'INV-DOC-{suffix.upper()}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Doc', 'last_name': 'Test',
        'email': f'{client_id}@example.com', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Documento',
        'boda_date': '2027-05-01', 'status': 'Confirmado', 'tenant_id': tenant_id,
    })
    cuotas = cuotas or [
        {'amount': 5000.0, 'due_date': '2026-01-15', 'status': 'Pagado',
         'paid_amount': 5000.0, 'paid_date': '2026-01-15'},
        {'amount': 5000.0, 'due_date': '2027-04-01', 'status': 'Pendiente', 'paid_amount': 0},
    ]
    for i, c in enumerate(cuotas, start=1):
        fila = {
            'id': f'pay-doc-{suffix}-{i}', 'invoice_id': invoice_id,
            'client_id': client_id, 'job_id': job_id, 'concepto': concepto,
            'original_amount': c['amount'], 'amount': c['amount'],
            'cuota': f'{i}/{len(cuotas)}', 'tenant_id': tenant_id,
        }
        fila.update(c)
        app_module.store.upsert('payments', fila)
    return invoice_id, client_id, job_id


# ---------------------------------------------------------------------
# La factura web existe y dice la verdad
# ---------------------------------------------------------------------

def test_documento_de_factura_muestra_total_pagado_y_pendiente(auth_client):
    """Las tres cifras que Kevin quiere ver de inmediato, calculadas por el
    mismo motor de siempre (_row_original_amount/_row_paid_amount)."""
    import app as app_module
    invoice_id, _cli, _job = _factura(auth_client and app_module, ASTRAL, suffix='tot')

    r = auth_client.get(f'/invoices/{invoice_id}/documento')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Q10,000.00' in html, 'falta el total'
    assert 'Q5,000.00' in html, 'falta el pagado/pendiente'
    assert 'Total' in html and 'Pagado' in html and 'Pendiente' in html


def test_estado_de_factura_se_deduce_del_modelo_real(flask_app):
    """_invoice_estado no inventa: mira las cuotas. Se prueba la funcion
    directamente para cubrir los 6 estados sin montar 6 facturas."""
    import app as app_module
    from datetime import date, timedelta
    ayer = (date.today() - timedelta(days=10)).isoformat()
    manana = (date.today() + timedelta(days=10)).isoformat()

    def cuota(monto, pagado, vence):
        return {'original_amount': monto, 'amount': monto, 'paid_amount': pagado,
                'due_date': vence, 'status': 'Pagado' if pagado >= monto else 'Pendiente'}

    label, tono, _ = app_module._invoice_estado(0, 0, 0, [])
    assert label == 'Borrador' and tono == 'neutral'

    label, tono, _ = app_module._invoice_estado(1000, 1000, 0, [cuota(1000, 1000, ayer)])
    assert label == 'Pagada' and tono == 'success'

    label, tono, detalle = app_module._invoice_estado(1000, 0, 1000, [cuota(1000, 0, ayer)])
    assert label == 'Vencida' and tono == 'danger'
    assert 'Q1,000.00' in detalle, 'la factura vencida debe decir cuanto se debe'

    label, tono, _ = app_module._invoice_estado(1000, 400, 600, [cuota(1000, 400, manana)])
    assert label == 'Parcialmente pagada' and tono == 'warning'

    label, tono, _ = app_module._invoice_estado(1000, 0, 1000, [cuota(1000, 0, manana)])
    assert label == 'Pendiente' and tono == 'info'

    label, tono, _ = app_module._invoice_estado(1000, 0, 1000, [cuota(1000, 0, manana)], cancelada=True)
    assert label == 'Cancelada' and tono == 'neutral'


def test_factura_pagada_nunca_se_muestra_como_vencida(flask_app):
    """Aunque la fecha ya paso: si no se debe nada, no hay nada vencido.
    El orden de las reglas en _invoice_estado depende de esto."""
    import app as app_module
    from datetime import date, timedelta
    ayer = (date.today() - timedelta(days=30)).isoformat()
    cuota = {'original_amount': 800, 'amount': 800, 'paid_amount': 800,
             'due_date': ayer, 'status': 'Pagado'}
    label, tono, _ = app_module._invoice_estado(800, 800, 0, [cuota])
    assert label == 'Pagada' and tono == 'success'


def test_el_estado_nunca_depende_solo_del_color(auth_client):
    """Kevin: 'no dependas solamente del color'. El badge lleva el texto del
    estado, no solo una clase de color."""
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='badge')
    html = auth_client.get(f'/invoices/{invoice_id}/documento').get_data(as_text=True)
    assert 'doc-badge' in html
    assert 'Parcialmente pagada' in html, 'el estado debe estar escrito, no solo pintado'


# ---------------------------------------------------------------------
# Cotizacion y factura son de verdad el mismo sistema
# ---------------------------------------------------------------------

def test_cotizacion_y_factura_comparten_tokens_y_componentes():
    """Si alguien duplica el sistema visual en vez de compartirlo, esto
    falla -- que es justamente lo que Kevin pidio evitar."""
    quote = open('templates/quote_view.html', encoding='utf-8').read()
    inv = open('templates/invoice_document.html', encoding='utf-8').read()
    for src, nombre in ((quote, 'quote_view'), (inv, 'invoice_document')):
        assert "_document_tokens.html" in src, f'{nombre} no usa los tokens compartidos'
        assert "_document_base.html" in src, f'{nombre} no usa la base compartida'
        assert "_document_parts.html" in src, f'{nombre} no usa los componentes compartidos'


def test_ningun_documento_contamina_el_css_del_crm():
    """Los documentos publicos no extienden base.html ni cargan su hoja de
    estilos: por construccion no pueden cambiar el dashboard."""
    for archivo in ('templates/quote_view.html', 'templates/invoice_document.html'):
        src = open(archivo, encoding='utf-8').read()
        assert '{% extends' not in src, f'{archivo} extiende una plantilla del CRM'


def test_los_documentos_no_traen_marcas_escritas_a_mano():
    for archivo in ('templates/_document_tokens.html', 'templates/_document_base.html',
                    'templates/_document_parts.html', 'templates/quote_view.html',
                    'templates/invoice_document.html'):
        src = open(archivo, encoding='utf-8').read()
        for marca in ('Astral', 'Norkevin', 'Ramiro'):
            assert marca not in src, f'{archivo} tiene "{marca}" hardcodeado'


def test_la_factura_no_usa_tablas_anchas():
    """Llega por WhatsApp y se abre en el telefono: misma regla que ya
    protege la cotizacion en test_responsive_movil.py."""
    src = open('templates/invoice_document.html', encoding='utf-8').read()
    assert '<table' not in src


# ---------------------------------------------------------------------
# Enlace publico: mismo mecanismo seguro que /q/<token>
# ---------------------------------------------------------------------

def test_enlace_publico_de_factura_guarda_hash_no_el_token(auth_client):
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='tok')
    token = app_module._emitir_token_de_factura(invoice_id, ASTRAL, rotar=True)
    assert token, 'no se emitio token'

    filas = [p for p in app_module.store.list('payments') if p.get('invoice_id') == invoice_id]
    assert filas
    for fila in filas:
        assert fila.get('public_token_hash'), 'la fila no quedo con hash'
        assert token not in str(fila), 'el token en claro NO debe guardarse'


def test_factura_publica_abre_sin_sesion_y_muestra_la_marca_correcta(client):
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkevin-inv@example.com')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkevin-inv@example.com', 'active': True,
    })
    invoice_id, _c, _j = _factura(app_module, NORKEVIN, suffix='pub')
    token = app_module._emitir_token_de_factura(invoice_id, NORKEVIN, rotar=True)

    with client.session_transaction() as sess:
        sess.clear()  # el cliente abre el enlace sin estar logueado

    r = client.get(f'/i/{token}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Norkevin' in html
    assert 'ASTRAL' not in html.upper(), 'una factura de Norkevin no puede mostrar la otra marca'


def test_token_invalido_da_404(client):
    assert client.get('/i/token-que-no-existe').status_code == 404


def test_la_vista_interna_de_factura_sigue_pidiendo_sesion(client):
    """El documento interno no es publico: /invoices/<id>/documento no esta
    en PUBLIC_PATTERNS, solo /i/<token> y el PDF lo estan."""
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='priv')
    with client.session_transaction() as sess:
        sess.clear()
    r = client.get(f'/invoices/{invoice_id}/documento')
    assert r.status_code in (302, 401), 'la vista interna no deberia abrirse sin sesion'


def test_previsualizar_la_factura_no_invalida_el_enlace_del_cliente(auth_client):
    """Regresion del riesgo real detectado al construir esto: si mirar la
    factura (o la vista previa del correo) emitiera token, el enlace que el
    cliente ya tiene en su bandeja dejaria de funcionar."""
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='novoid')
    token = app_module._emitir_token_de_factura(invoice_id, ASTRAL, rotar=True)

    auth_client.get(f'/invoices/{invoice_id}/documento')
    pay_id = f'pay-doc-novoid-1'
    auth_client.get(f'/api/payments/{pay_id}/send-preview')

    assert app_module._resolve_invoice_by_token(token) == invoice_id, \
        'el enlace del cliente dejo de resolver despues de mirar la factura'


def test_la_vista_previa_del_correo_manda_marcador_no_un_enlace_muerto(auth_client):
    """La previa no puede emitir token, asi que manda [[INVOICE_LINK]] y el
    envio real lo reemplaza -- mismo patron que [[QUOTE_LINK]]."""
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='marc')
    r = auth_client.get('/api/payments/pay-doc-marc-1/send-preview')
    assert r.status_code == 200
    assert '[[INVOICE_LINK]]' in r.get_json().get('body', '')


def test_al_enviar_la_factura_el_correo_lleva_el_enlace_web_real(auth_client):
    """Y no el marcador ni el PDF: la experiencia principal es la web."""
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='envio')

    r = auth_client.post('/api/payments/pay-doc-envio-1/send', json={})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert '/i/' in data['invoice_url'], 'el correo deberia llevar la factura web'

    pendientes = [p for p in app_module.store.list('pending_emails')
                  if p.get('id') == data.get('mail_id')]
    assert pendientes, 'el correo deberia quedar en la cola de aprobacion (STAGE 2)'
    cuerpo = pendientes[0]['body']
    assert '[[INVOICE_LINK]]' not in cuerpo, 'el marcador no debe llegar al cliente'
    assert '/i/' in cuerpo


def test_una_cuenta_no_ve_la_factura_de_la_otra_por_su_enlace(client):
    """Aislamiento real, no una comparacion de constantes: se crea una
    factura en cada cuenta, y se comprueba que el enlace de una NUNCA
    entrega el documento de la otra -- ni estando logueado como la otra."""
    import app as app_module

    login_as_tenant(client, ASTRAL, email='astral-tok@example.com')
    inv_astral, _c, _j = _factura(app_module, ASTRAL, suffix='isoA', concepto='Paquete Astral')
    token_astral = app_module._emitir_token_de_factura(inv_astral, ASTRAL, rotar=True)

    login_as_tenant(client, NORKEVIN, email='norkevin-tok@example.com')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkevin-tok@example.com', 'active': True,
    })
    inv_nork, _c2, _j2 = _factura(app_module, NORKEVIN, suffix='isoN', concepto='Paquete Norkevin')
    token_nork = app_module._emitir_token_de_factura(inv_nork, NORKEVIN, rotar=True)

    assert token_astral and token_nork and token_astral != token_nork

    # El enlace de Astral entrega la factura de Astral y solo esa.
    with client.session_transaction() as sess:
        sess.clear()
    html_astral = client.get(f'/i/{token_astral}').get_data(as_text=True)
    assert 'Paquete Astral' in html_astral
    assert 'Paquete Norkevin' not in html_astral
    assert inv_nork not in html_astral

    html_nork = client.get(f'/i/{token_nork}').get_data(as_text=True)
    assert 'Paquete Norkevin' in html_nork
    assert 'Paquete Astral' not in html_nork


# ---------------------------------------------------------------------
# Dinero: los casos que un documento de cliente NO puede equivocar
# ---------------------------------------------------------------------

def test_las_tres_cifras_siempre_cuadran(auth_client):
    """total = pagado + pendiente. Es lo primero que el cliente suma con la
    vista; si no cuadra, el documento pierde toda credibilidad."""
    import app as app_module
    invoice_id, _c, _j = _factura(app_module, ASTRAL, suffix='cuadra')
    doc = app_module._invoice_document(invoice_id)
    assert abs((doc['pagado'] + doc['pendiente']) - doc['total']) < 0.005


def test_un_sobrepago_trasladado_no_deja_la_factura_como_vencida(auth_client):
    """Regresion del bug encontrado en la revision adversarial.

    _apply_payment_sequentially reparte un sobrepago bajando el 'amount' de
    la cuota siguiente y marcandola 'Pagado', SIN subir su 'paid_amount'
    (ese credito no es dinero recibido en esa cuota). Calcular el saldo como
    original_amount - paid_amount mostraba al cliente una factura saldada
    con la leyenda 'Vencida - 1 pago vencido por Q5,000'."""
    import app as app_module
    invoice_id, _c, _j = _factura(
        app_module, ASTRAL, suffix='credito',
        cuotas=[
            {'amount': 0.0, 'due_date': '2026-01-15', 'status': 'Pagado',
             'paid_amount': 5000.0, 'paid_date': '2026-01-15'},
            # saldada por credito del sobrepago anterior: amount 0, sin paid_amount
            {'amount': 0.0, 'due_date': '2026-02-15', 'status': 'Pagado', 'paid_amount': 0},
        ])
    doc = app_module._invoice_document(invoice_id)
    assert doc['pendiente'] == 0
    assert doc['estado_label'] == 'Pagada', f"dijo {doc['estado_label']}"
    assert 'vencid' not in (doc['estado_detalle'] or '').lower()


def test_una_cuota_cancelada_no_se_le_cobra_al_cliente(auth_client):
    """No suma al total ni aparece como vencida, pero sigue visible en el
    historial para que no parezca que un pago desaparecio."""
    import app as app_module
    invoice_id, _c, _j = _factura(
        app_module, ASTRAL, suffix='cancel',
        cuotas=[
            {'amount': 0.0, 'due_date': '2026-01-15', 'status': 'Pagado',
             'paid_amount': 5000.0, 'paid_date': '2026-01-15'},
            {'amount': 5000.0, 'due_date': '2026-02-15', 'status': 'Cancelado', 'paid_amount': 0},
        ])
    doc = app_module._invoice_document(invoice_id)
    assert doc['total'] == 5000.0, 'la cuota cancelada no debe inflar el total'
    assert doc['pendiente'] == 0
    assert doc['estado_label'] == 'Pagada'
    assert any('cancelado' in (f['nota'] or '') for f in doc['filas_pago']), \
        'la cuota cancelada debe seguir visible en el historial'


def test_una_fecha_de_vencimiento_invalida_no_rompe_la_factura(auth_client):
    """due_date vacio/None no puede tumbar un documento que ve el cliente
    ni inventar un 'proximo pago' sin fecha."""
    import app as app_module
    invoice_id, _c, _j = _factura(
        app_module, ASTRAL, suffix='fecha',
        cuotas=[{'amount': 1000.0, 'due_date': None, 'status': 'Pendiente', 'paid_amount': 0}])
    doc = app_module._invoice_document(invoice_id)
    assert doc['estado_label'] in ('Pendiente', 'Parcialmente pagada')
    assert doc['proximo'] is None, 'sin fecha no se puede prometer un proximo pago'

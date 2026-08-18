"""Rutas publicas, autorizacion directa y arquitectura de envio.

Kevin, puntos 12/13/14 del plan post-incidente:
  - conocer un ID de otra empresa no debe alcanzar para ver sus datos;
  - las pruebas deben atacar endpoints y storage directo, no solo la UI;
  - todo lo que corra en segundo plano debe tener empresa explicita.

Los ultimos tres tests son "guardas de arquitectura": no prueban un caso de
uso sino que fijan una propiedad del codigo, para que si alguien mas
adelante agrega otro hilo o llama a send_email por la puerta de atras, esto
falle en rojo en vez de descubrirse con clientes reales.
"""
import ast
import pathlib
import uuid

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
RAIZ = pathlib.Path(__file__).resolve().parent.parent


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _login(client, tenant_id):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['tenant_id'] = tenant_id
        sess['user_email'] = 'dev@localhost'


# ------------------------------------------------- autorizacion directa (13)

def test_matriz_de_acceso_entre_empresas(client):
    """Los 4 casos que pidio Kevin, atacando el storage directo."""
    import app as app_module

    de_astral = _seed(app_module, 'clients', ASTRAL, first_name='A')
    de_norkevin = _seed(app_module, 'clients', NORKEVIN, first_name='N')

    casos = [
        (ASTRAL, de_astral, True),      # propio -> permitido
        (ASTRAL, de_norkevin, False),   # ajeno  -> bloqueado
        (NORKEVIN, de_norkevin, True),  # propio -> permitido
        (NORKEVIN, de_astral, False),   # ajeno  -> bloqueado
    ]
    for tenant, recurso, permitido in casos:
        ctx = app_module.app.test_request_context('/')
        ctx.push()
        try:
            from flask import session
            session['tenant_id'] = tenant
            visible = app_module.store.get('clients', recurso['id']) is not None
        finally:
            ctx.pop()
        assert visible is permitido, (
            f"{tenant} pidiendo un recurso de {recurso['tenant_id']}: "
            f"esperado permitido={permitido}"
        )


def test_sin_empresa_ningun_recurso_privado_es_visible(client):
    import app as app_module

    recurso = _seed(app_module, 'clients', ASTRAL, first_name='Privado')

    ctx = app_module.app.test_request_context('/')
    ctx.push()
    try:
        from flask import session
        session.pop('tenant_id', None)
        assert app_module.store.get('clients', recurso['id']) is None
    finally:
        ctx.pop()


def test_el_endpoint_de_cliente_no_sirve_datos_de_la_otra_empresa(client):
    """Ataque por endpoint: sesion de Astral pidiendo un id de Norkevin."""
    import app as app_module

    ajeno = _seed(app_module, 'clients', NORKEVIN, first_name='Ajeno',
                  last_name='DeNorkevin', email='ajeno@norkevin.com')

    _login(client, ASTRAL)
    resp = client.get('/clients/' + ajeno['id'])

    assert resp.status_code in (302, 404), \
        'un cliente de otra empresa no debe servirse'
    if resp.status_code == 200:
        assert b'ajeno@norkevin.com' not in resp.data


# ------------------------------------------------------ rutas publicas (12)

def test_el_portal_no_filtra_datos_entre_empresas(client):
    """Conocer el id no alcanza: la ruta publica resuelve la empresa DEL
    PROPIO REGISTRO, asi que no se puede pedir uno 'como si fuera' de otra.
    Lo que se comprueba es que cada portal muestre su cliente y solo ese."""
    import app as app_module

    a = _seed(app_module, 'clients', ASTRAL, first_name='ClienteAstral',
              email='a-unico@astral.com')
    n = _seed(app_module, 'clients', NORKEVIN, first_name='ClienteNorkevin',
              email='n-unico@norkevin.com')

    resp_a = client.get('/portal/' + a['id'])
    resp_n = client.get('/portal/' + n['id'])

    assert resp_a.status_code == 200
    assert resp_n.status_code == 200
    assert b'n-unico@norkevin.com' not in resp_a.data, \
        'el portal de Astral no debe filtrar datos de Norkevin'
    assert b'a-unico@astral.com' not in resp_n.data


def test_un_id_inexistente_no_expone_nada(client):
    """Sin registro no hay empresa que resolver: debe cortar, no mostrar."""
    resp = client.get('/portal/client-que-no-existe-12345')
    assert resp.status_code in (404, 302)


def test_el_pdf_publico_de_factura_resuelve_su_propia_empresa(client):
    """El PDF se pide por invoice_id, no por el id del registro: es un caso
    que ya se rompio una vez al cerrar el aislamiento."""
    import app as app_module

    invoice_id = 'INV-PUB-' + uuid.uuid4().hex[:6].upper()
    cliente = _seed(app_module, 'clients', ASTRAL, first_name='Pago',
                    last_name='Publico', email='pago@astral.com')
    job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda del PDF',
                client_id=cliente['id'], boda_date='2026-05-05',
                created='2025-06-01')
    _seed(app_module, 'payments', ASTRAL, invoice_id=invoice_id,
          amount=1000, status='Pendiente', job_id=job['id'],
          client_id=cliente['id'])

    resp = client.get('/invoices/' + invoice_id + '/pdf')
    assert resp.status_code == 200, 'el PDF publico debe seguir funcionando'


# --------------------------------- guardas de arquitectura (punto 14 y P1)

def _fuentes():
    return [RAIZ / 'app.py'] + sorted((RAIZ / 'src').rglob('*.py'))


def test_solo_hay_un_hilo_en_segundo_plano_y_no_arranca_solo():
    """El incidente ocurrio en un hilo sin sesion. Si alguien agrega otro,
    este test obliga a revisarlo antes de que llegue a produccion."""
    import app as app_module

    hilos = []
    for archivo in _fuentes():
        for n, linea in enumerate(archivo.read_text(encoding='utf-8').splitlines(), 1):
            if 'threading.Thread' in linea and not linea.strip().startswith('#'):
                hilos.append(archivo.name + ':' + str(n))

    assert len(hilos) <= 1, (
        'Aparecio un hilo nuevo en segundo plano: ' + str(hilos) +
        '. Cada ejecucion fuera de request debe tener empresa explicita.'
    )
    assert app_module._reminder_thread_started is False, \
        'el scheduler no debe arrancar sin ENABLE_REMINDER_SCHEDULER=1'


def test_nadie_llama_a_send_email_saltandose_la_validacion():
    """send_email solo debe alcanzarse via MailTracker.log_email, que es
    donde vive la validacion cross-company."""
    # Se analiza el arbol sintactico y no el texto: buscar la cadena
    # "send_email(" tambien encontraba menciones dentro de comentarios y
    # docstrings, que no son llamadas.
    llamadas = []
    for archivo in _fuentes():
        if archivo.name in ('mail_tracker.py', 'email_delivery.py'):
            continue
        arbol = ast.parse(archivo.read_text(encoding='utf-8'))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            f = nodo.func
            nombre = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None)
            if nombre == 'send_email':
                llamadas.append(archivo.name + ':' + str(nodo.lineno))

    assert not llamadas, (
        'Hay llamadas directas a send_email que se saltan la validacion '
        'cross-company: ' + str(llamadas)
    )


def test_el_freno_global_esta_en_el_ultimo_punto():
    """DISABLE_OUTBOUND_EMAIL debe vivir dentro de send_email, no en los
    llamadores: solo ahi cubre TODOS los caminos."""
    fuente = (RAIZ / 'src' / 'email_delivery.py').read_text(encoding='utf-8')
    cuerpo = fuente.split('def send_email(', 1)[1]
    assert 'DISABLE_OUTBOUND_EMAIL' in cuerpo[:1500], \
        'el freno global debe estar al principio de send_email'

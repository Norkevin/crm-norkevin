"""Paginas de error reales, y el titulo del navegador sin marca pegada.

Dos cosas que se veian todos los dias y no estaban:

  1. `templates/404.html` y `templates/500.html` existian pero NUNCA se
     mostraban: no habia ningun `@app.errorhandler` registrado, asi que
     Flask servia su pagina blanca por defecto, sin menu, sin marca y sin
     forma de volver. Un enlace viejo dejaba a Kevin en un callejon sin
     salida.

  2. 24 plantillas tenian "ASTRAL WEDDINGS CRM" escrito a mano en el
     <title>. Norkevin Photography veia el nombre de la OTRA empresa en la
     pestana del navegador -- incluido al compartir pantalla con un
     cliente.
"""
import glob
import os
import re

import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')


# ============================================================
# 404
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_una_ruta_inexistente_muestra_la_pagina_del_crm(auth_client, tenant_id):
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    resp = auth_client.get('/esta-ruta-no-existe-jamas')
    assert resp.status_code == 404

    html = resp.get_data(as_text=True)
    assert 'No encontrado' in html
    # Lo importante para el uso diario: una salida.
    assert '/dashboard' in html, 'la pagina de 404 no ofrece como volver'


def test_un_endpoint_de_api_inexistente_responde_json_no_html(auth_client):
    """El JS hace fetch().json(): devolverle HTML lo hace explotar al
    parsear en vez de mostrar un mensaje."""
    resp = auth_client.get('/api/esto-no-existe')
    assert resp.status_code == 404
    assert resp.is_json, 'una ruta /api/ devolvio HTML'
    assert resp.get_json()['ok'] is False


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_un_job_inexistente_no_deja_al_usuario_sin_salida(auth_client, tenant_id):
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    resp = auth_client.get('/jobs/job-que-no-existe-jamas')
    assert resp.status_code in (404, 302)
    if resp.status_code == 404:
        assert '/dashboard' in resp.get_data(as_text=True)


# ============================================================
# 500
# ============================================================

def test_un_error_no_controlado_muestra_pagina_y_no_la_traza(auth_client, flask_app, monkeypatch):
    """No se puede registrar una ruta nueva a esta altura: Flask lo prohibe
    una vez que la app atendio su primera peticion. Se provoca el error
    dentro de una ruta REAL, que ademas es mas fiel: eso es lo que pasaria
    de verdad.

    Con TESTING=True la excepcion sube (para no esconder bugs en la suite),
    asi que aca se apaga a proposito para ver lo que veria Kevin.
    """
    import app as app_module

    def _revienta(*_a, **_k):
        raise RuntimeError('detalle interno que el usuario no deberia ver')

    monkeypatch.setattr(app_module, '_canonical_clients', _revienta)

    previo = flask_app.config.get('TESTING')
    try:
        flask_app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
        resp = auth_client.get('/clients')
    finally:
        flask_app.config.update(TESTING=previo, PROPAGATE_EXCEPTIONS=None)

    assert resp.status_code == 500
    html = resp.get_data(as_text=True)
    assert 'detalle interno que el usuario no deberia ver' not in html, \
        'la excepcion cruda llego a la pantalla'
    assert 'RuntimeError' not in html and 'Traceback' not in html
    assert '/dashboard' in html, 'la pagina de 500 no ofrece como volver'


def test_en_los_tests_las_excepciones_siguen_subiendo(auth_client, flask_app, monkeypatch):
    """Si el handler se tragara las excepciones dentro de pytest, un bug
    real llegaria disfrazado de 500 y algun test podria darlo por bueno."""
    import app as app_module

    def _revienta(*_a, **_k):
        raise RuntimeError('esto tiene que llegar a pytest')

    monkeypatch.setattr(app_module, '_canonical_clients', _revienta)

    assert flask_app.config.get('TESTING') is True
    with pytest.raises(RuntimeError, match='esto tiene que llegar a pytest'):
        auth_client.get('/clients')


def test_los_errores_de_api_devuelven_json_legible(auth_client, flask_app, monkeypatch):
    """El JS hace fetch().json(): una pagina HTML de error lo hace explotar
    al parsear en vez de mostrar un mensaje util."""
    import app as app_module

    def _revienta(*_a, **_k):
        raise RuntimeError('interno')

    monkeypatch.setattr(app_module, '_build_recent_notifications', _revienta)

    previo = flask_app.config.get('TESTING')
    try:
        flask_app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
        resp = auth_client.get('/api/notifications/recent')
    finally:
        flask_app.config.update(TESTING=previo, PROPAGATE_EXCEPTIONS=None)

    assert resp.status_code == 500
    assert resp.is_json, 'una ruta /api/ devolvio HTML en vez de JSON'
    cuerpo = resp.get_json()
    assert cuerpo['ok'] is False
    assert 'Traceback' not in cuerpo['error']
    assert 'RuntimeError' not in cuerpo['error']


def test_un_error_http_conserva_su_codigo(auth_client, flask_app, monkeypatch):
    """El handler de Exception no puede convertir en 500 los errores HTTP
    que ya traen su propio codigo: esconderia la causa real. Se usa el
    403 real del endpoint destructivo, que es el que importa de verdad."""
    monkeypatch.delenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', raising=False)

    previo = flask_app.config.get('TESTING')
    try:
        flask_app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
        resp = auth_client.post('/api/admin/reset-test-data',
                                json={'confirm': 'BORRAR-tenant-norkevin'})
    finally:
        flask_app.config.update(TESTING=previo, PROPAGATE_EXCEPTIONS=None)

    assert resp.status_code == 403, \
        f'el 403 del endpoint destructivo se convirtio en {resp.status_code}'


# ============================================================
# El titulo del navegador no lleva una marca escrita a mano
# ============================================================

_MARCAS = re.compile(r'ASTRAL\s+WEDDINGS|NORKEVIN\s+PHOTOGRAPHY', re.IGNORECASE)
_TITULO = re.compile(r'\{%\s*block title\s*%\}(.*?)\{%\s*endblock\s*%\}', re.S)


def test_ninguna_plantilla_escribe_una_marca_en_el_titulo():
    ofensores = []
    for path in glob.glob(os.path.join(TEMPLATES_DIR, '**', '*.html'), recursive=True):
        with open(path, encoding='utf-8') as f:
            contenido = f.read()
        for bloque in _TITULO.findall(contenido):
            if _MARCAS.search(bloque):
                ofensores.append(os.path.relpath(path, TEMPLATES_DIR))
    assert not ofensores, (
        f'Estas plantillas escriben una marca a mano en el <title>: {sorted(set(ofensores))}. '
        'Usa {{ current_tenant.name }}, que sale del tenant de la sesion.'
    )


@pytest.mark.parametrize('tenant_id,esperado,ajeno', [
    (ASTRAL, 'Astral', 'Norkevin Photography'),
    (NORKEVIN, 'Norkevin', 'Astral Weddings'),
])
def test_el_titulo_del_navegador_lleva_la_marca_de_la_sesion(auth_client, tenant_id, esperado, ajeno):
    import app as app_module

    tenant = next((t for t in app_module.store.list('tenants') if t['id'] == tenant_id), None)
    if not tenant or not tenant.get('name'):
        pytest.skip(f'{tenant_id} no esta configurado en este entorno')

    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid',
                    name=tenant['name'])
    # login_as_tenant hace upsert del tenant sintetico con name=tenant_id;
    # se restaura el nombre real para comprobar que el titulo lo usa.
    app_module.store.upsert('tenants', dict(tenant, login_email=f'{tenant_id}@example.invalid'))

    resp = auth_client.get('/jobs')
    assert resp.status_code == 200
    titulo = re.search(r'<title>(.*?)</title>', resp.get_data(as_text=True), re.S)
    assert titulo, 'la pagina no tiene <title>'
    assert tenant['name'] in titulo.group(1), \
        f'el titulo no lleva la marca de la sesion: {titulo.group(1)!r}'

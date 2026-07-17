"""Fase 1 de conversion a PWA (Kevin: convertir flowingcrm.com en una app
instalable sin tocar backend/rutas/auth). Estas pruebas cubren solo lo que
es automatizable en este entorno: que el manifest/service worker/pagina
offline existen, son validos, y son alcanzables SIN sesion (si el gate de
login los bloqueara, un visitante instalando el PWA -- o el propio service
worker -- no podria cargarlos). Lo que no se puede probar aqui (instalacion
real en Android/iPhone, Lighthouse, beforeinstallprompt real) esta
documentado como pasos manuales para Kevin, no en esta suite."""
import json


def test_manifest_is_reachable_without_login(client):
    resp = client.get('/manifest.webmanifest')
    assert resp.status_code == 200
    assert 'application/manifest+json' in resp.headers['Content-Type']


def test_manifest_has_required_pwa_fields(client):
    resp = client.get('/manifest.webmanifest')
    data = json.loads(resp.data)
    assert data['name']
    assert data['short_name']
    assert data['display'] == 'standalone'
    assert data['start_url']
    assert data['theme_color']
    icon_sizes = {icon['sizes'] for icon in data['icons']}
    assert '192x192' in icon_sizes
    assert '512x512' in icon_sizes
    maskable = [i for i in data['icons'] if i.get('purpose') == 'maskable']
    assert maskable, 'debe incluir al menos un icono maskable'


def test_service_worker_is_reachable_without_login(client):
    resp = client.get('/service-worker.js')
    assert resp.status_code == 200
    assert 'javascript' in resp.headers['Content-Type']
    # Debe poder controlar todo el sitio, no solo /static/*.
    assert resp.headers.get('Service-Worker-Allowed') == '/'


def test_service_worker_never_caches_private_routes_by_name(client):
    """Regresion de la regla de seguridad de Kevin: el SW no debe cachear
    /api/*, OAuth, Gmail, Recurrente, pagos, contratos, facturas ni
    archivos. En vez de cachear todo salvo una lista negra, el SW usa una
    allowlist (solo /static/* y el manifest van a cache-first) -- esta
    prueba confirma que el codigo fuente sigue reflejando esa allowlist y
    no se coló ninguna regla que cachee rutas privadas."""
    resp = client.get('/service-worker.js')
    src = resp.data.decode('utf-8')
    assert "url.pathname.startsWith('/static/')" in src
    assert "url.pathname === '/manifest.webmanifest'" in src
    assert '/api/' not in src, 'el service worker no debe mencionar /api/ -- pasa por la allowlist, no por una lista negra'


def test_offline_page_is_reachable_without_login(client):
    resp = client.get('/offline.html')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Sin conexion' in html
    # No debe depender de recursos externos (Tailwind CDN, Google Fonts) --
    # tiene que poder renderizarse aunque no haya red.
    assert 'cdn.tailwindcss.com' not in html
    assert 'fonts.googleapis.com' not in html


def test_pwa_meta_tags_present_on_every_page(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'apple-mobile-web-app-capable' in html
    assert 'apple-touch-icon' in html


def test_bottom_nav_present_and_has_five_items(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'class="bottom-nav"' in html
    for path in ('/dashboard', '/clients', '/jobs', '/calendar'):
        assert f'data-nav="{path}"' in html
    assert 'openBottomNavMore' in html


def test_icon_files_exist_and_are_reachable(client):
    for name in ('icon-192.png', 'icon-512.png', 'icon-512-maskable.png', 'apple-touch-icon.png'):
        resp = client.get(f'/static/icons/{name}')
        assert resp.status_code == 200, f'{name} deberia existir en static/icons/'
        assert resp.headers['Content-Type'] == 'image/png'

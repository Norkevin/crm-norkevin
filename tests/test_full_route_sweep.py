"""Barre TODAS las rutas GET sin parametros dinamicos (enumeradas
directamente del url_map de Flask, no a mano) para atrapar bugs como el de
/leads-demo: una ruta que se le olvido pasar una variable que su template
necesitaba, y quedaba con un 500 silencioso hasta que alguien la visitaba."""
import pytest

# Rutas que intencionalmente NO deben dar 200 sin mas contexto (login flow,
# que redirige a Google; o exports que dependen de datos especificos).
SKIP_PATHS = {
    '/auth/google/callback',       # requiere ?code= real de Google
    '/auth/google/login/callback', # requiere ?code= real de Google
    '/auth/google/start',          # redirige a Google (302 esperado)
    '/auth/google/login/start',    # redirige a Google (302 esperado)
    '/logout',                     # redirige a /login (302 esperado)
}


def _all_parameterless_get_routes():
    import app as app_module
    routes = []
    for rule in app_module.app.url_map.iter_rules():
        if 'GET' not in rule.methods:
            continue
        path = str(rule)
        if '<' in path or path in SKIP_PATHS:
            continue
        routes.append(path)
    return sorted(routes)


@pytest.mark.parametrize('path', _all_parameterless_get_routes())
def test_route_does_not_500(auth_client, path):
    resp = auth_client.get(path)
    assert resp.status_code != 500, f'{path} respondio 500 -- revisa el traceback del servidor'
    assert resp.status_code < 500, f'{path} respondio {resp.status_code} (error de servidor)'

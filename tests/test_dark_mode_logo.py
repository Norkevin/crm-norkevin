"""Kevin: 'este logo esta en descargas para que lo pongas en la version
de modo oscuro' -- el logo normal (fondo claro) se pierde/ensucia sobre
el header oscuro, asi que se agrega una segunda variante pensada para
fondo oscuro y se alterna por CSS segun data-theme, sin depender de JS."""


def test_base_template_renders_both_logo_variants(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'logo-flow-crm.png' in html
    assert 'logo-flow-crm-dark.png' in html
    assert 'brand-logo-light' in html
    assert 'brand-logo-dark' in html


def test_dark_logo_file_exists_in_static():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'logo-flow-crm-dark.png')
    assert os.path.exists(path)


def test_css_hides_light_logo_and_shows_dark_logo_in_dark_theme(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert ':root[data-theme="dark"] .brand-logo-light { display: none; }' in html
    assert ':root[data-theme="dark"] .brand-logo-dark { display: block; }' in html

"""Kevin: 'quiero agregar en setting la opcion de ver todo en modo normal
y en modo oscuro, para cuando trabaje de noche no molestarme la vista
tanto'. La app usa color hardcodeado (#fff, etc.) en decenas de templates
ademas de las variables --sn-*, asi que en vez de reescribir cada pantalla
se invierte el documento entero via CSS (html[data-theme="dark"] { filter:
invert(1) hue-rotate(180deg); }) y se contra-invierte <img> para que el
logo/fotos no salgan con los colores al reves. Preferencia guardada en
localStorage (no hay backend involucrado, es puramente visual del
navegador de Kevin)."""


def test_base_template_sets_theme_before_paint_to_avoid_flash(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert "localStorage.getItem('flowcrm-theme')" in html
    assert "document.documentElement.setAttribute('data-theme'" in html


def test_base_template_has_dark_mode_invert_css(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'html[data-theme="dark"]' in html
    assert 'invert(1)' in html
    assert 'html[data-theme="dark"] img' in html, 'las imagenes reales deben contra-invertirse'


def test_base_template_exposes_set_app_theme_function(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'function setAppTheme(mode)' in html
    assert "localStorage.setItem('flowcrm-theme'" in html


def test_settings_page_has_light_and_dark_buttons(auth_client):
    resp = auth_client.get('/settings')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "setAppTheme('light')" in html
    assert "setAppTheme('dark')" in html
    assert 'Modo oscuro' in html
    assert 'Modo normal' in html

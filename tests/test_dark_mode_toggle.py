"""Kevin: 'quiero agregar en setting la opcion de ver todo en modo normal
y en modo oscuro, para cuando trabaje de noche no molestarme la vista
tanto'. Primera version invertia el documento entero via CSS filter, pero
Kevin la reviso a fondo y pidio pulirla con niveles de superficie reales
(fondo / tarjeta / input / hover distintos entre si) porque el invert no
permite esa jerarquia -- todo es la misma imagen invertida. Se reemplazo
por variables --sn-* redefinidas bajo :root[data-theme="dark"] que el
resto del CSS ya usaba (o se migro a usar) en vez de colores fijos.
Preferencia guardada en localStorage (no hay backend involucrado, es
puramente visual del navegador de Kevin)."""


def test_base_template_sets_theme_before_paint_to_avoid_flash(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert "localStorage.getItem('flowcrm-theme')" in html
    assert "document.documentElement.setAttribute('data-theme'" in html


def test_base_template_defines_dark_surface_levels_not_just_invert(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'invert(1)' not in html, 'el invert global no permite distinguir fondo/tarjeta/input entre si'
    assert ':root[data-theme="dark"]' in html
    # Deben existir al menos 3 niveles de superficie distintos entre si
    # (fondo, tarjeta/input, hover) -- no solo negro puro repetido.
    assert '--sn-canvas: #05070a' in html
    assert '--sn-white: #090d12' in html
    assert '--sn-surface-hover: #121a23' in html
    assert '--sn-ink: #f3f6f9' in html, 'el texto primario debe aclararse en modo oscuro'


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

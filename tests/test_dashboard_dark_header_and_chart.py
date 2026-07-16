"""Kevin (sobre el modo oscuro): 'lo que te encerre en rojo no se ve bien'
(el selector de tenant y los botones de campana/cuenta en la esquina
superior derecha tenian fondo blanco fijo -- invisibles/ilegibles contra
el header oscuro) 'y las lineas blancas en la grafica no me gustan
tampoco quitalas' (las guias punteadas del grafico de ingresos)."""


def test_header_icon_buttons_and_tenant_switcher_use_theme_variables(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert '.sn-icon-button {' in html
    # No deben quedar fondos fijos #fff para estos elementos del header --
    # eso es lo que los hacia invisibles en modo oscuro.
    icon_button_block = html[html.index('.sn-icon-button {'):html.index('.sn-icon-button {') + 300]
    assert 'background: #fff' not in icon_button_block
    assert 'var(--sn-white)' in icon_button_block

    tenant_block = html[html.index('.tenant-switcher {'):html.index('.tenant-switcher {') + 300]
    assert 'background: #fff' not in tenant_block
    assert 'var(--sn-white)' in tenant_block


def test_account_and_notification_menus_use_theme_variables(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    menu_block = html[html.index('.sn-notifications-menu,'):html.index('.sn-notifications-menu,') + 400]
    assert 'background: #fff' not in menu_block
    assert 'var(--sn-white)' in menu_block


def test_dashboard_chart_no_longer_draws_dashed_grid_lines(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'stroke-dasharray' not in html
    assert '#EEF0F3' not in html, 'la linea gris clara de la grilla no debe seguir hardcodeada'
    assert '#9AA1AB' not in html, 'el texto de los ejes no debe seguir hardcodeado, debe usar var(--sn-muted)'

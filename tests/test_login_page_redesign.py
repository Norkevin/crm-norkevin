"""Kevin: 'quiero que la portada de la pagina se vea asi, al inciar sesion
que se vea asi de profesional' -- mando una captura de un login split-
screen (panel oscuro con marca a la izquierda, tarjeta blanca de sign-in a
la derecha). El unico metodo real de login de este CRM es Google OAuth
(ALLOWED_LOGIN_EMAILS) -- no se agregaron campos de email/password falsos
que no hacen nada, solo se re-diseño visualmente alrededor del boton de
Google que ya funcionaba."""


def test_login_page_has_split_hero_and_card(client):
    resp = client.get('/login')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'login-hero' in html
    assert 'login-panel' in html
    assert 'login-card' in html


def test_login_page_still_only_offers_google_auth_no_fake_fields(client):
    resp = client.get('/login')
    html = resp.get_data(as_text=True)
    assert 'Iniciar sesion con Google' in html
    assert '/auth/google/login/start' in html
    assert 'type="password"' not in html, 'no hay login por password de verdad, no se debe fingir uno'


def test_login_page_uses_the_dark_logo_on_both_panels(client):
    """Kevin: 'diseño libre pero para el login quiero algo bonito que no se
    deforme' -- el rediseño unifico ambos paneles a tema oscuro (antes la
    tarjeta de la derecha era una isla blanca sobre gris), asi que ahora los
    dos usan el logo transparente pensado para fondos oscuros; el logo
    claro (pensado para fondos blancos) ya no aplica en esta pagina."""
    resp = client.get('/login')
    html = resp.get_data(as_text=True)
    assert html.count('logo-flow-crm-dark.png') == 2
    assert 'logo-flow-crm.png"' not in html


def test_login_error_messages_still_render(client):
    resp = client.get('/login?error=cuenta_no_autorizada')
    html = resp.get_data(as_text=True)
    assert 'no tiene acceso a este CRM' in html


def test_login_hero_hidden_on_narrow_viewports_via_media_query(client):
    resp = client.get('/login')
    html = resp.get_data(as_text=True)
    assert '@media (max-width: 900px)' in html
    assert '.login-hero { display: none; }' in html

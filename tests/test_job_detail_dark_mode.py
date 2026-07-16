"""Kevin: 'jobs se ve espantoso con el modo oscuro y el logo de flow crm
no quedo bien'. Dos causas:

1. El logo oscuro que Kevin subio era la imagen de marketing completa
   (1448x1086, con el logotype ocupando solo una franja chica en el
   centro), no un logo recortado -- al forzarlo a height:32px en el
   header se veia diminuto y aplastado contra los items del nav. Se
   recorto a la caja real del logotype (bbox detectado automaticamente
   sobre el fondo negro) para que quede a la misma proporcion que el
   logo claro (~5:1).

2. job_detail.html nunca se toco en la reconstruccion del modo oscuro --
   .workflow-step, .workflow-step-name, .sn-list-item, .sn-list-title y
   .workflow-rail.production/.delivery seguian con colores fijos (#fff,
   colores de texto casi negros, rail casi negro sobre fondo casi negro),
   asi que la lista de pasos del workflow y las facturas quedaban
   ilegibles/invisibles en modo oscuro."""


def test_dark_logo_has_the_same_aspect_ratio_as_the_light_logo():
    import os
    from PIL import Image
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    light = Image.open(os.path.join(base, 'static', 'logo-flow-crm.png'))
    dark = Image.open(os.path.join(base, 'static', 'logo-flow-crm-dark.png'))
    light_ratio = light.size[0] / light.size[1]
    dark_ratio = dark.size[0] / dark.size[1]
    assert abs(light_ratio - dark_ratio) < 0.5, (
        f'el logo oscuro (ratio {dark_ratio:.2f}) deberia tener una proporcion similar '
        f'al logo claro (ratio {light_ratio:.2f}) para no verse aplastado en el header'
    )


def test_workflow_step_uses_theme_variables_not_hardcoded_white(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    block = html[html.index('.workflow-step {'):html.index('.workflow-step {') + 400]
    assert 'background: #fff' not in block
    assert 'var(--sn-white)' in block


def test_workflow_step_name_uses_ink_variable_for_dark_mode_legibility(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    # ojo: '.workflow-step.skipped .workflow-step-name {' tambien contiene la
    # subcadena '.workflow-step-name {', hay que anclar al selector standalone.
    idx = html.index('\n.workflow-step-name {')
    block = html[idx:idx + 150]
    assert 'color: #30363c' not in block
    assert 'var(--sn-ink)' in block


def test_sn_list_item_uses_theme_variables(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    block = html[html.index('.sn-list-item {'):html.index('.sn-list-item {') + 200]
    assert 'background: #fff' not in block
    assert 'var(--sn-white)' in block


def test_workflow_rail_gets_a_lighter_dark_mode_override_so_it_stays_visible(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert ':root[data-theme="dark"] .workflow-rail.production' in html
    assert ':root[data-theme="dark"] .workflow-rail.delivery' in html

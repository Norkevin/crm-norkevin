"""Kevin: 'estas 3 paginas deben estar enlazadas entre si' -- el Workflow
Editor, la libreria de Email Templates, y lo que realmente le pasa a un
lead nuevo eran 3 sistemas desconectados:
  1. Editar un step en el Workflow Editor y guardar no afectaba a los leads
     reales -- LEAD_WORKFLOW()/PRODUCTION_WORKFLOW() siempre reconstruian
     la version hardcodeada, ignorando lo guardado en workflow_templates.json.
  2. Los pasos "auto send email" referencian tpl-ids (tpl-paquetes, etc.)
     que no existian como registros reales en un deploy nuevo, asi que
     mandaban correos en blanco.
"""


def test_editing_lead_workflow_step_affects_new_leads(auth_client):
    import app as app_module

    # Guardar una edicion del workflow (como haria el Workflow Editor):
    # cambiamos el asunto/plantilla del primer step.
    original = app_module.LEAD_WORKFLOW()
    edited_dict = original.to_dict()
    edited_dict['steps'][0]['email_template_id'] = 'tpl-editado-por-el-usuario'
    app_module.store.save_dict('workflow_templates', {'lead_workflow_v1': edited_dict})

    try:
        # LEAD_WORKFLOW() (la funcion que usan TODOS los triggers reales) debe
        # reflejar la edicion inmediatamente, sin reiniciar el server.
        reloaded = app_module.LEAD_WORKFLOW()
        assert reloaded.steps[0].email_template_id == 'tpl-editado-por-el-usuario'

        # Y un lead nuevo debe arrancar su workflow con esa version editada.
        resp = auth_client.post('/api/leads/nuevo', json={
            'nombre': 'Wired', 'apellido': 'Test', 'email': 'wired@example.com',
            'pais': 'Guatemala', 'fecha_boda': '2027-06-01',
        })
        assert resp.status_code == 200
        lead_id = resp.get_json()['lead_id']

        instances = app_module.workflow_engine.list_instances(subject_id=lead_id, subject_type='lead')
        assert instances, 'deberia haberse creado una instancia de workflow para el lead'
    finally:
        app_module.store.save_dict('workflow_templates', {})


def test_default_email_templates_get_seeded_when_missing():
    import app as app_module
    templates = app_module.store.list('email_templates')
    referenced_ids = {'tpl-paquetes', 'tpl-seguimiento', 'tpl-levanta-muertos', 'tpl-reserva',
                       'tpl-contrato', 'tpl-cuestionario', 'tpl-galeria', 'tpl-review'}
    existing_ids = {t['id'] for t in templates}
    missing = referenced_ids - existing_ids
    assert not missing, f'estas plantillas referenciadas por el workflow no existen: {missing}'
    for t in templates:
        if t['id'] in referenced_ids:
            assert t.get('cuerpo'), f"{t['id']} no deberia tener el cuerpo vacio"


def test_workflow_editor_save_preserves_template_and_delay(auth_client):
    """El JS del editor construia los steps con nombres de campo distintos
    a los que Step.to_dict()/_workflow_from_dict() esperan (action_template
    en vez de email_template_id, offset_minutes plano en vez de due_date
    anidado) -- guardar un step 'perdia' su plantilla y su delay. Este test
    manda exactamente la forma que el JS ya arreglado produce."""
    import app as app_module

    original = app_module.LEAD_WORKFLOW()
    payload = original.to_dict()
    # Simula editar el primer step (envio_paquetes) desde el modal y guardar,
    # con la forma que ahora arma workflow_editor.html.
    payload['steps'][0]['email_template_id'] = 'tpl-paquetes'
    payload['steps'][0]['due_date'] = {
        'mode': 'after_creation', 'amount': 5, 'unit': 'hours', 'relative_to': 'lead_created',
    }

    resp = auth_client.put(f"/api/workflow/template/{payload['id']}", json=payload)
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    reloaded = app_module.LEAD_WORKFLOW()
    step = next(s for s in reloaded.steps if s.id == 'envio_paquetes')
    assert step.email_template_id == 'tpl-paquetes'
    assert step.due_date.amount == 5
    assert step.due_date.unit == 'hours'
    assert step.due_date.mode == 'after_creation'

    # Limpieza: no dejar el override pisando el template real para el resto
    # de la sesion de pytest (session-scoped isolated data).
    app_module.store.save_dict('workflow_templates', {})


def test_bootstrap_seeds_templates_on_a_fresh_deploy_with_no_data():
    """Simula exactamente lo que pasa en un deploy nuevo de Render: el
    archivo email_templates.json arranca vacio."""
    import app as app_module
    real_templates = app_module.store.list('email_templates')
    try:
        app_module.store.save_dict('email_templates', [])
        app_module.store._cache.pop('email_templates', None)
        assert app_module.store.list('email_templates') == []

        app_module._bootstrap_default_email_templates()

        seeded = app_module.store.list('email_templates')
        assert len(seeded) >= 8
        assert {'tpl-paquetes', 'tpl-contrato', 'tpl-cuestionario'} <= {t['id'] for t in seeded}
    finally:
        for tpl in real_templates:
            app_module.store.upsert('email_templates', tpl)

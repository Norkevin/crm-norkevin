"""Cuando un cliente llena el formulario publico, Kevin debe recibir un
correo avisandole del lead nuevo (antes no pasaba nada). Tambien prueba que
el bell de notificaciones (poll en vivo) refleje el lead sin recargar."""


def test_public_lead_triggers_email_notification_to_company(client):
    """STAGE 2 (agosto 2026): _notify_new_lead ya no entrega de inmediato --
    tambien pasa por la cola de aprobacion (queue_email), asi que el aviso
    vive en pending_emails esperando revision, no en mail_log."""
    resp = client.post('/api/leads/nuevo', json={
        'nombre': 'Maria', 'apellido': 'Test', 'email': 'maria@example.com',
        'pais': 'Guatemala', 'fecha_boda': '2027-06-01',
    })
    assert resp.status_code == 200

    import app as app_module
    pendientes = app_module.store.list('pending_emails')
    match = [m for m in pendientes if 'Maria Test' in (m.get('subject') or '')]
    assert match, 'deberia haber un correo de notificacion de lead nuevo esperando aprobacion'
    assert match[0]['status'] == 'pending'
    assert match[0]['to']  # manda a algun destinatario configurado
    assert 'Nuevo lead' in match[0]['subject']


def test_captacion_form_also_triggers_notification(client):
    """Ver docstring del test anterior: STAGE 2 encola en vez de entregar."""
    resp = client.post('/api/captacion', json={
        'nombre': 'Pedro Captacion', 'email': 'pedro@example.com',
    })
    assert resp.status_code == 200

    import app as app_module
    pendientes = app_module.store.list('pending_emails')
    match = [m for m in pendientes if 'Pedro Captacion' in (m.get('subject') or '')]
    assert match, 'el formulario de captacion tambien debe notificar'


def test_notifications_endpoint_reflects_new_lead_without_reload(auth_client):
    resp = auth_client.post('/api/leads/nuevo', json={
        'nombre': 'Ana', 'apellido': 'Notif', 'email': 'ana.notif@example.com',
        'pais': 'Guatemala', 'fecha_boda': '2027-06-01',
    })
    assert resp.status_code == 200

    resp2 = auth_client.get('/api/notifications/recent')
    assert resp2.status_code == 200
    data = resp2.get_json()
    assert data['ok'] is True
    titles = [n['title'] for n in data['notifications']]
    assert any('Ana Notif' in t for t in titles)

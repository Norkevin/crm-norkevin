"""Kevin (celular real, iPhone): 'jobs y clientes lo dejaste horrible no
se entiende nada' -- las celdas con avatar+nombre+subtitulo (mas anchas
que texto simple) se desbordaban de la tarjeta en mobile real porque
estaban metidas en el patron generico label-izquierda/valor-derecha
(flexbox sin min-width:0 no encoge el contenido, y .sn-table-shell tenia
overflow-x:auto de una regla vieja que escondia el desborde en vez de
forzarlo a acomodarse). La celda de nombre ahora usa .stack-full: bloque
de ancho completo, sin la etiqueta redundante."""


def test_stack_full_class_present_on_name_cells(auth_client):
    import app as app_module
    import uuid
    suffix = uuid.uuid4().hex[:6]
    app_module.store.upsert('payments', {
        'id': f'pay-overflow-{suffix}', 'invoice_id': f'INV-{suffix}',
        'amount': 100, 'status': 'Pendiente', 'due_date': '2027-01-01',
        'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('leads', {
        'id': f'lead-overflow-{suffix}', 'nombre': f'Overflow Test {suffix}',
        'email': f'overflow-{suffix}@example.com', 'status': 'Nuevo',
        'tenant_id': 'tenant-norkevin',
    })

    for path, table_id in (('/jobs', 'jobs-table'), ('/clients', 'clients-table'),
                             ('/payments', 'payments-table'), ('/leads', 'leads-table')):
        resp = auth_client.get(path)
        html = resp.get_data(as_text=True)
        assert f'id="{table_id}"' in html
        assert 'class="sn-ellipsis stack-full"' in html, f'{path} deberia usar stack-full en la celda de nombre'


def test_stack_mobile_shell_does_not_allow_silent_horizontal_scroll(auth_client):
    """La regla vieja .sn-table-shell { overflow-x: auto; } (pensada para
    tablas SIN stack-mobile) escondia el desborde real en vez de forzar
    el wrap -- debe estar anulada cuando stack-mobile esta activo."""
    resp = auth_client.get('/jobs')
    html = resp.get_data(as_text=True)
    assert '.sn-table-shell.stack-mobile { overflow-x: visible; }' in html


def test_app_header_respects_safe_area_inset_top(auth_client):
    """El header pegado con position:sticky;top:0 se montaba encima del
    reloj/notch del iPhone en modo standalone -- necesita
    env(safe-area-inset-top)."""
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'env(safe-area-inset-top)' in html

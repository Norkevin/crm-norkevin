"""El cliente debe poder ver que incluye un paquete, ya sea una cotizacion
vieja (un solo paquete) o nueva (varias opciones, aceptada o no)."""


def test_legacy_quote_resolves_from_flat_fields(flask_app):
    import app as app_module

    quote = {
        'paquete_nombre': 'Gold',
        'precio_total': 18000,
        'incluye': ['Cobertura 10h', 'Video resumen'],
    }
    name, incluye = app_module._resolve_quote_package(quote)
    assert name == 'Gold'
    assert incluye == ['Cobertura 10h', 'Video resumen']


def test_multi_option_quote_not_yet_accepted_falls_back_to_first_option(flask_app):
    import app as app_module

    quote = {
        'options': [
            {'id': 'opt-1', 'name': 'Silver', 'precio_total': 10000, 'incluye': ['4h cobertura']},
            {'id': 'opt-2', 'name': 'Gold', 'precio_total': 18000, 'incluye': ['10h cobertura']},
        ],
    }
    name, incluye = app_module._resolve_quote_package(quote)
    assert name == 'Silver'
    assert incluye == ['4h cobertura']


def test_multi_option_quote_uses_the_option_the_client_actually_picked(flask_app):
    import app as app_module

    quote = {
        'options': [
            {'id': 'opt-1', 'name': 'Silver', 'precio_total': 10000, 'incluye': ['4h cobertura']},
            {'id': 'opt-2', 'name': 'Gold', 'precio_total': 18000, 'incluye': ['10h cobertura']},
        ],
        'selected_option_id': 'opt-2',
    }
    name, incluye = app_module._resolve_quote_package(quote)
    assert name == 'Gold'
    assert incluye == ['10h cobertura']


def test_quote_with_no_incluye_data_does_not_crash(flask_app):
    import app as app_module

    quote = {'paquete_nombre': 'Basico'}
    name, incluye = app_module._resolve_quote_package(quote)
    assert name == 'Basico'
    assert incluye == []

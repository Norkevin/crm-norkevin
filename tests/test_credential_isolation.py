"""Aislamiento de credenciales de Gmail entre empresas.

TRAMPA IMPORTANTE que ya casi provoca una correccion equivocada:

    tenant-norkevin              -> se llama "ASTRAL WEDDINGS"
    tenant-norkevin-photography  -> se llama "Norkevin Photography"

El id `tenant-norkevin` es heredado de cuando el proyecto era solo de
Norkevin y Astral fue la primera (y unica) cuenta. Por eso el archivo
google_token_tenant-norkevin.json contiene astralweddingsgt@gmail.com y
eso es CORRECTO, no un token mal asignado.

Si alguien "arregla" ese nombre asumiendo que tenant-norkevin es Norkevin,
va a mover la credencial de Astral a la empresa equivocada y a reproducir el
incidente. El primer test de aca existe para que eso falle en rojo.
"""
import importlib
import json

import pytest

from src import gmail_delivery

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def test_el_id_tenant_norkevin_es_en_realidad_astral():
    """Fija el mapeo id -> empresa para que no se 'corrija' al reves."""
    with open('data/tenants.json', encoding='utf-8') as fh:
        tenants = {t['id']: t for t in json.load(fh)}

    assert tenants[ASTRAL]['name'] == 'ASTRAL WEDDINGS'
    assert tenants[ASTRAL]['login_email'] == 'astralweddingsgt@gmail.com'
    assert tenants[NORKEVIN]['name'] == 'Norkevin Photography'
    assert tenants[NORKEVIN]['login_email'] == 'norkevinfoto@gmail.com'


@pytest.fixture
def gmail_aislado(monkeypatch, tmp_path):
    """gmail_delivery con un directorio de datos limpio y sin resolver."""
    monkeypatch.setenv('CRM_DATA_DIR', str(tmp_path))
    gd = importlib.reload(gmail_delivery)
    monkeypatch.setattr(gd, 'tenant_resolver', lambda: None)
    return gd


def test_cada_empresa_guarda_su_credencial_por_separado(gmail_aislado):
    gd = gmail_aislado
    gd.save_token({'access_token': 'a1', 'refresh_token': 'r1', 'email': 'astral@x.com'},
                  tenant_id=ASTRAL)
    gd.save_token({'access_token': 'a2', 'refresh_token': 'r2', 'email': 'norkevin@x.com'},
                  tenant_id=NORKEVIN)

    assert gd.connected_email(tenant_id=ASTRAL) == 'astral@x.com'
    assert gd.connected_email(tenant_id=NORKEVIN) == 'norkevin@x.com'
    assert gd._token_path(tenant_id=ASTRAL) != gd._token_path(tenant_id=NORKEVIN)


def test_norkevin_nunca_usa_la_credencial_de_astral(gmail_aislado):
    """Solo Astral conectada: Norkevin NO debe poder enviar por prestamo."""
    gd = gmail_aislado
    gd.save_token({'access_token': 'a1', 'refresh_token': 'r1', 'email': 'astral@x.com'},
                  tenant_id=ASTRAL)

    assert gd.is_connected(tenant_id=ASTRAL) is True
    assert gd.is_connected(tenant_id=NORKEVIN) is False
    assert gd.connected_email(tenant_id=NORKEVIN) == ''


def test_astral_nunca_usa_la_credencial_de_norkevin(gmail_aislado):
    """El caso simetrico: tampoco al reves."""
    gd = gmail_aislado
    gd.save_token({'access_token': 'a2', 'refresh_token': 'r2', 'email': 'norkevin@x.com'},
                  tenant_id=NORKEVIN)

    assert gd.is_connected(tenant_id=NORKEVIN) is True
    assert gd.is_connected(tenant_id=ASTRAL) is False


def test_una_credencial_legacy_en_disco_no_se_presta_a_nadie(gmail_aislado, tmp_path):
    """El agujero exacto del incidente: existia un google_token.json global
    (sin cuenta) y cualquier contexto sin tenant lo usaba para enviar."""
    gd = gmail_aislado
    legacy = tmp_path / 'google_token.json'
    legacy.write_text(json.dumps({
        'access_token': 'viejo', 'refresh_token': 'viejo-r',
        'email': 'astralweddingsgt@gmail.com',
    }), encoding='utf-8')

    # Ninguna empresa la hereda...
    assert gd.is_connected(tenant_id=ASTRAL) is False
    assert gd.is_connected(tenant_id=NORKEVIN) is False
    # ...y sin cuenta tampoco hay a que caer.
    assert gd.is_connected() is False
    assert gd._token_path() is None
    assert legacy.exists(), 'el archivo legacy no debe borrarse solo'


def test_sin_sesion_no_hay_credencial_aunque_ambas_esten_conectadas(gmail_aislado):
    """El worker fuera de request es el contexto donde ocurrio el incidente."""
    gd = gmail_aislado
    gd.save_token({'access_token': 'a1', 'refresh_token': 'r1', 'email': 'astral@x.com'},
                  tenant_id=ASTRAL)
    gd.save_token({'access_token': 'a2', 'refresh_token': 'r2', 'email': 'norkevin@x.com'},
                  tenant_id=NORKEVIN)

    assert gd.is_connected() is False, \
        'sin cuenta no debe elegir ninguna de las dos credenciales'


def test_desconectar_una_empresa_no_afecta_a_la_otra(gmail_aislado):
    gd = gmail_aislado
    gd.save_token({'access_token': 'a1', 'refresh_token': 'r1', 'email': 'astral@x.com'},
                  tenant_id=ASTRAL)
    gd.save_token({'access_token': 'a2', 'refresh_token': 'r2', 'email': 'norkevin@x.com'},
                  tenant_id=NORKEVIN)

    gd.disconnect(tenant_id=ASTRAL)

    assert gd.is_connected(tenant_id=ASTRAL) is False
    assert gd.is_connected(tenant_id=NORKEVIN) is True, \
        'desconectar una empresa no debe tumbar la conexion de la otra'


def test_no_se_puede_guardar_una_credencial_sin_cuenta(gmail_aislado):
    """Guardar sin cuenta volveria a crear un token global huerfano."""
    gd = gmail_aislado
    with pytest.raises(RuntimeError):
        gd.save_token({'access_token': 'x', 'refresh_token': 'y', 'email': 'z@x.com'})

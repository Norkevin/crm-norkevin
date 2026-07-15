import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGES_LINK = "https://drive.google.com/file/d/1irm5P-Oru9fUo55B58y4XBuUrMj76y3H/view?usp=sharing"


def _load_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_astral_wedding_packages_match_confirmed_catalog():
    packages = _load_json("data/packages.json")
    prices = {package["name"]: package["price"] for package in packages}

    expected_prices = {
        "PHOTO GOLD": 13500,
        "PHOTO SILVER": 9500,
        "PHOTO BRONZE": 6500,
        "VIDEO GOLD": 13500,
        "VIDEO SILVER": 9500,
        "VIDEO BRONZE": 6500,
        "GOLD MIX": 22500,
        "SILVER MIX": 16500,
        "HORA ADICIONAL": 1000,
        "SESION SAVE THE DATE": 1500,
        "SESION DE COMPROMISO": 1900,
        "SEGUNDO FOTOGRAFO": 2000,
    }

    assert prices == expected_prices


def test_send_packages_template_uses_public_catalog_link():
    templates = _load_json("data/email_templates.json")
    package_template = next(template for template in templates if template["id"] == "tpl-paquetes")

    assert PACKAGES_LINK in package_template["cuerpo"]
    assert package_template["adjuntos"] == []
    assert "Paquete Basico" not in package_template["cuerpo"]
    assert "Paquete Premium" not in package_template["cuerpo"]
    assert "Paquete Deluxe" not in package_template["cuerpo"]


def test_seed_send_packages_template_matches_active_template():
    active = next(template for template in _load_json("data/email_templates.json") if template["id"] == "tpl-paquetes")
    seeded = next(template for template in _load_json("data/seeds/email_templates.default.json") if template["id"] == "tpl-paquetes")

    assert seeded["cuerpo"] == active["cuerpo"]
    assert seeded["adjuntos"] == active["adjuntos"]


def test_quote_builder_shows_astral_catalog(auth_client):
    import app as app_module

    client_id = "client-quote-builder-astral"
    job_id = "job-quote-builder-astral"
    app_module.store.upsert("clients", {
        "id": client_id,
        "first_name": "Astral",
        "last_name": "Client",
        "email": "astral-client@example.com",
        "tenant_id": "tenant-norkevin",
    })
    app_module.upsert_job({
        "id": job_id,
        "nombre": "Astral Quote Builder",
        "client_id": client_id,
        "status": "Confirmado",
        "tenant_id": "tenant-norkevin",
    })

    resp = auth_client.get(f"/jobs/{job_id}/quote/pick-and-choose/new")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "PHOTO GOLD" in html
    assert "GOLD MIX" in html
    assert "Photo Collection" in html
    assert "Search packages" in html

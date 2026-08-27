"""Aislamiento de marca en documentos cliente-facing (prioridad 3, bloque
de cierre de brechas -- agosto 2026).

pdf_generator.py generaba TODO su contenido con "Astral Weddings" /
"info@astralweddings.com" hardcodeado, sin importar el tenant real del
contrato/cotizacion/factura (ver STABILIZATION_EXECUTION_REPORT.md,
seccion "hardcodes de marca"). Ahora cada funcion recibe un `brand` dict
resuelto via `resolve_pdf_brand(tenant_id)`, que a su vez usa la capa
canonica `src.tenant_brand_map` -- nunca un tenant_id-string-match ni un
default fijo.

Estos tests corren SIN Flask (import directo de src.pdf_generator +
src.tenant_brand_map + src.storage) -- confirmado ejecutable en el sandbox
de esta fase (ver STABILIZATION_EXECUTION_REPORT.md,
VERIFIED_IN_THIS_SANDBOX). El render binario completo (extraer texto del
PDF y confirmar 0 bytes de la otra marca) se deja para la corrida en
Windows con las dependencias completas, tal como pidio Kevin -- aca se
prueba la resolucion de identidad y que la generacion no explota con datos
sinteticos de las dos marcas."""
import pytest

from src.pdf_generator import (
    resolve_pdf_brand, generate_quote_pdf, generate_contract_pdf,
    generate_invoice_pdf, contract_terms, _UNRESOLVED_BRAND,
)

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def test_resolve_pdf_brand_distingue_las_dos_marcas():
    b_astral = resolve_pdf_brand(ASTRAL)
    b_norkevin = resolve_pdf_brand(NORKEVIN)

    assert b_astral['display_name'] != b_norkevin['display_name']
    assert 'Astral' in b_astral['display_name']
    assert 'Norkevin' in b_norkevin['display_name']
    assert b_astral['email'] != b_norkevin['email']
    assert 'astralweddingsgt' in b_astral['email']
    assert 'norkevinfoto' in b_norkevin['email']


def test_resolve_pdf_brand_sin_tenant_no_asume_ninguna_marca_real():
    """El caso peligroso: tenant_id=None NO debe caer en Astral (ni en
    ninguna marca real) por default -- eso seria el mismo bug con otro
    nombre. Debe usar el placeholder neutro explicito."""
    assert resolve_pdf_brand(None) == _UNRESOLVED_BRAND
    assert resolve_pdf_brand('tenant-que-no-existe') == _UNRESOLVED_BRAND
    assert 'Astral' not in _UNRESOLVED_BRAND['display_name']
    assert 'Norkevin' not in _UNRESOLVED_BRAND['display_name']


@pytest.mark.parametrize('tenant_id,other_needle', [
    (ASTRAL, 'Norkevin'),
    (NORKEVIN, 'Astral'),
])
def test_contract_terms_no_menciona_la_otra_marca(tenant_id, other_needle):
    brand = resolve_pdf_brand(tenant_id)
    job = {'price_total': 15000, 'plan_pago': 2, 'cuota_monto': 7500}
    terms = contract_terms(job, brand=brand)
    full_text = ' '.join(body for _title, body in terms)
    assert other_needle not in full_text
    assert brand['display_name'] in full_text


@pytest.mark.parametrize('tenant_id', [ASTRAL, NORKEVIN])
def test_generate_quote_pdf_con_marca_no_explota(tenant_id):
    brand = resolve_pdf_brand(tenant_id)
    quote = {'id': 'quote-test', 'precio_total': 1000,
             'paquete_nombre': 'Paquete Test', 'incluye': ['Item 1'],
             'created': '2026-01-01'}
    lead = {'nombre': 'Cliente Test', 'email': 'cliente@test.com',
            'telefono': '', 'fecha_tentativa': '', 'locacion': ''}
    pdf_bytes = generate_quote_pdf(quote, lead, brand=brand)
    assert pdf_bytes.startswith(b'%PDF')
    assert len(pdf_bytes) > 500


@pytest.mark.parametrize('tenant_id', [ASTRAL, NORKEVIN])
def test_generate_contract_pdf_con_marca_no_explota(tenant_id):
    brand = resolve_pdf_brand(tenant_id)
    contract = {'id': 'contract-test'}
    job = {'price_total': 15000, 'plan_pago': 1}
    client = {'first_name': 'Cliente', 'last_name': 'Test', 'phone': '',
              'email': 'cliente@test.com', 'address': ''}
    pdf_bytes = generate_contract_pdf(contract, job, client, brand=brand)
    assert pdf_bytes.startswith(b'%PDF')


@pytest.mark.parametrize('tenant_id', [ASTRAL, NORKEVIN])
def test_generate_invoice_pdf_con_marca_no_explota(tenant_id):
    brand = resolve_pdf_brand(tenant_id)
    invoice = {'invoice_id': 'inv-test', 'amount': 500, 'status': 'Pendiente',
               'due_date': '2026-12-01', 'concepto': 'Anticipo'}
    job = {'nombre': 'Boda Test', 'package': 'Basico'}
    client = {'first_name': 'Cliente', 'last_name': 'Test', 'phone': '',
              'email': 'cliente@test.com', 'address': ''}
    pdf_bytes = generate_invoice_pdf(invoice, job, client, brand=brand)
    assert pdf_bytes.startswith(b'%PDF')


def test_dos_marcas_generan_pdfs_distintos_para_los_mismos_datos():
    """Mismo quote/lead sintetico, distinto tenant -> los bytes del PDF
    deben diferir (si fueran identicos, brand no se estaria usando)."""
    quote = {'id': 'quote-mismo', 'precio_total': 2000,
             'paquete_nombre': 'Paquete', 'incluye': [], 'created': '2026-01-01'}
    lead = {'nombre': 'Cliente', 'email': 'c@test.com', 'telefono': '',
            'fecha_tentativa': '', 'locacion': ''}
    pdf_astral = generate_quote_pdf(quote, lead, brand=resolve_pdf_brand(ASTRAL))
    pdf_norkevin = generate_quote_pdf(quote, lead, brand=resolve_pdf_brand(NORKEVIN))
    assert pdf_astral != pdf_norkevin

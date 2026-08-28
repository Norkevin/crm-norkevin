"""
quote_numbering.py - Numeracion de cotizaciones por empresa (NORK-2026-0041,
AST-2026-0018).

Separado a proposito de tenant_brand_map.py: Kevin fue explicito en que la
resolucion segura de marca/tenant existente no se toca. Este modulo LEE la
marca canonica via tenant_brand_map.resolve_brand(), nunca la reemplaza,
duplica ni le agrega un fallback propio. Si la marca no resuelve (tenant_id
desconocido o legado sin confirmar), esto tampoco resuelve -- mismo
fail-hard: una cotizacion sin numero es un problema visible y corregible a
mano; una cotizacion numerada con el prefijo de la empresa equivocada es
exactamente la clase de bug que origino el incidente del 16 de agosto.

El contador en si (atomico, sin colisiones bajo concurrencia) vive en
JsonStore.next_sequence_number() -- ver src/storage.py. Este modulo solo
sabe traducir tenant_id -> prefijo y componer el string final.
"""
from . import tenant_brand_map

# Un prefijo por brand_key, no por tenant_id: si algun dia se rota el
# tenant_id interno (tenant_brand_map.py documenta que ya paso una vez),
# el prefijo de numeracion no se mueve solo porque cambio un id tecnico.
_PREFIJO_POR_BRAND_KEY = {
    'astral': 'AST',
    'norkevin': 'NORK',
    'ramiro-cruz': 'RC',
}


def prefix_for_tenant(tenant_id):
    """Prefijo de numeracion para tenant_id, o None si la marca no resuelve
    todavia o no tiene prefijo asignado. Nunca adivina ni cae a un default
    compartido entre empresas."""
    if not tenant_id:
        return None
    try:
        brand = tenant_brand_map.resolve_brand(tenant_id)
    except tenant_brand_map.UnresolvedBrandError:
        return None
    return _PREFIJO_POR_BRAND_KEY.get(brand.brand_key)


def format_quote_number(prefix, year, seq):
    return f'{prefix}-{year}-{seq:04d}'

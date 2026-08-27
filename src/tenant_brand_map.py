"""
tenant_brand_map.py - Identidad canonica de marca, separada del tenant_id.

Nace del incidente del 16 de agosto de 2026 (correos de ASTRAL WEDDINGS
mandados a clientes de Norkevin Photography) y de la regla ya fijada en
SEGURIDAD_AISLAMIENTO.md y tests/test_credential_isolation.py:

    NINGUN desarrollador debe deducir de que empresa es algo leyendo el
    string del id. `tenant-norkevin` es, hoy, la cuenta real de ASTRAL
    WEDDINGS -- es un id heredado de cuando el proyecto era solo de
    Norkevin y Astral fue la primera cuenta creada. Los ids no se
    renombran (viven en tokens de Gmail en disco, en tenant_id de miles
    de registros, y en enlaces publicos ya enviados).

Este modulo es la UNICA fuente de verdad para resolver:

    internal_tenant_id -> brand_key -> display_name -> sender_email

Nunca comparar `tenant_id` contra un string de marca directamente
(`if 'norkevin' in tenant_id`, `if nombre == 'Astral'`, etc). Siempre pasar
por las funciones de aca.

Confirmado con Kevin (fase de estabilizacion, agosto 2026):

    Astral Weddings   -> brand_key='astral',   sender_email=astralweddingsgt@gmail.com
    Norkevin Photography -> brand_key='norkevin', sender_email=norkevinfoto@gmail.com

Y confirmado en datos (no por nombre de id, por evidencia real):
    - TODOS los registros existentes de leads/jobs/clients/quotes/payments
      en produccion hoy tienen tenant_id='tenant-norkevin'.
    - El Gmail real conectado y usado para ese tenant_id es
      astralweddingsgt@gmail.com (confirmado via MCP de Gmail: el ultimo
      correo enviado por esa cuenta tiene ese remitente).
    - Los destinatarios reales de los correos del incidente del 16 de
      agosto (men12664@gmail.com, experiencegt01@gmail.com) aparecen en
      una campana de marketing masiva enviada por astralweddingsgt@gmail.com
      el 7 de junio de 2025 ("Somos Astral Weddings...") -- son leads
      reales de Astral, no datos de prueba.
    Conclusion: `tenant-norkevin` = Astral Weddings, por evidencia, no por
    el nombre del id. Esto coincide exactamente con lo que ya documenta
    SEGURIDAD_AISLAMIENTO.md y con _MULTI_TENANT_REAL_TENANTS en app.py.

`tenant-astral` (el segundo id legado, con name="ASTRAL FILMS" en
tenants.json) NO tiene ningun registro real asociado en ningun archivo de
datos -- es un stub sin uso, no un tenant de Norkevin disfrazado. NO se
asume que sea Norkevin Photography solo porque hace falta un segundo
tenant: eso seria la misma clase de error que causo el incidente.

`tenant-norkevin-photography` (id nuevo, no reutiliza ningun stub viejo)
es el id preparado en `_MULTI_TENANT_REAL_TENANTS` (app.py) para cuando
Norkevin Photography tenga datos reales propios en este CRM. Hoy no tiene
ningun registro -- es correcto que este vacio, no es un bug.

Este modulo NO renombra ninguna primary key ni foreign key existente.
Solo agrega una capa de resolucion canonica por encima de los ids legados.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BrandIdentity:
    internal_tenant_id: str
    brand_key: str
    display_name: str
    sender_email: str
    # None = no hay evidencia todavia de que este tenant tenga una cuenta
    # de Gmail propia conectada / registros reales. No inventar un valor.
    email_connection_id: Optional[str]
    # Evidencia en la que se basa esta fila (para poder auditar la
    # decision despues, no solo confiar en el comentario).
    evidence: str


# Fuente unica de verdad. Si mañana se confirma un nuevo tenant o se activa
# la conexion de Gmail de Norkevin Photography dentro del CRM, se agrega o
# edita UNA fila aca -- nada mas debe tocarse.
_CANONICAL_BRANDS = (
    BrandIdentity(
        internal_tenant_id='tenant-norkevin',
        brand_key='astral',
        display_name='Astral Weddings',
        sender_email='astralweddingsgt@gmail.com',
        email_connection_id='google_token_tenant-norkevin.json',
        evidence=(
            "100% de los registros reales actuales (leads/jobs/clients/"
            "quotes/payments) tienen este tenant_id. El Gmail real "
            "conectado y usado para enviar bajo este tenant_id es "
            "astralweddingsgt@gmail.com (confirmado en Gmail Sent, mensaje "
            "mas reciente). Destinatarios reales del incidente del "
            "16-ago-2026 aparecen en una campana de Astral Weddings del "
            "7-jun-2025 enviada por esa misma cuenta."
        ),
    ),
    BrandIdentity(
        internal_tenant_id='tenant-norkevin-photography',
        brand_key='norkevin',
        display_name='Norkevin Photography',
        sender_email='norkevinfoto@gmail.com',
        email_connection_id=None,  # todavia no hay token conectado a este id
        evidence=(
            "Id preparado en _MULTI_TENANT_REAL_TENANTS (app.py) para la "
            "migracion multi-cuenta que AUN NO se ejecuto "
            "(/api/admin/migrate-to-multi-tenant, dry_run). No tiene "
            "ningun registro real todavia -- eso es lo esperado, no un "
            "hueco a llenar con datos de tenant-norkevin."
        ),
    ),
    BrandIdentity(
        internal_tenant_id='tenant-ramiro-cruz',
        brand_key='ramiro-cruz',
        display_name='Ramiro Cruz Photo',
        sender_email='ramirocruz10x@gmail.com',
        email_connection_id=None,
        evidence=(
            "Igual que tenant-norkevin-photography: id preparado para la "
            "migracion multi-cuenta, sin datos reales todavia."
        ),
    ),
    # --- Ids legados sin marca real confirmada. NO USAR PARA ENVIAR CORREO. ---
    BrandIdentity(
        internal_tenant_id='tenant-astral',
        brand_key='UNRESOLVED_LEGACY_STUB',
        display_name='(sin marca confirmada -- stub legado sin datos)',
        sender_email='',
        email_connection_id=None,
        evidence=(
            "Segundo id legado en tenants.json (name='ASTRAL FILMS', "
            "inconsistente con 'ASTRAL WEDDINGS' usado en el resto del "
            "codigo). CERO registros encontrados con este tenant_id en "
            "leads/jobs/clients/quotes/payments/calendar/team.json. No se "
            "asume que sea Norkevin: no hay evidencia de ninguna marca. "
            "Requiere decision de Kevin antes de usarse para cualquier "
            "envio real."
        ),
    ),
)

_BY_TENANT_ID = {b.internal_tenant_id: b for b in _CANONICAL_BRANDS}
_BY_BRAND_KEY = {b.brand_key: b for b in _CANONICAL_BRANDS}


class UnresolvedBrandError(Exception):
    """El tenant_id no tiene una marca canonica confirmada.

    A proposito NO cae a un default ("Astral" o el primero de la lista):
    eso es exactamente el bug de origen del incidente. Quien reciba este
    error debe detenerse y pedir resolucion humana, no adivinar.
    """


def resolve_brand(tenant_id: Optional[str]) -> BrandIdentity:
    """Unico punto autorizado para resolver tenant_id -> marca real.

    Lanza UnresolvedBrandError si el tenant_id es None, vacio, desconocido,
    o si es un id legado sin marca confirmada (ej. 'tenant-astral'). Nunca
    devuelve un valor por defecto silencioso.
    """
    if not tenant_id:
        raise UnresolvedBrandError('tenant_id vacio: no se puede resolver marca')
    brand = _BY_TENANT_ID.get(tenant_id)
    if brand is None:
        raise UnresolvedBrandError(f'tenant_id desconocido: {tenant_id!r}')
    if brand.brand_key == 'UNRESOLVED_LEGACY_STUB':
        raise UnresolvedBrandError(
            f'{tenant_id!r} no tiene marca canonica confirmada todavia '
            f'(evidencia: {brand.evidence})'
        )
    return brand


def display_name_for_tenant(tenant_id: Optional[str], *, safe_default='(empresa sin identificar)') -> str:
    """Como resolve_brand(), pero para lugares donde antes habia un string
    hardcodeado (ej. el campo 'empresa' de un job) y una excepcion dura
    rompería un flujo que no es de envio de correo. Nunca devuelve el
    nombre de OTRA marca: si no se puede resolver, devuelve un texto
    explicitamente neutro, jamas 'ASTRAL WEDDINGS' por default."""
    try:
        return resolve_brand(tenant_id).display_name
    except UnresolvedBrandError:
        return safe_default


def sender_email_for_tenant(tenant_id: Optional[str]) -> str:
    """SI debe fallar duro: usarlo para decidir con que cuenta se manda un
    correo real es exactamente el punto donde no se puede adivinar."""
    return resolve_brand(tenant_id).sender_email


def email_connection_id_for_tenant(tenant_id: Optional[str]) -> Optional[str]:
    return resolve_brand(tenant_id).email_connection_id


def is_connection_owned_by_tenant(tenant_id: Optional[str], email_connection_id: Optional[str]) -> bool:
    """True solo si esa conexion de Gmail especifica es la que le
    corresponde a ese tenant_id segun el mapeo canonico. Cualquier otra
    combinacion (incluida una conexion valida pero de otra marca) es
    False -- este es el chequeo que falta para bloquear el fallback
    cross-tenant explicitamente, no solo por como esta hoy el codigo de
    gmail_delivery.py (que ya aisla por archivo por tenant_id, pero sin
    esta capa de verificacion explicita contra la marca esperada)."""
    if not tenant_id or not email_connection_id:
        return False
    try:
        brand = resolve_brand(tenant_id)
    except UnresolvedBrandError:
        return False
    return brand.email_connection_id == email_connection_id


def all_known_tenant_ids():
    return tuple(_BY_TENANT_ID.keys())


def all_resolved_brands():
    """Solo las marcas con brand_key real (excluye stubs sin resolver)."""
    return tuple(b for b in _CANONICAL_BRANDS if b.brand_key != 'UNRESOLVED_LEGACY_STUB')

"""Tokens para los enlaces publicos (contratos, cotizaciones, cuestionarios,
portal del cliente).

PREPARADO, NO ACTIVADO. Nada de esto rota ni cambia enlaces existentes
todavia: es la arquitectura lista para cuando Kevin defina la politica.

--------------------------------------------------------------------------
Por que hace falta
--------------------------------------------------------------------------

Esos enlaces son "bearer": el cliente los abre sin sesion, el enlace ES la
credencial. Hoy la URL lleva el id interno del registro, y los importados de
Studio Ninja se construyeron con el nombre de la boda:

    /contracts/contract-sn-boda-rebeca-y-jos

Cualquiera que sepa como se llamaba la boda puede reconstruir el enlace de su
contrato sin haberlo recibido nunca.

--------------------------------------------------------------------------
Como queda
--------------------------------------------------------------------------

El enlace lleva un token aleatorio, separado del id interno:

    /contracts/<token>          <- 256 bits de aleatoriedad

y en la base NO se guarda ese token sino su hash:

    {'id': 'contract-abc123', 'public_token_hash': '9f86d0...'}

Al llegar una peticion se hashea lo recibido y se compara. Kevin: "una
lectura accidental de la base de datos no entrega automaticamente todos los
enlaces privados utilizables".

El token en claro existe UNA sola vez: al generarlo, para poder armar el
enlace que se le manda al cliente. Despues no se puede recuperar -- si se
pierde, se genera uno nuevo. Es la misma logica que una contrasena.
"""
import hashlib
import hmac
import secrets

# 32 bytes = 256 bits. En url-safe base64 quedan 43 caracteres.
LARGO_BYTES = 32


def generar_token():
    """Token nuevo en claro. Solo se puede ver en este momento."""
    return secrets.token_urlsafe(LARGO_BYTES)


def hash_token(token):
    """Hash de un solo sentido, para guardar en la base.

    SHA-256 a secas y no bcrypt/scrypt a proposito: esto no es una
    contrasena elegida por una persona (que hay que proteger de fuerza
    bruta por ser corta y adivinable), sino 256 bits aleatorios. Contra eso
    la fuerza bruta no existe, y un hash rapido permite resolver el enlace
    sin costo en cada visita.
    """
    if not token:
        return None
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def token_coincide(token_recibido, hash_guardado):
    """Compara en tiempo constante.

    compare_digest y no `==` para no filtrar por cuanto tarda la
    comparacion cuantos caracteres del token se acertaron.
    """
    if not token_recibido or not hash_guardado:
        return False
    return hmac.compare_digest(hash_token(token_recibido), hash_guardado)


def huella(token_o_hash):
    """Version corta y segura para mostrar en pantallas y logs.

    Kevin: "no quiero que aparezcan completos en logs, auditorias,
    excepciones ni screenshots". Un token completo en un log es una
    credencial en un log.
    """
    if not token_o_hash:
        return ''
    s = str(token_o_hash)
    if len(s) <= 8:
        return '•' * len(s)
    return f'{s[:4]}{"•" * 6}{s[-2:]}'


def emitir_para(record):
    """Genera un token para un registro y devuelve (token_claro, record).

    El record vuelve con `public_token_hash` puesto y SIN el token en claro:
    quien llama decide que hacer con el (armar el enlace y mandarlo), pero
    lo que se persiste nunca lo contiene.
    """
    token = generar_token()
    record = dict(record)
    record['public_token_hash'] = hash_token(token)
    record.pop('public_token', None)  # por si alguien lo dejo en claro
    return token, record


def buscar_por_token(records, token):
    """Encuentra el registro cuyo hash coincide con el token recibido.

    Recorre comparando en tiempo constante. No usa un indice por hash a
    proposito por ahora: el volumen es de cientos de registros, no millones,
    y un indice seria complejidad sin beneficio real.
    """
    if not token:
        return None
    esperado = hash_token(token)
    for r in records or []:
        guardado = r.get('public_token_hash')
        if guardado and hmac.compare_digest(guardado, esperado):
            return r
    return None

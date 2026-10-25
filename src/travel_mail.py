"""Conditional travel paragraph for the existing plain-text email renderer."""
import hashlib
import re
from urllib.parse import parse_qs, unquote, urlsplit


_NOTICE = ('(Si su boda se realiza fuera de Ciudad de Guatemala, Antigua Guatemala, '
           'Tecpán o sus alrededores, podrían aplicarse cargos adicionales de viáticos. '
           'Consúltenos para confirmarlo.)')


_END = ('Estos montos se agregan al precio del paquete que elijan y corresponden '
        'a los gastos de traslado y logística de nuestro equipo.')
_BLOCK = re.compile(r'Como su boda se realizará en [^\n]+, se aplicarían los siguientes viáticos:\n'
                    r'.*?' + re.escape(_END), re.DOTALL)
# Exact legacy commercial paragraph inspected in the live template on 2026-10-13.
_LEGACY = re.compile(
    r'\ufeff?Para bodas en la distancia que me indicas se cobra un extra de viáticos '
    r'según el paquete contratado \(traslados, comidas \*no incluye cena el día de la boda\* y hospedaje\)'
    r'\s+Paquete de fotografía: Q2,500\s+Paquete de foto y video: Q4,000')


def strip_travel_block(body):
    body = _BLOCK.sub('', (body or '').replace('\r\n', '\n'))
    body = _LEGACY.sub('', body).replace(_NOTICE, '')
    return re.sub(r'\n{3,}', '\n\n', body).strip()


def travel_block(result):
    if not result.get('definitive') or not any(result.get(key) for key in ('photo_amount', 'photo_video_amount')):
        return ''
    location = result.get('venue_name') or result.get('normalized_location') or result['original_location']
    location = ' '.join(location.split())
    return (f'Como su boda se realizará en {location}, se aplicarían los siguientes viáticos:\n\n'
            f'• Paquetes únicamente de fotografía: Q{result["photo_amount"]:,.0f}.\n'
            f'• Paquetes de fotografía y video: Q{result["photo_video_amount"]:,.0f}.\n\n' + _END)


def compose_travel(body, result):
    """Regeneration is idempotent. Everything outside the conditional block stays intact."""
    base = strip_travel_block(body)
    block = travel_block(result) if result.get('definitive') else _NOTICE
    if not block:
        return base
    # Existing package templates put the real package URL on its own line.
    links = list(re.finditer(r'https?://[^\s<>]+', base))
    package_link = next((link for link in links if 'drive.google.com/' in link.group()), None)
    if package_link:
        end = package_link.end()
        return base[:end] + '\n\n' + block + base[end:]
    signature = re.search(r'(?m)^(?:¡Saludos!|Saludos[,!]?|Atentamente[,:]?)', base)
    if signature:
        return base[:signature.start()].rstrip() + '\n\n' + block + '\n\n' + base[signature.start():]
    return base + '\n\n' + block


def content_digest(subject, body):
    return hashlib.sha256(((subject or '') + '\0' + (body or '')).encode('utf-8')).hexdigest()


def package_link_keys(body):
    """Sharing parameters do not change the identity of a Google Drive package file."""
    keys = set()
    for raw in re.findall(r'https?://[^\s<>"\[\]]+', body or ''):
        raw = raw.rstrip('.,);')
        try:
            url = urlsplit(raw)
        except ValueError:
            continue
        if url.hostname == 'drive.google.com':
            match = re.search(r'/file/d/([^/]+)', unquote(url.path))
            file_id = match.group(1) if match else (parse_qs(url.query).get('id') or [''])[0]
            if file_id:
                keys.add(('drive-file', file_id))
                continue
        keys.add((url.hostname, url.path, url.query))
    return keys


def legacy_travel_conflict(body):
    """Unknown fixed travel text must be reviewed instead of silently rewriting it."""
    base = strip_travel_block(body)
    return bool(re.search(r'(?is)(?:viáticos|viaticos|traslado|hospedaje).{0,250}Q\s?\d', base))

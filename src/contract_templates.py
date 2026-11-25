"""Contract types are copied per job. Existing documents use the original wedding clauses."""
import re


STANDARD_CLAUSES = [
    ('1. Compromiso del Fotografo',
     '%estudio% se compromete a capturar, editar y entregar el material fotografico contratado en las fechas y plazos acordados. La calidad del trabajo se ajustara a los estandares profesionales de la industria.'),
    ('2. Tarifas y Deposito',
     'La tarifa total es de %total%. %plan_de_pago% Deposito requerido: %deposito%.'),
    ('3. Responsabilidades del Cliente',
     '* Proporcionar informacion veraz y oportuna sobre el evento\n* Realizar los pagos en las fechas acordadas\n* Coordinar con el fotografo los horarios y ubicaciones del evento\n* Notificar cualquier cambio con al menos 30 dias de anticipacion'),
    ('4. Responsabilidades del Fotografo',
     '* Asistir puntualmente a todos los eventos acordados\n* Entregar las fotografias editadas en un plazo maximo de 30 dias\n* Mantener una copia de seguridad de todas las imagenes por 1 ano\n* Proveer una galeria online privada para revision del cliente'),
    ('5. Cancelacion y Reembolso',
     'En caso de cancelacion por parte del cliente con menos de 60 dias de anticipacion, el deposito no sera reembolsable. Cancelaciones con mas de 60 dias tendran un reembolso del 50% del deposito.'),
    ('6. Propiedad Intelectual',
     '%estudio% retendra los derechos de autor sobre todas las imagenes. El cliente recibira una licencia personal no comercial para uso privado.'),
]


def resolve_clauses(pattern, job, studio):
    total = float(job.get('price_total') or 0)
    installments = int(job.get('plan_pago') or 1)
    installment_amount = float(job.get('cuota_monto') or 0)
    values = {
        '%estudio%': studio,
        '%total%': f'Q{total:,.2f}',
        '%deposito%': f'Q{total * 0.3:,.2f}',
        '%plan_de_pago%': (f'Pago en {installments} cuotas de Q{installment_amount:,.2f}.'
                           if installments > 1 else 'Pago en una sola exhibicion.'),
    }
    def render(text):
        return re.sub(r'%[a-z_]+%', lambda m: values.get(m[0], m[0]), text)
    return [(render(title), render(body)) for title, body in pattern]


def validated_clauses(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('Incluí entre 1 y 100 cláusulas.')
    clauses = []
    for item in items:
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(s, str) for s in item):
            raise ValueError('Cláusula inválida.')
        title, body = (s.strip() for s in item)
        if not body or len(title) > 200 or len(body) > 20000:
            raise ValueError('Cada cláusula necesita texto. El máximo es de 200 caracteres por título y 20,000 por cláusula.')
        clauses.append([title, body])
    return clauses

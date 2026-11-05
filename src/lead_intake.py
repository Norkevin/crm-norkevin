"""Present form answers separately from the customer's free-text message."""

ESTIMATED_BUDGETS = ('Q10,000', 'Q15,000', 'Q20,000 o más', 'Aún no lo tenemos claro')
# Accept submissions from forms that were already open before the options changed.
PREVIOUS_ESTIMATED_BUDGETS = ('Menos de Q5,000', 'Q5,000 a Q9,999', 'Q10,000 a Q14,999',
                              'Q15,000 a Q19,999', 'Q20,000 o más', 'Aún no lo sabemos')

BUDGET_ANSWERS = {
    'Sí, la inversión es compatible': 'Indican que los precios se ajustan a su presupuesto',
    'Necesitamos orientación': 'Necesitan ayuda para elegir una cobertura',
    'Presupuesto menor al catálogo': 'Indican que su presupuesto es menor que los precios del catálogo',
}


def lead_intake(lead):
    """Read old generated prefixes conservatively, without changing stored notes."""
    message = lead.get('notas') if 'notas' in lead else lead.get('notes')
    message = message if isinstance(message, str) else ''
    qualification = lead.get('intake_qualification')
    if not isinstance(qualification, dict):
        qualification = {}
    service = qualification.get('service')
    budget = qualification.get('budget_fit_declared')
    collection = qualification.get('collection')
    if service and budget and qualification.get('version') == 1:
        # Only the complete, known prefix is generated text. Keep any edited or
        # unrecognized notes intact rather than guessing what the client wrote.
        prefix = f'Servicio solicitado: {service}\nEncaje de inversión declarado: {budget}'
        if collection:
            prefix += f'\nColección de interés: {collection}'
        prefix += '\nSolicitud: revisar disponibilidad y propuesta. Disponibilidad y encaje pendientes de revisión.'
        if message == prefix:
            message = ''
        elif message.startswith(prefix + '\n\n'):
            message = message[len(prefix) + 2:]
    answers = []
    if service:
        answers.append(('Servicio', service))
    if budget:
        answers.append(('Sobre los precios (formulario anterior)', BUDGET_ANSWERS.get(budget, budget)))
    if qualification.get('estimated_budget'):
        answers.append(('Presupuesto estimado', qualification['estimated_budget']))
    if collection:
        answers.append(('Cobertura que les interesa', collection))
    current_budget = lead.get('presupuesto')
    return {'message': message, 'answers': answers,
            'budget_label': BUDGET_ANSWERS.get(current_budget, current_budget)}

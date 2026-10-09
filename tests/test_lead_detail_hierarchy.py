"""The summary must surface an actionable step, never a finished/locked one."""
from pathlib import Path
import re
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
import pytest


@pytest.mark.parametrize('steps,expected', [
    ([{'id': 'done', 'name': 'Terminado', 'status': 'done'},
      {'id': 'skip', 'name': 'Omitido', 'status': 'skipped'},
      {'id': 'lock', 'name': 'Bloqueado', 'status': 'pending', 'locked': True},
      {'id': 'next', 'name': 'Revisar correo', 'status': 'queued'}], 'Revisar correo'),
    ([{'id': 'pending', 'name': 'Enviar paquetes', 'status': 'pending'}], 'Enviar paquetes'),
    ([], 'Todo al día'),
])
def test_next_action_respects_step_state(steps, expected):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '{% block content %}{% endblock %}'}),
        FileSystemLoader(Path(__file__).resolve().parents[1] / 'templates'),
    ]), autoescape=True)
    env.filters['fecha_legible'] = lambda value: value
    env.filters['fecha_evento'] = lambda value: value
    html = env.get_template('lead_detail.html').render(
        current_tenant={'name': 'Prueba'}, lead={'id': 'test', 'nombre': 'Prueba', 'status': 'Nuevo'}, client=None,
        workflow_steps=steps, lead_steps=steps, prod_steps=[], packages=[],
        quotes=[], payments=[], contracts=[], questionnaires=[], files=[],
        mail_log=[], email_templates=[], url_for=lambda *a, **kw: '/static/' + kw['filename'],
    )
    assert re.search(r'<h2 id="lead-next-title">(.*?)</h2>', html).group(1) == expected
    if expected == 'Revisar correo':
        summary = html.split('aria-labelledby="lead-next-title"', 1)[1].split('</section>', 1)[0]
        assert 'href="/emails"' in summary
        assert 'Preparación automática' not in summary

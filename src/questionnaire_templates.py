"""Questionnaire schema shared by Settings and client forms."""
import json
import re
from pathlib import Path

DEFAULT_QUESTIONS = json.loads(Path(__file__).with_name('questionnaire_questions.json').read_text(encoding='utf-8'))
FIELD_TYPES = {'text', 'textarea', 'email', 'tel', 'date', 'time', 'number', 'radio', 'select'}


def questionnaire_fields(groups):
    fields = {f['id']: f for group in groups for f in group['fields']}
    for field in list(fields.values()):
        if field.get('details'):
            fields[field['id'] + '__details'] = {'type': 'textarea', 'required': False}
    return fields


def validate_template(data):
    """Normalize editable content; reject invalid or ambiguous field identifiers."""
    if not isinstance(data, dict):
        raise ValueError('La plantilla debe ser un objeto.')

    def text(value, limit, label, required=False):
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise ValueError(f'Revisa {label} (máximo {limit} caracteres).')
        return value.strip()

    result = {
        'name': text(data.get('name'), 160, 'el título', True),
        'description': text(data.get('description', ''), 2000, 'la introducción'),
        'questions': [],
    }
    groups = data.get('questions')
    if not isinstance(groups, list) or not 1 <= len(groups) <= 40:
        raise ValueError('Agrega entre 1 y 40 secciones.')
    ids = set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('Sección inválida.')
        fields = group.get('fields')
        if not isinstance(fields, list) or not fields:
            raise ValueError('Cada sección debe tener al menos una pregunta.')
        clean = {
            'group': text(group.get('group'), 160, 'el nombre de sección', True),
            'description': text(group.get('description', ''), 2000, 'la descripción de sección'),
            'columns': 2 if group.get('columns') == 2 else 1,
            'fields': [],
        }
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError('Pregunta inválida.')
            fid = field.get('id')
            if not isinstance(fid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', fid) or fid in ids:
                raise ValueError('Cada pregunta necesita un identificador único válido.')
            ids.add(fid)
            kind = field.get('type', 'text')
            if not isinstance(kind, str) or kind not in FIELD_TYPES:
                raise ValueError('Tipo de respuesta inválido.')
            item = {
                'id': fid, 'label': text(field.get('label'), 1000, 'la pregunta', True),
                'help': text(field.get('help', ''), 2000, 'el texto de ayuda'), 'type': kind,
                'required': field.get('required') is True, 'full': field.get('full') is True,
            }
            if kind in ('radio', 'select'):
                options = field.get('options')
                if not isinstance(options, list) or not 1 <= len(options) <= 30:
                    raise ValueError('Agrega entre 1 y 30 opciones por pregunta de selección.')
                item['options'] = [text(o, 300, 'las opciones', True) for o in options]
                if len(set(item['options'])) != len(item['options']):
                    raise ValueError('Las opciones de una pregunta no deben repetirse.')
                details = field.get('details')
                if details is not None:
                    if not isinstance(details, dict):
                        raise ValueError('Revisa la configuración del campo de detalles.')
                    when = details.get('when')
                    if not isinstance(when, list) or not when or any(not isinstance(o, str) or o not in item['options'] for o in when):
                        raise ValueError('Elige respuestas válidas para mostrar los detalles.')
                    item['details'] = {
                        'label': text(details.get('label'), 1000, 'el título del campo de detalles', True),
                        'help': text(details.get('help', ''), 2000, 'la ayuda del campo de detalles'),
                        'when': list(dict.fromkeys(when)),
                    }
            clean['fields'].append(item)
        result['questions'].append(clean)
    if len(ids) > 250:
        raise ValueError('El cuestionario admite hasta 250 preguntas.')
    if any(field['id'] + '__details' in ids for group in result['questions'] for field in group['fields'] if field.get('details')):
        raise ValueError('Un identificador de pregunta coincide con un campo de detalles.')
    return result

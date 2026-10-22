"""Selectable, paginated export of saved questionnaire questions and answers."""
import io
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate, Spacer

from src.pdf_document_system import COLOR, registrar_fuente_documental


def _answer_text(value):
    if value is None or value == '' or value == [] or value == {}:
        return 'Sin respuesta'
    if isinstance(value, bool):
        return 'Sí' if value else 'No'
    if isinstance(value, dict):
        return '\n'.join(f'{key}: {_answer_text(item)}' for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return '\n'.join(f'- {_answer_text(item)}' for item in value)
    return str(value)


def render_questionnaire_pdf(questionnaire, groups, brand, *, client_name='',
                             event_name='', event_date='', completed_at=''):
    """Keep original questions, details, unlisted answers and answer history."""
    buffer = io.BytesIO()
    fonts = registrar_fuente_documental()
    styles = {
        'title': ParagraphStyle('QuestionnaireTitle', fontName=fonts['bold'], fontSize=21,
                                leading=27, textColor=COLOR['text'], spaceAfter=14),
        'section': ParagraphStyle('QuestionnaireSection', fontName=fonts['bold'], fontSize=13,
                                  leading=18, textColor=COLOR['primary_dark'],
                                  spaceBefore=18, spaceAfter=10, keepWithNext=True),
        'question': ParagraphStyle('QuestionnaireQuestion', fontName=fonts['bold'], fontSize=10,
                                   leading=15, textColor=COLOR['text'],
                                   spaceBefore=10, spaceAfter=4),
        'body': ParagraphStyle('QuestionnaireAnswer', fontName=fonts['regular'], fontSize=10,
                               leading=15, textColor=COLOR['text'], spaceAfter=7,
                               splitLongWords=True, allowWidows=True, allowOrphans=True),
        'help': ParagraphStyle('QuestionnaireHelp', fontName=fonts['regular'], fontSize=9,
                               leading=13, textColor=COLOR['text_secondary'], spaceAfter=5),
    }
    story = []

    def add(value, style='body'):
        # Answers are plain text, never ReportLab markup (including <, & and links).
        text = escape(str(value)).replace('\r\n', '\n').replace('\r', '\n')
        story.append(Paragraph(text.replace('\n', '<br/>'), styles[style]))

    def answer(label, value, help_text=''):
        # Keep room for the question and the first answer lines without moving
        # an entire multi-page answer to a fresh page.
        story.append(CondPageBreak(25 * mm))
        add(label, 'question')
        if help_text:
            add(help_text, 'help')
        add(_answer_text(value))

    def section(label):
        story.append(CondPageBreak(45 * mm))
        add(label, 'section')

    title = questionnaire.get('name') or 'Cuestionario'
    add(title, 'title')
    for label, value in (
        ('Cliente', client_name), ('Evento', event_name), ('Fecha del evento', event_date),
        ('Estado', questionnaire.get('status') or 'Borrador'),
        ('Respondido el', completed_at),
    ):
        if value:
            add(f'{label}: {value}', 'help')
    if questionnaire.get('description'):
        story.append(Spacer(1, 5 * mm))
        add(questionnaire['description'])

    answers = questionnaire.get('answers') or {}
    included = set()
    for group in groups:
        section(group.get('group') or 'Preguntas')
        if group.get('description'):
            add(group['description'], 'help')
        for field in group.get('fields') or []:
            fid = field.get('id')
            included.add(fid)
            answer(field.get('label') or fid or 'Pregunta', answers.get(fid), field.get('help'))
            if field.get('details'):
                details_id = str(fid) + '__details'
                included.add(details_id)
                answer(field['details'].get('label') or 'Detalles', answers.get(details_id),
                       field['details'].get('help'))

    extra = [(key, value) for key, value in answers.items() if key not in included]
    if extra:
        section('Respuestas adicionales guardadas')
        for key, value in extra:
            answer(key, value)
    if questionnaire.get('answer_history'):
        section('Respuestas guardadas de preguntas anteriores')
        for previous in questionnaire['answer_history']:
            answer(previous.get('label') or previous.get('id') or 'Pregunta anterior', previous.get('value'))

    def page_frame(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(COLOR['primary_dark'])
        canvas.setFont(fonts['bold'], 9)
        # A paragraph wraps long brand names rather than drawing over the page edge.
        heading = Paragraph(escape(str(brand.get('display_name') or 'Flow')), styles['help'])
        _, height = heading.wrap(doc.width, 18 * mm)
        heading.drawOn(canvas, doc.leftMargin, A4[1] - 12 * mm - height)
        canvas.setStrokeColor(COLOR['border'])
        canvas.line(doc.leftMargin, 17 * mm, A4[0] - doc.rightMargin, 17 * mm)
        canvas.setFont(fonts['regular'], 8)
        canvas.setFillColor(COLOR['text_secondary'])
        canvas.drawString(doc.leftMargin, 12 * mm, 'Cuestionario - Preguntas y respuestas')
        canvas.drawRightString(A4[0] - doc.rightMargin, 12 * mm, f'Página {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=27 * mm, bottomMargin=25 * mm,
                            title=str(title), author=str(brand.get('display_name') or 'Flow'))
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
    return buffer.getvalue()

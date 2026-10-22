"""The client questionnaire's visual language, laid out for printable A4 pages."""
import io
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.pdf_document_system import COLOR


@lru_cache(maxsize=1)
def _fonts():
    # Static instances avoid embedding the web variable font's light default.
    directory = Path(__file__).resolve().parents[1] / 'static' / 'fonts'
    fonts = {}
    for weight, filename in (('regular', 'Regular'), ('medium', 'SemiBold'), ('bold', 'Bold')):
        name = 'QuestionnaireManrope-' + filename
        pdfmetrics.registerFont(TTFont(name, str(directory / ('Manrope-' + filename + '.ttf'))))
        fonts[weight] = name
    return fonts


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
                             event_name='', event_date='', completed_at='', theme=None):
    """Keep every saved answer and use the same tenant theme as the web."""
    buffer = io.BytesIO()
    fonts = _fonts()
    palette = dict(COLOR)
    for name, token in (('primary', 'primary'), ('primary_dark', 'primary_dark'),
                        ('primary_soft', 'primary_tint'), ('surface_2', 'surface_2'),
                        ('text', 'text_primary'), ('text_secondary', 'text_secondary'),
                        ('border', 'border'), ('success_text', 'success_text'),
                        ('success_soft', 'success_soft')):
        value = (theme or {}).get(token)
        if value:
            try:
                palette[name] = HexColor(value)
            except (TypeError, ValueError):
                pass  # Malformed cosmetic settings must not block a download.

    margin = 18 * mm
    width = A4[0] - 2 * margin
    styles = {
        'title': ParagraphStyle('QuestionnaireTitle', fontName=fonts['bold'], fontSize=27,
                                leading=33, textColor=palette['text'], spaceAfter=17),
        'section': ParagraphStyle('QuestionnaireSection', fontName=fonts['bold'], fontSize=15,
                                  leading=20, textColor=palette['primary_dark']),
        'number': ParagraphStyle('QuestionnaireSectionNumber', fontName=fonts['medium'], fontSize=12,
                                 leading=16, textColor=palette['primary_dark'], alignment=TA_CENTER),
        'question': ParagraphStyle('QuestionnaireQuestion', fontName=fonts['medium'], fontSize=10,
                                   leading=14, textColor=palette['text'], spaceAfter=5, keepWithNext=True),
        'body': ParagraphStyle('QuestionnaireAnswer', fontName=fonts['regular'], fontSize=10.5,
                               leading=16, textColor=palette['text'], spaceBefore=9, spaceAfter=19,
                               leftIndent=11, rightIndent=11, borderPadding=9, borderRadius=6,
                               backColor=palette['surface_2'], splitLongWords=True,
                               allowWidows=True, allowOrphans=True),
        'help': ParagraphStyle('QuestionnaireHelp', fontName=fonts['regular'], fontSize=8,
                               leading=11.5, textColor=palette['text_secondary'], spaceAfter=5),
        'intro': ParagraphStyle('QuestionnaireIntro', fontName=fonts['regular'], fontSize=9,
                                leading=14, textColor=palette['text_secondary'], spaceAfter=17),
        'meta_label': ParagraphStyle('QuestionnaireMetaLabel', fontName=fonts['medium'], fontSize=7.5,
                                     leading=11, textColor=palette['text_secondary'], spaceAfter=4),
        'meta_value': ParagraphStyle('QuestionnaireMetaValue', fontName=fonts['medium'], fontSize=9,
                                     leading=14, textColor=palette['text']),
    }
    styles['empty'] = ParagraphStyle('QuestionnaireEmpty', parent=styles['body'],
                                     textColor=palette['text_secondary'])
    story = []

    def paragraph(value, style='body'):
        # Labels and answers remain literal, searchable text, including < and &.
        text = escape(str(value)).replace('\r\n', '\n').replace('\r', '\n')
        return Paragraph(text.replace('\n', '<br/>'), styles[style])

    def answer(label, value, help_text=''):
        story.append(CondPageBreak(31 * mm))
        story.append(paragraph(label, 'question'))
        if help_text:
            help_paragraph = paragraph(help_text, 'help')
            help_paragraph.keepWithNext = True
            story.append(help_paragraph)
        text = _answer_text(value)
        story.append(paragraph(text, 'empty' if text == 'Sin respuesta' else 'body'))

    section_index = 0

    def section(label, description=''):
        nonlocal section_index
        section_index += 1
        story.append(CondPageBreak(52 * mm))
        content = [paragraph(label, 'section')]
        if description:
            content.extend([Spacer(1, 4), paragraph(description, 'help')])
        heading = Table([[paragraph(f'{section_index:02d}', 'number'), content]],
                        colWidths=[44, width - 44], cornerRadii=[7, 7, 7, 7], splitInRow=1,
                        style=TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), palette['primary_soft']),
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                            ('LEFTPADDING', (0, 0), (0, 0), 9),
                            ('LEFTPADDING', (1, 0), (1, 0), 10),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 13),
                            ('TOPPADDING', (0, 0), (-1, -1), 12),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                        ]))
        heading.spaceBefore = 13
        heading.spaceAfter = 16
        story.append(heading)

    title = questionnaire.get('name') or 'Cuestionario'
    story.append(paragraph(title, 'title'))
    metadata = [(label, value) for label, value in (
        ('Cliente', client_name), ('Evento', event_name), ('Fecha del evento', event_date),
    ) if value]
    if metadata:
        cells = [[paragraph(label, 'meta_label'), paragraph(value, 'meta_value')]
                 for label, value in metadata]
        panel = Table([cells], colWidths=[width / len(cells)] * len(cells),
                      cornerRadii=[7, 7, 7, 7], splitInRow=1, style=TableStyle([
                          ('BACKGROUND', (0, 0), (-1, -1), palette['primary_soft']),
                          ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                          ('LEFTPADDING', (0, 0), (-1, -1), 12),
                          ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                          ('TOPPADDING', (0, 0), (-1, -1), 13),
                          ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                      ]))
        panel.spaceAfter = 12
        story.append(panel)
    status = questionnaire.get('status') or 'Borrador'
    details = f'Estado: {status}'
    if completed_at:
        details += f'  ·  Respondido el: {completed_at}'
    story.append(paragraph(details, 'help'))
    if questionnaire.get('description'):
        story.append(Spacer(1, 7))
        story.append(paragraph(questionnaire['description'], 'intro'))

    answers = questionnaire.get('answers') or {}
    included = set()
    for group in groups:
        section(group.get('group') or 'Preguntas', group.get('description') or '')
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
        header = paragraph(str(brand.get('display_name') or 'Flow'), 'meta_value')
        _, height = header.wrap(doc.width * .70, 20 * mm)
        header.drawOn(canvas, doc.leftMargin, A4[1] - 12 * mm - height)
        if doc.page == 1:
            label = str(status)
            canvas.setFont(fonts['medium'], 8)
            pill_width = pdfmetrics.stringWidth(label, fonts['medium'], 8) + 20
            x = A4[0] - doc.rightMargin - pill_width
            y = A4[1] - 19 * mm
            canvas.setFillColor(palette['success_soft'] if status == 'Respondido' else palette['primary_soft'])
            canvas.roundRect(x, y, pill_width, 20, 10, fill=1, stroke=0)
            canvas.setFillColor(palette['success_text'] if status == 'Respondido' else palette['primary_dark'])
            canvas.drawCentredString(x + pill_width / 2, y + 7, label)
        canvas.setStrokeColor(palette['border'])
        canvas.setLineWidth(.5)
        canvas.line(doc.leftMargin, A4[1] - 24 * mm, A4[0] - doc.rightMargin, A4[1] - 24 * mm)
        canvas.line(doc.leftMargin, 19 * mm, A4[0] - doc.rightMargin, 19 * mm)
        canvas.setFont(fonts['regular'], 7.5)
        canvas.setFillColor(palette['text_secondary'])
        canvas.drawString(doc.leftMargin, 13 * mm, 'Cuestionario - Preguntas y respuestas')
        canvas.drawRightString(A4[0] - doc.rightMargin, 13 * mm, f'Página {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=margin, rightMargin=margin,
                            topMargin=32 * mm, bottomMargin=27 * mm,
                            title=str(title), author=str(brand.get('display_name') or 'Flow'))
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
    return buffer.getvalue()

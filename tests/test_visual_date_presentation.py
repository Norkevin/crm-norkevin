import pytest


@pytest.mark.parametrize('stored, expected', [
    ('2026-10-05', '5 de octubre de 2026'),
    ('2026-10-05T23:50:00-06:00', '5 de octubre de 2026'),
    ('2024-02-29', '29 de febrero de 2024'),
    ('2026-12-31', '31 de diciembre de 2026'),
    ('2026-02-29', ''),
    (None, ''),
])
def test_spanish_date_presentation_preserves_the_stored_calendar_day(stored, expected):
    from app import _format_date_es
    assert _format_date_es(stored) == expected

import pytest


@pytest.mark.parametrize('route', ['/dashboard', '/leads', '/clients', '/jobs'])
def test_mobile_shell_and_selected_tab_are_ready_before_first_paint(auth_client, route):
    html = auth_client.get(route).get_data(as_text=True)
    head = html[:html.index('</head>')]
    assert '/static/mobile.css?' in head
    assert '/static/flow-design.css?' in head
    assert '/static/tailwind.css?' in head
    assert 'cdn.tailwindcss.com' not in html
    assert f'href="{route}" class="bottom-nav-item active" aria-current="page"' in html

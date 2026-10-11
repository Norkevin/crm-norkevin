"""The loopback UI fixture must never create an entry point in the real CRM."""
import pytest


def test_browser_fixture_auth_entry_does_not_exist_in_crm(auth_client):
    response = auth_client.get('/__web_push_qa')
    assert response.status_code == 404


def test_web_push_ui_fixture_requires_explicit_browser_mode(monkeypatch):
    import os
    from tools.run_mobile_qa import main
    original = os.environ.get('CRM_DATA_DIR')
    monkeypatch.setattr('sys.argv', ['run_mobile_qa.py', '--web-push-ui'])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2
    assert os.environ.get('CRM_DATA_DIR') == original

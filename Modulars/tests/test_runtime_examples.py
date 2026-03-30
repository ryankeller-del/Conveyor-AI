from app_v3 import app


def test_run_examples_endpoint():
    client = app.test_client()
    response = client.get('/run/examples')
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload.get('examples'), list)
    assert any('test_command' in entry.get('payload', {}) for entry in payload['examples'])

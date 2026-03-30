from app_v3 import app


def test_run_examples_endpoint():
    client = app.test_client()
    response = client.get('/run/examples')
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload.get('examples'), list)
    assert any('test_command' in entry.get('payload', {}) for entry in payload['examples'])


def test_rehearsal_endpoints_expose_stage_manifest():
    client = app.test_client()
    response = client.get('/rehearsal/status')
    assert response.status_code == 200
    payload = response.get_json()
    assert "stage_manifest_current" in payload
    assert "rehearsal_state" in payload

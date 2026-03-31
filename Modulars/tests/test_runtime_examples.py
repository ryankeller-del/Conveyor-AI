import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def test_swarm_monitor_page_exposes_narrative_transcript():
    client = app.test_client()

    page = client.get('/swarm')
    assert page.status_code == 200
    assert b"Codex Swarm Monitor" in page.data

    transcript = client.get('/swarm/transcript')
    assert transcript.status_code == 200
    assert transcript.mimetype == 'text/plain'
    body = transcript.get_data(as_text=True)
    assert "swarm events" in body.lower() or "start a run" in body.lower()


def test_swarm_transcript_includes_background_queue_activity():
    client = app.test_client()
    payload = {
        "prompt": "Record a tiny background swarm update.",
        "target_files": ["app_v3.py"],
        "language": "general",
        "max_waves": 1,
        "max_total_tests": 2,
        "dynamic_spawning_enabled": False,
    }
    response = client.post('/run', json=payload)
    assert response.status_code == 200

    transcript = client.get('/swarm/transcript')
    body = transcript.get_data(as_text=True).lower()
    assert "run started" in body or "background run" in body or "files" in body

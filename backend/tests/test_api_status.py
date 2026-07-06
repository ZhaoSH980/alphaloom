"""/api/status — honest runtime-mode readout for the header.
offline (zero-quota replay) vs live (real endpoint) vs none (no LLM). The header
must reflect the ACTUAL backend, not a hardcoded label."""
from pathlib import Path

from fastapi.testclient import TestClient

from alphaloom.api.app import create_app
from alphaloom.llm.recording import RecordingLLMClient

REPO = Path(__file__).resolve().parents[2]


def _app(tmp_path, llm_client):
    return create_app(
        db_path=tmp_path / "d.sqlite", runs_db=tmp_path / "r.sqlite",
        record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=tmp_path / "ubp", frontend_dist=tmp_path / "nd",
        llm_client=llm_client)


def _rec(tmp_path, offline):
    def transport(_req):
        return {"choices": [{"message": {"content": "{}"}}]}
    return RecordingLLMClient(transport, tmp_path / "llm.sqlite",
                              model="astron-code-latest", offline=offline)


def test_status_offline_is_zero_quota(tmp_path):
    c = TestClient(_app(tmp_path, _rec(tmp_path, True)))
    r = c.get("/api/status")
    assert r.status_code == 200
    assert r.json() == {"llm_mode": "offline", "model": "astron-code-latest"}


def test_status_live_flags_real_endpoint(tmp_path):
    """offline=False → the header must say 'live' so a real run doesn't lie
    about being a zero-quota replay."""
    c = TestClient(_app(tmp_path, _rec(tmp_path, False)))
    assert c.get("/api/status").json()["llm_mode"] == "live"

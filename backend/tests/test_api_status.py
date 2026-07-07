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


def test_runtime_mode_can_switch_to_none(tmp_path):
    c = TestClient(_app(tmp_path, _rec(tmp_path, True)))

    r = c.post("/api/runtime-mode", json={"mode": "none"})

    assert r.status_code == 200
    assert r.json() == {"llm_mode": "none", "model": None}
    assert c.app.state.llm is None
    assert c.app.state.service.llm is None


def test_runtime_mode_can_switch_to_live_with_configured_client(tmp_path, monkeypatch):
    live = _rec(tmp_path, False)

    def build(_db, mode=None):
        assert mode == "live"
        return live

    monkeypatch.setattr("alphaloom.api.app._build_llm_client", build)
    c = TestClient(_app(tmp_path, _rec(tmp_path, True)))

    r = c.post("/api/runtime-mode", json={"mode": "live"})

    assert r.status_code == 200
    assert r.json() == {"llm_mode": "live", "model": "astron-code-latest"}
    assert c.app.state.llm is live
    assert c.app.state.service.llm is live


def test_runtime_mode_live_without_config_is_422_and_keeps_current(tmp_path, monkeypatch):
    current = _rec(tmp_path, True)

    def build(_db, mode=None):
        assert mode == "live"
        return None

    monkeypatch.setattr("alphaloom.api.app._build_llm_client", build)
    c = TestClient(_app(tmp_path, current))

    r = c.post("/api/runtime-mode", json={"mode": "live"})

    assert r.status_code == 422
    assert "LLM_BASE_URL" in r.text
    assert c.get("/api/status").json()["llm_mode"] == "offline"
    assert c.app.state.llm is current
    assert c.app.state.service.llm is current


def test_runtime_mode_live_error_reports_dotenv_paths(tmp_path, monkeypatch):
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alphaloom.llm.client._dotenv_candidates",
        lambda dotenv_path: [tmp_path / ".env", tmp_path / "backend" / ".env"])
    current = _rec(tmp_path, True)
    c = TestClient(_app(tmp_path, current))

    r = c.post("/api/runtime-mode", json={"mode": "live"})

    assert r.status_code == 422
    assert "Checked dotenv paths" in r.text
    assert ".env" in r.text
    assert c.app.state.llm is current


def test_runtime_mode_can_switch_to_offline_even_after_live(tmp_path, monkeypatch):
    offline = _rec(tmp_path, True)

    def build(_db, mode=None):
        assert mode == "offline"
        return offline

    monkeypatch.setattr("alphaloom.api.app._build_llm_client", build)
    c = TestClient(_app(tmp_path, _rec(tmp_path, False)))

    r = c.post("/api/runtime-mode", json={"mode": "offline"})

    assert r.status_code == 200
    assert r.json()["llm_mode"] == "offline"
    assert c.app.state.service.llm is offline

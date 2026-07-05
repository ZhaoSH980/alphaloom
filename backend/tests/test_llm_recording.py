import pytest
from alphaloom.llm.client import LLMConfig, OFFLINE_DEFAULTS
from alphaloom.llm.recording import RecordingLLMClient, ReplayMissError
from alphaloom.llm.retry import with_retry
from alphaloom.sandbox.audit import AuditLog

def _fake_transport(canned):
    calls = []
    def send(req):
        calls.append(req); return canned
    send.calls = calls
    return send

def test_offline_config_defaults(monkeypatch):
    monkeypatch.setenv("ALPHALOOM_OFFLINE", "1")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    cfg = LLMConfig.from_env(dotenv_path=None)
    assert cfg.model == OFFLINE_DEFAULTS["LLM_MODEL"]
    assert cfg.base_url == OFFLINE_DEFAULTS["LLM_BASE_URL"]

def test_record_then_replay(tmp_path):
    canned = {"choices": [{"message": {"content": "hi"}}]}
    tr = _fake_transport(canned)
    c = RecordingLLMClient(tr, tmp_path / "llm.sqlite", model="m", offline=False)
    assert c.chat([{"role": "user", "content": "q"}]) == canned
    assert c.cache_misses == 1 and len(tr.calls) == 1
    assert c.chat([{"role": "user", "content": "q"}]) == canned
    assert c.cache_hits == 1 and len(tr.calls) == 1

def test_offline_miss_raises(tmp_path):
    c = RecordingLLMClient(_fake_transport({}), tmp_path / "llm.sqlite", model="m", offline=True)
    with pytest.raises(ReplayMissError):
        c.chat([{"role": "user", "content": "never"}])

def test_temperature_int_float_same_key(tmp_path):
    tr = _fake_transport({"ok": 1})
    c = RecordingLLMClient(tr, tmp_path / "llm.sqlite", model="m", offline=False)
    c.chat([{"role": "user", "content": "q"}], temperature=1)
    c.chat([{"role": "user", "content": "q"}], temperature=1.0)
    assert len(tr.calls) == 1 and c.cache_hits == 1

def test_retry_backoff_on_rate_limit():
    waits = []; attempts = [0]
    def flaky(req):
        attempts[0] += 1
        if attempts[0] < 3:
            raise RuntimeError("HTTP 429 code 11210 busy")
        return {"ok": 1}
    assert with_retry(flaky, sleep=waits.append)({"x": 1}) == {"ok": 1}
    assert waits == [15.0, 30.0]

def test_audit_log():
    log = AuditLog()
    log.record(tool="llm_chat", params={"node": "analyst"}, note="ok")
    assert log.as_dicts()[0]["tool"] == "llm_chat"

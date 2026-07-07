import pytest
from alphaloom.llm.client import LLMConfig, LLMConfigError, OFFLINE_DEFAULTS
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

def test_forced_live_config_ignores_offline_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHALOOM_OFFLINE", "1")
    monkeypatch.setenv("LLM_BASE_URL", "https://spark.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "astron-code-latest")

    cfg = LLMConfig.from_env(dotenv_path=tmp_path / "no.env", offline=False)

    assert cfg.base_url == "https://spark.example/v1"
    assert cfg.api_key == "secret"
    assert cfg.model == "astron-code-latest"

def test_live_config_loads_backend_dotenv_by_default(monkeypatch, tmp_path):
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    env_dir = tmp_path / "backend"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "LLM_BASE_URL=https://spark.example/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=astron-code-latest\n",
        encoding="utf-8")

    cfg = LLMConfig.from_env(offline=False)

    assert cfg.base_url == "https://spark.example/v1"
    assert cfg.api_key == "test-key"
    assert cfg.model == "astron-code-latest"

def test_live_config_can_reload_dotenv_over_blank_process_values(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    env = tmp_path / ".env"
    env.write_text(
        "LLM_BASE_URL=https://spark.example/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=astron-code-latest\n",
        encoding="utf-8")

    cfg = LLMConfig.from_env(dotenv_path=env, offline=False,
                             dotenv_override=True)

    assert cfg.base_url == "https://spark.example/v1"
    assert cfg.api_key == "test-key"
    assert cfg.model == "astron-code-latest"

def test_live_config_error_lists_missing_keys(monkeypatch, tmp_path):
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(LLMConfigError) as exc:
        LLMConfig.from_env(dotenv_path=tmp_path / "missing.env", offline=False)

    message = str(exc.value)
    assert "LLM_BASE_URL" in message
    assert "LLM_API_KEY" in message
    assert "LLM_MODEL" in message
    assert "missing.env" in message

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

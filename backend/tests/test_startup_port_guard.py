import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "alphaloom_port_guard.ps1"


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows startup guard")


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/openapi.json":
            self.send_response(404)
            self.end_headers()
            return
        payload = getattr(self.server, "payload", b"{}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


@pytest.fixture
def hindsight_like_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
    server.payload = b'{"info":{"title":"Hindsight API"}}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _run_guard(port: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(GUARD),
            "-Port",
            str(port),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_port_guard_rejects_non_alphaloom_service(hindsight_like_service):
    result = _run_guard(hindsight_like_service)

    assert result.returncode == 2
    assert "PORT_OCCUPIED_BY_OTHER" in result.stdout

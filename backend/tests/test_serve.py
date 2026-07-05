from alphaloom.serve import create_default_app

def test_default_app_builds():
    app = create_default_app()
    paths = {r.path for r in app.routes}
    assert "/api/nodes" in paths and "/ws/runs/{run_id}" in paths

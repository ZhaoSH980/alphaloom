def test_websocket_library_available():
    """uvicorn[standard] 必须带 WS 库，否则 /ws 全 404（TestClient 测不出，故显式锁依赖）。"""
    import importlib
    assert importlib.util.find_spec("websockets") is not None, \
        "websockets missing: uvicorn[standard] not installed, WS will 404 under uvicorn"

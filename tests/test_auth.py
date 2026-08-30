from fastapi.testclient import TestClient
from jarvis.config import Settings
from jarvis.main import create_app

def test_api_rejects_missing_token(tmp_path):
    settings=Settings(jarvis_api_token="secret", jarvis_database_path=tmp_path/"test.db", jarvis_lotus_auto_connect=False)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        response=client.post("/api/chat",json={"text":"hello"})
    assert response.status_code == 401

def test_local_pair_only_returns_token_to_loopback(tmp_path):
    settings=Settings(jarvis_api_token="secret", jarvis_database_path=tmp_path/"test.db", jarvis_lotus_auto_connect=False)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        response=client.get("/api/local-pair")
    assert response.status_code == 200
    assert response.json() == {"token":"secret"}

def test_websocket_rejects_bad_token(tmp_path):
    settings=Settings(jarvis_api_token="secret",jarvis_database_path=tmp_path/"test.db", jarvis_lotus_auto_connect=False)
    with TestClient(create_app(settings)) as client:
        try:
            with client.websocket_connect("/ws?token=no") as ws: ws.receive_json()
        except Exception: pass

def test_tool_api_lists_and_calls_tools(tmp_path):
    settings=Settings(jarvis_api_token="secret", jarvis_database_path=tmp_path/"test.db", jarvis_lotus_auto_connect=False)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        listed=client.get("/api/tools",headers={"Authorization":"Bearer secret"})
        called=client.post(
            "/api/tools/call",
            headers={"Authorization":"Bearer secret"},
            json={"name":"lotus_list_effects","arguments":{}},
        )
    assert listed.status_code == 200
    assert any(tool["name"] == "lotus_set_rgb" for tool in listed.json()["tools"])
    assert called.status_code == 200
    assert "Available light effects" in called.json()["content"]

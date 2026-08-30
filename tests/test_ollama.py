import httpx, pytest
from jarvis.ollama import OllamaClient, OllamaUnavailable

@pytest.mark.asyncio
async def test_ollama_connection_error_is_clear(monkeypatch):
    async def fail(self, *args, **kwargs): raise httpx.ConnectError("nope")
    monkeypatch.setattr(httpx.AsyncClient, "post", fail)
    with pytest.raises(OllamaUnavailable, match="OLLAMA_BASE_URL"):
        await OllamaClient("http://none", "test", 1).chat([], [])

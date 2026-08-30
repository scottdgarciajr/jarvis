from __future__ import annotations

import httpx


class OllamaUnavailable(RuntimeError): pass

class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float) -> None:
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.is_success
        except httpx.HTTPError: return False

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json={"model": self.model, "messages": messages, "tools": tools, "stream": False})
                response.raise_for_status()
                return response.json()["message"]
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Ollama is unavailable at {self.base_url}. Check OLLAMA_BASE_URL and that Ollama is running.") from exc

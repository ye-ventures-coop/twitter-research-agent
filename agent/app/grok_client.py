"""Асинхронный клиент к OpenAI-совместимому API grok2api."""
from __future__ import annotations

import httpx


class GrokClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 300.0):
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    async def chat(self, model: str, system: str, user: str) -> str:
        """Один chat-completion запрос. Возвращает текст ответа."""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base}/v1/models",
                headers={"Authorization": f"Bearer {self._key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        return [m.get("id", "") for m in data.get("data", [])]

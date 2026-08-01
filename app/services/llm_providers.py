from __future__ import annotations

import logging
import subprocess
import httpx
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import select
from app.config import OLLAMA_HOST, OPENAI_API_KEY, OPENROUTER_API_KEY
from app.models import Settings

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 180.0

_ollama_proc: subprocess.Popen | None = None


def start_ollama() -> None:
    """Start ollama serve if not already running."""
    global _ollama_proc
    try:
        resp = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            logger.info("Ollama already running — skipping start")
            return
    except Exception:
        pass

    try:
        _ollama_proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Started ollama serve (pid=%d)", _ollama_proc.pid)
    except FileNotFoundError:
        logger.warning("ollama binary not found. Install Ollama or start it manually.")
    except Exception as e:
        logger.warning("Failed to start ollama: %s", e)


def stop_ollama() -> None:
    """Stop ollama serve if we started it."""
    global _ollama_proc
    if _ollama_proc is None:
        return
    if _ollama_proc.poll() is None:
        _ollama_proc.terminate()
        try:
            _ollama_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _ollama_proc.kill()
        logger.info("Stopped ollama serve")
    _ollama_proc = None


@dataclass
class LLMStatus:
    online: bool
    loaded: bool = False


class LLMProvider(ABC):
    name: str = ""

    @abstractmethod
    async def check_status(self, model: str) -> LLMStatus:
        ...

    @abstractmethod
    async def generate(self, prompt: str, model: str) -> str:
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        ...

    def supports_parallel(self) -> bool:
        return False

    def supports_unload(self) -> bool:
        return False


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self):
        self.host = OLLAMA_HOST

    async def check_status(self, model: str) -> LLMStatus:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                try:
                    r = await client.get(f"{self.host}/api/ps")
                    if r.status_code == 200:
                        running = r.json().get("models", [])
                        loaded = any(m.get("name", "").startswith(model) for m in running)
                        if loaded:
                            return LLMStatus(online=True, loaded=True)
                except Exception:
                    pass

                r = await client.get(f"{self.host}/api/tags")
                if r.status_code == 200:
                    return LLMStatus(online=True, loaded=False)
        except Exception:
            pass
        return LLMStatus(online=False)

    async def generate(self, prompt: str, model: str) -> str:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{self.host}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "keep_alive": "5m",
                    "options": {
                        "temperature": 0.1,
                        "num_ctx": 4096,
                        "num_predict": 512,
                        "num_gpu": 999,
                    },
                },
            )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/api/tags")
                if r.status_code == 200:
                    return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            pass
        return []

    def supports_parallel(self) -> bool:
        return False

    def supports_unload(self) -> bool:
        return True

    async def unload(self, model: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.host}/api/generate",
                    json={"model": model, "keep_alive": 0},
                )
                return resp.status_code == 200
        except Exception:
            return False


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.openai.com/v1"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def check_status(self, model: str) -> LLMStatus:
        if not self.api_key:
            return LLMStatus(online=False)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
                return LLMStatus(online=r.status_code == 200)
        except Exception:
            pass
        return LLMStatus(online=False)

    async def generate(self, prompt: str, model: str) -> str:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 512,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    async def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    return sorted([m["id"] for m in data if "gpt" in m["id"].lower()])
        except Exception:
            pass
        return []

    def supports_parallel(self) -> bool:
        return True


class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://openrouter.ai/api/v1"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "InboxZen",
        }

    async def check_status(self, model: str) -> LLMStatus:
        if not self.api_key:
            return LLMStatus(online=False)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
                return LLMStatus(online=r.status_code == 200)
        except Exception:
            pass
        return LLMStatus(online=False)

    async def generate(self, prompt: str, model: str) -> str:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 512,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    async def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    return sorted([m["id"] for m in data])
        except Exception:
            pass
        return []

    async def list_models_detailed(self) -> list[dict]:
        if not self.api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    models = []
                    for m in data:
                        pricing = m.get("pricing", {})
                        prompt_price = float(pricing.get("prompt", "0") or "0")
                        completion_price = float(pricing.get("completion", "0") or "0")
                        models.append({
                            "id": m["id"],
                            "name": m.get("name", m["id"]),
                            "context_length": m.get("context_length", 0),
                            "prompt_price": prompt_price,
                            "completion_price": completion_price,
                        })
                    return sorted(models, key=lambda x: x["id"])
        except Exception:
            pass
        return []

    def supports_parallel(self) -> bool:
        return True


async def get_provider(db=None) -> LLMProvider:
    provider_name = "ollama"
    api_key = ""

    if db is not None:
        result = await db.execute(select(Settings).where(Settings.key == "llm_provider"))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            provider_name = setting.value

        key_name = f"{provider_name}_api_key"
        if provider_name in ("openai", "openrouter"):
            result = await db.execute(select(Settings).where(Settings.key == key_name))
            setting = result.scalar_one_or_none()
            fallback_key = OPENAI_API_KEY if provider_name == "openai" else OPENROUTER_API_KEY
            api_key = (setting.value if setting else "") or fallback_key

    if provider_name == "openai":
        return OpenAIProvider(api_key=api_key)
    elif provider_name == "openrouter":
        return OpenRouterProvider(api_key=api_key)
    return OllamaProvider()

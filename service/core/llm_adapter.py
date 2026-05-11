"""Unified LLM call interface — abstracts Ollama vs OpenAI-compatible APIs."""

import re

import httpx
import requests

from service.config import ProviderConfig


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def call_llm_sync(provider_config: ProviderConfig, model: str, prompt: str) -> str:
    if provider_config.type == "ollama":
        return _call_ollama_sync(provider_config.url, model, prompt)
    return _call_openai_sync(provider_config, model, prompt)


async def call_llm_async(provider_config: ProviderConfig, model: str, prompt: str) -> str:
    if provider_config.type == "ollama":
        return await _call_ollama_async(provider_config.url, model, prompt)
    return await _call_openai_async(provider_config, model, prompt)


def _call_ollama_sync(url: str, model: str, prompt: str) -> str:
    resp = requests.post(
        f"{url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


async def _call_ollama_async(url: str, model: str, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


def _call_openai_sync(config: ProviderConfig, model: str, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    url = config.url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    resp = requests.post(
        url,
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _strip_think_tags(content)


async def _call_openai_async(config: ProviderConfig, model: str, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    url = config.url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _strip_think_tags(content)


def _strip_think_tags(text: str) -> str:
    return _THINK_RE.sub("", text)

"""
Provider adapters for live multi-model chat (Section 4.1–4.2).

One async interface, four implementations:

    async def stream_chat(spec, prompt, history) -> AsyncGenerator[str]
        spec    — registry entry (model id, provider, credentials, caps)
        prompt  — the user's message for this turn
        history — [{role: "user"|"assistant", content: str}, ...] prior turns
                  of THIS model inside the same session

Every adapter normalizes its vendor's streaming event format into plain
token strings:

    OpenAI / Groq (OpenAI-compatible)   choices[0].delta.content
    Anthropic                          content_block_delta -> delta.text
    Gemini                             streamGenerateContent candidates parts

Usage/cost facts are written into the `usage` dict the caller passes in —
providers that stream usage (OpenAI with include_usage, Anthropic
message_delta, Gemini usageMetadata) fill it; the caller falls back to an
estimate when they don't.

Non-negotiables:
  * API keys NEVER leave this module's process — read from env only, and the
    registry only ever reports *whether* a key exists, never the value.
  * No fake fallback: a missing key raises NotConfigured, and the API layer
    turns that into an honest "not connected" state for that model.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator

import httpx


class NotConfigured(Exception):
    """Provider API key missing — surface as 'not connected', never fake it."""


# ---------------------------------------------------------------------------
# Registry — the single place a new model gets added (Section 7).
# Model names are env-overridable so operators can move to newer releases
# without code changes: e.g. OPENAI_CHAT_MODEL=gpt-5-mini.
# ---------------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


REGISTRY: list[dict] = [
    {
        "id": "gpt-4o-mini",
        "provider": "openai",
        "label": "GPT-4o mini",
        "vendor_model_env": "OPENAI_CHAT_MODEL",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": _env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    },
    {
        "id": "claude-3-5-haiku",
        "provider": "anthropic",
        "label": "Claude 3.5 Haiku",
        "vendor_model_env": "ANTHROPIC_CHAT_MODEL",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
    },
    {
        "id": "gemini-2.0-flash",
        "provider": "gemini",
        "label": "Gemini 2.0 Flash",
        "vendor_model_env": "GEMINI_CHAT_MODEL",
        "api_key_env": "GOOGLE_API_KEY",
        "base_url": _env(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        ),
    },
    {
        # Same free GOOGLE_API_KEY, lighter/faster tier — no new adapter.
        "id": "gemini-2.5-flash-lite",
        "provider": "gemini",
        "label": "Gemini 2.5 Flash-Lite",
        "vendor_model_env": "GEMINI_CHAT_MODEL_LITE",
        "api_key_env": "GOOGLE_API_KEY",
        "base_url": _env(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        ),
    },
    {
        # Same free GROQ_API_KEY, fastest Groq tier (highest rate limits).
        "id": "llama-3.1-8b-instant",
        "provider": "groq",
        "label": "Llama 3.1 8B Instant (Groq)",
        "vendor_model_env": "GROQ_CHAT_MODEL_8B",
        "api_key_env": "GROQ_API_KEY",
        "base_url": _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    },
    {
        # Same free GROQ_API_KEY, Google's open-weights model.
        "id": "gemma2-9b-it",
        "provider": "groq",
        "label": "Gemma2 9B (Groq)",
        "vendor_model_env": "GROQ_CHAT_MODEL_GEMMA2",
        "api_key_env": "GROQ_API_KEY",
        "base_url": _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    },
    {
        "id": "llama-3.3-70b",
        "provider": "groq",
        "label": "Llama 3.3 70B (Groq)",
        "vendor_model_env": "GROQ_CHAT_MODEL",
        "api_key_env": "GROQ_API_KEY",
        "base_url": _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    },
]

REQUEST_TIMEOUT = float(_env("CHAT_TIMEOUT_SECONDS", "60"))   # per-model cap
MAX_OUTPUT_TOKENS = int(_env("CHAT_MAX_TOKENS", "512"))       # per-model cap

# Test seam: when set, stream_chat() calls this instead of the network.
_testing_stream = None


def get_spec(model_id: str) -> dict | None:
    return next((spec for spec in REGISTRY if spec["id"] == model_id), None)


def is_connected(spec: dict) -> bool:
    """True when a real GLOBAL API key is present (operator-funded)."""
    return bool(_env(spec["api_key_env"]))


def user_key_for(spec: dict, user) -> str | None:
    """The user's own BYOK key for this provider, decrypted — or None.

    Never raises; a decrypt failure (e.g. rotated SECRET_KEY) reads as
    'no key stored'.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    from .crypto import decrypt_secret
    from .models import UserProviderKey

    row = UserProviderKey.objects.filter(user=user, provider=spec["provider"]).first()
    if row is None:
        return None
    return decrypt_secret(row.key_ciphertext)


def get_api_key(spec: dict, user=None) -> str | None:
    """Resolution order: global env var (operator-funded) -> user's BYOK key."""
    return _env(spec["api_key_env"]) or user_key_for(spec, user)


def is_connected_for(spec: dict, user) -> bool:
    """Global key OR this user's stored key (BYOK, Fix C)."""
    return is_connected(spec) or user_key_for(spec, user) is not None


def is_reachable(spec: dict, user=None) -> bool:
    """Connected for real (globally or per-user), OR the testing seam is
    installed (test runs only). Drives the honest 'not connected' column."""
    return is_connected_for(spec, user) or _testing_stream is not None


def vendor_model(spec: dict) -> str:
    return _env(spec["vendor_model_env"]) or spec["id"]


def public_models(user=None) -> list[dict]:
    """Registry view for the API/frontend — booleans only, never keys.

    `connected` is user-aware: a globally configured provider is connected
    for everyone; a user-pasted BYOK key connects it for that user alone.
    """
    return [
        {
            "id": spec["id"],
            "provider": spec["provider"],
            "label": spec["label"],
            "model_name": vendor_model(spec),
            "connected": is_connected_for(spec, user),
        }
        for spec in REGISTRY
    ]


# ---------------------------------------------------------------------------
# SSE line parsing shared by all adapters (providers speak SSE)
# ---------------------------------------------------------------------------
async def _sse_payloads(response: httpx.Response) -> AsyncGenerator[dict]:
    """Yield parsed JSON payloads from a text/event-stream response body."""
    async for line in response.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue  # vendor keep-alives / comments


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
async def stream_chat(spec: dict, prompt: str, history: list[dict],
                      usage: dict, api_key: str | None = None) -> AsyncGenerator[str]:
    """Vendor-neutral streaming entry point. Yields plain token strings.

    `api_key` (resolved via get_api_key, env-first then BYOK) is optional —
    adapters fall back to the global env var when omitted.
    """
    if _testing_stream is not None:  # testing seam — see chat/tests
        async for token in _testing_stream(spec, prompt, history, usage, api_key=api_key):
            yield token
        return

    key = api_key or _env(spec["api_key_env"])
    if not key:
        raise NotConfigured(f"{spec['provider']} key not configured")

    dispatch = {
        "openai": _stream_openai_compatible,
        "groq": _stream_openai_compatible,
        "anthropic": _stream_anthropic,
        "gemini": _stream_gemini,
    }
    adapter = dispatch[spec["provider"]]
    async for token in adapter(spec, prompt, history, usage, api_key=key):
        yield token


async def _stream_openai_compatible(spec, prompt, history, usage, api_key) -> AsyncGenerator[str]:
    """OpenAI and Groq share the chat/completions SSE shape (Section 4.1)."""
    payload = {
        "model": vendor_model(spec),
        "messages": [*history, {"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    if spec["provider"] == "openai":
        payload["stream_options"] = {"include_usage": True}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{spec['base_url']}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {response.status_code}: {body}")
            async for chunk in _sse_payloads(response):
                if chunk.get("usage"):
                    usage.update(chunk["usage"])
                choices = chunk.get("choices") or []
                if choices:
                    token = choices[0].get("delta", {}).get("content")
                    if token:
                        yield token


async def _stream_anthropic(spec, prompt, history, usage, api_key) -> AsyncGenerator[str]:
    payload = {
        "model": vendor_model(spec),
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": True,
        "messages": [*history, {"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{spec['base_url']}/messages",
            json=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {response.status_code}: {body}")
            async for event in _sse_payloads(response):
                kind = event.get("type", "")
                if kind == "content_block_delta":
                    token = event.get("delta", {}).get("text")
                    if token:
                        yield token
                elif kind == "message_delta":
                    if event.get("usage", {}).get("output_tokens") is not None:
                        usage["output_tokens"] = event["usage"]["output_tokens"]
                        usage["completion_tokens"] = event["usage"]["output_tokens"]


async def _stream_gemini(spec, prompt, history, usage, api_key) -> AsyncGenerator[str]:
    # Gemini history shape: {role, parts: [{text}]}
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in history
    ]
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    url = (
        f"{spec['base_url']}/models/{vendor_model(spec)}:streamGenerateContent"
        f"?alt=sse&key={api_key}"
    )
    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS}}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        async with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {response.status_code}: {body}")
            async for chunk in _sse_payloads(response):
                if chunk.get("usageMetadata"):
                    usage["completion_tokens"] = chunk["usageMetadata"].get(
                        "candidatesTokenCount"
                    )
                candidates = chunk.get("candidates") or []
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts") or []
                    for part in parts:
                        token = part.get("text")
                        if token:
                            yield token


def estimate_tokens(text: str) -> int:
    """Fallback when a provider doesn't stream usage: ~4 chars/token."""
    return max(1, len(text) // 4)

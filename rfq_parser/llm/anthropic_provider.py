"""Anthropic (Messages API). Optional: switched on with RFQ_LLM_PROVIDER=anthropic, on your own key."""

from __future__ import annotations

import json

from .base import LLMUnavailable, b64, provider_failure

# Models that accept the server-side refusal fallback.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"
    key_env = "ANTHROPIC_API_KEY"
    default_model = "claude-opus-5-5"

    # USD per 1M tokens as (input, cache read, output). First-party API rates as of 2026-11-03.
    prices = {
        "claude-fable-5-2": (8.00, 0.20, 40.00),
        "claude-opus-5-5": (4.00, 0.20, 20.00),
        "claude-sonnet-5-5": (2.00, 0.10, 10.00),
    }

    def __init__(self, api_key: str | None = None):
        # With no explicit key the SDK resolves credentials itself (ANTHROPIC_API_KEY,
        # ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile), so there is no key check here.
        self._api_key = api_key
        self._client = None

    def build_request(self, *, model, task, instructions, parts, schema, effort) -> dict:
        request = {
            "model": model,
            "max_tokens": 16000,
            "system": instructions,
            "messages": [{"role": "user", "content": [self._part(p) for p in parts]}],
            # Thinking is adaptive by default on current models; effort is the only depth control.
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        }
        if model in _FALLBACK_MODELS:
            # If a safety classifier declines the request, the API re-runs it on a fallback model.
            request["betas"] = [_FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    @staticmethod
    def _part(part: dict) -> dict:
        if part["kind"] == "text":
            return {"type": "text", "text": part["text"]}
        if part["kind"] == "image":
            source = {"type": "base64", "media_type": part["mime"], "data": b64(part["data"])}
            return {"type": "image", "source": source}
        source = {"type": "base64", "media_type": "application/pdf", "data": b64(part["data"])}
        return {"type": "document", "source": source}

    def call(self, request: dict) -> dict:
        try:
            sdk = self._sdk()
            messages = sdk.beta.messages if "betas" in request else sdk.messages
            response = messages.create(**request)
        except Exception as exc:
            raise provider_failure(exc) from exc
        if response.stop_reason != "end_turn":  # refusal, or cut off at max_tokens
            raise LLMUnavailable(f"model stopped with {response.stop_reason}, not end_turn")
        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            output = json.loads(text)
        except ValueError as exc:
            raise LLMUnavailable("model answer was not valid JSON") from exc
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return {
            "model": response.model,  # the model that answered; differs from the request after a fallback
            "output": output,
            "usage": {
                "input_tokens": usage.input_tokens + cache_read + cache_write,
                "cached_input_tokens": cache_read,
                "output_tokens": usage.output_tokens,
            },
        }

    def _sdk(self):
        if self._client is None:
            import anthropic  # imported late: only needed when this provider is switched on

            kwargs = {"api_key": self._api_key} if self._api_key else {}
            self._client = anthropic.Anthropic(timeout=120.0, max_retries=3, **kwargs)
        return self._client
